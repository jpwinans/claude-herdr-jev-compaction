# Codex CLI setup

Part of **Herdr + Jev auto-compaction for Claude Code and Codex CLI**.
See the [shared overview, diagrams, and Jev tuning](../README.md), or the
[Claude Code setup guide](../claude/README.md). This page covers Codex-specific settings.

Compact at a finished task boundary once the context exceeds a configurable size.
This is for the **Codex CLI running inside a Herdr terminal pane**, on macOS/Linux.
It does not control the Codex desktop app, IDE extension, cloud, or `codex exec`.
Python 3.8+ and the standard library are sufficient.

## How it works

The [shared flow and timeline](../README.md#how-it-works) apply to Codex.
The steps below describe its prompt capture, rollout checks, and lifecycle guards.

1. `UserPromptSubmit` saves the last 4,000 characters of the exact prompt, the turn ID,
   and a new generation ID in a private per-pane state file.
2. `Stop` launches a detached worker and returns `{}` immediately. It ignores
   `stop_hook_active`, missing prompts, and mismatched turns.
3. After a two-second settling delay, the worker reads the last 2 MB of the rollout.
   It requires a matching `task_started` and `task_complete`, checks the final reply,
   and uses `token_count.info.last_token_usage.input_tokens` as context size.
   Cached input is already included; cumulative session usage is not context size.
4. At or above the size gate, Jev judges the captured prompt and final reply.
   It must score `done >= 0.75` and `waiting < 0.3`.
5. The worker checks that the prompt generation and rollout fingerprint have not
   changed, then asks Herdr to type `/compact`. It checks again before pressing Enter.

`PreCompact`, `Interrupt`, `SessionEnd`, and `SessionStart` invalidate pending work,
including when another session starts in the same pane.
A per-pane advisory lock prevents simultaneous injectors; each prompt generation is
judged at most once. Jev HTTP 529 responses retry twice (2 and 4 seconds). Missing or
unexpected transcript data, API errors, and Herdr failures skip the boundary.
The built-in Codex auto-compaction remains enabled as a backstop.

## Install

Requires a Codex release with lifecycle hooks. The development machine had
`codex-cli 0.157.1`, with `hooks` listed as stable and enabled by `codex features list`.
The parser targets its rollout record shapes. A [live smoke test](../docs/testing/codex-jev-smoke.md)
verified the Jev decision and Herdr-triggered compaction on this version. Start `codex` inside Herdr so it inherits `HERDR_PANE_ID`.

From the repository root:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/hooks"
cp codex/task-compact.py "${CODEX_HOME:-$HOME/.codex}/hooks/task-compact.py"
```

Merge the entries in [hooks.example.json](hooks.example.json) into
`${CODEX_HOME:-$HOME/.codex}/hooks.json`. Preserve any existing hooks, and register
this integration only once. The example honors `CODEX_HOME`, including paths with
spaces. If hooks are disabled, enable `features.hooks` in Codex configuration.
Restart Codex and review/trust the new hook definitions through its hook trust UI.

Set `TYPESAFE_API_KEY` in the environment that starts Codex, or on macOS store it
in the Keychain service `typesafe-api-key` under your login account (the same key
used by the Claude implementation). Do not put credentials in this repository.

Unlike Claude's settings example, these settings are shell environment variables:

```sh
export TASK_COMPACT_MIN_TOKENS=630000
export TASK_COMPACT_DRY=1
codex
```

| Variable | Default | Meaning |
|---|---|---|
| `TASK_COMPACT_MIN_TOKENS` | `630000` | Minimum latest input-token count before asking Jev; 60% of Astra’s 1.05M window. |
| `TASK_COMPACT_DRY` | unset | Exactly `1` judges and logs without typing anything. It still calls Jev. |
| `TYPESAFE_API_KEY` | unset | TypeSafe credential; macOS Keychain fallback. |
| `CODEX_HOME` | `~/.codex` | Installation and state location. |

The default is a fixed token count for Astra’s full 1.05M window; it does not
automatically scale to the active Codex session window. For a smaller window,
set the gate to 60% of the session’s effective window (for example, `155040` for
258,400 tokens). Keep the gate below Codex’s built-in
`model_auto_compact_token_limit` and the active context window. This hook does
not change either setting or enable a larger window.

## Verify

Offline tests (no API calls or terminal injection):

```sh
python3 codex/test_task_compact.py
```

For a live dry run, start a fresh Herdr Codex session with the variables above and
`TASK_COMPACT_MIN_TOKENS=1`, finish a small request, then inspect:

```sh
tail -1 "${CODEX_HOME:-$HOME/.codex}/state/task-compact/decisions.jsonl"
```

A finished task should log `"fire": true` without `"injected"`. Once verified,
unset `TASK_COMPACT_DRY` and restore your normal token gate before starting Codex.

## Troubleshooting and limits

- No log: check `HERDR_PANE_ID`, hook trust, and that **both** `UserPromptSubmit` and
  `Stop` were registered before the request. Hooks outside Herdr are no-ops.
- `small`: below the gate; no Jev call was made.
- `new-turn` / `new-turn-typed`: activity invalidated the decision. In the latter
  case, `/compact` may remain in the input box; delete it.
- `failed-closed`: the `error` field names the exception class. A `ValueError` can
  mean an incomplete, changed, compacted, or unsupported rollout. Check the rollout
  locally; requests and external error bodies are intentionally omitted from logs.
- `duplicate` / `worker-active`: another invocation already handled this boundary.
- A slow flush can miss the two-second window. This skips compaction until a later
  task rather than judging an unverified turn.
- Unsubmitted text is not represented in hook events or rollouts. The checks reduce
  races but cannot make terminal input atomic. Avoid typing or switching sessions
  in that pane while the worker is acting. Other hooks can also continue the turn.
- Jev only sees one request and final reply. Multi-turn tasks, active goals, and
  outstanding background work may require a broader judge than this mechanism.
- Rollouts are an internal, changing format. Unknown/missing required data skips
  compaction. The completion requirement may skip boundaries on other versions.
- State files retain the latest prompt excerpt with mode `0600`; lifecycle
  invalidation removes it. Interrupted hook execution may leave one behind.
  Decision logs and claim/lock files persist until removed.

## Privacy and uninstall

Above the gate, the hook sends up to 4,000 characters of your prompt and 8,000
characters of the final reply to `api.typesafe.ai`. Installing globally applies
in every Herdr project where these hooks run. Dry mode also sends this text.

Remove this integration's entries from `hooks.json`, restart Codex, then remove
its script and state directory if desired. Preserve unrelated hooks and files.
Already detached workers can finish briefly after removal.

## References

- [OpenAI lifecycle hook contract and trust requirements](https://learn.chatgpt.com/docs/hooks)
- [OpenAI configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Shared Jev rationale and tuning for both agents](../README.md#tuning-the-judge)
