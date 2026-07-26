# Task 11 review-fix report

## Status

Task 11 remains **开发中**. This repair added deterministic coverage and
documentation corrections only; it did not change production behavior, enable
live tests, or perform any Git operation.

## Changes

- Added hand-authored persisted-state assertions for every turn of all six
  deterministic acceptance conversations. Each turn now checks its exact
  `QuerySnapshot`, `recent_candidates`, `focused_product_id`, and
  `seen_product_ids`, while retaining the existing call-count and event-order
  assertions.
- Added
  `test_compiled_http_generation_failure_persists_candidates_for_follow_up_ordinal`.
  It uses the real compiled graph, ASGI HTTP boundary, and
  `SqliteConversationRepository`: the first request emits products and then
  fails generation as `partial`; the next request with the same conversation
  resolves ordinal two, persists focus `p2`, and has a complete SSE sequence.
- Added a test-only `SequencedResponseGenerator`; no production code changed.
- Tightened the opt-in live `不要小米了` parser assertion to accept exactly one
  Xiaomi-specific `exclude_brands/add` or `include_brands/remove` mutation.
- Corrected the credential preflight wording and added the precise new HTTP
  generation-failure test to the feature coverage matrix.

## TDD evidence

RED command:

```powershell
uv run pytest tests/integration/test_chat_api.py::test_compiled_http_generation_failure_persists_candidates_for_follow_up_ordinal -q -p no:cacheprovider
```

RED result: collection failed with `ImportError: cannot import name
'SequencedResponseGenerator'`; the new test lacked the required test-only
failure sequencer. After adding that fake, the first run exposed a mismatched
test fixture category/sub-category and produced text without products. The
fixture was aligned to the deterministic catalog.

GREEN command: same command.

GREEN result: `1 passed in 4.00s`. Production behavior already satisfied the
new scenario; the implementation change was test infrastructure and coverage.

## Focused verification

```powershell
uv run pytest tests/unit/test_multi_turn_workflow.py tests/integration/test_chat_api.py -q -p no:cacheprovider
# 83 passed in 8.24s

uv run ruff check tests/unit/test_multi_turn_workflow.py tests/integration/test_chat_api.py tests/integration/api_fakes.py tests/live/test_live_shopping_flow.py
# All checks passed!
```

## Files

- `tests/unit/test_multi_turn_workflow.py`
- `tests/integration/test_chat_api.py`
- `tests/integration/api_fakes.py`
- `tests/live/test_live_shopping_flow.py`
- `docs/features/multi-turn-query-engine.md`
- `.superpowers/sdd/2026-07-26-multi-turn-query-engine/task-11-report.md`

## Self-review and constraints

- Focused deterministic tests and changed-file Ruff pass.
- No final full suite or mypy was run; the controller owns those checks.
- `RUN_LIVE_TESTS` was not set and no live, DashScope, or Qdrant call occurred.
- No Git command or Git operation occurred.

## Final independent review and repair wave

The independent Task 11 review initially found three Important gaps and one
Minor gap: per-turn persisted-state assertions were incomplete, generation
failure persistence lacked a next-turn HTTP/SQLite proof, credential preflight
wording was imprecise, and the Xiaomi live-parser assertion was too broad.
The focused repair above addressed all four, and a fresh read-only reviewer
verdict was: `All findings addressed, no new Critical/Important breakage`.

The subsequent broad Tasks 1–11 review found four Important and two Minor
issues outside the original Task 11-only repair. One implementation agent fixed
the complete set with TDD: snapshot-aware missing-context recovery,
reference-less product-question resolution and attempt limits, mutated
`more_results` compilation, reference-brand taxonomy validation, blank semantic
term validation, and historical single-turn documentation wording. Full root
cause, RED/GREEN, focused test, Ruff, and mypy evidence is recorded in
`final-review-fix-report.md`. A new scoped read-only reviewer confirmed all six
findings addressed with no residual load-bearing finding or new breakage.

## Final fresh verification

Required full command:

```powershell
uv run pytest -q -p no:cacheprovider
# 399 passed, 2 skipped in 15.88s
```

Skip diagnostic rerun:

```powershell
uv run pytest -q -p no:cacheprovider -rs
# 399 passed, 2 skipped in 13.70s
# local Qdrant integration skipped: 502 Bad Gateway
# opt-in live test skipped: set RUN_LIVE_TESTS=1 to call real services
```

Static verification:

```powershell
uv run ruff check .
# All checks passed!

uv run mypy src scripts
# Success: no issues found in 39 source files
```

The first sandboxed mypy launch was denied access to `uv.exe`; the exact command
was rerun with the required execution approval and passed. The process
environment has no `DASHSCOPE_API_KEY`; local `.env` has a nonempty value whose
validity was not tested. `RUN_LIVE_TESTS` remained unset, and local Qdrant
returned `502 Bad Gateway`, so no live DashScope/index/retrieval verification
was run or claimed. Feature status remains **开发中**. No Git operation occurred.
