# Turn Reference Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This repository task must be executed inline without sub-agents.

**Goal:** Prevent model-generated product references that are not grounded in the current user message from bypassing the saved product focus.

**Architecture:** Keep the model responsible for extracting reference clues, but validate clue provenance at the `DashScopeTurnQueryParser` boundary using the current message. Invalid references reuse the existing one-retry structured correction path; corrected reference-less product questions then flow through the existing deterministic focus fallback.

**Tech Stack:** Python 3.11, Pydantic v2, OpenAI-compatible DashScope client, pytest.

## Global Constraints

- Do not use sub-agents.
- Do not execute Git commands or Git operations.
- `ProductReference.surface_text` must be a non-blank contiguous substring of the current message after trimming only its surrounding whitespace.
- Do not silently remove an invalid reference or make focus override a valid explicit reference.
- Preserve the existing two-attempt structured-output correction behavior and public error codes.

---

### Task 1: Add parser provenance regression tests

**Files:**
- Modify: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: `DashScopeTurnQueryParser.parse(message: str, context: TurnContext)`.
- Produces: coverage for corrected and twice-invalid ungrounded references.

- [x] **Step 1: Write the failing correction test**

Create a response whose `reference.surface_text` and `product_name` are
`"商品2"` while the current message is `"有哪些存储版本？"`, followed by a valid response
with `reference=null`. Assert the result has no reference and the model was called twice.

- [x] **Step 2: Write the failing safe-error test**

Return the same ungrounded response twice. Assert `TURN_QUERY_PARSE_FAILED`,
`retryable=True`, and two model calls.

- [x] **Step 3: Verify RED**

Run:

```bash
uv run pytest -q tests/unit/test_model_gateways.py -k "ungrounded_reference"
```

Expected: both tests fail because the current validator accepts the invented reference.

### Task 2: Implement prompt and parser provenance validation

**Files:**
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Test: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Change private method to
  `_validate_turn_query(self, content: str, context: TurnContext, message: str) -> TurnQuery`.
- Add private static validation that raises `ValueError` when trimmed
  `reference.surface_text` is empty or absent from `message`.

- [x] **Step 1: Add prompt contract and reference-less example**

State that `surface_text` must be copied from a contiguous span in the current `message`;
candidate and focus context cannot be converted into a reference. Add
`"有哪些存储版本？"` with `reference=null` as an example.

- [x] **Step 2: Add deterministic provenance validation**

After Pydantic parsing, validate:

```python
reference = parsed.reference
if reference is not None:
    surface_text = reference.surface_text.strip()
    if not surface_text or surface_text not in message:
        raise ValueError(
            "reference surface_text must be a contiguous span of the current message"
        )
```

Pass `message` from `parse` into `_validate_turn_query`.

- [x] **Step 3: Verify GREEN**

Run:

```bash
uv run pytest -q tests/unit/test_model_gateways.py -k "turn_query"
```

Expected: all selected parser tests pass.

### Task 3: Guard focused structured follow-up behavior

**Files:**
- Modify: `tests/unit/test_multi_turn_workflow.py`

**Interfaces:**
- Consumes: the existing reference-less product-question focus fallback.
- Produces: regression coverage for `ProductQuestion(kind="structured", field="sku")`.

- [x] **Step 1: Add workflow regression test**

Persist focus `p2`, parse `"有哪些存储版本？"` as a structured product question with
`reference=None`, drain the graph, and assert:

```python
assert harness.retrieval.fetch_product_calls == []
assert '"product_id":"p2"' in harness.response.prompts[0]
assert '"sku_id":"p2-black"' in harness.response.prompts[0]
assert repository.record.state.focused_product_id == "p2"
```

- [x] **Step 2: Run focused workflow tests**

Run:

```bash
uv run pytest -q tests/unit/test_multi_turn_workflow.py -k "reference_less_product_question"
```

Expected: all selected tests pass because the deterministic fallback already exists.

### Task 4: Update feature documentation and verify

**Files:**
- Modify: `docs/features/multi-turn-query-engine.md`

**Interfaces:**
- Documents the externally observable reference provenance rule and regression evidence.

- [x] **Step 1: Document the provenance invariant**

Add that `surface_text` must be a current-message source span, context-derived references
are rejected and corrected once, and reference-less follow-ups use focus.

- [x] **Step 2: Run targeted tests**

```bash
uv run pytest -q tests/unit/test_model_gateways.py tests/unit/test_multi_turn_workflow.py -k "turn_query or reference_less_product_question"
```

- [x] **Step 3: Run the full suite**

```bash
uv run pytest -q -p no:cacheprovider
```

Expected: zero failures.
