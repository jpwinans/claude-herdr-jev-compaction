# Compaction thresholds for Claude Opus 5.5 and GPT-6 Astra

Researched 2026-09-26. Scope: this repository's completion-aware Herdr hooks, with built-in compaction retained as a fallback. No runtime configuration was changed during research. The observations below describe the repository before the subsequent default update.

**Subsequent repository decision:** the user selected 60% of the full model windows: Claude `600000` (1M) and Codex `630000` (1.05M). These fixed defaults do not enable or detect a larger session window; smaller-window deployments must override them. Local Codex settings remain unchanged.

## Recommendation and confidence

There is no established optimal percentage in the primary sources reviewed for either exact model. A model's maximum window, a client's default compaction threshold, and a quality-optimal threshold are different quantities. No controlled threshold sweep for these two models was found.

For balanced coding sessions, **start the task-boundary gate at 60% of the actual session window**, and compare it with 50% and 70%. This is an engineering hypothesis, not a vendor recommendation or a measured optimum. It leaves 40% for additional work when a task has not finished at the gate. This reserve does not guarantee that the next task will fit. Keep the native compaction fallback enabled.

| Model / deployment | Window used for this calculation | Initial task-boundary gate | Interpretation |
|---|---:|---:|---|
| Opus 5.5 with its full 1M window | 1,000,000 | 600,000 (60%) | Keep the repository's existing Claude default as an experimental baseline. |
| Astra in the recent local Codex rollouts | 258,400 effective | 155,040 (60%; rounding to 155,000 is fine) | Conditional on moving the native fallback above the task gate; see local conflict below. |
| Astra through its full API window | 1,050,000 | 630,000 (60%) | Quality/continuity experiment only; incurs the documented long-context API price tier. Not the window observed in local Codex. |

There is no evidence here that Opus should intrinsically compact later than Astra as a percentage. Using the same baseline avoids inventing a model-specific difference. The repository's pre-update Codex gate of 120,000 is also a reasonable conservative experimental baseline: 46.4% of the locally reported effective window. Research does not establish that raising it to 60% improves results.

## Published facts

### Opus 5.5

Anthropic lists the exact model `claude-opus-5-5`, released September 22, 2026, with a 1M-token context window and 128K maximum output. These are capacity limits, not optimal compaction triggers. [Model specifications](https://platform.claude.com/docs/en/models/opus-5-5/overview)

Claude Code documents native 1M sessions, including Opus 4.7 and later on the Anthropic API, compacting at roughly 967K by default (96.7% of 1M). Some provider/configuration combinations use 200K instead. The native default establishes operating behavior, not a quality optimum. Verify the actual session's window before using 600K: that gate cannot work for a 200K deployment. [Claude Code model configuration](https://code.claude.com/docs/en/model-config#default-auto-compact-thresholds)

Anthropic describes declining recall as context grows as a gradient rather than a universal cliff. Its compaction guidance also warns that aggressive summarization can lose subtle facts, and recommends tuning on complex traces with recall prioritized. This supports workload-specific evaluation, not a universal 50%, 60%, or 80% rule. [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Its cost cookbook demonstrates batching history pruning at natural task boundaries to preserve cache reuse between rewrites. That is support for this repository's timing strategy, not evidence for a particular percentage or a guarantee that all compaction mechanisms share identical cache behavior. [Cost optimization cookbook](https://platform.claude.com/cookbook/cost-optimization-cost-optimization)

### GPT-6 Astra

OpenAI lists `gpt-6-astra` with a 1,050,000-token context window and 128,000 maximum output. Requests exceeding 272,000 input tokens have 2x input/cache rates and 1.5x output rates for the full request. The price boundary is about 25.9% of the advertised window; it is not a reasoning-quality boundary. API pricing does not establish how subscription Codex usage is metered. [Astra model specifications and pricing](https://developers.openai.com/api/docs/models/gpt-6-astra)

OpenAI's compaction guide exposes a configurable token threshold and describes preserving state through a compacted representation. It does not prescribe an Astra-specific optimal percentage. [Compaction guide](https://developers.openai.com/api/docs/guides/compaction)

Codex separately exposes `model_context_window`, `model_auto_compact_token_limit`, and `model_auto_compact_token_limit_scope`. The scope can count the total active context or growth after the carried prefix. A native threshold therefore should not be assumed to use exactly the same counter as this repository's input-token gate. [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

## Local observations and a configuration conflict

Read-only inspection on the research date found:

- `~/.codex/models_cache.json`, Astra entry: `context_window=272000`, `max_context_window=872000`, `effective_context_window_percent=95`.
- Two recent local Astra rollouts report `model_context_window=258400`, consistent with 272,000 × 0.95. Only model/window fields were extracted; conversation content is not reproduced here.
- `~/.codex/config.toml`: `model="gpt-6-astra"`, `model_auto_compact_token_limit=103360`, and scope `total`.
- The repo's [Codex hook](../../codex/task-compact.py) then defaulted to `TASK_COMPACT_MIN_TOKENS=120000` and checks latest input tokens, including the cached subset once.
- The [Claude hook](../../claude/task-compact.py) defaults to `600000` and sums input, cache-read input, and cache-creation input.

The native local Codex threshold is **40% of 258,400**, below the then-current hook's **46.4%** gate. Native compaction can therefore happen before the completion-aware hook becomes eligible. The counters and timing differ, so this is not a proof that the hook can never fire; it is a configuration ordering problem to resolve before comparing thresholds.

These files are observations of this machine, not universal product defaults. Session overrides, alternate profiles, changed model catalogs, or different providers can change the effective values.

If adopting the proposed 60% task gate, place the native fallback above it. An 80–90% fallback (about 207K–233K of the observed effective window) is a possible experiment, not an official Astra recommendation. If the current 103,360 fallback is intentional, keep it and test a task gate below it instead; for example, 80,000 is about 31% of 258,400. Neither configuration was applied.

## Cost-sensitive extended Astra sessions

For a separately verified full-window Astra API deployment, a cost-focused starting gate of **210,000 input tokens (20% of 1.05M)** leaves about 62K before the 272K pricing boundary. This is an economic heuristic, not a model quality optimum. A long unfinished task can cross the price boundary before the Stop hook acts, and compaction itself has cost. The repository's task-boundary mechanism cannot guarantee staying in the lower price tier.

## How to determine the optimum for this workload

1. Define the objective: retain correctness and continuity first, then minimize elapsed time and billed cost per successfully completed task.
2. Run representative multi-task coding sessions at 50%, 60%, and 70% of the actual session window, plus the client's native policy as a baseline. Keep model, effort, tool setup, task set, and judge criteria fixed. Include multiple runs because results are stochastic.
3. Measure test/acceptance pass rate, missed constraints after compaction, re-reading and repeated work, user corrections, compaction count, mid-task native compactions, wall time, and actual input/cache/output costs.
4. Include tasks that finish just below and above each gate and long tasks that cross it without finishing. A Stop-only gate is a minimum eligibility threshold, not an exact compaction point.
5. Increase the gate if compaction loses useful detail or creates repeated work; lower it if accumulated context raises cost/latency or reliably worsens task performance. Choose the least costly setting that meets the same quality bar, rather than the one that merely compacts most often.

Until those trials exist, 60% is a transparent starting assumption and both the earlier 600K / 120K defaults and the subsequently selected 600K / 630K defaults remain unvalidated heuristics.
