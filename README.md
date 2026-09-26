# Claude + Jev + Herdr: auto-compact Claude Code at work boundaries

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org)
[![Dependencies: none](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen.svg)](task-compact.py)
[![Claude Code: Stop hook](https://img.shields.io/badge/Claude%20Code-Stop%20hook-orange.svg)](https://docs.anthropic.com/en/docs/claude-code/hooks)

Claude Code compacts the conversation when context fills up, whether or not you're in the middle of a task.
This Stop hook compacts **at completed work and requested pause boundaries**. At the end of each turn,
a small, fast model judges whether the requested chunk is delivered or deliberately paused with enough
state to resume. If that boundary is reached, the hook types `/compact` into the session.

It's one Python file that uses only the standard library.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/flow-dark.svg">
  <img alt="Turn ends, size gate, Jev judges whether the task is done, Herdr types /compact" src="assets/flow-light.svg" width="100%">
</picture>

## Contents

- [Why compact at task boundaries](#why-compact-at-task-boundaries)
- [The pieces: Herdr and Jev](#the-pieces-herdr-and-jev)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Tuning the questions](#tuning-the-questions)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [Limitations](#limitations)
- [Uninstall](#uninstall)

## Why compact at task boundaries

**Why not wait until the context is full?** A long context costs you well before it runs out:

- **Quality drops.** Everything left in the context competes for the model's attention: finished tasks, files it read an
  hour ago, stale tool output. The model picks up details from old work and follows instructions less reliably.
- **Every turn costs more and takes longer.** Each request sends the whole context again. Even with prompt caching, a
  turn at 700k tokens is far slower and more expensive than the same turn at 50k.
- **The built-in compaction ignores what you're doing.** Claude Code compacts when it hits its token limit, not when
  it finishes something. That's usually in the middle of a task, since long tasks are what fill the context.

**Why is compacting mid-task bad?** Compaction swaps the conversation for a summary. Claude Code's summary keeps the
gist, including your requests, errors and how they were fixed, pending tasks and current work, but
[full tool outputs and intermediate reasoning are gone](https://code.claude.com/docs/en/context-window#what-survives-compaction).
Mid-task, that verbatim detail is exactly what the model is still using:

- The exact error messages, stack traces and test output it's working from
- File contents it read, beyond the snippets the summary kept and the recently modified files Claude Code re-reads
- Why it rejected earlier approaches, so it may try them again

After a mid-task compaction, the model often spends turns re-reading files and re-running commands to rebuild what it
lost.

**At a task boundary, almost nothing needs to carry over.** The work is finished and its results are in the files, so
the summary only has to record what was done. The next task starts with a small, clean context. Anthropic's docs make
the same recommendation: [run `/compact` at a natural break](https://code.claude.com/docs/en/prompt-caching), such as
between tasks, instead of waiting for auto-compaction to trigger mid-task. This hook does that for you. It compacts
early, past 600k tokens by default (60% of a 1M window), but only at a completed chunk or requested pause boundary. The built-in auto-compact
remains a backstop for tasks that run long without finishing.

## The pieces: Herdr and Jev

### Herdr: typing `/compact` into the right pane

[![Herdr running Claude Code alongside another agent](https://raw.githubusercontent.com/herdrdev/herdr/master/assets/screenshot.png)](https://herdr.dev)
<sub>Screenshot, and the logo used in the diagrams: [herdrdev/herdr](https://github.com/herdrdev/herdr) (Apache-2.0).</sub>

[Herdr](https://herdr.dev) is a terminal workspace manager for AI coding agents. It works like tmux, but it knows about
agents and shows each one as working, blocked, or idle. Every pane it launches gets a `HERDR_PANE_ID` environment
variable, and its CLI can type into any pane with `herdr pane send-text` and `herdr pane send-keys`.

That matters because Claude Code has no hook output or API that starts a compaction. It only happens when the built-in
token threshold is reached or when someone types `/compact`. Herdr lets the hook do the typing, into exactly the pane
Claude is running in.

### Jev: deciding whether a work boundary is reached

[Jev](https://docs.typesafe.ai) from TypeSafe is a "System One" model. Instead of generating text, it answers typed
questions with structured values that code can use directly. One question type is the
[**Noul**](https://docs.typesafe.ai/primitives/noul), a yes/no question answered with a probability from 0 to 1.

![The hook's two questions in the Jev Playground: done 89%, waiting 7%](assets/jev-playground.png)
<sub>The hook's questions in the TypeSafe Playground, judging a finished task that ends with an offer of optional extra work.</sub>

The hook sends Jev your last request and Claude's final reply, and asks two Nouls in one call, which takes
about 100–400 ms. The request is the last prompt you typed: a background-task notification or a message from
another session can wake a turn, but it isn't what the reply is judged against.

| Noul | Question | Compacts when |
|---|---|---|
| `done` | Has the requested chunk reached a completed or resumable stopping boundary? | ≥ 0.75 |
| `waiting` | Is missing user input blocking delivery of that requested boundary? | < 0.3 |

A separate judge is more dependable than asking Claude to mark the end of its own tasks. It doesn't forget the marker
or add it too early, and it costs the main model nothing.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/timeline-dark.svg">
  <img alt="Timeline of one turn end: Stop fires, the hook waits 2 s, asks Jev, and Herdr types /compact and Enter" src="assets/timeline-light.svg" width="100%">
</picture>

- **Only inside Herdr.** Without `HERDR_PANE_ID`, the hook does nothing and sends nothing anywhere.
- **Size gate first.** Jev is only asked once the context passes `TASK_COMPACT_MIN_TOKENS`.
- **Fails closed.** Any Jev or Herdr error means no compaction. HTTP 529 (overloaded) is retried twice.
- **Never interrupts you.** If you send a new message before the hook acts, it doesn't type anything, and it never judges a turn other than the one that just ended.
- **Enter is sent separately**, 0.5 s after the text, so Claude's input box doesn't treat it as a paste.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with hooks
- [Herdr](https://herdr.dev), with Claude Code running inside a Herdr pane
- Python 3.8+ (standard library only)
- A TypeSafe API key from <https://console.typesafe.ai/keys>

## Installation

1. **Install Herdr**, then start `claude` inside a Herdr pane:
   ```sh
   curl -fsSL https://herdr.dev/install.sh | sh
   ```
2. **Store your TypeSafe API key** in one of two places:
   - The environment variable `TYPESAFE_API_KEY`
   - On macOS, the Keychain, which keeps the key out of files:
     ```sh
     security add-generic-password -a "$USER" -s typesafe-api-key -w
     ```
3. **Copy the hook:**
   ```sh
   mkdir -p ~/.claude/hooks && cp task-compact.py ~/.claude/hooks/
   ```
4. **Register it** by adding the `Stop` entry from [`settings.example.json`](settings.example.json) to
   `~/.claude/settings.json`. If you already have `Stop` hooks, append it to that array.
5. **Set the gate for your context window** (see [Configuration](#configuration)).

Running sessions pick up the hook without a restart.

### Try it end to end

Set `"TASK_COMPACT_MIN_TOKENS": "1"` in the `env` block of `settings.json`. Start `claude` in a Herdr pane, ask
something small like "what does `ls -la` do?", and watch `/compact` get typed in. Then check the log, and remove the
override:

```sh
tail -1 ~/.claude/state/task-compact.log
```
```json
{"t": "...", "session": "...", "fire": true, "tokens": 18342, "done": 0.95, "waiting": 0.02, "attempts": 1, "injected": true}
```

### Self-check

```sh
python3 test_task_compact.py   # prints PASS; no network or Herdr needed
```

## Configuration

Environment variables go in the `env` block of `~/.claude/settings.json`:

| Variable | Default | Purpose |
|---|---|---|
| `TASK_COMPACT_MIN_TOKENS` | `600000` | Only ask Jev once the context is this large. The default is 60% of a 1M window; use about `120000` for a 200k window. |
| `TASK_COMPACT_DRY` | unset | Set to `1` to log decisions without typing `/compact`. Useful for collecting scores before you trust it. |
| `TYPESAFE_API_KEY` | unset | API key. On macOS, the Keychain item `typesafe-api-key` is used if this is unset. |

The thresholds (`DONE_MIN = 0.75`, `WAITING_MAX = 0.3`) and the Jev questions are at the top of `task-compact.py`.

If you set `autoCompactWindow`, keep it above `TASK_COMPACT_MIN_TOKENS`, or the built-in compaction fires first.

## Toggle Jev compaction

This repo includes an `auto-compaction` skill for both Claude Code
(`.claude/skills`) and Codex (`.agents/skills`). Invoke `auto-compaction off` or
`auto-compaction on` in either harness; the skill affects only that harness.
The command-line helper requires an explicit single target:

```sh
python3 skills/auto-compaction/scripts/toggle.py off --target codex
python3 skills/auto-compaction/scripts/toggle.py on --target claude
python3 skills/auto-compaction/scripts/toggle.py status --target codex
```

The toggle affects the installed Jev hook across the selected app's sessions.
Off enables its existing dry mode: judgments are logged, but `/compact` is not
issued. On restores prior behavior. Built-in context-limit compaction is
unaffected, and already running hook workers may finish. No hook registration
or thresholds are changed. Reload skill discovery if a running agent does not
see the new skill yet.

## Tuning the questions

Every decision is logged to `~/.claude/state/task-compact.log` with the token count and both scores.
The current questions accepted all 100 intended boundaries and rejected all 20 negative controls in a
synthetic evaluation against `jev-1.13.0`, with thresholds unchanged. See the
[evaluation report](evaluations/boundaries/README.md) for cases, raw scores, revisions, and limitations.

The `done` question covers completed requests, requested milestones, handoffs, cancellations, and
user-requested pauses with enough state to resume. Immediate continuation overrides a completed substep.
The `waiting` question excludes deliberate pauses and prepared review checkpoints, as well as optional
follow-up offers. Unexpected missing input before delivering the requested chunk still blocks compaction.

To try your own cases, open the Playground in the [TypeSafe console](https://console.typesafe.ai). Paste the
`QUESTIONS` dict from the script as the questions, and use JSON state like this:

```json
{
  "user_request": "Rename the getUser function to fetchUser across the codebase.",
  "assistant_final_reply": "Renamed getUser to fetchUser in 23 files. Typecheck and tests pass. Want me to also rename getUsers?"
}
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Nothing appears in the log | Claude isn't running in a Herdr pane (`echo $HERDR_PANE_ID` is empty), or the hook isn't registered. Run `/hooks` in Claude Code to check. |
| Every line says `"skip": "small"` | The context is under `TASK_COMPACT_MIN_TOKENS`. That's expected; set it to `1` to test. |
| `"error": "<HTTPError 401: 'Unauthorized'>"` | The API key is missing or invalid. Check `TYPESAFE_API_KEY` or the Keychain item. |
| `"skip": "new-turn"` | You sent a new message before the hook acted, so it left the new turn alone. That's expected. |
| `"skip": "new-turn-typed"` | You sent a message in the half-second between the hook typing `/compact` and pressing Enter. The hook didn't submit it, but `/compact` is left in your input box; delete it. |
| `"skip": "unreadable"` | The transcript couldn't be read, or had a broken line. The hook skips rather than risk missing a new turn. |
| `"skip": "no-reply"` or `"no-request"` | The finished reply wasn't the newest entry in the transcript, or its request wasn't in the last 2 MB (for example, after a very large tool result). The hook skips rather than judge the wrong turn. |
| `"injected": false` | A `herdr` command failed; the `error` field has its output. Check that `herdr` is on the hook's `PATH`. |
| `/compact` is typed but not submitted | Enter arrived too soon. Increase the `time.sleep(0.5)` in `inject()`. |

## Privacy

Once the size gate is passed, every turn end sends your last request (truncated to 4,000 characters) and Claude's final
reply (truncated to 8,000 characters) to `api.typesafe.ai`. This happens in every project where Claude runs inside
Herdr. If some projects shouldn't send text out, add a `cwd` check in `main()`; the hook input includes `cwd`.

Claude Code's auto mode may refuse to install this hook for you, flagging it as self-modification or data exfiltration.
That's expected: install it yourself, or explicitly approve those steps.

## Limitations

- **Herdr only.** Outside Herdr, there's no reliable way for a hook to type into Claude's own terminal.
- **Judges one turn at a time.** Jev sees only the last request and final reply, not the whole conversation. A task
  spread across several requests may compact after an early part is finished.
- **Two-second guess.** The hook waits 2 seconds for Claude to go idle before typing. On a very slow machine, that may
  not be long enough.

## Uninstall

Remove the `Stop` entry from `~/.claude/settings.json`, then:

```sh
rm ~/.claude/hooks/task-compact.py ~/.claude/state/task-compact.log
```

## License

[MIT](LICENSE)
