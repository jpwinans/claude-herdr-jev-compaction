#!/usr/bin/env python3
"""Stop hook: run /compact at completed chunks and requested pause boundaries.

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
# Boundary prompt evaluated on 100 positives and 20 negatives; see evaluations/boundaries/.
DONE_MIN, WAITING_MAX = 0.75, 0.3
LOG = os.path.expanduser("~/.claude/state/task-compact.log")

QUESTIONS = {'done': {'type': 'noul',
          'instructions': 'The conversation has reached a good stopping boundary for context '
                          'compaction. Judge the current requested chunk of work, not whether the '
                          'entire larger project is finished. Evaluate the boundary from the '
                          'supplied request and reply; a concrete report that the requested '
                          'artifact is saved or the review is complete is sufficient evidence. Do '
                          'not require the full artifact inline or independently verify its '
                          'contents. First check for ongoing work: if the reply says the assistant '
                          'is now working, will continue immediately, or still owes actions within '
                          'the current request, answer false even if it also reports a completed '
                          'substep or saved artifact. This veto takes precedence over positive '
                          'examples. An explicit user-requested pause or limited milestone ends '
                          'the current request; an unsolicited status update does not. Interpret '
                          'pause requests by meaning, not keywords: postponing to another day or '
                          'session, taking a break, parking work, or saying enough for now all '
                          'request a stopping boundary. A recorded next step for a later session '
                          'is evidence of resumability, not a promise to continue immediately.',
          'criteria': {'true': 'The requested work is delivered, a requested milestone or review '
                               'handoff is ready, the user ended or narrowed the task and that '
                               'instruction was honored, or the user explicitly paused work and '
                               'the reply records enough progress and next steps to resume. A '
                               'later continuation or optional follow-up does not undo a completed '
                               'boundary. Completed reviews and diagnoses qualify even when they '
                               'report bugs or unresolved issues: fixing them is a separate task '
                               'unless requested. Drafts, runbooks, plans, and other handoff '
                               'documents qualify when prepared; their later use by a human is not '
                               'unfinished assistant work.',
                       'false': 'The assistant is continuing immediately, is in the middle of a '
                                'tool sequence or investigation, stopped short of the requested '
                                'chunk without the user requesting a pause, or explicitly reports '
                                'unsaved or unrecorded state that would be lost. A status update '
                                'or merely ending a message is not a boundary.'}},
 'waiting': {'type': 'noul',
             'instructions': 'The assistant is blocked on user input before reaching the requested '
                             'stopping boundary.',
             'criteria': {'true': 'Required clarification, data, access, or approval is missing '
                                  'and prevents delivery of the requested chunk of work; there is '
                                  'no completed requested milestone or explicit user-requested '
                                  'pause.',
                          'false': 'The requested chunk is delivered, or the user explicitly '
                                   'paused, stopped, canceled, or deferred work. A requested '
                                   'review or approval checkpoint has been prepared and reached. '
                                   'Optional follow-up offers and future work after a deliberate '
                                   'pause are not blocking input. Deferral expressed in ordinary '
                                   'language or scheduled for a later day is also a user-requested '
                                   'pause.'}}}


def tail_entries(transcript, nbytes=2_000_000):
    """Non-sidechain entries in the last `nbytes`, each with its byte offset as "_at". None if unreadable."""
    try:
        with open(transcript, "rb") as f:
            start = max(0, f.seek(0, 2) - nbytes)
            f.seek(max(0, start - 1))  # one byte early, to tell whether the seek cuts a line
            data = f.read()
    except OSError:
        return None
    cut = start > 0 and data[:1] != b"\n"
    data = data[1:] if start else data
    out, at = [], start
    for i, line in enumerate(data.split(b"\n")):
        pos, at = at, at + len(line) + 1
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            if i == 0 and cut:
                continue  # the seek cut this record in half
            return None  # a broken or half-written record could hide a new turn: fail closed
        if not e.get("isSidechain"):
            e["_at"] = pos
            out.append(e)
    return out


def usage_tokens(e):
    # Context size = prompt tokens of an API call.
    u = (e.get("message") or {}).get("usage") or {}
    return sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def is_prompt(e):
    """A user turn: text or attachments, not a tool result or an injected meta message."""
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    c = (e.get("message") or {}).get("content")
    return any(b.get("type") != "tool_result" for b in c) if isinstance(c, list) else bool(c)


def is_human_prompt(e):
    """A prompt the user typed. Task notifications and peer hand-backs also start turns, but they
    aren't the request Jev should judge the reply against."""
    if not is_prompt(e):
        return False
    origin = e.get("origin")
    if isinstance(origin, dict) and "kind" in origin:
        return origin["kind"] == "human"
    return not prompt_text(e).lstrip().startswith("<task-notification>")


def prompt_text(e):
    c = (e.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    return c or ""


def reply_tail(e):
    c = (e.get("message") or {}).get("content")
    text = "\n".join(b.get("text", "") for b in c if b.get("type") == "text") if isinstance(c, list) else ""
    return " ".join(text.split())[-200:]


def find_turn(entries, reply, stop_size=None):
    """(skip reason or None, request, tokens) for the turn that ended with `reply`.

    Each reply block is its own transcript entry, so the newest assistant entry must be the one the
    reply ends with. Anything newer (a prompt, more assistant output) means another turn has started,
    and that turn isn't ours to judge. Missing pieces fail closed.

    `stop_size` is the transcript size when Stop fired. The request that just finished was written
    before then, so any prompt past it belongs to a newer turn, even one that ended with the same text.
    """
    if stop_size is not None and any(is_prompt(e) and e["_at"] >= stop_size for e in entries):
        return "new-turn", "", 0
    reply = " ".join(reply.split())
    for end in range(len(entries) - 1, -1, -1):
        e = entries[end]
        if is_prompt(e):
            return "new-turn", "", 0
        if e.get("type") == "assistant":
            tail = reply_tail(e)
            if not (reply and tail and reply.endswith(tail)):
                return "no-reply", "", 0  # not flushed yet, or a newer turn is running
            break
    else:
        return "no-reply", "", 0
    # The request is the user's last prompt: a notification or hand-back that woke this turn isn't.
    for i in range(end - 1, -1, -1):
        if is_human_prompt(entries[i]):
            tokens = next((t for t in map(usage_tokens, reversed(entries[i:end + 1])) if t), 0)
            request = prompt_text(entries[i])
            return (None if request else "no-request"), request, tokens
    return "no-request", "", 0  # the request fell outside the transcript tail


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


def inject(text, unchanged):
    """Types `text` and Enter into this pane unless a new turn starts first. Returns what happened."""
    def herdr(*args):
        subprocess.run(["herdr", "pane", *args], check=True, capture_output=True, timeout=10)
    pane = os.environ["HERDR_PANE_ID"]
    if not unchanged():
        return "new-turn"
    herdr("send-text", pane, text)
    time.sleep(0.5)  # separate Enter so the TUI doesn't treat it as a paste
    if not unchanged():
        # ponytail: leaves the text in the input box; clear it if herdr gets a clear-line key
        return "new-turn-typed"
    herdr("send-keys", pane, "enter")
    return "sent"


def log(**kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}) + "\n")


def decide(data):
    """Returns (fire, details). Pure apart from the Jev call."""
    reply = data.get("last_assistant_message") or ""
    entries = tail_entries(data.get("transcript_path", ""))
    if entries is None:
        return False, {"skip": "unreadable"}
    skip, request, tokens = find_turn(entries, reply, data.get("_stop_size"))
    if skip:
        return False, {"tokens": tokens, "skip": skip}
    if tokens < MIN_TOKENS:
        return False, {"tokens": tokens, "skip": "small"}
    done, waiting, attempts = jev(request, reply)
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
        # Transcript growth means a new turn started (user typed fast): the moment has passed.
        unchanged = lambda: os.path.exists(transcript) and os.path.getsize(transcript) == size
        try:
            result = inject("/compact", unchanged)
            info["injected"] = result == "sent"
            if result != "sent":
                info["skip"] = result
        except (subprocess.SubprocessError, OSError) as e:  # OSError: herdr not on PATH
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
    transcript = data.get("transcript_path") or ""
    if not os.path.exists(transcript):
        return
    data["_stop_size"] = os.path.getsize(transcript)
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "TASK_COMPACT_BG": "1"}, start_new_session=True)
    p.stdin.write(json.dumps(data).encode())
    p.stdin.close()


if __name__ == "__main__":
    main()
