# Multi-Turn Query Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent multi-turn Query compiler that resolves references only against the latest displayed products, deterministically merges turn-level slot operations into an executable query snapshot, asks resumable clarifying questions, and routes focused product questions without repeating full-catalog retrieval.

**Architecture:** Replace the single-turn intent entry with one structured `TurnQuery` call followed by deterministic reference resolution and snapshot compilation. Persist only domain conversation state in SQLite through a repository abstraction; continue to use JSON/`ProductCatalog` as the product fact source and Qdrant as a derived retrieval index. Convert each compiled `QuerySnapshot` back into the existing `ParsedIntent`/`SearchConstraints` contract so the current retrieval, rerank, evidence, and response stages remain authoritative.

**Tech Stack:** Python 3.11, Pydantic 2, FastAPI, LangGraph, aiosqlite, Qdrant, OpenAI-compatible DashScope chat API, pytest/pytest-asyncio, Ruff, mypy.

## Global Constraints

- Read `docs/README.md`, `docs/features/multi-turn-query-engine.md`, and `docs/superpowers/specs/2026-07-26-multi-turn-query-engine-design.md` before implementation.
- Do not execute any Git command or Git operation. Each task ends with a test and review checkpoint instead of a commit.
- Product JSON remains the unique authoritative product fact source; Qdrant remains a derived retrieval index.
- SQLite stores only `QuerySnapshot`, latest candidates, focus, seen IDs, and pending clarification state; it does not store complete products, Qdrant chunks, model outputs, or generated replies.
- Product references are resolved only against the latest displayed candidate list plus its current focused product; `seen_product_ids` never expands the reference domain.
- Any reference that does not resolve to exactly one valid target enters clarification; never default to the first product.
- A category/sub-category switch resets all prior query constraints, latest candidates, focus, seen IDs, and pending clarification unless the user explicitly restates a condition in the new turn.
- First release assumes the product JSON and every `product_id` remain unchanged for the lifetime of stored conversations.
- First release is single-instance SQLite with optimistic version checks; do not add MySQL or Redis implementations.
- Keep `POST /api/v1/chat/stream` request fields and existing SSE event names compatible.
- Use TDD for every behavior: add the focused failing test, verify the expected failure, write the smallest implementation, then rerun the focused test and the affected test module.

---

## File Structure

### New production files

- `src/shop_agent/models/turn_query.py`: model-owned turn intent, reference clues, product-question shape, relative-price direction, and validated slot operations.
- `src/shop_agent/models/conversation.py`: query snapshot, candidate references, pending clarification, persisted conversation state, and repository record version.
- `src/shop_agent/services/conversation_repository.py`: repository protocol and SQLite implementation with optimistic concurrency.
- `src/shop_agent/services/reference_resolver.py`: deterministic latest-candidate/focus resolution with clarification results.
- `src/shop_agent/services/multi_turn_query_compiler.py`: slot merging, category reset, relative-price compilation, and snapshot-to-`ParsedIntent` conversion.

### Existing production files to modify

- `src/shop_agent/errors.py`: add conversation, turn parsing, and product-knowledge errors.
- `src/shop_agent/config.py`, `.env.example`, `.gitignore`, `pyproject.toml`: SQLite path and runtime dependency.
- `src/shop_agent/services/ports.py`: add the workflow-facing `TurnQueryParser`; remove the legacy parser only when the graph switches in Task 8.
- `src/shop_agent/services/dashscope_chat.py`: add the structured `DashScopeTurnQueryParser` and compact context prompt.
- `src/shop_agent/services/qdrant_store.py`: request-level seen-product exclusion and exact product knowledge scroll.
- `src/shop_agent/services/retrieval.py`: forward excluded IDs and expose `fetch_product_chunks(product_id)`.
- `src/shop_agent/models/state.py`: carry loaded conversation, TurnQuery, compiled snapshot, resolved target, product knowledge, and persistence metadata.
- `src/shop_agent/workflow/dependencies.py`: inject the turn parser and conversation repository and extend retrieval operations.
- `src/shop_agent/workflow/nodes.py`: add multi-turn nodes, split candidate selection from product event emission, and add focused product response prompts.
- `src/shop_agent/workflow/graph.py`: compile the new clarification, search, more-results, product-question, and non-shopping routes.
- `src/shop_agent/api/dependencies.py`: construct the SQLite repository and new parser.
- `docs/README.md`, `docs/features/multi-turn-query-engine.md`, `docs/features/text-shopping-workflow.md`: keep index, code entries, status, behavior, and verification current with implementation.

### New tests

- `tests/unit/test_turn_query_models.py`
- `tests/unit/test_conversation_models.py`
- `tests/unit/test_conversation_repository.py`
- `tests/unit/test_reference_resolver.py`
- `tests/unit/test_multi_turn_query_compiler.py`
- `tests/unit/test_multi_turn_workflow.py`

### Existing tests to modify

- `tests/unit/test_model_gateways.py`
- `tests/unit/test_qdrant_filters.py`
- `tests/unit/test_retrieval_service.py`
- `tests/unit/workflow_fakes.py`
- `tests/unit/test_workflow_routes.py`
- `tests/unit/test_workflow_stream.py`
- `tests/unit/test_settings.py`
- `tests/integration/api_fakes.py`
- `tests/integration/test_chat_api.py`
- `tests/live/test_live_shopping_flow.py`

---

### Task 1: Define TurnQuery and persistent conversation models

**Files:**
- Create: `src/shop_agent/models/turn_query.py`
- Create: `src/shop_agent/models/conversation.py`
- Modify: `src/shop_agent/errors.py`
- Create: `tests/unit/test_turn_query_models.py`
- Create: `tests/unit/test_conversation_models.py`

**Interfaces:**
- Consumes: existing `CanonicalSkuKey`, `NumericConstraint`, `ParsedIntent`, and `SearchConstraints` from `src/shop_agent/models/query.py`.
- Produces: `TurnQuery`, `ProductReference`, `ProductQuestion`, `SlotOperation`, `SemanticTermOperation`, `QuerySnapshot`, `CandidateReference`, `PendingClarification`, `ConversationState`, and `ConversationRecord`.

- [ ] **Step 1: Write failing TurnQuery validation tests**

Add exact cases for valid scalar/list/SKU operations and invalid field/operator combinations:

```python
import pytest
from pydantic import ValidationError

from shop_agent.models.turn_query import SlotOperation, TurnQuery


def test_turn_query_accepts_budget_replacement_and_brand_addition() -> None:
    query = TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "refine_search",
            "slot_operations": [
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 300,
                },
                {
                    "slot": "constraints.include_brands",
                    "operation": "add",
                    "value": "小米",
                },
            ],
        }
    )

    assert query.slot_operations[0].value == 300
    assert query.slot_operations[1].value == "小米"


@pytest.mark.parametrize(
    "payload",
    [
        {"slot": "constraints.max_price", "operation": "add", "value": 300},
        {"slot": "constraints.include_brands", "operation": "replace", "value": "小米"},
        {"slot": "constraints.max_price", "operation": "clear", "value": 300},
        {"slot": "constraints.sku_constraints", "operation": "add", "value": "512GB"},
    ],
)
def test_slot_operation_rejects_invalid_contract(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SlotOperation.model_validate(payload)
```

- [ ] **Step 2: Run the new model tests and verify the import failure**

Run: `uv run pytest tests/unit/test_turn_query_models.py -q -p no:cacheprovider`

Expected: FAIL during collection with `ModuleNotFoundError: shop_agent.models.turn_query`.

- [ ] **Step 3: Implement exact TurnQuery model contracts**

Define these literals and models with `ConfigDict(extra="forbid")`:

```python
TurnIntent = Literal[
    "new_search",
    "refine_search",
    "switch_category",
    "more_results",
    "product_question",
    "clarification_answer",
    "non_shopping",
]
ReferenceTarget = Literal["product", "brand"]
ReferenceKind = Literal["ordinal", "demonstrative", "brand", "product_name"]
RelativePriceDirection = Literal["cheaper", "more_expensive"]
StructuredFactField = Literal["title", "brand", "category", "display_price", "sku"]
SlotOperationKind = Literal["replace", "add", "remove", "clear"]
SlotName = Literal[
    "category",
    "sub_category",
    "constraints.min_price",
    "constraints.max_price",
    "constraints.price_preference",
    "constraints.include_brands",
    "constraints.exclude_brands",
    "constraints.required_features",
    "constraints.excluded_features",
    "constraints.sku_constraints",
    "constraints.numeric_constraints",
]


class ProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: ReferenceTarget
    surface_text: str = Field(min_length=1)
    kind: ReferenceKind
    ordinal: int | None = Field(default=None, ge=1)
    brand: str | None = None
    product_name: str | None = None


class ProductQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    kind: Literal["structured", "semantic"]
    field: StructuredFactField | None = None


class SemanticTermOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["add", "remove", "clear"]
    value: str | None = None


class SlotOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: SlotName
    operation: SlotOperationKind
    value: str | float | NumericConstraint | None = None
    sku_key: CanonicalSkuKey | None = None


class TurnQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    intent: TurnIntent
    reference: ProductReference | None = None
    semantic_term_operations: list[SemanticTermOperation] = Field(default_factory=list)
    slot_operations: list[SlotOperation] = Field(default_factory=list)
    relative_price: RelativePriceDirection | None = None
    product_question: ProductQuestion | None = None
    cancel_pending: bool = False
```

Add model validators enforcing:

- `ordinal` exists only for `kind="ordinal"` and is required for that kind.
- `brand` is required only for `kind="brand"`; `product_name` is required only for `kind="product_name"`.
- structured product questions require `field`; semantic product questions require `field=None`.
- `product_question` is required only for `intent="product_question"`.
- scalar slots accept only `replace/clear`; list slots accept only `add/remove/clear`; SKU operations require `sku_key`; `clear` requires `value=None`; other operations require a value.
- the same scalar slot appears at most once and a list/SKU/numeric `clear` is not combined with another operation for that slot in one turn.

- [ ] **Step 4: Write failing persisted-state invariant tests**

```python
import pytest
from pydantic import ValidationError

from shop_agent.models.conversation import CandidateReference, ConversationState


def test_conversation_requires_focus_to_belong_to_latest_candidates() -> None:
    with pytest.raises(ValidationError, match="focused product"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=[
                CandidateReference(rank=1, product_id="p1", display_price=399)
            ],
            focused_product_id="p2",
            seen_product_ids=["p1"],
        )


def test_conversation_requires_recent_candidates_to_be_seen() -> None:
    with pytest.raises(ValidationError, match="recent candidates"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=[
                CandidateReference(rank=1, product_id="p1", display_price=399)
            ],
            seen_product_ids=[],
        )
```

- [ ] **Step 5: Implement conversation models and snapshot conversion**

Use `ConversationRecord` to keep the SQL version outside the serialized domain state:

```python
class QuerySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = None
    sub_category: str | None = None
    semantic_terms: list[str] = Field(default_factory=list)
    constraints: SearchConstraints = Field(default_factory=SearchConstraints)

    def to_parsed_intent(self) -> ParsedIntent:
        terms = list(dict.fromkeys([
            self.sub_category or self.category or "商品",
            *self.semantic_terms,
            *self.constraints.required_features,
        ]))
        return ParsedIntent(
            schema_version=1,
            intent="product_search",
            retrieval_query="、".join(terms),
            category=self.category,
            sub_category=self.sub_category,
            constraints=self.constraints,
        )


class CandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank: int = Field(ge=1)
    product_id: str = Field(min_length=1)
    display_price: float = Field(ge=0)


class PendingClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ambiguous_reference", "missing_context", "condition_conflict"]
    candidate_product_ids: list[str] = Field(default_factory=list)
    suspended_turn_query: TurnQuery
    attempt_count: int = Field(default=1, ge=1, le=2)


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    conversation_id: str = Field(min_length=1, max_length=128)
    query_snapshot: QuerySnapshot | None = None
    recent_candidates: list[CandidateReference] = Field(default_factory=list)
    focused_product_id: str | None = None
    seen_product_ids: list[str] = Field(default_factory=list)
    pending_clarification: PendingClarification | None = None


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: ConversationState
    version: int = Field(ge=1)
```

Validate unique contiguous ranks beginning at one, unique recent and seen IDs, focus membership in recent IDs, and recent IDs as a subset of seen IDs. Add `CONVERSATION_UNAVAILABLE`, `CONVERSATION_CONFLICT`, `TURN_QUERY_PARSE_FAILED`, and `PRODUCT_KNOWLEDGE_UNAVAILABLE` to `ErrorCode`.

- [ ] **Step 6: Run model tests and the existing query-model suite**

Run: `uv run pytest tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_query_models.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Review checkpoint (no Git)**

Confirm `ConversationState.model_dump_json()` contains no product body, SKU list, Qdrant Chunk, model response, or generated reply. Confirm no `Any` appears in the public model annotations.

---

### Task 2: Add the SQLite conversation repository

**Files:**
- Create: `src/shop_agent/services/conversation_repository.py`
- Create: `tests/unit/test_conversation_repository.py`
- Modify: `pyproject.toml`
- Modify: `src/shop_agent/config.py`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: `ConversationState`, `ConversationRecord`, `ServiceError`.
- Produces: `ConversationRepository.load(conversation_id)`, `ConversationRepository.save(state, expected_version)`, and `SqliteConversationRepository`.

- [ ] **Step 1: Add failing settings and repository persistence tests**

```python
from pathlib import Path

import pytest

from shop_agent.models.conversation import ConversationState
from shop_agent.services.conversation_repository import SqliteConversationRepository


@pytest.mark.asyncio
async def test_sqlite_repository_survives_repository_recreation(tmp_path: Path) -> None:
    database = tmp_path / "conversation.sqlite3"
    first = SqliteConversationRepository(database)
    state = ConversationState(schema_version=1, conversation_id="c1")

    saved = await first.save(state, expected_version=None)
    second = SqliteConversationRepository(database)
    loaded = await second.load("c1")

    assert saved.version == 1
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.state == state


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_stale_version(tmp_path: Path) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversation.sqlite3")
    state = ConversationState(schema_version=1, conversation_id="c1")
    await repository.save(state, expected_version=None)
    await repository.save(state, expected_version=1)

    with pytest.raises(ServiceError) as error:
        await repository.save(state, expected_version=1)

    assert error.value.code == "CONVERSATION_CONFLICT"
    assert error.value.retryable is True
```

Add a settings assertion:

```python
assert Settings(dashscope_api_key="test").conversation_db_path == Path(
    ".data/conversations.sqlite3"
)
```

- [ ] **Step 2: Run tests and verify imports/settings fail**

Run: `uv run pytest tests/unit/test_conversation_repository.py tests/unit/test_settings.py -q -p no:cacheprovider`

Expected: FAIL because the repository module and `conversation_db_path` do not exist.

- [ ] **Step 3: Add the SQLite dependency and runtime path configuration**

Add `"aiosqlite>=0.20"` to project dependencies, add this setting, document its environment variable, and ignore the runtime directory:

```python
conversation_db_path: Path = Field(default=Path(".data/conversations.sqlite3"))
```

```dotenv
CONVERSATION_DB_PATH=.data/conversations.sqlite3
```

```gitignore
.data/
```

Run `uv lock` after editing `pyproject.toml`; this is dependency lock generation, not a Git operation.

- [ ] **Step 4: Implement repository protocol and optimistic writes**

```python
class ConversationRepository(Protocol):
    async def load(self, conversation_id: str) -> ConversationRecord | None:
        raise NotImplementedError

    async def save(
        self,
        state: ConversationState,
        *,
        expected_version: int | None,
    ) -> ConversationRecord:
        raise NotImplementedError
```

`SqliteConversationRepository` must:

- create the parent directory;
- lazily run the following DDL behind one `asyncio.Lock`:

```sql
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```
- open a short-lived `aiosqlite.connect()` context per operation;
- insert new state only when `expected_version is None`;
- update with `WHERE conversation_id = ? AND version = ?` and increment by one;
- commit successful writes;
- map an insert primary-key race or zero-row update to retryable `CONVERSATION_CONFLICT`;
- map other `aiosqlite.Error` values to retryable `CONVERSATION_UNAVAILABLE` without exposing file paths or SQL text.

Use compact Pydantic JSON for `state_json` and `ConversationState.model_validate_json()` on reads.

- [ ] **Step 5: Add exact failure normalization and session-isolation tests**

Test `c1` and `c2` save/load independently. Patch `aiosqlite.connect` to raise `aiosqlite.OperationalError("secret path")` and assert:

```python
assert error.value.code == "CONVERSATION_UNAVAILABLE"
assert error.value.message == "conversation storage unavailable"
assert "secret" not in error.value.message
```

- [ ] **Step 6: Run repository and settings tests**

Run: `uv run pytest tests/unit/test_conversation_repository.py tests/unit/test_settings.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Review checkpoint (no Git)**

Open the temporary database through the repository test only; confirm no production `.data` database was created by unit tests and no product JSON is present in `state_json`.

---

### Task 3: Implement deterministic latest-candidate reference resolution

**Files:**
- Create: `src/shop_agent/services/reference_resolver.py`
- Create: `tests/unit/test_reference_resolver.py`

**Interfaces:**
- Consumes: `ProductReference`, `ConversationState`, `ProductCatalog`.
- Produces: `ReferenceResolution` and `resolve_reference(reference: ProductReference, state: ConversationState, catalog: ProductCatalog) -> ReferenceResolution`.

- [ ] **Step 1: Write parameterized failing resolution tests**

Create a three-product Catalog with two products sharing one brand. Cover these exact outcomes:

```python
@pytest.mark.parametrize(
    ("reference", "focus", "expected_product", "clarifies"),
    [
        ({"target_type": "product", "surface_text": "第二个", "kind": "ordinal", "ordinal": 2}, None, "p2", False),
        ({"target_type": "product", "surface_text": "第四个", "kind": "ordinal", "ordinal": 4}, None, None, True),
        ({"target_type": "product", "surface_text": "它", "kind": "demonstrative"}, "p2", "p2", False),
        ({"target_type": "product", "surface_text": "它", "kind": "demonstrative"}, None, None, True),
    ],
)
def test_reference_resolution(
    reference: dict[str, object],
    focus: str | None,
    expected_product: str | None,
    clarifies: bool,
) -> None:
    state = _three_candidate_state(focus=focus)
    result = resolve_reference(
        ProductReference.model_validate(reference),
        state,
        _three_product_catalog(),
    )

    assert result.product_id == expected_product
    assert result.needs_clarification is clarifies
```

Add explicit tests that:

- one latest candidate lets “它” resolve without a focus;
- one matching brand resolves a product;
- two latest candidates sharing the requested brand clarify for a product target;
- `target_type="brand"` resolves the unique brand value even when the focus supplies it;
- a product present only in `seen_product_ids` is not resolvable;
- an empty latest candidate list clarifies instead of searching older state.

- [ ] **Step 2: Run the resolver tests and verify the module is missing**

Run: `uv run pytest tests/unit/test_reference_resolver.py -q -p no:cacheprovider`

Expected: FAIL during collection with `ModuleNotFoundError`.

- [ ] **Step 3: Implement cardinality-based resolution**

```python
class ReferenceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str | None = None
    brand: str | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None
    candidate_product_ids: list[str] = Field(default_factory=list)


def resolve_reference(
    reference: ProductReference,
    state: ConversationState,
    catalog: ProductCatalog,
) -> ReferenceResolution:
    latest_ids = [item.product_id for item in state.recent_candidates]
    latest_products = [catalog.get(product_id) for product_id in latest_ids]

    if reference.target_type == "brand":
        if reference.kind == "brand" and reference.brand is not None:
            brands = {reference.brand} if any(
                product.brand.casefold() == reference.brand.strip().casefold()
                for product in latest_products
            ) else set()
        elif state.focused_product_id is not None:
            brands = {catalog.get(state.focused_product_id).brand}
        else:
            brands = {product.brand for product in latest_products}
        if len(brands) == 1:
            return ReferenceResolution(brand=next(iter(brands)))
        return _clarification(state, catalog)

    if reference.kind == "ordinal" and reference.ordinal is not None:
        matches = [
            item.product_id
            for item in state.recent_candidates
            if item.rank == reference.ordinal
        ]
    elif reference.kind == "brand" and reference.brand is not None:
        expected = reference.brand.strip().casefold()
        matches = [
            product.product_id
            for product in latest_products
            if product.brand.casefold() == expected
        ]
    elif reference.kind == "product_name" and reference.product_name is not None:
        expected = reference.product_name.strip().casefold()
        matches = [
            product.product_id
            for product in latest_products
            if product.title.casefold() == expected
        ]
    elif state.focused_product_id is not None:
        matches = [state.focused_product_id]
    elif len(latest_ids) == 1:
        matches = latest_ids
    else:
        matches = []

    if len(matches) == 1:
        return ReferenceResolution(product_id=matches[0])
    return _clarification(state, catalog)
```

Implement `_clarification(state, catalog)` in the same file. It returns `needs_clarification=True`, the latest candidate IDs, and a message built from `“第{rank}款：{title}”` entries. Resolution order must be ordinal, exact Catalog product-name/brand filtering within latest candidates, then focused/single-candidate demonstrative fallback. Normalize only surrounding whitespace and Unicode case for comparison; do not use fuzzy edit-distance matching. Return success only for exactly one product when the target is a product. Clarification messages must not mention any older seen product.

- [ ] **Step 4: Run resolver tests**

Run: `uv run pytest tests/unit/test_reference_resolver.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Review checkpoint (no Git)**

Search the resolver for access to `seen_product_ids`; it may be used only in assertions/tests, never as a resolution source. Confirm no model score or confidence threshold participates in the result.

---

### Task 4: Implement deterministic snapshot compilation and relative prices

**Files:**
- Create: `src/shop_agent/services/multi_turn_query_compiler.py`
- Create: `tests/unit/test_multi_turn_query_compiler.py`
- Modify: `docs/features/multi-turn-query-engine.md`
- Modify: `docs/superpowers/specs/2026-07-26-multi-turn-query-engine-design.md`

**Interfaces:**
- Consumes: `TurnQuery`, optional existing `ConversationState`, resolved product/brand, and `ProductCatalog` taxonomy.
- Produces: `QueryMergeResult(snapshot, intent, state, needs_clarification, clarification_message)`.

- [ ] **Step 1: Write failing merge tests for add/remove/replace/clear**

```python
def test_refinement_replaces_budget_and_preserves_other_slots() -> None:
    state = conversation_with_snapshot(
        max_price=500,
        required_features=["适合通勤"],
    )
    turn = turn_query(
        intent="refine_search",
        slot_operations=[
            {
                "slot": "constraints.max_price",
                "operation": "replace",
                "value": 300,
            }
        ],
    )

    result = merge_turn_query(turn, state, catalog())

    assert result.snapshot.constraints.max_price == 300
    assert result.snapshot.constraints.required_features == ["适合通勤"]
    assert result.state.seen_product_ids == []
```

Add exact cases for semantic term add/remove/clear, brand include/exclude mutual removal, required/excluded feature mutual removal, SKU add/remove/clear, numeric add/remove/clear, and price clear.

- [ ] **Step 2: Add failing category-switch and relative-price tests**

```python
def test_category_switch_resets_old_constraints_and_reference_state() -> None:
    result = merge_turn_query(
        turn_query(
            intent="refine_search",
            slot_operations=[
                {"slot": "category", "operation": "replace", "value": "数码电子"},
                {"slot": "sub_category", "operation": "replace", "value": "智能手机"},
            ],
        ),
        conversation_with_earphone_snapshot(
            max_price=500,
            required_features=["降噪"],
            recent=[candidate("p1", 399)],
        ),
        catalog(),
    )

    assert result.intent == "switch_category"
    assert result.snapshot.sub_category == "智能手机"
    assert result.snapshot.constraints == SearchConstraints()
    assert result.state.recent_candidates == []
    assert result.state.focused_product_id is None
    assert result.state.seen_product_ids == []
```

Relative-price assertions:

- focus at `459.00` plus cheaper gives `max_price == 458.99`;
- no focus with `[399, 459, 529]` plus cheaper gives `398.99`;
- no focus with the same list plus more expensive gives `530.01`;
- no latest candidates returns `needs_clarification=True` without a `ParsedIntent`;
- an explicit max-price operation in the same turn overrides `relative_price="cheaper"`.

- [ ] **Step 3: Run compiler tests and verify the module is missing**

Run: `uv run pytest tests/unit/test_multi_turn_query_compiler.py -q -p no:cacheprovider`

Expected: FAIL during collection with `ModuleNotFoundError`.

- [ ] **Step 4: Implement compiler result and operation application**

```python
class QueryMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["new_search", "refine_search", "switch_category", "more_results"]
    state: ConversationState
    snapshot: QuerySnapshot | None = None
    parsed_intent: ParsedIntent | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None


def merge_turn_query(
    turn: TurnQuery,
    state: ConversationState,
    catalog: ProductCatalog,
    *,
    resolved_product_id: str | None = None,
    resolved_brand: str | None = None,
) -> QueryMergeResult:
    base_state = state.model_copy(deep=True)
    base_snapshot = _select_base_snapshot(turn, base_state)
    intent = _resolve_search_intent(turn, base_snapshot, base_state)
    snapshot = _apply_semantic_operations(base_snapshot, turn.semantic_term_operations)
    snapshot = _apply_slot_operations(snapshot, turn.slot_operations, catalog)
    snapshot = _apply_resolved_brand(snapshot, turn, resolved_brand)
    snapshot = _apply_relative_price(
        snapshot,
        turn,
        base_state,
        resolved_product_id=resolved_product_id,
    )
    conflict = _price_conflict(snapshot)
    if conflict is not None:
        return _clarification_result(intent, base_state, snapshot, conflict)
    next_state = _update_search_state(base_state, snapshot, intent)
    return QueryMergeResult(
        intent=intent,
        state=next_state,
        snapshot=snapshot,
        parsed_intent=snapshot.to_parsed_intent(),
    )
```

Define every helper named above as a private pure function in the same file. `_select_base_snapshot` returns an empty snapshot for new/switch and rejects refine/more without history; `_resolve_search_intent` compares old/new category pairs and forces `switch_category`; `_apply_semantic_operations` and `_apply_slot_operations` return copies; `_apply_resolved_brand` adds the Catalog-resolved brand to `include_brands` for “这个牌子的还有吗” and removes the same value from `exclude_brands`; `_apply_relative_price` uses `resolved_product_id` when the user explicitly names an ordinal/product and otherwise uses the confirmed focus/latest-batch baseline; `_price_conflict` returns the fixed clarification text or `None`; `_clarification_result` constructs a result without `parsed_intent`; `_update_search_state` performs the approved recent/focus/seen resets.

Implementation order:

1. reject refinement without an existing snapshot as `missing_context` clarification;
2. derive the turn’s category pair from operations;
3. if the valid pair differs from the old pair, reset state and force `switch_category`;
4. apply semantic and slot operations to a deep copy;
5. validate brand values and SKU keys/values through current Catalog taxonomy;
6. apply explicit price operations, then apply relative price only if no explicit boundary was supplied;
7. map `min_price > max_price` to `condition_conflict` clarification;
8. clear recent/focus/seen for new searches, switches, and refinements;
9. keep snapshot and seen IDs unchanged for `more_results`;
10. call `snapshot.to_parsed_intent()` only after validation.

Use `Decimal("0.01")` for the relative step and convert the final two-decimal value to float at the Pydantic boundary.

- [ ] **Step 5: Synchronize exact model terminology in both approved design documents**

Document the final `SlotOperation` shape, `SemanticTermOperation`, `ProductQuestion`, `ConversationRecord` version placement, and the `Decimal("0.01")` rule. Do not change the approved external behavior.

- [ ] **Step 6: Run compiler/model tests**

Run: `uv run pytest tests/unit/test_multi_turn_query_compiler.py tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_query_compiler.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Review checkpoint (no Git)**

For every mutation test, assert the input `ConversationState` and `QuerySnapshot` remain unchanged. Confirm no compiler branch calls a model, Embedder, Qdrant, or Reranker.

---

### Task 5: Add the single-call DashScope TurnQuery parser

**Files:**
- Modify: `src/shop_agent/models/turn_query.py`
- Modify: `src/shop_agent/services/ports.py`
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Modify: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: current message, `ConversationState`, Catalog categories/pairs/brands/SKU taxonomy.
- Produces: `TurnQueryParser.parse(message: str, context: TurnContext) -> TurnQuery` and `DashScopeTurnQueryParser`.

- [ ] **Step 1: Write failing prompt-context tests**

Build a state containing one snapshot, three latest candidates, focus `p2`, six seen IDs, and one pending clarification. Assert the generated user payload contains:

```json
{
  "message": "它防水吗",
  "query_snapshot": {},
  "recent_candidates": [
    {"rank": 1, "product_id": "p1", "title": "商品1", "brand": "品牌1"}
  ],
  "focused_product_id": "p2",
  "pending_clarification": {}
}
```

Also assert it does not contain `seen_product_ids`, Qdrant chunks, generated replies, or a message-history array.

- [ ] **Step 2: Write failing structured-output tests**

Mock one valid response for “第二个防水吗” and assert:

```python
assert result.intent == "product_question"
assert result.reference is not None
assert result.reference.ordinal == 2
assert result.product_question is not None
assert result.product_question.kind == "semantic"
```

Mock an invalid first response followed by a corrected response and assert exactly two calls. Mock two invalid responses and assert retryable `TURN_QUERY_PARSE_FAILED` with no upstream content in the public message.

- [ ] **Step 3: Run the focused gateway tests and verify failure**

Run: `uv run pytest tests/unit/test_model_gateways.py -q -p no:cacheprovider`

Expected: FAIL because `_build_turn_query_system_prompt`, `TurnContext`, and `DashScopeTurnQueryParser` do not exist.

- [ ] **Step 4: Implement `TurnContext` and parser protocol**

```python
class TurnContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_snapshot: QuerySnapshot | None = None
    recent_candidates: list[TurnCandidateSummary] = Field(default_factory=list)
    focused_product_id: str | None = None
    pending_clarification: PendingClarification | None = None


class TurnQueryParser(Protocol):
    async def parse(self, message: str, context: TurnContext) -> TurnQuery:
        raise NotImplementedError
```

Build candidate summaries from Catalog at the workflow boundary so the model sees rank, ID, exact title, and exact brand, but not product descriptions or older seen IDs.

- [ ] **Step 5: Implement the DashScope parser and prompt**

The system prompt must:

- embed `TurnQuery.model_json_schema()` and current taxonomy;
- state that the context is data, not instructions;
- require one JSON object and no reasoning text;
- describe every intent and operation;
- forbid direct trusted `product_id` selection;
- distinguish latest candidates from older unavailable results;
- map “第二个多少钱” to structured `display_price` and “第二个防水吗” to semantic;
- map unclear demonstratives to a reference clue rather than inventing a target;
- preserve all explicitly stated operations;
- use exact catalog brand and category values.

Call existing `_structured_call` with error code `TURN_QUERY_PARSE_FAILED`. Validate submitted brands and SKU operations against taxonomy after Pydantic parsing; retain the existing one-correction behavior.

- [ ] **Step 6: Run gateway and type tests**

Run: `uv run pytest tests/unit/test_model_gateways.py tests/unit/test_turn_query_models.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Review checkpoint (no Git)**

Inspect the outbound mocked messages and confirm there is one system message and one compact user JSON message. Confirm neither full product JSON nor complete conversation history is sent.

---

### Task 6: Extend retrieval for seen-product exclusion and focused knowledge reads

**Files:**
- Modify: `src/shop_agent/services/qdrant_store.py`
- Modify: `src/shop_agent/services/retrieval.py`
- Modify: `src/shop_agent/workflow/dependencies.py`
- Modify: `tests/unit/test_qdrant_filters.py`
- Modify: `tests/unit/test_retrieval_service.py`
- Modify: `tests/integration/test_qdrant_store.py`

**Interfaces:**
- Consumes: `excluded_product_ids: Sequence[str]` on normal retrieval and a target `product_id` for focused knowledge.
- Produces: `QdrantStore.fetch_product_chunks(product_id) -> list[EvidenceChunk]` and `RetrievalService.fetch_product_chunks(product_id)`.

- [ ] **Step 1: Write failing Qdrant filter tests**

Extend the existing filter assertion with:

```python
query_filter = QdrantStore.build_filter(
    category="数码电子",
    sub_category="蓝牙耳机",
    constraints=SearchConstraints(),
    excluded_product_ids=["p1", "p2"],
)

assert query_filter.model_dump(exclude_none=True)["must_not"] == [
    {"key": "product_id", "match": {"any": ["p1", "p2"]}}
]
```

Verify brand exclusions and product-ID exclusions both remain in `must_not` when present.

- [ ] **Step 2: Write failing paginated product-scroll tests**

Mock `client.scroll` to return two pages. Assert each call uses:

```python
models.Filter(
    must=[models.FieldCondition(key="product_id", match=models.MatchValue(value="p1"))]
)
```

and `with_payload=True`, `with_vectors=False`. Assert the result is two `EvidenceChunk` objects in scroll order and has no `score` attribute.

- [ ] **Step 3: Run Qdrant/retrieval tests and verify signature failures**

Run: `uv run pytest tests/unit/test_qdrant_filters.py tests/unit/test_retrieval_service.py -q -p no:cacheprovider`

Expected: FAIL because the filter/search signatures and focused read do not exist.

- [ ] **Step 4: Implement exclusion forwarding**

Change signatures consistently:

```python
async def retrieve_chunks(
    self,
    intent: ParsedIntent,
    *,
    excluded_product_ids: Sequence[str] = (),
) -> list[RetrievedChunk]:
    if intent.intent != "product_search" or intent.retrieval_query is None:
        raise ServiceError(
            "RETRIEVAL_UNAVAILABLE",
            "product search intent required",
            retryable=False,
        )
    query_vector = await self._embedder.embed_query(intent.retrieval_query)
    return await self._store.search(
        query_vector,
        category=intent.category,
        sub_category=intent.sub_category,
        constraints=intent.constraints,
        excluded_product_ids=excluded_product_ids,
    )


async def search(
    self,
    query_vector: list[float],
    *,
    category: str | None,
    sub_category: str | None,
    constraints: SearchConstraints,
    excluded_product_ids: Sequence[str] = (),
) -> list[RetrievedChunk]:
    response = await self._client.query_points(
        collection_name=self._settings.qdrant_collection,
        query=query_vector,
        query_filter=self.build_filter(
            category=category,
            sub_category=sub_category,
            constraints=constraints,
            excluded_product_ids=excluded_product_ids,
        ),
        with_payload=True,
        limit=self._settings.retrieval_chunk_limit,
    )
    return self._validate_search_points(response.points)
```

Add one `product_id MatchAny` condition when the exclusion sequence is non-empty. Deduplicate IDs while preserving first occurrence order. Extract the existing strict point validation loop into `QdrantStore._validate_search_points(points: Sequence[models.ScoredPoint]) -> list[RetrievedChunk]`; keep its current payload checks and `RETRIEVAL_UNAVAILABLE` normalization unchanged.

- [ ] **Step 5: Implement exact product knowledge scroll**

```python
async def fetch_product_chunks(self, product_id: str) -> list[EvidenceChunk]:
    offset: int | str | UUID | None = None
    chunks: list[EvidenceChunk] = []
    while True:
        points, offset = await self._client.scroll(
            collection_name=self._settings.qdrant_collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="product_id",
                        match=models.MatchValue(value=product_id),
                    )
                ]
            ),
            limit=64,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        # Strictly validate each payload as EvidenceChunk with point_id.
        if offset is None:
            return chunks
```

Map transport errors to retryable `PRODUCT_KNOWLEDGE_UNAVAILABLE` and malformed payloads to non-retryable `PRODUCT_KNOWLEDGE_UNAVAILABLE`. Expose the same method through `RetrievalService` and workflow `RetrievalOperations` without Embedding or Reranker calls.

- [ ] **Step 6: Run focused and existing retrieval tests**

Run: `uv run pytest tests/unit/test_qdrant_filters.py tests/unit/test_retrieval_service.py tests/integration/test_qdrant_store.py -q -p no:cacheprovider`

Expected: PASS when local Qdrant integration prerequisites are available; otherwise only the existing explicitly skipped integration case may skip.

- [ ] **Step 7: Review checkpoint (no Git)**

Confirm `fetch_product_chunks` never calls Embedder or `query_points`, and normal retrieval never places `seen_product_ids` inside `SearchConstraints`.

---

### Task 7: Add workflow conversation loading, TurnQuery parsing, resolution, and resumable clarification

**Files:**
- Modify: `src/shop_agent/models/state.py`
- Modify: `src/shop_agent/workflow/dependencies.py`
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `tests/unit/workflow_fakes.py`
- Create: `tests/unit/test_multi_turn_workflow.py`

**Interfaces:**
- Consumes: `TurnQueryParser`, `ConversationRepository`, `resolve_reference`, existing Catalog and response generator.
- Produces: `load_conversation`, `parse_turn_query`, `resume_pending_action`, `resolve_reference`, `persist_clarification`, and their conditional routes.

- [ ] **Step 1: Replace workflow fakes with explicit turn and repository fakes**

Add:

```python
class FakeTurnQueryParser:
    def __init__(self, turns: Sequence[TurnQuery]) -> None:
        self.turns = iter(turns)
        self.calls: list[tuple[str, TurnContext]] = []

    async def parse(self, message: str, context: TurnContext) -> TurnQuery:
        self.calls.append((message, context))
        return next(self.turns)


class FakeConversationRepository:
    def __init__(self, record: ConversationRecord | None = None) -> None:
        self.record = record
        self.loads: list[str] = []
        self.saves: list[tuple[ConversationState, int | None]] = []

    async def load(self, conversation_id: str) -> ConversationRecord | None:
        self.loads.append(conversation_id)
        return self.record

    async def save(self, state: ConversationState, *, expected_version: int | None) -> ConversationRecord:
        self.saves.append((state, expected_version))
        self.record = ConversationRecord(
            state=state,
            version=1 if expected_version is None else expected_version + 1,
        )
        return self.record
```

- [ ] **Step 2: Write failing context-load and ambiguity workflow tests**

Test that a stored record is loaded before parser invocation and its compact context reaches the parser. Then test a three-candidate state plus a demonstrative reference with no focus:

```python
events = await drain(graph, "那个防水吗")

assert retrieval.retrieve_calls == []
assert repository.saves[0][0].pending_clarification is not None
assert repository.saves[0][0].pending_clarification.suspended_turn_query.product_question.text == "是否防水"
assert [event_name(event) for event in events] == ["text_delta"]
assert "第一款" in event_delta(events[0])
```

With `caplog`, assert one-line JSON records named `turn_query`, `reference_resolution`, and `turn_route` contain request/conversation IDs and structured summaries but no product body. Reuse the existing newline/Unicode line-separator attack value and assert it cannot create a second log line.

- [ ] **Step 3: Write failing clarification resume/cancel/limit tests**

Cover:

- pending ambiguous product question plus answer “第二个” restores the suspended product question with reference rank two;
- `cancel_pending=True` clears pending and emits “已取消刚才的问题。”;
- a clear new search discards pending and proceeds as a new search;
- the second unresolved clarification clears pending and emits a request to restate the complete need.

- [ ] **Step 4: Run focused workflow-node tests and verify missing-node failures**

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py -q -p no:cacheprovider`

Expected: FAIL because the state fields, dependencies, and multi-turn nodes are missing.

- [ ] **Step 5: Extend `ShoppingState` and workflow dependencies**

Add optional fields for:

```python
conversation_record: ConversationRecord
conversation_state: ConversationState
turn_query: TurnQuery
resolved_product_id: str
resolved_brand: str
query_snapshot: QuerySnapshot
pending_expected_version: int | None
product_knowledge: list[EvidenceChunk]
clarification_message: str
```

Add `turn_query_parser: TurnQueryParser | None = None` and `conversation_repository: ConversationRepository | None = None` as the final dataclass fields while retaining `intent_parser` temporarily so existing constructors and the production graph remain runnable during this isolated task. Every new node must call a private dependency guard that raises `RuntimeError("multi-turn workflow dependency is not configured")` when either value is absent. New unit harnesses supply both dependencies. Task 8 removes the legacy parser field and makes both new dependencies required when it switches the graph. Preserve existing retrieval/evidence/response dependencies.

- [ ] **Step 6: Implement entry and clarification nodes**

`load_conversation` loads by state `conversation_id`; a miss creates an empty `ConversationState` without saving. `parse_turn_query` builds compact `TurnContext` from Catalog summaries and calls the parser. `resume_pending_action` either clears pending on cancellation/new search or copies the clarification answer’s reference into `suspended_turn_query` and routes it back through resolution.

On unresolved references, create/update `PendingClarification`, persist using the loaded version, and only then emit the clarification text. On the second unresolved attempt, clear pending before persistence.

Emit the approved `turn_query`, `reference_resolution`, and `turn_route` single-line JSON logs through the existing safe encoder. Log IDs, intent, clue kind, candidate count, route, and clarification reason; do not log full product JSON, evidence text, or model reasoning.

- [ ] **Step 7: Test entry and clarification nodes directly**

Call `load_conversation`, `parse_turn_query`, `resume_pending_action`, `resolve_reference`, and `persist_clarification` through `build_nodes()` in the focused tests. Do not modify `build_graph()` in this task; keeping the new nodes dormant preserves the existing single-turn graph until Task 8 connects the complete search branch.

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_routes.py -q -p no:cacheprovider`

Expected: the new node tests and all unchanged single-turn route tests PASS.

- [ ] **Step 8: Review checkpoint (no Git)**

Confirm an ambiguity trace contains zero Embedder, Qdrant, Reranker, evidence, and product-event calls. Confirm pending state is saved before its `text_delta` is emitted.

---

### Task 8: Connect compiled snapshots to the existing search pipeline and persist displayed batches

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `src/shop_agent/workflow/graph.py`
- Modify: `src/shop_agent/services/query_compiler.py`
- Modify: `src/shop_agent/services/retrieval.py`
- Modify: `src/shop_agent/workflow/dependencies.py`
- Modify: `src/shop_agent/api/dependencies.py`
- Modify: `tests/unit/workflow_fakes.py`
- Create: `tests/unit/test_api_dependencies.py`
- Modify: `tests/unit/test_multi_turn_workflow.py`
- Modify: `tests/unit/test_workflow_routes.py`
- Modify: `tests/unit/test_workflow_stream.py`
- Modify: `tests/unit/test_query_compiler.py`

**Interfaces:**
- Consumes: `merge_turn_query`, existing effective query compiler, retrieval/evidence services, and repository.
- Produces: search/more-results routes, persisted `recent_candidates`/`seen_product_ids`, pure candidate selection, and separate product event emission.

- [ ] **Step 1: Write failing multi-turn search refinement tests**

Start with a stored earphone snapshot at max 500 and parser output replacing max with 300. Assert:

```python
assert retrieval.retrieve_calls[0].intent.constraints.max_price == 300
assert retrieval.retrieve_calls[0].excluded_product_ids == ()
assert repository.record.state.query_snapshot.constraints.max_price == 300
assert [item.product_id for item in repository.record.state.recent_candidates] == ["p1", "p2", "p3"]
```

Assert the original max 500 state object remains unchanged.

- [ ] **Step 2: Write failing category reset and more-results tests**

For a switch from earphones to smartphones, assert old budget/features/SKU/focus/seen IDs are absent from the retrieval call and persisted state. For `more_results`, assert:

```python
assert retrieval.retrieve_calls[0].excluded_product_ids == (
    "p1", "p2", "p3", "p4", "p5", "p6"
)
assert persisted.seen_product_ids == ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"]
assert [item.product_id for item in persisted.recent_candidates] == ["p7", "p8", "p9"]
```

- [ ] **Step 3: Write failing persistence-before-product-event and no-result tests**

Make the fake repository append `"persist"` and the writer append `"product"`; assert order begins `persist, product`. For zero retrieval and zero eligible candidates, assert the compiled snapshot is saved, recent/focus are cleared, and no product event is emitted.

Also patch constructors in `test_api_dependencies.py` and assert `build_api_dependencies(settings)` passes a `SqliteConversationRepository` at `settings.conversation_db_path` and a taxonomy-configured `DashScopeTurnQueryParser` into `WorkflowDependencies`.

- [ ] **Step 4: Run focused workflow tests and verify failures**

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_routes.py tests/unit/test_workflow_stream.py tests/unit/test_api_dependencies.py -q -p no:cacheprovider`

Expected: FAIL on absent merge/persistence/event-split behavior.

- [ ] **Step 5: Implement search and more-results nodes**

Add `merge_query_snapshot` which calls `merge_turn_query` and places the compiled `ParsedIntent` into state. Rename `compile_query` node/function to `compile_effective_query` while preserving existing price behavior and tests. `retrieve_chunks` forwards `seen_product_ids` only for `more_results`.

Split current selection and event logic:

```python
async def decide_candidates(self, state: ShoppingState) -> dict[str, object]:
    selected = self.dependencies.evidence_service.select_candidates(
        state["validated_candidates"],
        self.dependencies.settings.final_product_limit,
        constraints=state["effective_constraints"],
    )
    return {"selected_products": selected, "response_mode": "shopping"}


async def emit_product_events(
    self,
    state: ShoppingState,
    writer: StreamWriter,
) -> dict[str, object]:
    for rank, item in enumerate(state["selected_products"], start=1):
        writer({"event": "product", "data": self._product_event(rank, item).model_dump(mode="json")})
    return {}
```

`persist_search_result` builds `CandidateReference` entries using the exact emitted `display_price`, replaces latest candidates, clears focus, and either replaces or appends seen IDs depending on the route. Persist before `emit_product_events`.

`persist_no_results` saves the compiled snapshot and clears latest candidates/focus before the no-results response. Preserve seen IDs for a failed `more_results`; reset them for changed constraints.

Log `query_snapshot_compiled` with old/new snapshot summaries and applied operations. After each successful repository write, log `conversation_persisted` with conversation ID, expected version, saved version, and state kind. Use the same safe single-line JSON encoder as existing intent logs.

- [ ] **Step 6: Rebuild graph search routes**

Wire:

```text
route_turn(search/more)
  -> merge_query_snapshot
  -> compile_effective_query
  -> retrieve_chunks
  -> aggregate_products
  -> semantic_rerank
  -> validate_evidence
  -> decide_candidates
  -> persist_search_result
  -> emit_product_events
  -> generate_response
```

Both zero-retrieval and zero-eligible branches must pass through `persist_no_results` before `generate_response`.

Remove the legacy `intent_parser` field from `WorkflowDependencies`, make `turn_query_parser` and `conversation_repository` required, and update every workflow test constructor. Update `build_api_dependencies()` to instantiate:

```python
conversation_repository = SqliteConversationRepository(
    resolved_settings.conversation_db_path
)
turn_query_parser = DashScopeTurnQueryParser(
    resolved_settings,
    categories=[product.category for product in catalog.all()],
    sub_categories=[product.sub_category for product in catalog.all()],
    category_pairs=[
        (product.category, product.sub_category)
        for product in catalog.all()
    ],
    brands=catalog.brands(),
    sku_taxonomy=catalog.sku_taxonomy(),
)
```

Pass both into the production `WorkflowDependencies`. Repository initialization remains lazy; do not open a long-lived SQLite connection in FastAPI lifespan.

- [ ] **Step 7: Run search workflow and compiler suites**

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_routes.py tests/unit/test_workflow_stream.py tests/unit/test_query_compiler.py tests/unit/test_api_dependencies.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 8: Review checkpoint (no Git)**

Confirm product events still use Catalog facts and matched SKUs. Confirm refinement calls full retrieval with no old candidate restriction, while only `more_results` forwards accumulated seen IDs.

---

### Task 9: Add focused product fact and knowledge-question routing

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `src/shop_agent/workflow/graph.py`
- Modify: `tests/unit/workflow_fakes.py`
- Modify: `tests/unit/test_multi_turn_workflow.py`
- Modify: `tests/unit/test_workflow_stream.py`

**Interfaces:**
- Consumes: resolved latest-candidate `product_id`, `ProductQuestion`, Catalog, focused Qdrant knowledge, response generator, and repository.
- Produces: structured product fact prompts, semantic product knowledge prompts, and persisted focus.

- [ ] **Step 1: Write failing structured-fact tests**

For “第二个多少钱”, configure `ProductQuestion(kind="structured", field="display_price")` and assert:

```python
assert retrieval.fetch_product_calls == []
assert "459.0" in response.prompts[0]
assert repository.record.state.focused_product_id == "p2"
assert repository.record.state.recent_candidates == original_recent_candidates
```

Repeat for brand and SKU, asserting facts come from Catalog/latest candidate state and no Embedding/full retrieval occurs.

- [ ] **Step 2: Write failing semantic-question tests**

For “第二个防水吗”, return two `EvidenceChunk` values only for `p2`. Assert one `fetch_product_chunks("p2")` call, zero Embedder/full search/rerank calls, prompt inclusion of exact chunk IDs/text, and absence of other product facts/chunks.

When the focused read returns no chunks, assert the prompt instructs “现有商品资料不足以判断” and does not invite common-knowledge completion. When the focused read raises `PRODUCT_KNOWLEDGE_UNAVAILABLE`, assert the error propagates and no response prompt runs.

- [ ] **Step 3: Run focused tests and verify route failure**

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_stream.py -q -p no:cacheprovider`

Expected: FAIL because product-question nodes and routes do not exist.

- [ ] **Step 4: Implement product question fact extraction and prompts**

Add pure helpers:

```python
def build_structured_product_question_prompt(
    question: ProductQuestion,
    product_id: str,
    state: ShoppingState,
    dependencies: WorkflowDependencies,
) -> str:
    facts = _structured_question_facts(
        question,
        product_id,
        state,
        dependencies,
    )
    return _verified_product_question_prompt(question.text, facts=facts, chunks=[])


def build_semantic_product_question_prompt(
    question: ProductQuestion,
    product_id: str,
    chunks: Sequence[EvidenceChunk],
    dependencies: WorkflowDependencies,
) -> str:
    product = dependencies.catalog.get(product_id)
    identity = {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
    }
    evidence = [chunk.model_dump(mode="json") for chunk in chunks]
    return _verified_product_question_prompt(
        question.text,
        facts=identity,
        chunks=evidence,
    )
```

Implement `_structured_question_facts` as an exhaustive match over the five `StructuredFactField` values. Implement `_verified_product_question_prompt` with compact JSON, the existing `SAFETY_RULES`, and an explicit “现有商品资料不足以判断” instruction when `chunks` is empty for a semantic question. Structured fields are exactly title, brand, category/sub-category, latest emitted display price, and matching SKU facts. Semantic prompts include only target Catalog identity plus fetched chunks and the existing safety rules.

- [ ] **Step 5: Implement product nodes and focus persistence**

Add `load_product_facts`, conditional `fetch_product_knowledge`, `persist_focus`, and `generate_product_response`. `persist_focus` verifies the target remains in latest candidates, updates no query/seen/recent fields, saves with the loaded expected version, and runs before response streaming.

- [ ] **Step 6: Wire the product-question branch and run tests**

Run: `uv run pytest tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_stream.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Review checkpoint (no Git)**

Confirm a product question cannot introduce a product outside latest candidates and cannot call general `retrieve_chunks`. Confirm semantic prompts contain no evidence from a different product ID.

---

### Task 10: Preserve the HTTP/SSE contract across persistent conversations

**Files:**
- Modify: `tests/integration/api_fakes.py`
- Modify: `tests/integration/test_chat_api.py`

**Interfaces:**
- Consumes: the production-shaped compiled graph, workflow fakes, and `SqliteConversationRepository`.
- Produces: multi-request API integration coverage behind the unchanged chat endpoint.

- [ ] **Step 1: Write a failing two-request API integration test**

Use a real compiled graph with workflow fakes and `SqliteConversationRepository(tmp_path / "chat.sqlite3")`. Send:

```text
conversation c1: “推荐蓝牙耳机”
conversation c1: “预算改成300”
conversation c2: “推荐蓝牙耳机”
```

Assert c1’s second retrieval has max 300, c2 starts from an empty snapshot, and both responses preserve `message_start → product/text_delta → message_end` ordering.

- [ ] **Step 2: Add failing API error-contract tests**

Keep request validation and SSE parsing assertions unchanged. Add parameterized checks for:

```text
CONVERSATION_UNAVAILABLE -> failed before product
CONVERSATION_CONFLICT -> failed before product
TURN_QUERY_PARSE_FAILED -> failed before product
PRODUCT_KNOWLEDGE_UNAVAILABLE -> failed before product question text
```

Every payload must hide upstream/SQL details and preserve `retryable` from `ServiceError`.

- [ ] **Step 3: Run API tests and verify multi-request failures**

Run: `uv run pytest tests/integration/test_chat_api.py -q -p no:cacheprovider`

Expected: FAIL because the integration fakes do not yet drive two turns through one persistent compiled graph.

- [ ] **Step 4: Update integration fakes for persistent graph execution**

Keep the existing `FakeGraph` for endpoint-only error tests. Add a helper that builds the real graph with `FakeTurnQueryParser`, a temporary `SqliteConversationRepository`, fake retrieval/evidence/response services, and a deterministic Catalog:

```python
graph = build_graph(
    WorkflowDependencies(
        turn_query_parser=parser,
        conversation_repository=repository,
        retrieval_service=retrieval,
        evidence_service=evidence,
        response_generator=response,
        catalog=catalog,
        settings=settings,
    )
)
```

Feed one parser output per API request and reuse the same repository object/path across requests.

- [ ] **Step 5: Run API and affected workflow tests**

Run: `uv run pytest tests/integration/test_chat_api.py tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_stream.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 6: Review checkpoint (no Git)**

Confirm `ChatRequest` fields, maximum lengths, endpoint path, SSE event names, and `message_end` statuses remain compatible. Confirm no SQLite state is shared across different conversation IDs.

---

### Task 11: Complete end-to-end scenarios, documentation, and full verification

**Files:**
- Modify: `tests/unit/test_multi_turn_workflow.py`
- Modify: `tests/integration/test_chat_api.py`
- Modify: `tests/live/test_live_shopping_flow.py`
- Modify: `docs/README.md`
- Modify: `docs/features/multi-turn-query-engine.md`
- Modify: `docs/features/text-shopping-workflow.md`
- Modify: `docs/superpowers/specs/2026-07-26-multi-turn-query-engine-design.md`

**Interfaces:**
- Consumes: the complete feature.
- Produces: final deterministic acceptance coverage, opt-in live scenarios, current documentation, and verification evidence.

- [ ] **Step 1: Add one deterministic acceptance test per confirmed conversation**

Cover these exact sequences with Fake model outputs:

```text
推荐跑步鞋 -> 要轻量的 -> 预算500以内
展示三款 -> 第二个防水吗 -> 它续航怎么样
展示三款无焦点 -> 那个防水吗 -> 第二个
展示耳机 -> 再看看手机
展示399/459/529 -> 再便宜一点
展示A/B/C -> 换一批D/E/F -> 换一批G/H/I -> 第二个
```

For each turn assert persisted snapshot, latest candidates, focus, seen IDs, downstream call counts, and emitted event order.

- [ ] **Step 2: Add opt-in live parser scenarios without making deterministic CI depend on DashScope**

Under the existing `live` marker, add representative TurnQuery checks for:

```text
“预算改成300” -> refine_search + max_price replace
“不要小米了” -> exclude/add or include/remove operations that compile to no included 小米
“第二个怎么样” -> product_question + ordinal 2
“那个小米的” -> brand reference clue
“再看看手机” with earphone context -> category operation that compiler forces to switch
```

Keep them behind `RUN_LIVE_TESTS=1` and validate the structured result before exercising retrieval.

- [ ] **Step 3: Run the complete test suite**

Run: `uv run pytest -q -p no:cacheprovider`

Expected: all deterministic tests PASS; only pre-existing or explicitly opt-in live tests may skip.

- [ ] **Step 4: Run static verification**

Run: `uv run ruff check .`

Expected: PASS.

Run: `uv run mypy src scripts`

Expected: PASS with all source files checked.

- [ ] **Step 5: Run opt-in live verification when credentials and Qdrant are available**

Run in PowerShell:

```powershell
Set-Item Env:RUN_LIVE_TESTS '1'
uv run pytest tests/live/test_live_shopping_flow.py -m live -q -p no:cacheprovider
Remove-Item Env:RUN_LIVE_TESTS
```

Expected: the original single-turn flow and new TurnQuery scenarios PASS. If the environment lacks credentials or services, record the exact external prerequisite and leave feature status `开发中` rather than claiming live completion.

- [ ] **Step 6: Update feature and index documentation in the same implementation change**

Update `docs/README.md` code entries to the actual model/repository/compiler/retrieval/workflow/API files. Update `docs/features/multi-turn-query-engine.md` from proposed design to implemented behavior, record actual test counts and commands, and set status:

- `已完成` only if deterministic, static, and opt-in live verification all pass;
- `开发中` if deterministic/static checks pass but live verification is unavailable or a confirmed behavior remains incomplete.

Add a link from the single-turn document’s multi-turn exclusion paragraph to the new feature document without rewriting the single-turn history. Keep the design spec synchronized with any exact names changed during implementation.

- [ ] **Step 7: Perform plan/spec coverage review**

Check off each design requirement against an implemented test: latest-only references, focus, resumable clarification, category reset, deterministic operations, relative price, accumulated seen IDs, focused Qdrant reads, SQLite persistence/version conflict, no Redis/MySQL, static product assumption, SSE compatibility, and generation-failure persistence.

- [ ] **Step 8: Final review checkpoint (no Git)**

Report changed files, verification commands and exact results, any skipped external checks, and remaining documented limitations. Do not stage, commit, branch, push, or open a PR.
