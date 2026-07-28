# Category Taxonomy Reference Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert natural-language product-type expressions into validated Catalog taxonomy scopes so unique scopes become hard filters, ambiguous scopes trigger resumable clarification, and exhausted result batches never leak across categories.

**Architecture:** Extend `TurnQuery` with a category reference containing the user surface span and exact Catalog candidates. Reuse `reference_resolver.py` for deterministic candidate-count resolution, pass the trusted scope into the multi-turn compiler, and add one workflow node between product-reference resolution and turn routing. Persist ambiguous category scopes in the existing SQLite JSON state without DDL.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph, pytest, Ruff, mypy.

## Global Constraints

- Read `docs/README.md` and update `docs/features/multi-turn-query-engine.md` in the same behavior change.
- Do not execute Git commands; repository instructions override generic commit steps.
- Use TDD: every production behavior starts with a failing focused test.
- LLM handles language matching; backend validates Catalog membership and decides by candidate count.
- A unique candidate resolves, multiple candidates clarify, and an explicit reference with zero candidates does not retrieve.
- Categoryless requests with `category_reference=null` continue to support global semantic search.
- No new dependency, external API field, database table, or Qdrant payload field.

---

### Task 1: Category reference and pending-state models

**Files:**
- Modify: `src/shop_agent/models/turn_query.py`
- Modify: `src/shop_agent/models/conversation.py`
- Modify: `src/shop_agent/models/__init__.py`
- Test: `tests/unit/test_turn_query_models.py`
- Test: `tests/unit/test_conversation_models.py`

**Interfaces:**
- Produces: `CategoryCandidate(category: str, sub_category: str | None)`.
- Produces: `CategoryReference(surface_text: str, candidates: list[CategoryCandidate])`.
- Produces: `TurnQuery.category_reference: CategoryReference | None`.
- Produces: `PendingClarification.candidate_category_scopes: tuple[CategoryCandidate, ...]`.

- [x] **Step 1: Add failing model tests**

Cover:

```python
reference = CategoryReference(
    surface_text="耳机",
    candidates=[
        CategoryCandidate(category="数码电子", sub_category="真无线耳机")
    ],
)
assert TurnQuery(
    schema_version=1,
    intent="new_search",
    category_reference=reference,
).category_reference == reference
```

Also assert duplicate scopes fail, blank `surface_text` fails, a category reference cannot coexist with `category` or `sub_category` slot operations, defaults are independent, and old pending JSON without `candidate_category_scopes` still loads.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py
```

Expected: collection or test failures because the new models and fields do not exist.

- [x] **Step 3: Implement minimal models**

Add frozen, extra-forbidden models:

```python
class CategoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: str = Field(min_length=1)
    sub_category: str | None = None


class CategoryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_text: str = Field(min_length=1)
    candidates: list[CategoryCandidate] = Field(default_factory=list)
```

Normalize surrounding whitespace, reject duplicate `(category, sub_category)` scopes, add `category_reference` to `TurnQuery`, and reject coexistence with direct category slots. Extend pending kind with `ambiguous_category` and add a normalized unique tuple of category scopes with an empty default. Export the new public models.

- [x] **Step 4: Verify GREEN**

Run the Task 1 command and require all selected tests to pass.

---

### Task 2: Structured parser taxonomy-reference contract

**Files:**
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Test: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: Task 1 `CategoryReference`.
- Produces: validated parser output whose surface span is grounded and whose candidates are exact Catalog scopes in stable taxonomy order.

- [x] **Step 1: Add failing parser tests**

Add mocked structured responses for:

```json
{
  "schema_version": 1,
  "intent": "new_search",
  "category_reference": {
    "surface_text": "耳机",
    "candidates": [
      {"category": "数码电子", "sub_category": "真无线耳机"}
    ]
  }
}
```

Assert the parser accepts this for “推荐耳机”. Add correction tests for an ungrounded span, unknown category, invalid category/subcategory pair, duplicate or out-of-order candidates, and coexistence with direct category slots. Assert an empty candidate list is accepted for an explicit unsupported type.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/unit/test_model_gateways.py
```

Expected: new category-reference assertions fail because the prompt and contextual validation are absent.

- [x] **Step 3: Implement prompt and validation**

Update `_build_turn_query_system_prompt` to require:

```text
用户的品类说法可以是简称、别名或上位词；category_reference.surface_text 复制当前原文；
candidates 必须列出所有合理的精确 Catalog scope；模型不得为了避免澄清只返回一个；
已识别的品类词不得只写入 semantic_term_operations。
```

In `DashScopeTurnQueryParser._validate_turn_query`, validate the surface span, exact category or category pair, uniqueness, and stable order derived from the injected sorted taxonomy. Preserve the existing one-retry structured correction behavior.

- [x] **Step 4: Verify GREEN**

Run the Task 2 command and require all tests to pass.

---

### Task 3: Deterministic category resolution and compiler binding

**Files:**
- Modify: `src/shop_agent/services/reference_resolver.py`
- Modify: `src/shop_agent/services/multi_turn_query_compiler.py`
- Test: `tests/unit/test_reference_resolver.py`
- Test: `tests/unit/test_multi_turn_query_compiler.py`

**Interfaces:**
- Produces: `CategoryResolution` with outcome `resolved`, `ambiguous`, or `unsupported`.
- Produces: `resolve_category_reference(reference, catalog, allowed_scopes=None)`.
- Changes: `merge_turn_query(..., resolved_category_scope: CategoryCandidate | None = None)`.

- [x] **Step 1: Add failing resolver and compiler tests**

Assert:

```python
resolved = resolve_category_reference(
    CategoryReference(
        surface_text="耳机",
        candidates=[
            CategoryCandidate(
                category="数码电子",
                sub_category="真无线耳机",
            )
        ],
    ),
    catalog,
)
assert resolved.scope == CategoryCandidate(
    category="数码电子",
    sub_category="真无线耳机",
)
```

Cover multi-candidate clarification text, empty candidates, Catalog-invalid defensive handling, allowed-scope intersection, category-only targets, and compiler behavior for new search, same-category refinement, category switch, and SKU validation after trusted scope application.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/unit/test_reference_resolver.py tests/unit/test_multi_turn_query_compiler.py
```

Expected: failures because category resolution and the compiler argument do not exist.

- [x] **Step 3: Implement resolver**

Add:

```python
CategoryResolutionOutcome = Literal["resolved", "ambiguous", "unsupported"]

class CategoryResolution(BaseModel):
    outcome: CategoryResolutionOutcome
    scope: CategoryCandidate | None = None
    candidate_scopes: list[CategoryCandidate] = Field(default_factory=list)
    message: str | None = None
```

Intersect with `allowed_scopes` when supplied. One valid scope resolves; multiple valid scopes return
`你说的是{labels}中的哪一种？`; zero scopes return
`当前商品目录暂不支持“{surface_text}”，请换一种商品类型。`.

- [x] **Step 4: Implement compiler binding**

Pass `resolved_category_scope` into `_resolve_search_intent`. Include category-only scopes in valid switch targets, seed the operation base with the resolved category and subcategory before applying SKU operations, and retain the old slot-operation path when no scope was resolved.

- [x] **Step 5: Verify GREEN**

Run the Task 3 command and require all tests to pass.

---

### Task 4: Resumable category clarification in the workflow

**Files:**
- Modify: `src/shop_agent/models/state.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `src/shop_agent/workflow/graph.py`
- Test: `tests/unit/test_multi_turn_workflow.py`

**Interfaces:**
- Produces state keys: `resolved_category_scope` and `allowed_category_scopes`.
- Produces node: `WorkflowNodes.resolve_category_reference`.
- Produces route: category resolution either continues to `route_turn` or persists a fixed clarification.

- [x] **Step 1: Add failing workflow tests**

Cover:

- unique “耳机” scope reaches retrieval with `数码电子 / 真无线耳机`;
- multiple shoe scopes persist `ambiguous_category` and skip retrieval;
- “跑步鞋” clarification resumes the suspended 500-yuan query;
- an answer outside pending scopes is rejected and clears pending on the second failed attempt;
- explicit zero candidates persist before emitting the fixed unsupported text;
- `category_reference=null` still permits categoryless search;
- a fresh `new_search` discards an older category pending.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/unit/test_multi_turn_workflow.py
```

Expected: new tests fail because the node, state fields, and pending merge behavior are absent.

- [x] **Step 3: Implement pending merge**

For `ambiguous_category`, require the clarification answer to contain `category_reference`, merge it into the suspended turn, merge any answer-side constraints with the existing helper, and expose the immutable pending scopes through `allowed_category_scopes`. Extend `_has_explicit_query_progress` to include category references.

- [x] **Step 4: Implement node and graph**

Add `resolve_category_reference` after `resolve_reference`. On:

- resolved: put the trusted scope in state;
- ambiguous first attempt: store `ambiguous_category` pending and route to `persist_clarification`;
- unsupported first attempt: keep the existing query state unchanged and route the fixed text through `persist_clarification`;
- unresolved pending answer: clear pending and emit the existing attempt-limit response.

Pass `resolved_category_scope` into `merge_turn_query` and add category-reference counts to structured logs.

- [x] **Step 5: Verify GREEN**

Run the Task 4 command and require all tests to pass.

---

### Task 5: HTTP regression and exhaustive-result behavior

**Files:**
- Modify: `tests/integration/test_chat_api.py`
- Modify: `tests/live/test_live_shopping_flow.py`

**Interfaces:**
- Consumes the complete workflow from Tasks 1–4.
- Proves the external SSE and persisted-state behavior.

- [x] **Step 1: Add failing integration acceptance tests**

Use the compiled graph fakes to assert:

- “推荐耳机” stores the exact scope;
- repeated `more_results` forwards all seen IDs while retaining the scope;
- after all true-wireless earphones are exhausted, the fixed exhausted response is emitted and phones are never selected;
- category clarification is persisted before its `text_delta`;
- zero-candidate unsupported response performs no retrieval.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/integration/test_chat_api.py
```

Expected: new assertions fail before the full workflow contract is complete.

- [x] **Step 3: Complete minimal integration behavior**

Make only the production adjustments exposed by the failing integration tests. Do not add fallback aliases or change public SSE event schemas.

- [x] **Step 4: Verify GREEN**

Run the Task 5 integration command and require all tests to pass.

- [x] **Step 5: Add opt-in live cases**

Extend the live parser/flow acceptance to cover “推荐耳机”, “推荐手机”, “推荐鞋”, and
“推荐T恤”. The release gate repeats each phrase five times: unique types must always return one
correct candidate, and ambiguous types must always return all reasonable candidates. Any omission
requires replacing the candidate list with the complete taxonomy match matrix before completion.

---

### Task 6: Feature documentation and full verification

**Files:**
- Modify: `docs/features/multi-turn-query-engine.md`

**Interfaces:**
- Documents the final implemented contract, workflow, compatibility boundary, tests, and change record.

- [x] **Step 1: Update the feature document**

Add the category-reference contract, unique/multiple/zero behavior, pending scope storage,
categoryless-search boundary, workflow node, compatibility note for old categoryless snapshots,
coverage matrix entries, and a 2026-07-28 change record.

- [x] **Step 2: Run focused and full verification**

Run:

```bash
uv run pytest -q tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_model_gateways.py tests/unit/test_reference_resolver.py tests/unit/test_multi_turn_query_compiler.py tests/unit/test_multi_turn_workflow.py tests/integration/test_chat_api.py
uv run pytest -q -p no:cacheprovider
uv run ruff check .
uv run mypy src scripts
```

Expected: every command exits 0; the full suite may skip only explicitly opt-in live tests.

- [ ] **Step 3: Run live verification when credentials and services are available**

Run the exact opt-in command already documented by `tests/live/test_live_shopping_flow.py`, with
proxy variables removed as required by this repository. Record actual pass/fail output; do not report
unrun live coverage as passing.

Attempted on 2026-07-28 with credentials enabled. The first parametrized DashScope call did not
return within approximately 120 seconds, so the run was interrupted and remains an external
verification gate rather than a passing check.

- [x] **Step 4: Review the implementation against the design**

Confirm every design requirement has a test, no explicit product type can reach retrieval with an
empty scope, categoryless global semantic search still works, and no Git command was executed.
