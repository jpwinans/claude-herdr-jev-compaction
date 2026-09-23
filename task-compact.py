#!/usr/bin/env python3
"""Stop hook: run /compact when a turn ends with the task finished.

TypeSafe's Jev judges the last user request + final reply with two Nouls
(done, waiting). If done and not waiting, this types `/compact` into the
session's herdr pane (Claude Code has no hook output that triggers
compaction). Outside herdr it does nothing.

The hook returns at once; the Jev call and injection run in a detached copy.
Env: TASK_COMPACT_MIN_TOKENS (default 600000), TASK_COMPACT_DRY=1 logs only.
Key: TYPESAFE_API_KEY, else (macOS) Keychain item `typesafe-api-key`.
Decisions are logged to ~/.claude/state/task-compact.log for threshold tuning.
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request

# Ask Jev only past 60% of a 1M context window (use ~120000 for 200k); built-in auto-compact is the backstop.
MIN_TOKENS = int(os.environ.get("TASK_COMPACT_MIN_TOKENS", "600000"))
# Playground runs (jev-1.13.0): finished tasks scored 0.89-0.97, unfinished ones <= 0.23.
DONE_MIN, WAITING_MAX = 0.75, 0.3
LOG = os.path.expanduser("~/.claude/state/task-compact.log")

QUESTIONS = {
    "done": {"type": "noul",
             "instructions": "The assistant has finished the task the user asked for.",
             "criteria": {"true": "Work delivered or question answered; nothing left in progress",
                          "false": "Work partial, blocked, or the assistant says it will continue"}},
    "waiting": {"type": "noul",
                "instructions": "The requested task cannot be completed until the user answers, approves, or provides something.",
                "criteria": {"true": "The assistant stopped before finishing and needs the user's input to continue the requested work",
                             "false": "The requested work is complete; any question only offers optional extra work beyond what was asked"}},
}


def tail_entries(transcript, nbytes=2_000_000):
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not e.get("isSidechain"):
            out.append(e)
    return out


def context_tokens(entries):
    # Context size = prompt tokens of the most recent API call.
    for e in reversed(entries):
        u = (e.get("message") or {}).get("usage")
        if u:
            return sum(u.get(k, 0) for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return 0


def last_user_prompt(entries):
    for e in reversed(entries):
        if e.get("type") != "user" or e.get("isMeta"):
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, list):  # tool_result turns have no text blocks
            c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
        if c:
            return c
    return ""


def api_key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if k or sys.platform != "darwin":
        return k
    r = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                        "-s", "typesafe-api-key", "-w"], capture_output=True, text=True)
    return r.stdout.strip()


def jev(request, reply):
    body = {"model": "jev-latest",
            "state": {"user_request": request[-4000:], "assistant_final_reply": reply[-8000:]},
            "questions": QUESTIONS}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", json.dumps(body).encode(),
                                 {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})
    # 529 = overloaded; retry twice (2s, 4s) since this runs in a detached background copy. Anything
    # else, or a 529 on the last attempt, raises as before so background() fails closed.
    for attempt, delay in enumerate((2, 4, None), 1):
        try:
            a = json.load(urllib.request.urlopen(req, timeout=20))["answers"]
            return a["done"]["noul"], a["waiting"]["noul"], attempt
        except urllib.error.HTTPError as e:
            if e.code != 529 or delay is None:
                raise
            time.sleep(delay)


def inject(text):
    pane = os.environ["HERDR_PANE_ID"]
    subprocess.run(["herdr", "pane", "send-text", pane, text], check=True, capture_output=True)
    time.sleep(0.5)  # separate Enter so the TUI doesn't treat it as a paste
    subprocess.run(["herdr", "pane", "send-keys", pane, "enter"], check=True, capture_output=True)
    return True


def log(**kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}) + "\n")


def decide(data):
    """Returns (fire, details). Pure apart from the Jev call."""
    entries = tail_entries(data.get("transcript_path", ""))
    tokens = context_tokens(entries)
    if tokens < MIN_TOKENS:
        return False, {"tokens": tokens, "skip": "small"}
    done, waiting, attempts = jev(last_user_prompt(entries), data.get("last_assistant_message") or "")
    return done >= DONE_MIN and waiting < WAITING_MAX, {"tokens": tokens, "done": done, "waiting": waiting,
                                                         "attempts": attempts}


def background(data, settle=2):
    # Stop fires before the final turn is flushed to the transcript and before the TUI is idle.
    time.sleep(settle)
    transcript = data.get("transcript_path", "")
    size = os.path.getsize(transcript) if os.path.exists(transcript) else 0
    try:
        fire, info = decide(data)
    except Exception as e:  # fail closed: no compaction
        return log(session=data.get("session_id"), error=repr(e)[:200])
    if fire and not os.environ.get("TASK_COMPACT_DRY"):
        # A new turn started (user typed fast): the moment has passed.
        if os.path.exists(transcript) and os.path.getsize(transcript) != size:
            info["skip"] = "new-turn"
        else:
            try:
                info["injected"] = inject("/compact")
            except (subprocess.CalledProcessError, OSError) as e:  # OSError: herdr not on PATH
                err = getattr(e, "stderr", None)
                info["injected"], info["error"] = False, (err.decode() if err else repr(e))[:200]
    log(session=data.get("session_id"), fire=fire, **info)


def main():
    raw = sys.stdin.read()
    data = json.loads(raw)
    if os.environ.get("TASK_COMPACT_BG"):
        return background(data)
    # herdr only: that's where long-running sessions live and how /compact gets typed in.
    if data.get("stop_hook_active") or not os.environ.get("HERDR_PANE_ID"):
        return
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "TASK_COMPACT_BG": "1"}, start_new_session=True)
    p.stdin.write(raw.encode())
    p.stdin.close()


if __name__ == "__main__":
    main()
