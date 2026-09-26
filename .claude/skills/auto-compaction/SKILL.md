---
name: auto-compaction
description: Turn this repository's Jev auto-compaction on or off for claude only, or show its status. Does not control built-in context-limit compaction.
---

This skill controls only claude. Never change another harness through it.

Run the bundled helper relative to this skill's directory:

```sh
python3 <skill-directory>/scripts/toggle.py off --target claude
```

Use `on`, `off`, or `status` as requested. Always use `--target claude`.
If the action is missing, show status and ask whether to turn it on or off.
Do not toggle merely because the user is discussing compaction.

The helper changes only a marked dry-mode assignment inside `main()` of the
installed `task-compact.py` hook. This is a user-wide setting for the selected
app, across projects and sessions. Off prevents the hook from issuing `/compact`;
Jev judgments and decision logs continue. On removes the override and restores
the prior behavior, including any existing environment-based dry mode. It does
not alter thresholds, hook registrations, trust settings, or built-in compaction.

Run `status` after a change and report the target and outcome. Changes apply to
new hook processes; already running workers can still finish. Status reports
this skill's override and hook presence, not whether every prerequisite for
compaction is met. If the user wants built-in compaction disabled too, explain
that this skill only controls Jev and handle that separate request explicitly.
