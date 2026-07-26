# Final review fix report

## Status

Tasks 1–11 remain **开发中**. This report covers the final-review repair wave
only. No Git command or Git operation is authorized, and no live DashScope or
Qdrant verification will be enabled or run.

## Root-cause investigation and data flow

### Important 1 — missing-context recovery resets an existing search

Data flow: a relative-price search turn reaches `merge_query_snapshot`; with a
persisted `QuerySnapshot` but no recent display-price candidates,
`merge_turn_query()` returns `PRICE_BASELINE_MESSAGE`. The workflow persists a
`missing_context` pending clarification containing the suspended search turn.
On the explicit-budget answer, `resume_pending_action()` calls
`_merge_pending_turn()`, which currently rewrites every `missing_context`
suspended intent to `new_search`. `merge_turn_query()` consequently selects an
empty base snapshot and loses the persisted category and all untouched
constraints.

Root cause: `_merge_pending_turn()` uses the clarification kind alone to choose
`new_search`; it does not distinguish a genuinely absent `query_snapshot` from
a snapshot whose recent price baseline is unavailable. The intent must become
`new_search` only when the loaded conversation has no snapshot; otherwise it
must preserve the suspended search intent.

### Important 2 — reference-less product questions bypass deterministic resolution

Data flow: `resolve_reference()` immediately returns “not required” when
`TurnQuery.reference` is `None`, even for `product_question`. The graph routes
to `load_product_facts()`, where `_validated_product_question_target()` finds no
`resolved_product_id` and raises `PRODUCT_KNOWLEDGE_UNAVAILABLE`. This bypasses
the approved focus/single-candidate fallback and multi-candidate clarification
rules in `reference_resolver.py`.

For an existing `ambiguous_reference` pending item, `_merge_pending_turn()`
also replaces the suspended reference with `None` when a
`clarification_answer` supplies no new reference. The same early return then
bypasses `resolve_reference_service()` and its second-attempt path, so the turn
falls into product-knowledge failure instead of clearing pending and requesting
a complete restatement.

Root cause: the workflow equates “no explicit clue” with “no resolution
needed.” A product question without a clue still needs an implicit
demonstrative resolution against focus/latest candidates, and pending merge
must retain the suspended clue when the answer does not replace it.

### Important 3 — mutated more-results turns discard query operations

Data flow: `merge_turn_query()` selects the existing snapshot, then takes an
early return whenever intent is `more_results` and no brand was resolved. That
return validates and re-emits the old snapshot before semantic operations,
slot operations, relative-price compilation, category-switch detection, or
the normal search-state reset can run. `retrieve_chunks()` then treats the turn
as a pure pagination request and forwards all seen-product exclusions.

Root cause: pagination identity is inferred only from the original intent and
resolved-brand special case. It must instead be inferred from whether the turn
contains any query mutation. A mutated `more_results` turn is a refinement (or
category switch) and must apply operations, clear display state, and search the
full catalog; only a mutation-free turn may preserve snapshot/seen state and
exclude seen products.

### Important 4 — reference brands are absent from parser taxonomy validation

Data flow: `DashScopeTurnQueryParser._validate_turn_query()` validates the
target category pair and iterates only over `slot_operations`.
`_validate_turn_operation()` checks include/exclude brand values, but
`ProductReference(kind="brand").brand` never enters that loop. Pydantic checks
only the reference shape, so an invented brand passes the first structured
response without invoking the existing correction attempt.

Root cause: post-Pydantic taxonomy validation has no reference-brand boundary.
The reference brand must be compared exactly with the configured catalog brand
enumeration inside `_validate_turn_query()`, allowing `_structured_call()` to
perform its one correction and normalize a twice-invalid result to retryable
`TURN_QUERY_PARSE_FAILED`.

### Minor 1 — semantic add/remove allow blank values

Data flow: `SemanticTermOperation.validate_value()` rejects only `None` for
`add`/`remove`. Empty and whitespace-only strings therefore satisfy the public
model even though the compiler later strips and rejects them as an invalid
condition.

Root cause: the model boundary does not enforce the documented non-empty
contract. It must strip and reject blank add/remove values while leaving
`clear` restricted to `value=None`.

### Minor 2 — single-turn feature page describes historical behavior as current

Data flow: the page already labels the first-stage scope as historical, but its
`本地运行` and `代码与验证` sections still state that the current service does
not restore `conversation_id` and that production `build_graph()` compiles a
single-turn graph. Those statements now conflict with the multi-turn production
entry documented in `multi-turn-query-engine.md`.

Root cause: the historical design body was retained without marking two
runtime-verification paragraphs as historical snapshots. They should remain
for provenance while explicitly delegating current production graph and
session behavior to the multi-turn feature document.

## TDD evidence

### Minor 1 — semantic operation non-empty contract

RED:

```powershell
uv run pytest tests/unit/test_turn_query_models.py::test_semantic_term_add_and_remove_reject_blank_values -q -p no:cacheprovider
# 3 failed: empty and whitespace-only values did not raise ValidationError

uv run pytest tests/unit/test_turn_query_models.py::test_semantic_term_add_strips_surrounding_whitespace -q -p no:cacheprovider
# 1 failed: surrounding whitespace remained in value
```

GREEN after adding boundary normalization and validation:

```powershell
uv run pytest tests/unit/test_turn_query_models.py::test_semantic_term_add_and_remove_reject_blank_values tests/unit/test_turn_query_models.py::test_semantic_term_add_strips_surrounding_whitespace -q -p no:cacheprovider
# 4 passed in 0.03s
```

### Important 4 — reference-brand taxonomy

RED:

```powershell
uv run pytest tests/unit/test_model_gateways.py::test_turn_query_parser_corrects_invalid_reference_brand tests/unit/test_model_gateways.py::test_turn_query_parser_normalizes_twice_invalid_reference_brand -q -p no:cacheprovider
# 2 failed: the invalid first response was accepted and twice-invalid did not raise
```

GREEN after exact catalog-brand validation in `_validate_turn_query()`:

```powershell
# same command
# 2 passed in 2.40s
```

### Important 3 — mutated more-results

RED:

```powershell
uv run pytest tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_price_operation_becomes_refinement tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_semantic_operation_becomes_refinement tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_relative_price_becomes_refinement -q -p no:cacheprovider
# 3 failed: all returned intent=more_results and retained the old snapshot
```

GREEN after removing the premature pagination return and classifying mutations:

```powershell
uv run pytest tests/unit/test_multi_turn_query_compiler.py::test_more_results_preserves_snapshot_candidates_focus_and_seen_ids tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_price_operation_becomes_refinement tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_semantic_operation_becomes_refinement tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_relative_price_becomes_refinement tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_resolved_brand_becomes_refine_and_clears_batch_state -q -p no:cacheprovider
# 5 passed in 0.07s

uv run pytest tests/unit/test_multi_turn_workflow.py::test_more_results_with_query_mutation_refines_from_full_catalog -q -p no:cacheprovider
# 3 passed in 0.85s; semantic, price, and relative cases used no seen exclusions
```

### Important 2 — reference-less product questions and attempt limit

RED:

```powershell
uv run pytest tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_uses_focused_product tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_uses_only_recent_candidate tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_with_multiple_candidates_clarifies tests/unit/test_multi_turn_workflow.py::test_ambiguous_pending_answer_without_reference_exits_attempt_limit -q -p no:cacheprovider
# 4 failed in load_product_facts with PRODUCT_KNOWLEDGE_UNAVAILABLE
```

GREEN after implicit demonstrative resolution and suspended-clue preservation:

```powershell
uv run pytest tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_uses_focused_product tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_uses_only_recent_candidate tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_with_multiple_candidates_clarifies tests/unit/test_multi_turn_workflow.py::test_ambiguous_pending_answer_without_reference_exits_attempt_limit tests/unit/test_multi_turn_workflow.py::test_second_unresolved_attempt_clears_pending_and_requests_complete_restatement -q -p no:cacheprovider
# 5 passed in 0.62s
```

### Important 1 — snapshot-aware missing-context recovery

RED:

```powershell
uv run pytest tests/unit/test_multi_turn_workflow.py::test_missing_price_baseline_answer_preserves_existing_snapshot_and_retrieves -q -p no:cacheprovider
# 1 failed: retrieval received category=None and lost untouched snapshot fields
```

GREEN after choosing `new_search` only when no loaded snapshot exists:

```powershell
uv run pytest tests/unit/test_multi_turn_workflow.py::test_missing_price_baseline_answer_preserves_existing_snapshot_and_retrieves tests/unit/test_multi_turn_workflow.py::test_missing_context_answer_builds_new_query_and_resumes_retrieval tests/unit/test_multi_turn_workflow.py::test_missing_context_answer_builds_new_search_and_preserves_suspended_slots -q -p no:cacheprovider
# 3 passed in 0.65s
```

### Minor 2 — historical single-turn documentation

No automated test was added for human prose. The `本地运行` and `代码与验证`
sections now explicitly label the single-turn statements as historical and
link current production graph/session behavior to the multi-turn feature page.

## Verification

Fresh related-module verification after all code and test changes:

```powershell
uv run pytest tests/unit/test_turn_query_models.py tests/unit/test_multi_turn_query_compiler.py tests/unit/test_model_gateways.py tests/unit/test_multi_turn_workflow.py tests/integration/test_chat_api.py -q -p no:cacheprovider
# 230 passed in 7.96s

uv run ruff check src/shop_agent/models/turn_query.py src/shop_agent/services/dashscope_chat.py src/shop_agent/services/multi_turn_query_compiler.py src/shop_agent/workflow/nodes.py tests/unit/test_turn_query_models.py tests/unit/test_model_gateways.py tests/unit/test_multi_turn_query_compiler.py tests/unit/test_multi_turn_workflow.py
# All checks passed!

uv run mypy src scripts
# Success: no issues found in 39 source files
```

The controller owns the final full pytest run; this repair agent intentionally
did not run it.

## Files

- `src/shop_agent/models/turn_query.py`
- `src/shop_agent/services/dashscope_chat.py`
- `src/shop_agent/services/multi_turn_query_compiler.py`
- `src/shop_agent/workflow/nodes.py`
- `tests/unit/test_turn_query_models.py`
- `tests/unit/test_model_gateways.py`
- `tests/unit/test_multi_turn_query_compiler.py`
- `tests/unit/test_multi_turn_workflow.py`
- `docs/features/multi-turn-query-engine.md`
- `docs/features/text-shopping-workflow.md`
- `.superpowers/sdd/2026-07-26-multi-turn-query-engine/final-review-fix-report.md`

## Self-review and constraints

- Root causes were traced before test or production changes.
- Pure `more_results` still preserves snapshot/focus/seen state, while every
  tested query mutation refines without seen exclusions.
- Reference-less product questions never expand beyond focus/latest candidates;
  a second unresolved answer clears pending before emitting restatement text.
- Feature status remains **开发中** and `docs/README.md` was not changed because
  no code entry moved.
- No Git command or Git operation was run.
- `RUN_LIVE_TESTS` was not set; no DashScope or Qdrant live call was made.
