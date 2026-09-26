#!/usr/bin/env python3
"""Codex lifecycle hooks: compact completed tasks in a Herdr terminal pane.

UserPromptSubmit captures the exact request; Stop detaches a judge worker.
Other lifecycle events invalidate pending workers. See README.md for setup.
"""
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

STATE = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "state" / "task-compact"
LOG = str(STATE / "decisions.jsonl")
# 60% of Astra's 1.05M window; override for smaller session windows.
MIN_TOKENS = int(os.environ.get("TASK_COMPACT_MIN_TOKENS", "630000"))
DONE_MIN, WAITING_MAX = 0.75, 0.3

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


def api_key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if k or sys.platform != "darwin":
        return k
    r = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                        "-s", "typesafe-api-key", "-w"], capture_output=True, text=True, timeout=5)
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



def state_path(data):
    identity = os.environ.get("HERDR_PANE_ID", "") + "\0" + data["session_id"]
    return STATE / (hashlib.sha256(identity.encode()).hexdigest() + ".json")


def read_state(data):
    try:
        return json.loads(state_path(data).read_text())
    except (OSError, ValueError):
        return None


def write_state(data, value):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = state_path(data)
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(path):
    try:
        s = os.stat(path)
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns
    except OSError:
        return None


def read_tail(path, limit=2_000_000):
    """Bounded JSONL read; malformed and unfinished records fail closed."""
    with open(path, "rb") as stream:
        end = stream.seek(0, 2)
        start = max(0, end - limit)
        stream.seek(max(0, start - 1))
        raw = stream.read()
    if start:
        boundary, raw = raw[:1], raw[1:]
        if boundary != b"\n":
            _, separator, raw = raw.partition(b"\n")
            if not separator:
                raise ValueError("no complete transcript records")
    if raw and not raw.endswith(b"\n"):
        raise ValueError("unfinished transcript record")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) or not isinstance(row.get("payload"), dict) for row in rows):
        raise ValueError("unsupported transcript record")
    return rows


def context_tokens(rows, data):
    """Validate this completed turn, then use its latest API input count.

    cached_input_tokens is a subset of input_tokens, not an additional cost.
    Cumulative total_token_usage is not the current context size.
    """
    start = None
    for i, row in enumerate(rows):
        p = row["payload"]
        if row.get("type") == "event_msg" and p.get("type") == "task_started":
            start = i
    if start is None:
        raise ValueError("missing turn start")
    if rows[start]["payload"].get("turn_id") != data["turn_id"]:
        raise ValueError("new turn")
    tokens, complete = None, False
    for row in rows[start + 1:]:
        p = row["payload"]
        kind = p.get("type")
        if row.get("type") == "compacted":
            raise ValueError("already compacted")
        if row.get("type") != "event_msg":
            # Anything model-visible after completion could represent new activity.
            if complete and row.get("type") in ("response_item", "turn_context"):
                raise ValueError("activity after completion")
            continue
        if kind in ("turn_aborted", "context_compacted"):
            raise ValueError("interrupted or compacted")
        if complete and kind in ("user_message", "agent_message", "item_completed"):
            raise ValueError("activity after completion")
        if kind == "token_count" and p.get("info") is not None:
            tokens = p["info"].get("last_token_usage", {}).get("input_tokens")
        if kind == "task_complete":
            if p.get("turn_id") != data["turn_id"] or p.get("last_agent_message") != data["last_assistant_message"]:
                raise ValueError("completion mismatch")
            complete = True
    if not complete:
        raise ValueError("turn not flushed or not complete")
    if type(tokens) is not int or tokens < 0:
        raise ValueError("missing input token count")
    return tokens


def background(data, settle=2):
    time.sleep(settle)
    request_state = data["_request_state"]
    path = data["transcript_path"]
    details = {"session": data["session_id"], "turn": data["turn_id"]}
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    # One judge/injector per pane, including duplicate hook registrations.
    pane_key = hashlib.sha256(os.environ["HERDR_PANE_ID"].encode()).hexdigest()
    with (STATE / (pane_key + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return log(**details, skip="worker-active")
        try:
            if read_state(data) != request_state:
                return log(**details, skip="new-turn")
            before = fingerprint(path)
            tokens = context_tokens(read_tail(path), data)
            unchanged = lambda: (before is not None and fingerprint(path) == before
                                 and read_state(data) == request_state)
            if not unchanged():
                return log(**details, skip="new-turn")
            if tokens < MIN_TOKENS:
                return log(**details, tokens=tokens, skip="small")
            # Claim once before judging. A failed worker can safely skip this boundary.
            claim = state_path(data).with_suffix(".claimed")
            generation = request_state["generation"]
            if claim.exists() and claim.read_text() == generation:
                return log(**details, skip="duplicate")
            claim.write_text(generation)
            done, waiting, attempts = jev(request_state["prompt"], data["last_assistant_message"])
            if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in (done, waiting)):
                raise ValueError("invalid Jev probability")
            fire = done >= DONE_MIN and waiting < WAITING_MAX
            details.update(tokens=tokens, done=done, waiting=waiting, attempts=attempts, fire=fire)
            if fire and os.environ.get("TASK_COMPACT_DRY") != "1":
                result = inject("/compact", unchanged)
                details["injected"] = result == "sent"
                if result != "sent":
                    details["skip"] = result
            log(**details)
        except Exception as error:
            # Never log request/reply text, API credentials, or external error bodies.
            log(**details, error=type(error).__name__, skip="failed-closed")


def handle(data):
    if not os.environ.get("HERDR_PANE_ID") or not data.get("session_id"):
        return
    event = data.get("hook_event_name")
    if event == "UserPromptSubmit":
        # Generation also invalidates a worker when a prompt is steered into the same turn.
        write_state(data, {"generation": uuid.uuid4().hex, "turn_id": data.get("turn_id"),
                           "prompt": data.get("prompt", "")[-4000:]})
    elif event in ("PreCompact", "Interrupt", "SessionEnd", "SessionStart"):
        state_path(data).unlink(missing_ok=True)
    elif event == "Stop":
        if data.get("stop_hook_active") or not data.get("turn_id") or not data.get("last_assistant_message"):
            return
        request = read_state(data)
        if not request or not request.get("prompt") or request.get("turn_id") != data["turn_id"]:
            return
        if not fingerprint(data.get("transcript_path") or ""):
            return
        data["_request_state"] = request
        process = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        process.stdin.write(json.dumps(data).encode())
        process.stdin.close()


def main():
    try:
        data = json.load(sys.stdin)
        if sys.argv[1:] == ["--worker"]:
            background(data)
        else:
            handle(data)
    except Exception as error:
        with contextlib.suppress(Exception):
            log(error=type(error).__name__, skip="invalid-hook")
    if sys.argv[1:] != ["--worker"]:
        print("{}")


if __name__ == "__main__":
    main()
