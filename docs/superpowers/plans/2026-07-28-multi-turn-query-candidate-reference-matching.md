# Multi-Turn Query Candidate Reference Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute inline; do not dispatch sub-agents.

**Feature Ownership:** This is an implementation slice of the existing
“多轮 Query 编译与指代消解” feature. Its canonical current-state document is
`docs/features/multi-turn-query-engine.md`, and its existing feature-index row is
in `docs/README.md`. Implementation must update those two existing records in
place; it must not create a second feature document or a second index row.

**Goal:** Let the turn-query LLM interpret open-ended references against every recently displayed product, while backend code validates the exhaustive match matrix and remains solely responsible for unique product/brand binding and clarification.

**Architecture:** Extend the existing `ProductReference` contract with an exhaustive `candidate_matches` matrix. The LLM marks each current `recent_candidate` as matching or not matching the user's surface expression; parser validation requires every current candidate exactly once and in rank order. The reference resolver consumes only validated candidate IDs for new turns, applies deterministic cardinality, target-type, focus, and pending-clarification rules, and retains the existing clue-based resolver only as a compatibility path for already persisted v1 pending turns and existing non-LLM fakes.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph, OpenAI-compatible DashScope structured JSON output, SQLite conversation state, pytest.

## Global Constraints

- Do not execute any Git command or Git operation unless the user separately authorizes that exact operation.
- Preserve `TurnQuery.schema_version = 1`; this is an additive, backward-compatible internal contract change.
- Preserve the public `POST /api/v1/chat/stream` request, SSE event schema, SQLite table schema, and public error codes.
- Do not add full message history, product descriptions, SKU bodies, RAG chunks, or generated replies to `TurnContext`.
- The LLM handles language interpretation only; it must not produce a trusted final `resolved_product_id` or `resolved_brand`.
- A non-null new `ProductReference` must assess every `recent_candidate` exactly once and in rank order.
- Backend code derives zero/one/many outcomes from the validated matrix; it never accepts model confidence or silently chooses from multiple product matches.
- `product_question` always resolves a product target, even if the model labels the surface expression as a brand target.
- A brand target may match multiple products only when Catalog maps all matched products to one unique brand.
- Initial ambiguity stores only the matched candidate IDs; a clarification answer cannot escape that saved candidate subset.
- Existing persisted pending turns whose references have no `candidate_matches` continue through the current ordinal/demonstrative/brand/product-name compatibility resolver.
- Update the existing `docs/features/multi-turn-query-engine.md` and its existing
  `docs/README.md` index row in the same implementation because this changes an
  internal data contract and user-visible multi-turn reference behavior.
- Do not rename the indexed feature or create another feature document; candidate
  matching remains part of “多轮 Query 编译与指代消解”.

## Scope and Non-Scope

This plan touches more than eight files because `ProductReference` is shared by model validation, parser prompts, deterministic resolution, persisted pending turns, workflow tests, HTTP tests, and live parser acceptance.

**In scope:**

- Exhaustive per-candidate LLM match output.
- Parser-side provenance, coverage, order, and ID validation.
- Unique product binding, unique brand derivation, narrowed ambiguity, and pending-subset enforcement.
- The reported `三星这个不错` failure.
- Natural references supported by the information already present in `TurnCandidateSummary`, including “第二个”“中间那个”“三星那个”“小米那个”.
- Backward-compatible loading and execution of v1 pending references without the new matrix.

**Out of scope:**

- Adding `display_price` to `TurnCandidateSummary` for “最便宜的那个”.
- Adding feature summaries for “拍照最好的那个”.
- Long-term conversation history, user profiles, image position references, product comparison, cart, or transaction behavior.
- A new `product_selection` intent for preference-only utterances such as “这个不错”; this plan preserves the existing `product_question` response behavior after fixing target binding.
- SQLite migrations or a `ConversationState` schema-version bump.

## Component Flow

```text
ConversationState.recent_candidates
          |
          v
Catalog title/brand projection
          |
          v
TurnContext.recent_candidates ------+
          |                          |
          v                          |
DashScopeTurnQueryParser             |
  ProductReference.candidate_matches |
          |                          |
          v                          |
context-aware exhaustive validation  |
          |                          |
          v                          |
WorkflowNodes.resolve_reference      |
  expected target + pending subset   |
          |                          |
          v                          |
ReferenceResolver + ProductCatalog <-+
      |             |             |
      v             v             v
resolved product  resolved brand  clarification
```

The load-bearing assumption is that the model marks every *plausible* candidate as `matches=true` instead of selecting its favorite candidate. Exhaustive output validation prevents omissions from the JSON structure, but it cannot prove semantic correctness; exact legacy clue fields remain available for diagnostics and backward compatibility, while zero/one/many backend policy prevents an explicitly multi-match output from becoming a silent guess.

---

### Task 1: Add the backward-compatible candidate-match model contract

**Files:**
- Modify: `src/shop_agent/models/turn_query.py`
- Modify: `src/shop_agent/models/__init__.py`
- Test: `tests/unit/test_turn_query_models.py`
- Test: `tests/unit/test_conversation_models.py`

**Interfaces:**
- Produces: `ReferenceCandidateMatch(product_id: str, matches: bool)`.
- Produces: `ProductReference.candidate_matches: list[ReferenceCandidateMatch]`.
- Preserves: existing `target_type`, `surface_text`, `kind`, `ordinal`, `brand`, and `product_name` fields for compatibility and diagnostics.
- Preserves: old serialized `ProductReference` payloads by defaulting `candidate_matches` to an empty list.

- [ ] **Step 1: Write failing model tests for normalization, uniqueness, independence, and legacy loading**

Add these imports and tests to `tests/unit/test_turn_query_models.py`:

```python
from shop_agent.models.turn_query import (
    ProductQuestion,
    ProductReference,
    ReferenceCandidateMatch,
    TurnQuery,
)


def test_reference_candidate_match_normalizes_opaque_product_id() -> None:
    match = ReferenceCandidateMatch(product_id=" p2 ", matches=True)

    assert match.product_id == "p2"
    assert match.matches is True


@pytest.mark.parametrize("product_id", ["", "   ", 1, None])
def test_reference_candidate_match_rejects_invalid_product_id(
    product_id: object,
) -> None:
    with pytest.raises(ValidationError):
        ReferenceCandidateMatch(product_id=product_id, matches=True)


def test_product_reference_requires_unique_candidate_match_ids() -> None:
    with pytest.raises(ValidationError, match="candidate match product IDs"):
        ProductReference(
            target_type="product",
            surface_text="小米那个",
            kind="brand",
            brand="小米",
            candidate_matches=[
                ReferenceCandidateMatch(product_id="p1", matches=True),
                ReferenceCandidateMatch(product_id="p1", matches=False),
            ],
        )


def test_product_reference_candidate_match_defaults_are_independent() -> None:
    first = ProductReference(
        target_type="product",
        surface_text="第二个",
        kind="ordinal",
        ordinal=2,
    )
    second = ProductReference(
        target_type="product",
        surface_text="第三个",
        kind="ordinal",
        ordinal=3,
    )

    first.candidate_matches.append(
        ReferenceCandidateMatch(product_id="p2", matches=True)
    )

    assert second.candidate_matches == []
```

Add a persisted legacy-state test to `tests/unit/test_conversation_models.py`:

```python
def test_legacy_pending_reference_without_candidate_matches_still_loads() -> None:
    payload = {
        "schema_version": 1,
        "conversation_id": "c1",
        "query_snapshot": None,
        "recent_candidates": [
            {"rank": 1, "product_id": "p1", "display_price": 399}
        ],
        "focused_product_id": None,
        "seen_product_ids": ["p1"],
        "pending_clarification": {
            "kind": "ambiguous_reference",
            "candidate_product_ids": ["p1"],
            "suspended_turn_query": {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "那个",
                    "kind": "demonstrative",
                    "ordinal": None,
                    "brand": None,
                    "product_name": None,
                },
                "semantic_term_operations": [],
                "slot_operations": [],
                "relative_price": None,
                "product_question": {
                    "text": "那个怎么样",
                    "kind": "semantic",
                    "field": None,
                },
                "cancel_pending": False,
            },
            "attempt_count": 1,
        },
    }

    restored = ConversationState.model_validate(payload)

    assert restored.pending_clarification is not None
    reference = restored.pending_clarification.suspended_turn_query.reference
    assert reference is not None
    assert reference.candidate_matches == []
```

- [ ] **Step 2: Run the model tests and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_turn_query_models.py \
  tests/unit/test_conversation_models.py
```

Expected: collection or test failures because `ReferenceCandidateMatch` and `ProductReference.candidate_matches` do not exist.

- [ ] **Step 3: Implement the additive model**

Add this class before `ProductReference` in `src/shop_agent/models/turn_query.py`:

```python
class ReferenceCandidateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    matches: bool

    @field_validator("product_id", mode="before")
    @classmethod
    def normalize_product_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("candidate match product IDs must be strings")
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate match product IDs cannot be blank")
        return normalized
```

Add the new field and uniqueness check to `ProductReference`:

```python
class ProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: ReferenceTarget
    surface_text: str = Field(min_length=1)
    kind: ReferenceKind
    ordinal: int | None = Field(default=None, ge=1)
    brand: str | None = None
    product_name: str | None = None
    candidate_matches: list[ReferenceCandidateMatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reference_clue(self) -> "ProductReference":
        if (self.kind == "ordinal") != (self.ordinal is not None):
            raise ValueError("ordinal is required only for ordinal references")
        if (self.kind == "brand") != (self.brand is not None):
            raise ValueError("brand is required only for brand references")
        if (self.kind == "product_name") != (self.product_name is not None):
            raise ValueError("product_name is required only for product_name references")
        product_ids = [item.product_id for item in self.candidate_matches]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("candidate match product IDs must be unique")
        return self
```

Export `ReferenceCandidateMatch` from `src/shop_agent/models/__init__.py`:

```python
from shop_agent.models.turn_query import (
    ProductQuestion,
    ProductReference,
    ReferenceCandidateMatch,
    SemanticTermOperation,
    SlotOperation,
    TurnCandidateSummary,
    TurnQuery,
)
```

Add `"ReferenceCandidateMatch"` to `__all__`.

- [ ] **Step 4: Run the model tests and verify GREEN**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_turn_query_models.py \
  tests/unit/test_conversation_models.py
```

Expected: all tests in both files pass, including the legacy pending payload.

---

### Task 2: Require exhaustive candidate assessment at the LLM parser boundary

**Files:**
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Test: `tests/unit/test_model_gateways.py`
- Test: `tests/live/test_live_shopping_flow.py`

**Interfaces:**
- Consumes: `TurnContext.recent_candidates`, already ordered by display rank.
- Consumes: `ProductReference.candidate_matches`.
- Produces: every new non-null LLM reference contains exactly one match entry for every recent candidate, in the same order.
- Preserves: the existing two-attempt structured-output correction path and `TURN_QUERY_PARSE_FAILED`.

- [ ] **Step 1: Update the shared parser test response helper to emit a complete matrix**

In `tests/unit/test_model_gateways.py`, update every mocked structured response with a non-null `reference` so it includes the current context IDs in rank order:

```python
"candidate_matches": [
    {"product_id": "p1", "matches": False},
    {"product_id": "p2", "matches": True},
    {"product_id": "p3", "matches": False},
],
```

For brand ambiguity fixtures, mark every plausible candidate `True`. Keep responses with `"reference": None` unchanged.

- [ ] **Step 2: Write failing correction and safe-failure tests**

Add to `tests/unit/test_model_gateways.py`:

```python
@pytest.mark.asyncio
async def test_turn_query_parser_corrects_incomplete_candidate_matches(
    settings: Settings,
) -> None:
    incomplete = _chat_response(
        json.dumps(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "中间那个",
                    "kind": "ordinal",
                    "ordinal": 2,
                    "candidate_matches": [
                        {"product_id": "p2", "matches": True}
                    ],
                },
                "product_question": {
                    "text": "中间那个怎么样",
                    "kind": "semantic",
                    "field": None,
                },
            },
            ensure_ascii=False,
        )
    )
    complete = _chat_response(
        json.dumps(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "中间那个",
                    "kind": "ordinal",
                    "ordinal": 2,
                    "candidate_matches": [
                        {"product_id": "p1", "matches": False},
                        {"product_id": "p2", "matches": True},
                        {"product_id": "p3", "matches": False},
                    ],
                },
                "product_question": {
                    "text": "中间那个怎么样",
                    "kind": "semantic",
                    "field": None,
                },
            },
            ensure_ascii=False,
        )
    )
    create = AsyncMock(side_effect=[incomplete, complete])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = await _turn_parser(settings, client).parse(
        "中间那个怎么样",
        _turn_context(),
    )

    assert result.reference is not None
    assert [item.product_id for item in result.reference.candidate_matches] == [
        "p1",
        "p2",
        "p3",
    ]
    assert [
        item.product_id
        for item in result.reference.candidate_matches
        if item.matches
    ] == ["p2"]
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_turn_query_parser_rejects_twice_reordered_candidate_matches(
    settings: Settings,
) -> None:
    invalid = _chat_response(
        json.dumps(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "第二个",
                    "kind": "ordinal",
                    "ordinal": 2,
                    "candidate_matches": [
                        {"product_id": "p2", "matches": True},
                        {"product_id": "p1", "matches": False},
                        {"product_id": "p3", "matches": False},
                    ],
                },
                "product_question": {
                    "text": "第二个怎么样",
                    "kind": "semantic",
                    "field": None,
                },
            },
            ensure_ascii=False,
        )
    )
    create = AsyncMock(side_effect=[invalid, invalid])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(ServiceError) as raised:
        await _turn_parser(settings, client).parse(
            "第二个怎么样",
            _turn_context(),
        )

    assert raised.value.code == "TURN_QUERY_PARSE_FAILED"
    assert raised.value.retryable is True
    assert create.await_count == 2
```

- [ ] **Step 3: Run the focused parser tests and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_model_gateways.py \
  -k "candidate_matches"
```

Expected: the incomplete and reordered outputs are currently accepted.

- [ ] **Step 4: Add deterministic coverage validation**

Add this method to `DashScopeTurnQueryParser` in `src/shop_agent/services/dashscope_chat.py`:

```python
    @staticmethod
    def _validate_reference_candidate_matches(
        parsed: TurnQuery,
        context: TurnContext,
    ) -> None:
        reference = parsed.reference
        if reference is None:
            return
        expected_ids = [
            candidate.product_id for candidate in context.recent_candidates
        ]
        actual_ids = [
            item.product_id for item in reference.candidate_matches
        ]
        if actual_ids != expected_ids:
            raise ValueError(
                "reference candidate_matches must cover every recent candidate "
                "exactly once in rank order"
            )
```

Call it in `_validate_turn_query` immediately after `surface_text` provenance validation:

```python
        self._validate_reference_candidate_matches(parsed, context)
```

Do not require a matrix when `reference is None`; focus and sole-candidate fallback remain backend behavior.

- [ ] **Step 5: Update the system prompt with the exhaustive-match contract**

Replace the current reference-rule paragraph in `_build_turn_query_system_prompt` with rules that retain provenance and add:

```text
candidate_matches 必须按 recent_candidates 的 rank 顺序逐项输出，每个候选
product_id 恰好一次；matches=true 表示该候选符合当前 surface_text，false 表示不符合。
必须标记所有可能匹配项，不能为了避免澄清只选择一个。product_id 只能逐字复制
recent_candidates 中的值，不能生成其他 ID。reference=null 时不得输出候选匹配。
```

Add three complete `TurnQuery` examples to the prompt fixture data:

```json
[
  {
    "input": "中间那个怎么样",
    "output": {
      "schema_version": 1,
      "intent": "product_question",
      "reference": {
        "target_type": "product",
        "surface_text": "中间那个",
        "kind": "ordinal",
        "ordinal": 2,
        "candidate_matches": [
          {"product_id": "p1", "matches": false},
          {"product_id": "p2", "matches": true},
          {"product_id": "p3", "matches": false}
        ]
      },
      "product_question": {
        "text": "中间那个怎么样",
        "kind": "semantic",
        "field": null
      }
    }
  },
  {
    "input": "小米那个怎么样",
    "output": {
      "schema_version": 1,
      "intent": "product_question",
      "reference": {
        "target_type": "product",
        "surface_text": "小米那个",
        "kind": "brand",
        "brand": "小米",
        "candidate_matches": [
          {"product_id": "p1", "matches": true},
          {"product_id": "p2", "matches": true},
          {"product_id": "p3", "matches": false}
        ]
      },
      "product_question": {
        "text": "小米那个怎么样",
        "kind": "semantic",
        "field": null
      }
    }
  },
  {
    "input": "有哪些存储版本？",
    "output": {
      "schema_version": 1,
      "intent": "product_question",
      "reference": null,
      "product_question": {
        "text": "有哪些存储版本？",
        "kind": "structured",
        "field": "sku"
      }
    }
  }
]
```

Keep the existing warning that candidate content is untrusted data and that the model must not output final trusted facts.

- [ ] **Step 6: Assert the live parser contract**

In `_assert_live_turn_query_parser_contracts` in `tests/live/test_live_shopping_flow.py`, replace the ordinal-only assertions with exhaustive-matrix assertions:

```python
    ordinal = await parser.parse("第二个怎么样", context)
    assert ordinal.intent == "product_question"
    assert ordinal.reference is not None
    assert [
        item.product_id for item in ordinal.reference.candidate_matches
    ] == [product.product_id for product in earphones]
    assert [
        item.product_id
        for item in ordinal.reference.candidate_matches
        if item.matches
    ] == [earphones[1].product_id]
```

For `"那个小米的"`, calculate the expected IDs from the Catalog rather than assuming only one match:

```python
    brand = await parser.parse("那个小米的", context)
    assert brand.reference is not None
    assert [
        item.product_id
        for item in brand.reference.candidate_matches
        if item.matches
    ] == [
        product.product_id for product in earphones if product.brand == "小米"
    ]
```

- [ ] **Step 7: Run parser tests and verify GREEN**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests/unit/test_model_gateways.py
```

Expected: all parser unit tests pass.

Optional live verification, only when the existing live-test credentials and opt-in flag are available:

```bash
RUN_LIVE_TESTS=1 uv run pytest -q -p no:cacheprovider \
  tests/live/test_live_shopping_flow.py \
  -k "turn_query_parser_contracts"
```

Expected: the live parser returns a complete candidate matrix for ordinal and brand expressions.

---

### Task 3: Resolve validated candidate matrices with deterministic cardinality

**Files:**
- Modify: `src/shop_agent/services/reference_resolver.py`
- Test: `tests/unit/test_reference_resolver.py`

**Interfaces:**
- Change: `resolve_reference(reference: ProductReference, state: ConversationState, catalog: ProductCatalog, *, expected_target_type: ReferenceTarget | None = None, allowed_product_ids: Sequence[str] | None = None) -> ReferenceResolution`.
- New-turn path: use `candidate_matches`.
- Compatibility path: use the existing `kind`-specific resolver when `candidate_matches == []`.
- Product target: exactly one allowed matched ID resolves; otherwise clarify.
- Brand target: one unique Catalog brand across allowed matched IDs resolves; otherwise clarify.
- Clarification candidates: matched subset when non-empty, otherwise the allowed subset, otherwise all latest candidates.

- [ ] **Step 1: Add a matrix helper to the resolver tests**

In `tests/unit/test_reference_resolver.py`, import `ReferenceCandidateMatch` and add:

```python
def _matrix_reference(
    matched_ids: set[str],
    *,
    target_type: str = "product",
    surface_text: str = "那个",
) -> ProductReference:
    return ProductReference(
        target_type=target_type,
        surface_text=surface_text,
        kind="demonstrative",
        candidate_matches=[
            ReferenceCandidateMatch(
                product_id=product_id,
                matches=product_id in matched_ids,
            )
            for product_id in ("p1", "p2", "p3")
        ],
    )
```

- [ ] **Step 2: Write failing resolver tests**

Add:

```python
def test_matrix_resolves_one_product_without_using_clue_kind() -> None:
    result = resolve_reference(
        _matrix_reference({"p3"}, surface_text="中间语义由模型判断"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(product_id="p3")


def test_matrix_product_ambiguity_lists_only_matched_candidates() -> None:
    result = resolve_reference(
        _matrix_reference({"p1", "p2"}, surface_text="共享品牌那个"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2"]
    assert "Alpha One" in (result.clarification_message or "")
    assert "Beta Two" in (result.clarification_message or "")
    assert "Gamma Three" not in (result.clarification_message or "")


def test_matrix_brand_target_allows_multiple_products_of_one_brand() -> None:
    result = resolve_reference(
        _matrix_reference(
            {"p1", "p2"},
            target_type="brand",
            surface_text="共享品牌的",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(brand="共享品牌")


def test_expected_product_target_overrides_model_brand_target() -> None:
    result = resolve_reference(
        _matrix_reference(
            {"p3"},
            target_type="brand",
            surface_text="唯一品牌那个",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
        expected_target_type="product",
    )

    assert result == ReferenceResolution(product_id="p3")


def test_pending_allowed_ids_prevent_escape_to_unmatched_product() -> None:
    result = resolve_reference(
        _matrix_reference({"p3"}, surface_text="第三个"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
        expected_target_type="product",
        allowed_product_ids=("p1", "p2"),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2"]
    assert "Gamma Three" not in (result.clarification_message or "")


def test_invalid_matrix_coverage_fails_closed_to_clarification() -> None:
    incomplete = ProductReference(
        target_type="product",
        surface_text="第二个",
        kind="ordinal",
        ordinal=2,
        candidate_matches=[
            ReferenceCandidateMatch(product_id="p2", matches=True)
        ],
    )

    result = resolve_reference(
        incomplete,
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2", "p3"]
```

- [ ] **Step 3: Run resolver tests and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests/unit/test_reference_resolver.py
```

Expected: failures because the resolver ignores `candidate_matches` and has no expected-target or allowed-subset parameters.

- [ ] **Step 4: Implement matrix-first resolution with compatibility fallback**

Update imports in `src/shop_agent/services/reference_resolver.py`:

```python
from collections.abc import Sequence

from shop_agent.models.turn_query import ReferenceTarget
```

Change the public resolver:

```python
def resolve_reference(
    reference: ProductReference,
    state: ConversationState,
    catalog: ProductCatalog,
    *,
    expected_target_type: ReferenceTarget | None = None,
    allowed_product_ids: Sequence[str] | None = None,
) -> ReferenceResolution:
    """Resolve only against products shown in the latest candidate batch."""
    latest_products = [
        (candidate, catalog.get(candidate.product_id))
        for candidate in state.recent_candidates
    ]

    if not reference.candidate_matches:
        return _resolve_legacy_reference(
            reference,
            state,
            latest_products,
            expected_target_type=expected_target_type,
            allowed_product_ids=allowed_product_ids,
        )

    latest_ids = [candidate.product_id for candidate, _ in latest_products]
    matrix_ids = [item.product_id for item in reference.candidate_matches]
    if matrix_ids != latest_ids:
        return _clarification(_allowed_scope(latest_products, allowed_product_ids))

    allowed_ids = (
        set(latest_ids)
        if allowed_product_ids is None
        else set(allowed_product_ids)
    )
    matched_ids = {
        item.product_id
        for item in reference.candidate_matches
        if item.matches and item.product_id in allowed_ids
    }
    matches = [
        (candidate, product)
        for candidate, product in latest_products
        if candidate.product_id in matched_ids
    ]
    target_type = expected_target_type or reference.target_type

    if target_type == "brand":
        brands = {product.brand for _, product in matches}
        if len(brands) == 1:
            return ReferenceResolution(brand=next(iter(brands)))
    elif len(matches) == 1:
        return ReferenceResolution(product_id=matches[0][0].product_id)

    clarification_scope = matches or _allowed_scope(
        latest_products,
        allowed_product_ids,
    )
    return _clarification(clarification_scope)
```

Move the existing implementation into a compatibility helper:

```python
def _resolve_legacy_reference(
    reference: ProductReference,
    state: ConversationState,
    latest_products: list[tuple[CandidateReference, Product]],
    *,
    expected_target_type: ReferenceTarget | None,
    allowed_product_ids: Sequence[str] | None,
) -> ReferenceResolution:
    allowed_products = _allowed_scope(latest_products, allowed_product_ids)
    target_type = expected_target_type or reference.target_type
    if target_type == "brand":
        return _resolve_brand_reference(reference, state, allowed_products)
    matches = _resolve_product_matches(reference, state, allowed_products)
    allowed_ids = {
        candidate.product_id for candidate, _ in allowed_products
    }
    matches = [product_id for product_id in matches if product_id in allowed_ids]
    if len(matches) == 1:
        return ReferenceResolution(product_id=matches[0])
    return _clarification(allowed_products)
```

Add:

```python
def _allowed_scope(
    latest_products: list[tuple[CandidateReference, Product]],
    allowed_product_ids: Sequence[str] | None,
) -> list[tuple[CandidateReference, Product]]:
    if allowed_product_ids is None:
        return latest_products
    allowed = set(allowed_product_ids)
    return [
        (candidate, product)
        for candidate, product in latest_products
        if candidate.product_id in allowed
    ]
```

Keep `_resolve_brand_reference`, `_resolve_product_matches`, and normalization unchanged for legacy pending records and existing fake-parser tests.

- [ ] **Step 5: Run resolver tests and verify GREEN**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests/unit/test_reference_resolver.py
```

Expected: all legacy and matrix-based resolver tests pass.

---

### Task 4: Integrate expected product targets and pending candidate subsets into the workflow

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Test: `tests/unit/test_multi_turn_workflow.py`
- Test: `tests/unit/test_workflow_stream.py`
- Test: `tests/integration/test_chat_api.py`

**Interfaces:**
- Consumes: `resolve_reference(reference: ProductReference, state: ConversationState, catalog: ProductCatalog, *, expected_target_type: ReferenceTarget | None = None, allowed_product_ids: Sequence[str] | None = None) -> ReferenceResolution`.
- Produces: `resolved_product_id` for a unique matrix match even if the model emitted `target_type="brand"` on a `product_question`.
- Produces: pending ambiguity containing only the matched product subset.
- Preserves: reference-less focus and sole-candidate fallback, clarification attempt limit, response generation, persistence ordering, and SSE shape.

- [ ] **Step 1: Add a matrix reference helper to workflow tests**

In `tests/unit/test_multi_turn_workflow.py`, import `ReferenceCandidateMatch` and add:

```python
def _matched_reference(
    matched_ids: set[str],
    *,
    target_type: str = "product",
    surface_text: str = "那个",
) -> ProductReference:
    return ProductReference(
        target_type=target_type,
        surface_text=surface_text,
        kind="demonstrative",
        candidate_matches=[
            ReferenceCandidateMatch(
                product_id=product_id,
                matches=product_id in matched_ids,
            )
            for product_id in ("p1", "p2", "p3")
        ],
    )
```

- [ ] **Step 2: Write the reported Samsung-shape regression test**

Add to `tests/unit/test_multi_turn_workflow.py`:

```python
@pytest.mark.asyncio
async def test_product_question_treats_unique_brand_target_as_product_match(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    harness.retrieval.product_chunks["p3"] = [
        EvidenceChunk(
            chunk_id="p3:summary",
            point_id="point-p3-summary",
            product_id="p3",
            chunk_type="product_summary",
            text="第三款商品的已验证资料。",
            source_path="data/p3.json",
        )
    ]
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(), version=2)
    )
    parser = FakeTurnQueryParser(
        [
            _turn(
                "product_question",
                reference=_matched_reference(
                    {"p3"},
                    target_type="brand",
                    surface_text="品牌 3",
                ),
                question=_semantic_question("品牌 3 这个不错"),
            )
        ]
    )

    events = await _drain_graph(
        _workflow_dependencies(harness, parser, repository),
        "品牌 3 这个不错",
    )

    assert harness.retrieval.fetch_product_calls == ["p3"]
    assert repository.record is not None
    assert repository.record.state.focused_product_id == "p3"
    assert [part["data"]["event"] for part in events] == [
        "text_delta",
        "text_delta",
    ]
```

- [ ] **Step 3: Write ambiguity narrowing and pending-subset tests**

Add:

```python
@pytest.mark.asyncio
async def test_two_product_matches_persist_only_matched_clarification_candidates(
    tmp_path: Path,
) -> None:
    ambiguous = _turn(
        "product_question",
        reference=_matched_reference(
            {"p1", "p2"},
            surface_text="共享品牌那个",
        ),
        question=_semantic_question("共享品牌那个怎么样"),
    )
    harness, dependencies, _, repository = _dependencies(
        tmp_path,
        turns=[ambiguous],
        record=ConversationRecord(state=_conversation(), version=4),
    )
    nodes = build_nodes(dependencies)
    state: dict[str, Any] = initial_state("共享品牌那个怎么样")
    await _load_and_parse(nodes, state)
    state.update(await nodes.resume_pending_action(state, lambda _: None))
    state.update(await nodes.resolve_reference(state))
    events: list[dict[str, object]] = []
    state.update(await nodes.persist_clarification(state, events.append))

    assert repository.record is not None
    pending = repository.record.state.pending_clarification
    assert pending is not None
    assert pending.candidate_product_ids == ("p1", "p2")
    assert "第一款" in events[0]["data"]["delta"]  # type: ignore[index]
    assert "第二款" in events[0]["data"]["delta"]  # type: ignore[index]
    assert "第三款" not in events[0]["data"]["delta"]  # type: ignore[index]
    assert harness.retrieval.fetch_product_calls == []


@pytest.mark.asyncio
async def test_clarification_answer_cannot_escape_saved_candidate_subset(
    tmp_path: Path,
) -> None:
    suspended = _turn(
        "product_question",
        reference=_matched_reference({"p1", "p2"}),
        question=_semantic_question("共享品牌那个怎么样"),
    )
    pending = PendingClarification(
        kind="ambiguous_reference",
        candidate_product_ids=("p1", "p2"),
        suspended_turn_query=suspended,
        attempt_count=1,
    )
    answer = _turn(
        "clarification_answer",
        reference=_matched_reference({"p3"}, surface_text="第三个"),
    )
    repository = FakeConversationRepository(
        ConversationRecord(
            state=_conversation(pending=pending),
            version=5,
        )
    )
    harness = build_harness(tmp_path)

    events = await _drain_graph(
        _workflow_dependencies(
            harness,
            FakeTurnQueryParser([answer]),
            repository,
        ),
        "第三个",
    )

    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert repository.record is not None
    assert repository.record.state.pending_clarification is None
    assert repository.record.state.focused_product_id is None
    assert harness.retrieval.fetch_product_calls == []
```

- [ ] **Step 4: Run the workflow tests and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_multi_turn_workflow.py \
  -k "unique_brand_target or matched_clarification_candidates or escape_saved_candidate_subset"
```

Expected: the unique brand target reaches `PRODUCT_KNOWLEDGE_UNAVAILABLE`, ambiguity includes all three products, or the clarification answer resolves outside the saved subset.

- [ ] **Step 5: Pass expected target and pending subset from the workflow**

In `WorkflowNodes.resolve_reference` in `src/shop_agent/workflow/nodes.py`, retain the existing synthetic demonstrative reference for reference-less product questions so focus/sole-candidate legacy behavior remains unchanged.

Before calling the service, derive:

```python
        loaded_pending = _loaded_pending(state)
        allowed_product_ids = (
            loaded_pending.candidate_product_ids
            if loaded_pending is not None
            and loaded_pending.kind == "ambiguous_reference"
            else None
        )
        expected_target_type = (
            "product" if turn.intent == "product_question" else None
        )
```

Call:

```python
        resolution = resolve_reference_service(
            reference,
            conversation,
            self.dependencies.catalog,
            expected_target_type=expected_target_type,
            allowed_product_ids=allowed_product_ids,
        )
```

Do not modify routing, focus persistence, product knowledge fetch, or `_validated_product_question_target`; the corrected resolution now supplies the required `resolved_product_id`.

- [ ] **Step 6: Update stream and HTTP fake turns to exercise the new path**

Keep legacy fake references valid because `candidate_matches` defaults to `[]`. Add one explicit matrix case to `tests/unit/test_workflow_stream.py` so the stream-level test verifies the new binding path rather than only compatibility:

```python
candidate_matches=[
    ReferenceCandidateMatch(product_id="p1", matches=False),
    ReferenceCandidateMatch(product_id="p2", matches=True),
    ReferenceCandidateMatch(product_id="p3", matches=False),
],
```

Add an HTTP regression test to `tests/integration/test_chat_api.py` using the existing `compiled_chat_dependencies` two-turn setup:

```python
@pytest.mark.asyncio
async def test_compiled_http_unique_brand_matrix_reaches_product_response(
    tmp_path: Path,
) -> None:
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {
                        "slot": "category",
                        "operation": "replace",
                        "value": "数码电子",
                    },
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
                "intent": "product_question",
                "reference": {
                    "target_type": "brand",
                    "surface_text": "测试品牌 3",
                    "kind": "brand",
                    "brand": "测试品牌 3",
                    "candidate_matches": [
                        {"product_id": "p1", "matches": False},
                        {"product_id": "p2", "matches": False},
                        {"product_id": "p3", "matches": True},
                    ],
                },
                "product_question": {
                    "text": "测试品牌 3 这个不错",
                    "kind": "structured",
                    "field": "title",
                },
            }
        ),
    ]
    dependencies, _, repository, _, _ = compiled_chat_dependencies(
        tmp_path,
        turns=turns,
    )

    await _post(
        dependencies,
        {"conversation_id": "brand-product", "message": "展示三款"},
    )
    response = await _post(
        dependencies,
        {
            "conversation_id": "brand-product",
            "message": "测试品牌 3 这个不错",
        },
    )

    events = parse_sse(response.text)
    assert "error" not in [event.name for event in events]
    assert events[-1].data["status"] == "completed"
    saved = await repository.load("brand-product")
    assert saved is not None
    assert saved.state.focused_product_id == "p3"
```

The compiled integration Catalog defines `p3.brand == "测试品牌 3"` in
`tests/integration/api_fakes.py`; keep that exact taxonomy value.

- [ ] **Step 7: Run workflow and integration tests and verify GREEN**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_multi_turn_workflow.py \
  tests/unit/test_workflow_stream.py \
  tests/integration/test_chat_api.py
```

Expected: all selected files pass; the unique brand-shaped product question completes and focuses `p3`, while two matches persist only their narrowed subset.

---

### Task 5: Update the canonical multi-turn Query documentation and verify

**Files:**
- Modify: `docs/features/multi-turn-query-engine.md`
- Modify: `docs/README.md`
- Verify: all files under `tests/`

**Interfaces:**
- Documents: LLM candidate-match matrix, backend cardinality, compatibility behavior, pending-subset enforcement, and current context limits.
- Preserves: public API and storage table.

- [ ] **Step 1: Update feature behavior and scope**

In `docs/features/multi-turn-query-engine.md`, replace statements saying the model only extracts a clue and never outputs candidate IDs with this precise rule:

```markdown
模型对当前消息中的显式指代表达执行语言理解，并为
`recent_candidates` 中每个候选输出一次 `product_id + matches` 判断。
这些 ID 只能逐字复制最近候选，不能成为可信最终绑定。解析器验证矩阵完整、有序且
无重复；代码再根据零个、一个或多个匹配生成无法确认、`resolved_product_id` 或澄清。
商品问答强制使用商品目标；品牌目标允许多个商品命中，但 Catalog 映射出的品牌必须
唯一。模型不输出最终 `resolved_product_id`、`resolved_brand` 或置信度。
```

Update the `ProductReference` example:

```json
{
  "target_type": "product",
  "surface_text": "小米那个",
  "kind": "brand",
  "ordinal": null,
  "brand": "小米",
  "product_name": null,
  "candidate_matches": [
    {"product_id": "p1", "matches": true},
    {"product_id": "p2", "matches": true},
    {"product_id": "p3", "matches": false}
  ]
}
```

Document:

- `candidate_matches=[]` is accepted only as the backward-compatible form of persisted v1 pending turns and non-LLM test fakes.
- New LLM references must cover every recent candidate once in rank order.
- Two matching Xiaomi products trigger a clarification listing only those two products.
- The next clarification answer is restricted to the saved candidate subset.
- Reference-less questions still use focus, then sole-candidate fallback, then clarification.
- Current candidate summaries support rank/title/brand semantics but not price- or feature-based superlatives.

- [ ] **Step 2: Update the documentation coverage matrix**

Add exact test evidence:

```markdown
| LLM 候选匹配矩阵完整性、顺序与纠错 | `tests/unit/test_model_gateways.py::test_turn_query_parser_corrects_incomplete_candidate_matches`、`test_turn_query_parser_rejects_twice_reordered_candidate_matches` |
| 唯一商品、同品牌多商品、目标类型覆盖与歧义收窄 | `tests/unit/test_reference_resolver.py::test_matrix_resolves_one_product_without_using_clue_kind`、`test_matrix_brand_target_allows_multiple_products_of_one_brand`、`test_expected_product_target_overrides_model_brand_target`、`test_matrix_product_ambiguity_lists_only_matched_candidates` |
| 品牌式商品追问与澄清候选边界 | `tests/unit/test_multi_turn_workflow.py::test_product_question_treats_unique_brand_target_as_product_match`、`test_two_product_matches_persist_only_matched_clarification_candidates`、`test_clarification_answer_cannot_escape_saved_candidate_subset` |
```

- [ ] **Step 3: Update the feature index**

In `docs/README.md`, keep the existing feature name and update that same row’s
code-entry list:

```markdown
| 多轮 Query 编译与指代消解 | 开发中 | [features/multi-turn-query-engine.md](features/multi-turn-query-engine.md) | `src/shop_agent/models/turn_query.py`、`src/shop_agent/models/conversation.py`、`src/shop_agent/services/ports.py`、`src/shop_agent/services/conversation_repository.py`、`src/shop_agent/services/reference_resolver.py`、`src/shop_agent/services/multi_turn_query_compiler.py`、`src/shop_agent/services/dashscope_chat.py`、`src/shop_agent/services/retrieval.py`、`src/shop_agent/services/qdrant_store.py`、`src/shop_agent/workflow/nodes.py`、`src/shop_agent/workflow/graph.py`、`src/shop_agent/api/dependencies.py`、`src/shop_agent/api/chat.py`、`tests/unit/test_model_gateways.py`、`tests/unit/test_reference_resolver.py`、`tests/unit/test_multi_turn_workflow.py`、`tests/integration/test_chat_api.py`、`tests/live/test_live_shopping_flow.py` |
```

Use the row exactly as shown so the feature index includes both parser-boundary
and resolver test entry points without introducing a new feature identity.

- [ ] **Step 4: Run focused contract verification**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/unit/test_turn_query_models.py \
  tests/unit/test_conversation_models.py \
  tests/unit/test_model_gateways.py \
  tests/unit/test_reference_resolver.py \
  tests/unit/test_multi_turn_workflow.py \
  tests/unit/test_workflow_stream.py \
  tests/integration/test_chat_api.py
```

Expected: zero failures.

- [ ] **Step 5: Run the complete non-live suite**

Run:

```bash
uv run pytest -q -p no:cacheprovider
```

Expected: zero failures; live tests remain skipped unless explicitly enabled by the existing project flag.

- [ ] **Step 6: Perform the manual reported-scenario acceptance check**

With the service running through the project’s existing startup procedure, run:

```bash
uv run python scripts/chat_client.py
```

Enter:

```text
推荐一款手机
三星这个不错
```

Pass criteria:

- The second turn does not emit `PRODUCT_KNOWLEDGE_UNAVAILABLE`.
- Logs contain `resolved_product_id="p_digital_027"` and no `resolved_brand`-only product-question route.
- The conversation persists `focused_product_id="p_digital_027"`.

Then exercise an ambiguity fixture or test dataset containing two Xiaomi candidates and one Apple candidate:

```text
小米那个不错
```

Pass criteria:

- The response asks which Xiaomi product was intended.
- Only the two Xiaomi products appear in the clarification.
- Selecting either saved Xiaomi candidate resumes the suspended question.
- Selecting the Apple candidate does not resolve and clears the pending action under the existing second-attempt rule.

- [ ] **Step 7: Record fresh verification evidence in the feature document**

Replace the stale counts in the `Fresh 验证` section of `docs/features/multi-turn-query-engine.md` with the exact command output from Steps 4 and 5, including pass and skip counts and the current date.

Do not claim live verification unless Step 6 or the opt-in live parser test actually ran.

## Rollback and Failure Handling

- This implementation changes no database table and writes no external state beyond the existing conversation rows.
- Rolling back code leaves pending rows containing the additive `candidate_matches` field unreadable by the old `extra="forbid"` model. Before a production rollback, delete only affected demo conversation rows through an explicitly approved operational procedure or deploy a compatibility patch that ignores the additive field. Do not perform deletion as part of implementation.
- New code can read old pending rows because `candidate_matches` defaults to `[]` and the legacy resolver remains.
- If live parser quality shows frequent incomplete matrices, do not relax coverage validation. Improve prompt examples or structured-call constraints while retaining one-entry-per-candidate enforcement.
- If live parser quality shows semantically wrong single matches, the premise has failed: retain the matrix for observability, but route vulnerable expressions back through deterministic clue validation or clarification rather than silently trusting the single match.

## Acceptance Summary

- `三星这个不错` uniquely binds `p_digital_027` and no longer fails before product knowledge lookup.
- “第二个” and “中间那个” may be interpreted by the LLM but resolve only through validated current-candidate IDs.
- Two Xiaomi matches produce a two-item clarification, not a guessed product and not a three-item list containing Apple.
- Clarification recovery cannot select a product outside the saved ambiguous subset.
- Reference-less focus behavior, brand refinement, relative-price behavior, SSE contracts, and persisted v1 pending turns remain compatible.
- Full non-live tests pass, documentation and index match the implemented contract, and no Git operation is performed.
