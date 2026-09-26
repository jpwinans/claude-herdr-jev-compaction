# Codex / Jev live smoke test

Verified locally on 2026-09-26 with Codex CLI 0.157.1, Herdr 0.8.2, GPT-6 Astra,
and the installed `codex/task-compact.py` hook. The actual session reported a
258,400-token effective context window.

The test used a dedicated Herdr pane and empty temporary working directory.
The six hook definitions were reviewed and trusted through Codex's hook UI;
existing pacing and Herdr session hooks remained installed. Jev authenticated
using the existing macOS Keychain credential; no credential was printed or stored
in this repository.

## Results

1. **Dry run:** with `TASK_COMPACT_MIN_TOKENS=1` and `TASK_COMPACT_DRY=1`, a
   completed arithmetic-answer task produced 18,964 input tokens. Jev returned
   `done=0.99`, `waiting=0.01` on its first attempt. The worker logged `fire=true`
   without an `injected` field.
2. **Actual compaction:** a fresh session with the same one-token gate and
   `TASK_COMPACT_DRY=0` completed another arithmetic-answer task. Jev again returned
   `done=0.99`, `waiting=0.01`; the worker logged `fire=true, injected=true`.
   The CLI displayed “Compacting context” and returned to idle. Its rollout
   contained one `compacted` record, confirming actual compaction rather than
   just successful command submission.

3. **Normal local gate:** after removing both test environment overrides, a fresh
   session completed a small task with 18,958 input tokens and logged `skip=small`.
   All installed hook commands retained the 155,040-token local default.

This verifies the real lifecycle-hook → Jev → Herdr → Codex compaction path.
The temporary one-token gate tests triggering without filling a model window;
it does not establish the quality or optimal timing of compaction at 60%.
Offline regression tests separately cover the production threshold boundary,
turn cancellation, duplicate workers, replacement sessions, and failure handling.

The local installation uses a 155,040-token Jev gate (60% of 258,400) and a
232,560-token native fallback (90%). The repository's full-window Astra default
remains 630,000; installation-specific overrides are intentional. Codex caps
SessionEnd hook timeouts at three seconds, so the example uses that limit.
