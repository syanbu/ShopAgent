# Parsed Intent Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log the final validated `ParsedIntent` JSON object for every successfully classified request without allowing user-controlled values to split or forge log lines.

**Architecture:** Add one structured, single-line `INFO` log at the `structure_intent` workflow boundary, after parsing and taxonomy normalization. Serialize the complete payload with `json.dumps(..., ensure_ascii=True)` before emitting it through Uvicorn's server logger, so correlation IDs and free-form intent fields cannot inject physical log lines.

**Tech Stack:** Python 3.11+, standard-library `logging` and `json`, Pydantic v2, LangGraph, pytest `caplog`.

## Global Constraints

- Log both `product_search` and `non_shopping` intents.
- Log the final Pydantic-validated and taxonomy-normalized object, not raw model output.
- Preserve values after JSON decoding and emit the encoded payload on one physical line.
- Include `request_id` and `conversation_id`.
- Do not change SSE events, intent schemas, routing, or error behavior.
- Do not update `docs/README.md`; this operational log is not a new indexed feature.
- Do not execute Git commands without separate authorization for that exact operation.

---

### Task 1: Log the final parsed intent

**Files:**
- Modify: `tests/unit/test_workflow_routes.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `docs/features/text-shopping-workflow.md`

**Interfaces:**
- Consumes: `IntentParser.parse(message: str) -> ParsedIntent`, `ShoppingState`, and `WorkflowDependencies.id_factory`.
- Produces: one `uvicorn.error` `INFO` record whose message is `parsed_intent request_id=<id> conversation_id=<id> json=<object>`.

- [x] **Step 1: Write failing tests for shopping and non-shopping logs**

Add imports and a parameterized test to `tests/unit/test_workflow_routes.py`:

```python
import json
import logging

from shop_agent.workflow.nodes import build_nodes


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("推荐蓝牙耳机", "product_search"),
        ("你好", "non_shopping"),
    ],
)
@pytest.mark.asyncio
async def test_structure_intent_logs_final_json_for_every_intent(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    message: str,
    expected_intent: str,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await nodes.structure_intent(initial_state(message))

    records = [
        record
        for record in caplog.records
        if record.name == "uvicorn.error"
        and record.getMessage().startswith("parsed_intent ")
    ]
    assert len(records) == 1
    log_message = records[0].getMessage()
    assert "request_id=request-fixed" in log_message
    assert "conversation_id=conversation-fixed" in log_message
    parsed_json = log_message.partition(" json=")[2]
    assert json.loads(parsed_json)["intent"] == expected_intent
    if expected_intent == "product_search":
        assert json.loads(parsed_json)["constraints"]["max_price"] == 500
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_workflow_routes.py::test_structure_intent_logs_final_json_for_every_intent -q
```

Expected: both parameter cases fail because no `parsed_intent` log record exists.

- [x] **Step 3: Add the minimal workflow-node log**

In `src/shop_agent/workflow/nodes.py`, import logging, use the Uvicorn server logger, resolve the existing or generated correlation IDs, and log the final object before returning:

```python
import logging


logger = logging.getLogger("uvicorn.error")


async def structure_intent(self, state: ShoppingState) -> dict[str, object]:
    parsed = await self.dependencies.intent_parser.parse(state["user_message"])
    updates: dict[str, object] = {
        "parsed_intent": parsed,
        "response_mode": (
            "shopping" if parsed.intent == "product_search" else "non_shopping"
        ),
    }
    request_id = state.get("request_id")
    if request_id is None:
        request_id = self.dependencies.id_factory()
        updates["request_id"] = request_id
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        conversation_id = self.dependencies.id_factory()
        updates["conversation_id"] = conversation_id
    logger.info(
        "parsed_intent request_id=%s conversation_id=%s json=%s",
        request_id,
        conversation_id,
        json.dumps(
            parsed.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return updates
```

- [x] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_workflow_routes.py::test_structure_intent_logs_final_json_for_every_intent -q
```

Expected: `2 passed`.

- [x] **Step 5: Document the operational behavior**

Add this statement to the “代码与验证” section of `docs/features/text-shopping-workflow.md`:

```markdown
意图识别成功后，`structure_intent` 节点以 `INFO` 级别输出最终
`ParsedIntent` 单行 JSON，并附带 `request_id` 和 `conversation_id`。
购物与非购物意图均记录；意图识别失败时沿用原有错误链路，不输出成功对象。
```

- [x] **Step 6: Run the affected and full verification suites**

Run:

```bash
uv run pytest tests/unit/test_workflow_routes.py -q
uv run pytest -q -m "not live"
uv run ruff check .
uv run mypy src
```

Expected: all pytest tests pass; Ruff and mypy exit successfully with no errors.

- [x] **Step 7: Review without Git operations**

Re-read the three modified files and confirm the log contains only the requested final intent object plus correlation IDs. Do not stage, commit, or run any Git command.

---

### Task 2: Prevent log-line injection

**Files:**
- Modify: `tests/unit/test_workflow_routes.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `docs/features/text-shopping-workflow.md`
- Modify: `docs/superpowers/specs/2026-07-23-parsed-intent-logging-design.md`

**Interfaces:**
- Consumes: the resolved `request_id`, user-supplied or generated `conversation_id`, and final `ParsedIntent`.
- Produces: `parsed_intent <compact-json>`, where `<compact-json>` contains `request_id`, `conversation_id`, and `intent`.
- Preserves: the original values after `json.loads()`, graph state, SSE output, and the `INFO` log level.

- [x] **Step 1: Write a failing log-injection regression test**

Add a test that passes `conversation_id = "ok\nINFO forged=true"` to
`WorkflowNodes.structure_intent`, captures the `uvicorn.error` record, asserts
that the rendered message contains no physical newline, parses the text after
`parsed_intent ` with `json.loads()`, and confirms that the decoded
`conversation_id` still contains the original newline.

- [x] **Step 2: Run the regression test and verify RED**

Run:

```bash
.venv/bin/pytest tests/unit/test_workflow_routes.py::test_structure_intent_escapes_log_line_separators -q
```

Expected: FAIL because the current `%s` formatting writes the newline directly.

- [x] **Step 3: Serialize the complete log payload**

Replace the positional log fields with:

```python
log_payload = {
    "request_id": request_id,
    "conversation_id": conversation_id,
    "intent": parsed.model_dump(mode="json"),
}
logger.info(
    "parsed_intent %s",
    json.dumps(
        log_payload,
        ensure_ascii=True,
        separators=(",", ":"),
    ),
)
```

- [x] **Step 4: Run the regression and existing logging tests**

Run:

```bash
.venv/bin/pytest tests/unit/test_workflow_routes.py -q
```

Expected: all workflow route tests pass.

- [x] **Step 5: Update the current feature document**

Document the compact JSON payload and note that correlation IDs and intent
values are JSON-escaped so one request produces exactly one physical log line.

- [x] **Step 6: Run the full verification suite**

Run:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -m "not live"
```

Expected: formatting, lint, and types pass; all non-live tests pass.
