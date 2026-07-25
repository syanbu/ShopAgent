# Evidence Validation Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-candidate serial evidence-model calls with request-local concurrency capped at five while preserving candidate order, validation semantics, and error identity.

**Architecture:** `EvidenceService.validate_candidates()` creates one task per candidate and uses a request-local `asyncio.Semaphore(5)` only around `EvidenceMapper.map_conditions()`. Tasks are gathered in input order; if one fails, sibling tasks are cancelled and awaited before the original exception is re-raised. There is no process-wide or deployment-wide limiter.

**Tech Stack:** Python 3.11+, asyncio, pytest-asyncio, Pydantic, LangGraph service layer

## Global Constraints

- Evidence concurrency is fixed at exactly five per `validate_candidates()` call.
- Do not add an environment setting or process-global semaphore.
- Preserve `supported` and `unknown` eligibility and reject only `contradicted` semantic conditions.
- Preserve input order in the returned `ValidatedCandidate` list.
- Preserve the original `ServiceError` instead of wrapping it in `ExceptionGroup`.
- Git operations are omitted because repository instructions require separate user authorization for each operation.

---

### Task 1: Concurrent evidence validation

**Files:**
- Modify: `tests/unit/test_evidence_service.py`
- Modify: `src/shop_agent/services/evidence.py`

**Interfaces:**
- Consumes: `EvidenceMapper.map_conditions(product_id, conditions, evidence) -> EvidenceAssessment`
- Produces: unchanged `EvidenceService.validate_candidates(...) -> list[ValidatedCandidate]`

- [x] **Step 1: Write the failing concurrency-limit test**

Add an async mapper double that records active calls, yields once with `await asyncio.sleep(0)`, and returns one valid assessment per product. Validate ten candidates and assert the observed maximum active calls is exactly five.

- [x] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/unit/test_evidence_service.py::test_validate_candidates_limits_evidence_concurrency_to_five -q -p no:cacheprovider`

Expected: FAIL because the current serial loop observes only one active call.

- [x] **Step 3: Implement request-local concurrency**

Extract the existing single-candidate body into `_validate_candidate(...)`. In `validate_candidates()`, create `asyncio.Semaphore(5)`, schedule all candidates, and acquire the semaphore only around `self._mapper.map_conditions(...)`.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: PASS with maximum active calls equal to five.

- [x] **Step 5: Write and verify order-preservation test**

Use a mapper whose candidates complete in a different order, then assert `ValidatedCandidate` results remain in the original candidate order.

- [x] **Step 6: Write and verify failure-cancellation test**

Use a mapper where one request raises a specific `ServiceError` and siblings block. Assert siblings receive cancellation and the same `ServiceError` instance is re-raised.

### Task 2: Documentation and regression verification

**Files:**
- Modify: `docs/features/cross-category-shopping-constraints.md`
- Modify: `docs/features/text-shopping-workflow.md`

**Interfaces:**
- Consumes: the Task 1 concurrency behavior
- Produces: documented request-local concurrency contract

- [x] **Step 1: Document the concurrency boundary**

State that evidence validation runs at most five model requests concurrently within one chat turn, does not impose a process-global limit, preserves candidate order, and leaves model-call count unchanged.

- [x] **Step 2: Run focused regression tests**

Run: `uv run pytest tests/unit/test_evidence_service.py tests/unit/test_workflow_routes.py -q -p no:cacheprovider`

- [x] **Step 3: Run complete verification**

Run `uv run pytest -q -p no:cacheprovider` with a workspace-local `--basetemp`, followed by `uv run ruff check .` and `uv run mypy src scripts`.
