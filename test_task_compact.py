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
    f.write("\n".join(map(json.dumps, rows)) + "\n")
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
def skip_of(extra, reply="Renamed in 23 files."):
    fire, info = tc.decide({"transcript_path": transcript(tc.MIN_TOKENS + 10_000, extra),
                            "last_assistant_message": reply})
    assert not fire, info
    return info["skip"]
prompt = {"type": "user", "message": {"content": "now add tests"}}
image = {"type": "user", "message": {"content": [{"type": "image", "source": {"type": "base64"}}]}}
tool_use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}}
assert skip_of([prompt]) == "new-turn"
assert skip_of([image]) == "new-turn"  # image-only prompts start turns too
# A newer turn repeats the reply's text and keeps working: don't anchor on its copy.
assert skip_of([prompt, say("Renamed in 23 files.", tc.MIN_TOKENS + 20_000), tool_use]) == "no-reply"
# The reply isn't the newest assistant entry (not flushed yet): don't anchor on an older identical one.
assert skip_of([], reply="Old reply.") == "no-reply"
assert skip_of([], reply="Something else.") == "no-reply"
# A queued message starts a turn that ends with the same text before the hook reads the transcript:
# the prompt past the Stop-time size gives it away.
repeat = transcript(tc.MIN_TOKENS + 10_000)
stop_size = os.path.getsize(repeat)
with open(repeat, "a") as f:
    f.write(json.dumps(prompt) + "\n" + json.dumps(say("Renamed in 23 files.", tc.MIN_TOKENS + 20_000)) + "\n")
fire, info = tc.decide({**data(repeat), "_stop_size": stop_size})
assert not fire and info["skip"] == "new-turn", info

# A broken record after the first line fails closed; a first line cut by the tail seek is fine.
broken = transcript(tc.MIN_TOKENS + 10_000)
with open(broken, "a") as f:
    f.write('{"type": "user", "message": {"content": "half-writ')
fire, info = tc.decide(data(broken))
assert not fire and info["skip"] == "unreadable", info
assert len(tc.tail_entries(big, nbytes=os.path.getsize(big) - 5)) == len(tc.tail_entries(big)) - 1

# The request fell outside the transcript tail, or has no text to judge.
headless = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
headless.write(json.dumps(say("Renamed in 23 files.", tc.MIN_TOKENS + 10_000)) + "\n")
headless.close()
fire, info = tc.decide(data(headless.name))
assert not fire and info["skip"] == "no-request", info
assert skip_of([image, say("Renamed in 23 files.", tc.MIN_TOKENS + 20_000)]) == "no-request"

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

# background + inject, with herdr stubbed: record commands, optionally act when one runs.
calls, on_call = [], {}
real_run = tc.subprocess.run
def fake_run(cmd, **kw):
    calls.append(cmd[2])
    on_call.get(cmd[2], lambda: None)()
    return real_run(["true"])
tc.subprocess.run = fake_run
current = {}
def run_bg(session, jev=fake_jev(0.9, 0.05)):
    calls.clear()
    tc.jev = jev
    open(tc.LOG, "w").close()
    current["t"] = transcript(tc.MIN_TOKENS + 10_000)
    tc.background({**data(current["t"]), "session_id": session}, settle=0)
    return json.loads(open(tc.LOG).read())
def user_types():
    with open(current["t"], "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "next"}}) + "\n")

row = run_bg("sent")
assert calls == ["send-text", "send-keys"] and row["injected"] is True, (calls, row)

os.environ["TASK_COMPACT_DRY"] = "1"
row = run_bg("dry")
del os.environ["TASK_COMPACT_DRY"]
assert calls == [] and row["fire"] is True and "injected" not in row, (calls, row)

# The user sends a message while Jev is thinking: nothing is typed.
def jev_while_user_types(request, reply):
    user_types()
    return 0.9, 0.05, 1
row = run_bg("typing", jev_while_user_types)
assert calls == [] and row["skip"] == "new-turn" and row["injected"] is False, (calls, row)

# ... or between the text and Enter: Enter is never sent.
on_call["send-text"] = user_types
row = run_bg("typing-late")
on_call.clear()
assert calls == ["send-text"] and row["skip"] == "new-turn-typed", (calls, row)

# A hung herdr call times out and is logged, not raised.
def hang(cmd, **kw):
    raise tc.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
tc.subprocess.run = hang
row = run_bg("hung")
assert row["injected"] is False and "TimeoutExpired" in row["error"], row
tc.subprocess.run = real_run

# main() hands the background copy the transcript size at Stop time.
handed = {}
class Pipe(io.BytesIO):
    def close(self):
        handed.update(json.loads(self.getvalue()))
tc.subprocess.Popen = lambda *a, **kw: type("P", (), {"stdin": Pipe()})()
tc.sys.stdin = io.StringIO(json.dumps(data(big)))
tc.main()
assert handed["_stop_size"] == os.path.getsize(big), handed

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
