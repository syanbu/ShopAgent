# No-Results Fixed Responses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Use `subagent-driven-development` only if the user explicitly authorizes sub-agent execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Distinguish exhausted `more_results`, ordinary zero-match searches, and unreliable candidates with deterministic fixed Chinese responses that bypass the answer model.

**Architecture:** Add a transient `no_result_reason` to `ShoppingState`. The retrieval, evidence-validation, and final-selection nodes assign the reason at the point where the empty result becomes known. All empty branches persist the no-result conversation state first, then a dedicated node emits one fixed `text_delta`; successful search and other response routes keep their current behavior.

**Tech Stack:** Python 3.11, Pydantic typed domain models, LangGraph, FastAPI SSE, pytest, Ruff, mypy.

> **后续规则：** 本计划最初要求耗尽时清空最近候选与焦点。该持久化规则已被
> `2026-07-28-preserve-candidates-after-exhausted-more-results.md` 取代；当前实现对纯
> `more_results` 保留上一批候选、焦点和 `seen_product_ids`。

## Global Constraints

- This capability belongs to `docs/features/multi-turn-query-engine.md`; update that document in the same implementation and do not create another feature identity.
- Do not modify `docs/README.md` because the existing feature row and code-entry scope already cover the affected workflow.
- Do not execute any Git command or operation. Replace commit checkpoints with focused test and review checkpoints.
- Fixed no-result text must not call `ResponseGenerator`.
- Keep the public SSE contract unchanged: `message_start -> text_delta -> message_end`, with no `product` event and completed status.
- Keep the existing persistence order: `persist_no_results` must succeed before the fixed text is emitted.
- Do not modify retrieval, reranking, evidence assessment, SKU matching, SQLite tables, or serialized `ConversationState`.
- Preserve the current failed-`more_results` state behavior: retain `seen_product_ids`, clear `recent_candidates` and focus.
- The implementation assumes only mutation-free `more_results` retains that intent; existing compiler tests must continue to protect this boundary.

---

### Task 1: Add transient no-result reasons and fixed response contracts

**Files:**
- Modify: `src/shop_agent/models/state.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `tests/unit/test_workflow_routes.py`
- Modify: `tests/unit/test_multi_turn_workflow.py`

**Interfaces:**
- Produces: `NoResultReason = Literal["exhausted", "no_matches", "insufficient_evidence"]`.
- Produces: `ShoppingState.no_result_reason: NoResultReason`.
- Produces: `NO_RESULT_MESSAGES: dict[NoResultReason, str]`.
- Produces: `WorkflowNodes.emit_no_results_response(state, writer) -> dict[str, object]`.
- Consumes: `ShoppingState.search_intent` and the existing retrieval, validation, and selection outputs.

- [ ] **Step 1: Change the existing zero-retrieval and zero-evidence tests to require fixed text**

In `tests/unit/test_workflow_routes.py`, replace the model-prompt assertions in
`test_no_hits_skips_rerank_validation_and_decision` with:

```python
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。"
    )
    assert harness.response.prompts == []
```

Replace the final assertions in `test_evidence_empty_skips_candidate_decision` with:

```python
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "找到了一些候选商品，但现有信息不足以确认它们符合要求，"
        "建议您调整筛选条件。"
    )
    assert harness.response.prompts == []
```

- [ ] **Step 2: Add failing tests for exhausted `more_results` and empty final selection**

In `tests/unit/test_multi_turn_workflow.py`, extend
`test_failed_more_results_preserves_seen_ids` with:

```python
    events = await _drain_graph(
        _workflow_dependencies(harness, parser, repository),
        "换一批",
    )

    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "当前条件下没有更多符合要求的商品了。"
    )
    assert harness.response.prompts == []
```

Keep its existing assertions for `seen_product_ids`, `recent_candidates`, and focus.
Replace the existing ignored return value from `_drain_graph` rather than calling the
graph twice.

Add this graph-level regression near the other no-result persistence tests:

```python
@pytest.mark.asyncio
async def test_empty_final_selection_uses_insufficient_evidence_response(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    parser = FakeTurnQueryParser(
        [
            _search_turn(
                "new_search",
                [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                    {
                        "slot": "constraints.max_price",
                        "operation": "replace",
                        "value": 0,
                    },
                ],
            )
        ]
    )
    repository = FakeConversationRepository()

    events = await _drain_graph(
        _workflow_dependencies(harness, parser, repository),
        "找零元耳机",
    )

    assert harness.evidence.validate_calls
    assert harness.evidence.select_calls
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "找到了一些候选商品，但现有信息不足以确认它们符合要求，"
        "建议您调整筛选条件。"
    )
    assert harness.response.prompts == []
    assert repository.record is not None
    assert repository.record.state.recent_candidates == []
```

Add this remaining-candidate evidence regression:

```python
@pytest.mark.asyncio
async def test_more_results_with_only_ineligible_remaining_products_is_exhausted(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path, product_count=6, eligible=False)
    stored = _conversation()
    repository = FakeConversationRepository(
        ConversationRecord(state=stored, version=2)
    )
    parser = FakeTurnQueryParser([_search_turn("more_results")])

    events = await _drain_graph(
        _workflow_dependencies(harness, parser, repository),
        "换一批",
    )

    assert harness.retrieval.retrieve_calls[0].excluded_product_ids == (
        "p1",
        "p2",
        "p3",
    )
    assert harness.evidence.validate_calls
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "当前条件下没有更多符合要求的商品了。"
    )
    assert harness.response.prompts == []
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_workflow_routes.py::test_no_hits_skips_rerank_validation_and_decision \
  tests/unit/test_workflow_routes.py::test_evidence_empty_skips_candidate_decision \
  tests/unit/test_multi_turn_workflow.py::test_failed_more_results_preserves_seen_ids \
  tests/unit/test_multi_turn_workflow.py::test_empty_final_selection_uses_insufficient_evidence_response \
  tests/unit/test_multi_turn_workflow.py::test_more_results_with_only_ineligible_remaining_products_is_exhausted
```

Expected: failures show the current two model-generated deltas, a non-empty
`harness.response.prompts`, and the empty final-selection path still taking the success
route.

- [ ] **Step 4: Add the transient state type**

In `src/shop_agent/models/state.py`, add the alias before `ShoppingState`:

```python
NoResultReason = Literal[
    "exhausted",
    "no_matches",
    "insufficient_evidence",
]
```

Add this field to `ShoppingState`:

```python
    no_result_reason: NoResultReason
```

Do not add the field to `ConversationState`.

- [ ] **Step 5: Add fixed messages and source-local reason assignment**

In `src/shop_agent/workflow/nodes.py`, import `NoResultReason` with
`ShoppingState`:

```python
from shop_agent.models.state import NoResultReason, ShoppingState
```

Add the exact constants beside `SAFETY_RULES`:

```python
NO_RESULT_MESSAGES: dict[NoResultReason, str] = {
    "exhausted": "当前条件下没有更多符合要求的商品了。",
    "no_matches": (
        "当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。"
    ),
    "insufficient_evidence": (
        "找到了一些候选商品，但现有信息不足以确认它们符合要求，"
        "建议您调整筛选条件。"
    ),
}
```

Add this helper near the route helpers:

```python
def _no_result_reason(
    state: ShoppingState,
    ordinary_reason: Literal["no_matches", "insufficient_evidence"],
) -> NoResultReason:
    return (
        "exhausted"
        if state["search_intent"] == "more_results"
        else ordinary_reason
    )
```

Update `retrieve_chunks` so its empty branch writes the reason:

```python
        if not chunks:
            updates.update(
                {
                    "response_mode": "no_results",
                    "no_result_reason": _no_result_reason(state, "no_matches"),
                }
            )
```

Update `validate_evidence` so its empty-eligible branch writes:

```python
        if not any(candidate.eligible for candidate in validated):
            updates.update(
                {
                    "response_mode": "no_results",
                    "no_result_reason": _no_result_reason(
                        state,
                        "insufficient_evidence",
                    ),
                }
            )
```

Update `decide_candidates` so a final empty selection is marked:

```python
        updates: dict[str, object] = {
            "selected_products": selected,
            "response_mode": "shopping",
        }
        if not selected:
            updates.update(
                {
                    "response_mode": "no_results",
                    "no_result_reason": _no_result_reason(
                        state,
                        "insufficient_evidence",
                    ),
                }
            )
        return updates
```

- [ ] **Step 6: Add the deterministic response node**

Add this method to `WorkflowNodes` beside `generate_clarification`:

```python
    async def emit_no_results_response(
        self,
        state: ShoppingState,
        writer: StreamWriter,
    ) -> dict[str, object]:
        reason = state.get("no_result_reason")
        if reason is None:
            raise RuntimeError("no-result response requires a reason")
        message = NO_RESULT_MESSAGES[reason]
        writer(
            {
                "event": "text_delta",
                "data": TextDeltaData(delta=message).model_dump(mode="json"),
            }
        )
        return {"response_text": message}
```

In `build_verified_response_prompt`, replace the old `if not selected` prompt branch
with a fail-closed invariant:

```python
    if not selected:
        raise RuntimeError("shopping response requires selected products")
```

This prevents a future empty branch from silently reintroducing model-generated
no-result text.

- [ ] **Step 7: Add the missing-reason regression**

In `tests/unit/test_workflow_routes.py`, import `build_nodes` and add:

```python
@pytest.mark.asyncio
async def test_fixed_no_result_response_requires_reason(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))
    events: list[dict[str, object]] = []

    with pytest.raises(
        RuntimeError,
        match="no-result response requires a reason",
    ):
        await nodes.emit_no_results_response(
            {"response_mode": "no_results"},
            events.append,
        )

    assert events == []
```

- [ ] **Step 8: Run the node contract tests**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_workflow_routes.py::test_no_hits_skips_rerank_validation_and_decision \
  tests/unit/test_workflow_routes.py::test_evidence_empty_skips_candidate_decision \
  tests/unit/test_workflow_routes.py::test_fixed_no_result_response_requires_reason
```

Expected: PASS. The two user-visible paths emit exact fixed messages, and a missing
reason fails before emitting text.

---

### Task 2: Route every empty search through persistence and fixed emission

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `src/shop_agent/workflow/graph.py`
- Modify: `tests/unit/test_workflow_routes.py`
- Modify: `tests/unit/test_multi_turn_workflow.py`

**Interfaces:**
- Consumes: `WorkflowNodes.emit_no_results_response`.
- Produces: `SelectionRoute = Literal["has_products", "no_products"]`.
- Produces: `route_selection(state: ShoppingState) -> SelectionRoute`.
- Preserves: all no-result branches call `persist_no_results` before emitting text.

- [ ] **Step 1: Add failing selection-route tests**

In `tests/unit/test_workflow_routes.py`, import `route_selection` from
`shop_agent.workflow.nodes` and add:

```python
def test_selection_route_distinguishes_empty_and_nonempty_products(
    tmp_path: Path,
) -> None:
    assert route_selection({"selected_products": []}) == "no_products"

    harness = build_harness(tmp_path)
    selected = SelectedProduct(
        product_id="p1",
        rerank_score=0.9,
        evidence_ids=["p1:summary"],
        decision_reasons=["test"],
        matched_sku_ids=["p1-black"],
    )
    assert route_selection({"selected_products": [selected]}) == "has_products"
```

Add `SelectedProduct` to the test imports.

- [ ] **Step 2: Run the route test and verify RED**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_workflow_routes.py::test_selection_route_distinguishes_empty_and_nonempty_products
```

Expected: collection fails because `route_selection` does not exist.

- [ ] **Step 3: Implement selection routing**

In `src/shop_agent/workflow/nodes.py`, add the route type:

```python
SelectionRoute = Literal["has_products", "no_products"]
```

Add the route function beside `route_validation`:

```python
def route_selection(state: ShoppingState) -> SelectionRoute:
    return "has_products" if state["selected_products"] else "no_products"
```

- [ ] **Step 4: Rewire the graph**

In `src/shop_agent/workflow/graph.py`, import `route_selection`. Register the
deterministic node:

```python
    builder.add_node(
        "emit_no_results_response",
        nodes.emit_no_results_response,
    )
```

Replace:

```python
    builder.add_edge("decide_candidates", "persist_search_result")
```

with:

```python
    builder.add_conditional_edges(
        "decide_candidates",
        route_selection,
        {
            "has_products": "persist_search_result",
            "no_products": "persist_no_results",
        },
    )
```

Replace:

```python
    builder.add_edge("persist_no_results", "generate_response")
```

with:

```python
    builder.add_edge("persist_no_results", "emit_no_results_response")
    builder.add_edge("emit_no_results_response", END)
```

Keep the existing zero-retrieval and zero-eligible edges pointed at
`persist_no_results`.

- [ ] **Step 5: Strengthen persistence-before-text assertions**

Update `test_no_result_paths_persist_before_text_and_clear_latest_focus` in
`tests/unit/test_multi_turn_workflow.py`. Add the expected fixed message to its
parameterization:

```python
@pytest.mark.parametrize(
    ("return_hits", "eligible", "expected_message"),
    [
        (
            False,
            True,
            "当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。",
        ),
        (
            True,
            False,
            "找到了一些候选商品，但现有信息不足以确认它们符合要求，"
            "建议您调整筛选条件。",
        ),
    ],
)
```

Collect events manually so the writer callback can assert persistence has completed:

```python
    events: list[dict[str, Any]] = []
    async for part in build_graph(
        _workflow_dependencies(harness, parser, repository)
    ).astream(initial_state("继续筛选"), stream_mode="custom", version="v2"):
        assert repository.trace == ["persist"]
        events.append(part)
```

Add:

```python
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == expected_message
    assert harness.response.prompts == []
```

- [ ] **Step 6: Run workflow tests and verify GREEN**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_workflow_routes.py \
  tests/unit/test_multi_turn_workflow.py \
  tests/unit/test_workflow_stream.py
```

Expected: all selected tests pass. Successful searches still call the response model;
all no-result paths emit exactly one fixed delta and make zero response-model calls.

---

### Task 3: Add an HTTP regression for exhausted results

**Files:**
- Modify: `tests/integration/test_chat_api.py`

**Interfaces:**
- Consumes: the existing compiled graph, SQLite repository, SSE parser, and
  `SequencedResponseGenerator`.
- Verifies: the public API emits fixed exhausted text with completed status and does
  not make a second response-model call.

- [ ] **Step 1: Add the failing compiled HTTP dialogue test**

Add this test near the other compiled multi-turn HTTP tests:

```python
@pytest.mark.asyncio
async def test_compiled_http_more_results_exhaustion_uses_fixed_text(
    tmp_path: Path,
) -> None:
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "more_results",
            }
        ),
    ]
    dependencies, _, repository, _, _ = compiled_chat_dependencies(
        tmp_path,
        turns=turns,
        response_generator=SequencedResponseGenerator(["首轮推荐"]),
    )

    first = await _post(
        dependencies,
        {"conversation_id": "exhausted-results", "message": "推荐蓝牙耳机"},
    )
    exhausted = await _post(
        dependencies,
        {"conversation_id": "exhausted-results", "message": "还有别的吗"},
    )

    assert "product" in [event.name for event in parse_sse(first.text)]
    events = parse_sse(exhausted.text)
    assert [event.name for event in events] == [
        "message_start",
        "text_delta",
        "message_end",
    ]
    assert events[1].data["delta"] == "当前条件下没有更多符合要求的商品了。"
    assert events[-1].data["status"] == "completed"

    saved = await repository.load("exhausted-results")
    assert saved is not None
    assert saved.state.recent_candidates == []
    assert saved.state.focused_product_id is None
    assert saved.state.seen_product_ids == ["p1", "p2", "p3"]
```

Using a one-item `SequencedResponseGenerator` is intentional: if the exhausted turn
still calls the model, the second request raises `StopIteration` and the assertions fail.

- [ ] **Step 2: Run the HTTP regression and verify GREEN**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/integration/test_chat_api.py::test_compiled_http_more_results_exhaustion_uses_fixed_text
```

Expected: PASS with one fixed `text_delta`, completed status, no product event, and
preserved seen IDs.

- [ ] **Step 3: Run the complete HTTP integration file**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/integration/test_chat_api.py
```

Expected: all tests pass and the existing public error/partial/completed contracts remain
unchanged.

---

### Task 4: Update the canonical multi-turn feature document and verify

**Files:**
- Modify: `docs/features/multi-turn-query-engine.md`
- Verify: `docs/README.md` remains unchanged
- Verify: all files under `tests/`

**Interfaces:**
- Documents: fixed no-result classifications, internal transient reason, graph route,
  persistence order, and exact regression evidence.
- Preserves: existing feature identity, API schema, SQLite schema, and external
  dependencies.

- [ ] **Step 1: Update external behavior in the existing feature document**

In `docs/features/multi-turn-query-engine.md`, add a subsection after
“条件细化与换一批” named `### 无结果与结果耗尽`. Document these exact rules:

```markdown
### 无结果与结果耗尽

无结果响应由后端根据工作流事实选择固定文案，不调用回答模型：

- 纯 `more_results` 没有新商品时返回“当前条件下没有更多符合要求的商品了。”
- 新搜索、条件细化或品类切换零召回时返回“当前筛选条件下没有找到匹配商品，建议您
  放宽或修改筛选条件。”
- 有召回但证据校验或最终 SKU 选择没有可展示商品时返回“找到了一些候选商品，但现有
  信息不足以确认它们符合要求，建议您调整筛选条件。”

内部 `no_result_reason` 只存在于单次 `ShoppingState`，不进入 SQLite 或 SSE 数据。
所有无结果分支先执行 `persist_no_results`，保存成功后才发送一个固定
`text_delta`。失败的纯 `more_results` 保留 `seen_product_ids`，同时沿用现有规则清空
最近候选和焦点。
```

- [ ] **Step 2: Update workflow, decisions, coverage, and change record**

In the workflow diagram, replace the no-result generation edge with:

```text
persist_no_results -> emit_no_results_response -> END
```

Add a key decision stating that deterministic workflow states use fixed responses and
do not spend a model call. Add coverage rows naming these exact tests:

```markdown
| 无结果原因分类与固定文案 | `tests/unit/test_workflow_routes.py::test_no_hits_skips_rerank_validation_and_decision`、`test_evidence_empty_skips_candidate_decision`；`tests/unit/test_multi_turn_workflow.py::test_failed_more_results_preserves_seen_ids`、`test_empty_final_selection_uses_insufficient_evidence_response`、`test_more_results_with_only_ineligible_remaining_products_is_exhausted` |
| 结果耗尽的 HTTP/SSE 与持久化边界 | `tests/integration/test_chat_api.py::test_compiled_http_more_results_exhaustion_uses_fixed_text`；`tests/unit/test_multi_turn_workflow.py::test_no_result_paths_persist_before_text_and_clear_latest_focus` |
```

Add a `2026-07-28` change-record row:

```markdown
| 2026-07-28 | 区分结果耗尽、零匹配与候选信息不足，并改为后端固定文案 | 避免“没有更多商品”被误报为筛选失败，同时移除确定性无结果场景的回答模型调用 |
```

Do not add a new feature document and do not change the `docs/README.md` feature row.

- [ ] **Step 3: Run focused verification**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_workflow_routes.py \
  tests/unit/test_multi_turn_workflow.py \
  tests/unit/test_workflow_stream.py \
  tests/integration/test_chat_api.py
```

Expected: zero failures.

- [ ] **Step 4: Run full verification**

Run:

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider
env -u ALL_PROXY -u all_proxy .venv/bin/ruff check .
env -u ALL_PROXY -u all_proxy .venv/bin/mypy src scripts
```

Expected:

- pytest: zero failures; the existing live test remains skipped unless
  `RUN_LIVE_TESTS=1` is explicitly enabled.
- Ruff: `All checks passed!`
- mypy: `Success: no issues found in 39 source files`

- [ ] **Step 5: Record fresh verification evidence**

Replace the stale counts and timing in the `Fresh 验证` section of
`docs/features/multi-turn-query-engine.md` with the exact outputs from Step 4. State
that the opt-in live test was not run unless it actually ran.

- [ ] **Step 6: Manual acceptance**

With the existing service running, reuse one `conversation_id` and enter:

```text
推荐一款1000元以内的蓝牙耳机
还有别的产品吗
还有别的耳机吗
```

Pass criteria:

- Turns with additional products keep returning product events normally.
- The first exhausted turn returns exactly
  `当前条件下没有更多符合要求的商品了。`
- A separate over-constrained search returns exactly
  `当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。`
- No exhausted or ordinary no-match turn calls the answer model.
- Server logs still show `conversation_persisted` before the client receives text.

## Rollback and Failure Handling

- The change has no database migration and writes no new persisted field. Reverting the
  workflow code restores the old model-generated no-result text.
- Do not weaken reason classification by inferring “conditions are too strict”; the
  backend only knows that the current conditions yielded no displayable result.
- If a future compiler lets mutated `more_results` retain that intent, update the
  compiler boundary before relying on `exhausted`; do not patch the response node with
  message-text heuristics.
- If an unclassified empty path reaches `emit_no_results_response`, keep the fail-closed
  internal error. Do not silently call the answer model or emit a generic fallback.

## Acceptance Summary

- Pure `more_results` exhaustion uses a fixed “没有更多” response.
- Ordinary zero matches and insufficient evidence use distinct fixed responses.
- Empty final SKU selection cannot pass through the successful-search route.
- All no-result responses are emitted after persistence and make zero answer-model calls.
- Public SSE, SQLite schema, successful search, product question, clarification, and
  non-shopping behavior remain compatible.
- `docs/features/multi-turn-query-engine.md` describes the implemented behavior, and no
  new feature document or index entry is introduced.
- No Git operation is performed.
