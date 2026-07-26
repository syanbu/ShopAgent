# Task 11 Brief: Complete end-to-end scenarios, documentation, and verification

Requirements source: `docs/superpowers/plans/2026-07-26-multi-turn-query-engine.md`, Task 11.

## Scope

- Modify only Task 11 acceptance/live/API tests and the mapped documentation needed to address review findings.
- Do not redo Tasks 1–10 or change production behavior unless a failing Task 11 test proves a production defect.
- Do not execute any Git command or Git operation.
- Every file modification must use `apply_patch`.
- Do not run or enable opt-in live tests.
- Keep the feature status `开发中` unless opt-in live verification actually passes.

## Review findings to address

1. In all six deterministic acceptance conversations, every turn must explicitly assert the exact persisted `QuerySnapshot`, `recent_candidates`, `focused_product_id`, and `seen_product_ids`, in addition to existing downstream-call counts and event ordering.
2. Add a real compiled-graph + SQLite HTTP test proving that when generation fails after product events, the already persisted candidate state can be referenced by a subsequent request using the same `conversation_id`.
3. Tighten the live parser case for `不要小米了` so it accepts only a Xiaomi-specific `exclude_brands/add` or `include_brands/remove` operation and rejects unrelated brand mutations; keep all five parser scenarios before indexing/retrieval and behind `RUN_LIVE_TESTS=1`.
4. Correct documentation preflight wording: the process environment lacks `DASHSCOPE_API_KEY`, local `.env` contains a nonempty setting whose validity was not checked, and `RUN_LIVE_TESTS` is unset. Do not claim live success.

## Required verification and report

- Use TDD for the new generation-failure persistence behavior: record an expected RED, then GREEN.
- Run focused Task 11 tests and Ruff for changed files; do not run the final full suite (the controller will do that after broad review).
- Write the full implementation/fix record to `task-11-report.md` in this directory using `apply_patch`.
- Report exact commands/results, changed files, concerns, and confirm that no Git operation or live call occurred.
