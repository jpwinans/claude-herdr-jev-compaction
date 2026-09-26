# Jev compaction boundary evaluation — 2026-09-26

**Recommended candidate: [v4.json](v4.json).** Live Jev accepted all 100 intended
boundaries and rejected all 20 negative controls, using the unchanged gates
`done >= 0.75` and `waiting < 0.30`. The installed hook was not modified.

The question now evaluates completion of the **current requested chunk**, including
user-requested pauses and prepared review checkpoints. It does not require the
whole project to be complete. Immediate continuation takes precedence over a
completed substep. A next step recorded for another session is not immediate
continuation.

## Cases and method

[cases.json](cases.json) contains 100 positive cases, in five batches of 20, plus
20 negative controls. All cases and labels were written before the first API call
and remained unchanged. Each positive batch contains four cases from each category:
completed requests, resumable pauses, requested milestones, deliberate scope
endings, and handoffs. These cover coding, reading, research, writing, planning,
design, and operational work. P005 reproduces the reading-pause scenario that
motivated the experiment.

Negative controls cover immediate continuation, incomplete work, unrequested
pauses, missing input, unsafe or unrecorded state, and instructions embedded in
quoted data. Labels encode a deliberate policy: a requested approval checkpoint
is a boundary; an unexpected approval blocker before delivering the requested
chunk is not. Explicitly reported unsafe transient state prevents acceptance.

Only `user_request` and `assistant_final_reply` are sent as state; labels, case IDs,
categories, and batch numbers are not sent. Requests use the hook's 4,000/8,000
character tail limits and `jev-latest`. All responses reported `jev-1.13.0`.
Thresholds stayed fixed. Calls used the existing TypeSafe credential without
printing or saving it. No compaction or Herdr command was invoked.

## Checkpoints and revisions

| Checkpoint | Candidate | Good boundaries accepted | Negative controls rejected | Decision |
|---|---|---:|---:|---|
| Baseline, cases 1–20 | Installed questions | 14/20 | Not run | Establish comparison |
| Cases 1–20 | v1 | 17/20 | 20/20 | Clarify completed reviews and saved artifacts |
| Cases 1–20 recheck | v2 | 20/20 | 19/20 | Add immediate-continuation veto |
| Cases 1–20 recheck | v3 | 20/20 | 20/20 | Advance |
| Cases 21–40 | v3 | 20/20 | Previous controls passed | No revision |
| Cases 41–60 | v3 | 19/20 | Previous controls passed | Clarify ordinary-language deferral |
| Cases 41–60 recheck | v4 | 20/20 | 20/20 | Advance |
| Cases 61–80 | v4 | 20/20 | Previous controls passed | No revision |
| Cases 81–100 | v4 | 20/20 | Previous controls passed | Select v4 |
| Cases 1–40 regression | v4 | 40/40 | Previous controls passed | Final coverage: 100 positives, 20 negatives |

Changes were driven by these observed mistakes:

- **v1 → v2:** P004 (completed review with findings), P017 (saved email draft),
  and P020 (completed runbook) scored below the completion threshold. Clarified
  that concrete delivery reports count without requiring inline artifact contents,
  and that downstream fixing or execution need not be part of the request.
- **v2 → v3:** N018 (“I am now wiring it into the application”) incorrectly fired
  at `done=0.75`, `waiting=0.04`. Added a precedence rule rejecting immediate
  continuation even when a substep is saved or finished.
- **v3 → v4:** P048 (“Let's pick this up Monday”) scored `done=0.70`,
  `waiting=0.10`. Clarified semantic pause requests and the difference between
  a resume instruction for later and an immediate continuation. Its v4 scores
  were `done=0.87`, `waiting=0.06`.

## Final results

| Category | Correct |
|---|---:|
| Completed requests | 20/20 |
| Resumable pauses | 20/20 |
| Requested milestones | 20/20 |
| Deliberate scope endings | 20/20 |
| Handoffs | 20/20 |
| Negative controls | 20/20 |

The original reading pause went from baseline `done=0.42`, `waiting=0.33` to v4
`done=0.94`, `waiting=0.03`. The lowest final positive completion score was 0.79;
the highest positive waiting score was 0.14. The highest negative completion
score was 0.18. There were 300 live judgments across baseline, revisions, controls,
and regression checks, with no API errors. See [summary.json](summary.json).

This is the best of the four candidates tested, not proof of a globally optimal
prompt. These are mostly concise synthetic examples authored and labeled by the
same agent that tuned the prompt. Cases 61–100 were first evaluated after v4 was
fixed and passed without another revision; the negative controls were reused
during tuning. Scores are not established as calibrated probabilities, and this
run does not measure repeat-to-repeat variability, long-message truncation,
hidden running work, or preservation of information by actual compaction.
The judge can only assess the supplied request/reply. Existing lifecycle and
new-turn guards remain necessary.

## Artifacts and reproduction

- [Final questions](v4.json): drop-in `QUESTIONS` data, retaining the hook's two keys.
- [Frozen cases](cases.json) and [case authoring script](build_cases.py).
- [Runner](run.py): standard library only, never imports the installed hook.
- [Raw responses](results/): every API response, score, decision, model, and usage;
  each file also records case/question hashes and thresholds.
- [Baseline](baseline.json), [v1](v1.json), [v2](v2.json), [v3](v3.json).

Run from the repository root with the existing Keychain credential or
`TYPESAFE_API_KEY` available:

```sh
python3 evaluations/boundaries/run.py v4 batch1 --tag=-repeat
python3 evaluations/boundaries/run.py v4 negatives --tag=-repeat
```

Use `batch2` through `batch5` for subsequent batches. Existing result files are
never overwritten. The runner also supports `all`, `positives`, and explicit
comma-separated case IDs. Running it makes real API calls. API failures are
recorded separately from classification errors.
