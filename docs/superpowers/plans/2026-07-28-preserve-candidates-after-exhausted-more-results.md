# Preserve Candidates After Exhausted More Results Implementation Plan

> **For agentic workers:** Execute inline with TDD. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the last displayed product batch referenceable after a pure `more_results` turn returns no displayable products.

**Architecture:** Change only `persist_no_results`. For `search_intent == "more_results"`, retain the existing `recent_candidates`, `focused_product_id`, and `seen_product_ids`; for new searches, refinements, and category switches, preserve the existing clearing behavior.

**Tech Stack:** Python 3.11, LangGraph, Pydantic v2, pytest.

## Global Constraints

- Update `docs/features/multi-turn-query-engine.md` in the same behavior change.
- Do not execute Git commands.
- Do not expand product-reference resolution to `seen_product_ids`.
- Do not change the fixed exhausted-result response.

---

### Task 1: Preserve the last reference domain after exhausted pagination

**Files:**
- Modify: `tests/unit/test_multi_turn_workflow.py`
- Modify: `tests/integration/test_chat_api.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `docs/features/multi-turn-query-engine.md`

**Interfaces:**
- Consumes: `ShoppingState.search_intent` and persisted `ConversationState`.
- Produces: an exhausted pure `more_results` state that keeps the last displayed candidate batch and focus.

- [x] **Step 1: Write failing unit and HTTP regression assertions**

Assert that an exhausted `more_results` turn retains `recent_candidates`, `focused_product_id`, and `seen_product_ids`, and that a subsequent product question can resolve against the retained batch.

- [x] **Step 2: Verify RED**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q \
  tests/unit/test_multi_turn_workflow.py::test_failed_more_results_preserves_latest_reference_context \
  tests/integration/test_chat_api.py::test_compiled_http_more_results_exhaustion_preserves_follow_up_reference
```

Expected: assertions fail because `persist_no_results` clears the recent candidates and focus.

- [x] **Step 3: Implement the minimal state update**

In `WorkflowNodes.persist_no_results`, retain the three display-context fields only when `search_intent == "more_results"`. Keep all other branches unchanged.

- [x] **Step 4: Verify GREEN and regressions**

Run the focused tests, then the full test suite, Ruff, and mypy.

- [x] **Step 5: Update feature documentation**

Document that a failed pure `more_results` turn does not become a new display batch and therefore preserves the last reference domain.
