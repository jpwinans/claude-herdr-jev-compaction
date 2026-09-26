# Claude Code setup

Part of **Herdr + Jev auto-compaction for Claude Code and Codex CLI**.
See the [shared overview, diagrams, and Jev tuning](../README.md), or the
[Codex CLI setup guide](../codex/README.md). This page covers Claude-specific settings.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with hooks
- [Herdr](https://herdr.dev), with Claude Code running inside a Herdr pane
- Python 3.8+ (standard library only)
- A TypeSafe API key from <https://console.typesafe.ai/keys>

## Installation

Run the shell commands below from the repository root.

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
   mkdir -p ~/.claude/hooks && cp claude/task-compact.py ~/.claude/hooks/
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
python3 claude/test_task_compact.py   # prints PASS; no network or Herdr needed
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

Dry mode also sends these excerpts to TypeSafe. See the [shared privacy notes](../README.md#privacy).

## Limitations

See also the [limitations shared by Claude Code and Codex CLI](../README.md#limitations).

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

[MIT](../LICENSE)
