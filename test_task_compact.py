"""Self-check for task-compact.py: python3 test_task_compact.py"""
import importlib.util, io, json, os, tempfile, urllib.error

spec = importlib.util.spec_from_file_location("tc", os.path.join(os.path.dirname(__file__), "task-compact.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)


def say(text, tokens=0):
    usage = {"usage": {"input_tokens": 5, "cache_read_input_tokens": tokens}} if tokens else {}
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}], **usage}}


def transcript(tokens, extra=()):
    rows = [
        {"type": "user", "message": {"content": "old request"}},
        say("Old reply.", 1),
        {"type": "user", "message": {"content": [{"type": "text", "text": "rename getUser"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}],
                                          "usage": {"input_tokens": 5, "cache_read_input_tokens": tokens}}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "<system-reminder>"}},
        say("Renamed in\n23 files."),  # usage-less block: tokens come from the call before it
        {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 1}}},
        {"type": "system", "subtype": "stop_hook_summary"},
        *extra,
    ]
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("\n".join(map(json.dumps, rows)) + "\nnot json\n")
    f.close()
    return f.name


real_jev = tc.jev

seen = {}
def fake_jev(done, waiting, attempts=1):
    def j(request, reply):
        seen["request"] = request
        return done, waiting, attempts
    return j

big, small = transcript(tc.MIN_TOKENS + 10_000), transcript(tc.MIN_TOKENS - 10_000)
data = lambda t: {"transcript_path": t, "last_assistant_message": "Renamed in 23 files."}

tc.jev = fake_jev(0.9, 0.05)
fire, info = tc.decide(data(big))
assert fire and info["tokens"] == tc.MIN_TOKENS + 10_005, info
assert seen["request"] == "rename getUser", seen  # skips tool_result + meta turns

tc.jev = fake_jev(0.9, 0.9)   # finished but blocked on the user
assert not tc.decide(data(big))[0]
tc.jev = fake_jev(0.2, 0.05)  # not finished
assert not tc.decide(data(big))[0]

def boom(*a):
    raise AssertionError("Jev must not be called")
tc.jev = boom
fire, info = tc.decide(data(small))
assert not fire and info["skip"] == "small", info

# The user started a new turn before the hook read the transcript: never judge (or compact) it.
newer = transcript(tc.MIN_TOKENS + 10_000, [{"type": "user", "message": {"content": "now add tests"}},
                                            say("Working on it.", tc.MIN_TOKENS + 20_000)])
fire, info = tc.decide(data(newer))
assert not fire and info["skip"] == "new-turn", info
# The reply isn't in the transcript (not flushed yet), or its request fell outside the tail.
fire, info = tc.decide({"transcript_path": big, "last_assistant_message": "Something else."})
assert not fire and info["skip"] == "no-reply", info
headless = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
headless.write(json.dumps(say("Renamed in 23 files.", tc.MIN_TOKENS + 10_000)) + "\n")
headless.close()
fire, info = tc.decide(data(headless.name))
assert not fire and info["skip"] == "no-request", info

# background: Jev errors fail closed and are logged, not raised.
tc.LOG = tempfile.mktemp()
def err(*a):
    raise OSError("network down")
tc.jev = err
tc.background({**data(big), "session_id": "s1"}, settle=0)
row = json.loads(open(tc.LOG).read())
assert row["session"] == "s1" and "network down" in row["error"], row

# background: a missing herdr binary is logged as injected=false, not raised.
tc.jev = fake_jev(0.9, 0.05)
os.environ["HERDR_PANE_ID"] = "p1"
os.environ["PATH"], path = "/nonexistent", os.environ["PATH"]
open(tc.LOG, "w").close()
tc.background({**data(big), "session_id": "s2"}, settle=0)
os.environ["PATH"] = path
row = json.loads(open(tc.LOG).read())
assert row["injected"] is False and "FileNotFoundError" in row["error"], row

# background: dry mode and a turn started while Jev was thinking never inject.
injected = []
real_inject, tc.inject = tc.inject, lambda text: injected.append(text) or True
os.environ["TASK_COMPACT_DRY"] = "1"
tc.jev = fake_jev(0.9, 0.05)
tc.background({**data(big), "session_id": "s3"}, settle=0)
del os.environ["TASK_COMPACT_DRY"]
assert injected == [], injected
def jev_while_user_types(request, reply):
    with open(big, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "next"}}) + "\n")
    return 0.9, 0.05, 1
tc.jev = jev_while_user_types
open(tc.LOG, "w").close()
tc.background({**data(big), "session_id": "s4"}, settle=0)
row = json.loads(open(tc.LOG).read())
assert injected == [] and row["skip"] == "new-turn", row
tc.inject = real_inject

# jev() retries only on HTTP 529, at most 3 attempts, sleeping 2s then 4s. No network, no real sleep.
tc.jev = real_jev
tc.api_key = lambda: "testkey"
sleeps = []
tc.time.sleep = lambda s: sleeps.append(s)


def http_error(code):
    return urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", code, "err", None, None)


def response(done, waiting):
    body = json.dumps({"answers": {"done": {"noul": done}, "waiting": {"noul": waiting}}}).encode()
    return io.BytesIO(body)


calls = {"n": 0}
def urlopen_529_then_ok(req, timeout=20):
    calls["n"] += 1
    if calls["n"] == 1:
        raise http_error(529)
    return response(0.9, 0.1)
tc.urllib.request.urlopen = urlopen_529_then_ok
sleeps.clear()
assert tc.jev("req", "reply") == (0.9, 0.1, 2)
assert sleeps == [2], sleeps

def urlopen_always_529(req, timeout=20):
    raise http_error(529)
tc.urllib.request.urlopen = urlopen_always_529
sleeps.clear()
try:
    tc.jev("req", "reply")
    raise AssertionError("expected HTTPError")
except urllib.error.HTTPError as e:
    assert e.code == 529
assert sleeps == [2, 4], sleeps

def urlopen_500(req, timeout=20):
    raise http_error(500)
tc.urllib.request.urlopen = urlopen_500
sleeps.clear()
try:
    tc.jev("req", "reply")
    raise AssertionError("expected HTTPError")
except urllib.error.HTTPError as e:
    assert e.code == 500
assert sleeps == [], sleeps  # not retried

print("PASS")
