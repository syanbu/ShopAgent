# Evidence Diagnostic Logging Implementation Plan

> **For agentic workers:** Execute inline with test-driven development. The repository instructions prohibit Git operations unless separately authorized.

**Goal:** Add enough structured logs to identify whether the evidence model omitted, rewrote, duplicated, or added condition IDs.

**Architecture:** Log the evidence mapping request and raw model output at the model boundary in `DashScopeEvidenceMapper`. Log the exact expected/returned set difference immediately before `EvidenceService` raises the existing mismatch error. Keep all records single-line JSON and exclude credentials and model-client configuration.

**Tech Stack:** Python 3.11+, standard `logging` and `json`, Pydantic, pytest.

## Global Constraints

- Do not change evidence validation or retry behavior.
- Do not log API keys, authorization headers, environment variables, or full evidence text.
- Do not execute Git commands.

---

### Task 1: Evidence model boundary logs

**Files:**
- Modify: `tests/unit/test_model_gateways.py`
- Modify: `src/shop_agent/services/dashscope_chat.py`

- [x] Add a failing test asserting that `evidence_mapping_input` contains the product ID, complete conditions, and evidence metadata without evidence text.
- [x] Add a failing test asserting that `evidence_model_raw_output` contains the product ID and raw response as escaped single-line JSON.
- [x] Run the focused tests and confirm they fail because the log records do not exist.
- [x] Add the minimal logging around `DashScopeEvidenceMapper.map_conditions`.
- [x] Run the focused tests and confirm they pass.

### Task 2: Condition mismatch diagnostics

**Files:**
- Modify: `tests/unit/test_evidence_service.py`
- Modify: `src/shop_agent/services/evidence.py`

- [x] Extend the existing mismatch test to assert `expected`, `returned`, `missing`, `unexpected`, and parsed checks.
- [x] Run the focused test and confirm it fails because the diagnostic log does not exist.
- [x] Add the minimal error log immediately before the existing `ServiceError`.
- [x] Run the focused test and confirm it passes without changing the exception contract.

### Task 3: Verification

**Files:**
- Verify all modified production and test files.

- [x] Run the two affected unit-test modules.
- [x] Run the full unit-test suite.
- [x] Run Ruff and mypy.
