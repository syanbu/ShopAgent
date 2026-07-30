from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.models.conversation import (
    CandidateReference,
    ConversationState,
    QuerySnapshot,
)
from shop_agent.models.product import Product
from shop_agent.models.query import NumericConstraint, SearchConstraints
from shop_agent.models.turn_query import (
    CategoryCandidate,
    CategoryReference,
    TurnQuery,
)
from shop_agent.services.multi_turn_query_compiler import (
    INVALID_CONDITION_MESSAGE,
    merge_turn_query,
)


def _product(
    product_id: str,
    *,
    brand: str,
    category: str,
    sub_category: str,
    properties: dict[str, str],
    price: float,
) -> Product:
    return Product.model_validate(
        {
            "product_id": product_id,
            "title": f"测试商品 {product_id}",
            "brand": brand,
            "category": category,
            "sub_category": sub_category,
            "base_price": price,
            "image_path": f"images/{product_id}.jpg",
            "skus": [
                {
                    "sku_id": f"{product_id}-sku",
                    "properties": properties,
                    "price": price,
                }
            ],
            "rag_knowledge": {
                "marketing_description": "测试商品描述",
                "official_faq": [],
                "user_reviews": [],
            },
        }
    )


@pytest.fixture
def catalog(tmp_path: Path) -> ProductCatalog:
    products = [
        _product(
            "earphone-apple",
            brand="Apple 苹果",
            category="数码电子",
            sub_category="蓝牙耳机",
            properties={"颜色": "黑色"},
            price=399,
        ),
        _product(
            "phone-xiaomi-512",
            brand="小米",
            category="数码电子",
            sub_category="智能手机",
            properties={"存储配置": "512GB"},
            price=459,
        ),
        _product(
            "phone-xiaomi-1tb",
            brand="小米",
            category="数码电子",
            sub_category="智能手机",
            properties={"存储配置": "1TB"},
            price=529,
        ),
        _product(
            "shoe-nike",
            brand="Nike 耐克",
            category="服饰运动",
            sub_category="跑步鞋",
            properties={"鞋码": "42码"},
            price=699,
        ),
    ]
    return ProductCatalog(
        tmp_path,
        {product.product_id: product for product in products},
        {},
    )


def _turn(
    intent: str = "refine_search",
    **updates: object,
) -> TurnQuery:
    return TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": intent,
            **updates,
        }
    )


def _candidate(product_id: str, rank: int, price: float) -> CandidateReference:
    return CandidateReference(
        product_id=product_id,
        rank=rank,
        display_price=price,
    )


def _state(
    snapshot: QuerySnapshot | None = None,
    *,
    recent: list[CandidateReference] | None = None,
    focus: str | None = None,
) -> ConversationState:
    candidates = recent or []
    return ConversationState(
        schema_version=1,
        conversation_id="conversation-1",
        query_snapshot=snapshot,
        recent_candidates=candidates,
        focused_product_id=focus,
        seen_product_ids=[candidate.product_id for candidate in candidates],
    )


def _assert_input_unchanged(
    state: ConversationState,
    before: ConversationState,
) -> None:
    assert state == before
    assert state.model_dump_json() == before.model_dump_json()
    assert state.query_snapshot == before.query_snapshot


def test_refinement_replaces_budget_and_preserves_unrelated_slot(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤"],
            constraints=SearchConstraints(
                max_price=500,
                required_features=["佩戴舒适"],
            ),
        ),
        recent=[_candidate("earphone-apple", 1, 399)],
        focus="earphone-apple",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 300,
                }
            ]
        ),
        state,
        catalog,
    )

    assert result.intent == "refine_search"
    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 300
    assert result.snapshot.constraints.required_features == ["佩戴舒适"]
    assert result.snapshot.semantic_terms == ["通勤"]
    assert result.result_strategy == "stable_refine"
    assert result.state.recent_candidates == state.recent_candidates
    assert result.state.focused_product_id == state.focused_product_id
    assert result.state.seen_product_ids == state.seen_product_ids
    _assert_input_unchanged(state, before)


def test_resolved_category_scope_seeds_a_new_search_snapshot(
    catalog: ProductCatalog,
) -> None:
    scope = CategoryCandidate(
        category="数码电子",
        sub_category="蓝牙耳机",
    )
    result = merge_turn_query(
        _turn(
            "new_search",
            category_reference=CategoryReference(
                surface_text="耳机",
                candidates=[scope],
            ),
        ),
        _state(),
        catalog,
        resolved_category_scope=scope,
    )

    assert result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="蓝牙耳机",
    )
    assert result.parsed_intent is not None
    assert result.parsed_intent.sub_category == "蓝牙耳机"


def test_resolved_category_scope_is_applied_before_sku_validation(
    catalog: ProductCatalog,
) -> None:
    scope = CategoryCandidate(
        category="数码电子",
        sub_category="智能手机",
    )
    result = merge_turn_query(
        _turn(
            "new_search",
            category_reference=CategoryReference(
                surface_text="手机",
                candidates=[scope],
            ),
            slot_operations=[
                {
                    "slot": "constraints.sku_constraints",
                    "operation": "add",
                    "sku_key": "storage",
                    "value": "512GB",
                }
            ],
        ),
        _state(),
        catalog,
        resolved_category_scope=scope,
    )

    assert result.needs_clarification is False
    assert result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(
            sku_constraints={"storage": ["512GB"]},
        ),
    )


def test_resolved_category_scope_switches_and_resets_old_conditions(
    catalog: ProductCatalog,
) -> None:
    scope = CategoryCandidate(
        category="服饰运动",
        sub_category="跑步鞋",
    )
    result = merge_turn_query(
        _turn(
            "refine_search",
            category_reference=CategoryReference(
                surface_text="跑步鞋",
                candidates=[scope],
            ),
        ),
        _state(
            QuerySnapshot(
                category="数码电子",
                sub_category="蓝牙耳机",
                constraints=SearchConstraints(max_price=500),
            )
        ),
        catalog,
        resolved_category_scope=scope,
    )

    assert result.intent == "switch_category"
    assert result.snapshot == QuerySnapshot(
        category="服饰运动",
        sub_category="跑步鞋",
    )


def test_resolved_top_level_category_is_a_valid_switch_target(
    catalog: ProductCatalog,
) -> None:
    scope = CategoryCandidate(category="服饰运动")
    result = merge_turn_query(
        _turn(
            "refine_search",
            category_reference=CategoryReference(
                surface_text="服饰运动",
                candidates=[scope],
            ),
        ),
        _state(
            QuerySnapshot(
                category="数码电子",
                sub_category="智能手机",
            )
        ),
        catalog,
        resolved_category_scope=scope,
    )

    assert result.intent == "switch_category"
    assert result.snapshot == QuerySnapshot(category="服饰运动")


def test_semantic_terms_add_and_remove_in_stable_order(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤", "旧需求"],
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            semantic_term_operations=[
                {"operation": "remove", "value": "旧需求"},
                {"operation": "add", "value": "轻量"},
                {"operation": "add", "value": "通勤"},
            ]
        ),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.semantic_terms == ["通勤", "轻量"]
    _assert_input_unchanged(state, before)


def test_semantic_terms_clear_without_mutating_input(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤", "轻量"],
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(semantic_term_operations=[{"operation": "clear"}]),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.semantic_terms == []
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("slot", "opposite_slot", "initial", "expected"),
    [
        (
            "constraints.include_brands",
            "constraints.exclude_brands",
            {"exclude_brands": ["APPLE 苹果", "Nike 耐克"]},
            (["Apple 苹果"], ["Nike 耐克"]),
        ),
        (
            "constraints.exclude_brands",
            "constraints.include_brands",
            {"include_brands": ["APPLE 苹果", "小米"]},
            (["小米"], ["Apple 苹果"]),
        ),
    ],
)
def test_brand_add_removes_case_insensitive_equivalent_from_opposite_side(
    catalog: ProductCatalog,
    slot: str,
    opposite_slot: str,
    initial: dict[str, list[str]],
    expected: tuple[list[str], list[str]],
) -> None:
    del opposite_slot
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(**initial),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {"slot": slot, "operation": "add", "value": "Apple 苹果"}
            ]
        ),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.include_brands == expected[0]
    assert result.snapshot.constraints.exclude_brands == expected[1]
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("slot", "initial", "expected"),
    [
        (
            "constraints.required_features",
            {"excluded_features": ["防水", "笨重"]},
            (["防水"], ["笨重"]),
        ),
        (
            "constraints.excluded_features",
            {"required_features": ["防水", "轻量"]},
            (["轻量"], ["防水"]),
        ),
    ],
)
def test_feature_add_removes_equivalent_from_opposite_side(
    catalog: ProductCatalog,
    slot: str,
    initial: dict[str, list[str]],
    expected: tuple[list[str], list[str]],
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(**initial),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {"slot": slot, "operation": "add", "value": "防水"}
            ]
        ),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.required_features == expected[0]
    assert result.snapshot.constraints.excluded_features == expected[1]
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("slot", "initial", "expected_field", "expected"),
    [
        (
            "constraints.include_brands",
            {"include_brands": ["Apple 苹果", "小米"]},
            "include_brands",
            ["小米"],
        ),
        (
            "constraints.exclude_brands",
            {"exclude_brands": ["Apple 苹果", "小米"]},
            "exclude_brands",
            [],
        ),
        (
            "constraints.required_features",
            {"required_features": ["防水", "轻量"]},
            "required_features",
            ["轻量"],
        ),
        (
            "constraints.excluded_features",
            {"excluded_features": ["笨重", "入耳式"]},
            "excluded_features",
            [],
        ),
    ],
)
def test_brand_and_feature_slots_support_remove_and_clear(
    catalog: ProductCatalog,
    slot: str,
    initial: dict[str, list[str]],
    expected_field: str,
    expected: list[str],
) -> None:
    operation = "remove" if expected else "clear"
    raw_operation: dict[str, object] = {"slot": slot, "operation": operation}
    if operation == "remove":
        raw_operation["value"] = next(iter(initial.values()))[0]
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(**initial),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(slot_operations=[raw_operation]),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert getattr(result.snapshot.constraints, expected_field) == expected
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        ("add", "1TB", ["512GB", "1TB"]),
        ("remove", "512GB", []),
        ("clear", None, []),
    ],
)
def test_sku_operations_are_stable_and_copy_on_write(
    catalog: ProductCatalog,
    operation: str,
    value: str | None,
    expected: list[str],
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(
                sku_constraints={"storage": ["512GB"]}
            ),
        )
    )
    before = state.model_copy(deep=True)
    raw_operation: dict[str, object] = {
        "slot": "constraints.sku_constraints",
        "operation": operation,
        "sku_key": "storage",
    }
    if value is not None:
        raw_operation["value"] = value

    result = merge_turn_query(
        _turn(slot_operations=[raw_operation]),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.sku_constraints.get("storage", []) == expected
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize("operation", ["add", "remove", "clear"])
def test_numeric_operations_are_stable_and_copy_on_write(
    catalog: ProductCatalog,
    operation: str,
) -> None:
    original = NumericConstraint(
        field="storage",
        operator=">=",
        value=256,
        unit="GB",
    )
    added = NumericConstraint(
        field="storage",
        operator=">=",
        value=512,
        unit="GB",
    )
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(numeric_constraints=[original]),
        )
    )
    before = state.model_copy(deep=True)
    raw_operation: dict[str, object] = {
        "slot": "constraints.numeric_constraints",
        "operation": operation,
    }
    if operation == "add":
        raw_operation["value"] = added
        expected = [original, added]
    elif operation == "remove":
        raw_operation["value"] = original
        expected = []
    else:
        expected = []

    result = merge_turn_query(
        _turn(slot_operations=[raw_operation]),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.numeric_constraints == expected
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("slot", "expected_min", "expected_max", "expected_preference"),
    [
        ("constraints.min_price", None, 800, "value"),
        ("constraints.max_price", 300, None, "value"),
        ("constraints.price_preference", 300, 800, None),
    ],
)
def test_price_slots_clear_independently(
    catalog: ProductCatalog,
    slot: str,
    expected_min: float | None,
    expected_max: float | None,
    expected_preference: str | None,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(
                min_price=300,
                max_price=800,
                price_preference="value",
            ),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(slot_operations=[{"slot": slot, "operation": "clear"}]),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.min_price == expected_min
    assert result.snapshot.constraints.max_price == expected_max
    assert result.snapshot.constraints.price_preference == expected_preference
    _assert_input_unchanged(state, before)


def test_category_switch_resets_old_state_and_keeps_only_restated_slots(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤"],
            constraints=SearchConstraints(
                max_price=500,
                include_brands=["Apple 苹果"],
                required_features=["降噪"],
                sku_constraints={"color": ["黑色"]},
            ),
        ),
        recent=[_candidate("earphone-apple", 1, 399)],
        focus="earphone-apple",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            semantic_term_operations=[{"operation": "add", "value": "拍演唱会"}],
            slot_operations=[
                {"slot": "category", "operation": "replace", "value": "数码电子"},
                {
                    "slot": "sub_category",
                    "operation": "replace",
                    "value": "智能手机",
                },
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 8000,
                },
                {
                    "slot": "constraints.include_brands",
                    "operation": "add",
                    "value": "小米",
                },
                {
                    "slot": "constraints.sku_constraints",
                    "operation": "add",
                    "sku_key": "storage",
                    "value": "512GB",
                },
            ],
        ),
        state,
        catalog,
    )

    assert result.intent == "switch_category"
    assert result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="智能手机",
        semantic_terms=["拍演唱会"],
        constraints=SearchConstraints(
            max_price=8000,
            include_brands=["小米"],
            sku_constraints={"storage": ["512GB"]},
        ),
    )
    assert result.state.recent_candidates == []
    assert result.state.focused_product_id is None
    assert result.state.seen_product_ids == []
    _assert_input_unchanged(state, before)


def test_new_search_starts_from_an_empty_snapshot(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤"],
            constraints=SearchConstraints(max_price=500),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            "new_search",
            slot_operations=[
                {"slot": "category", "operation": "replace", "value": "数码电子"},
                {
                    "slot": "sub_category",
                    "operation": "replace",
                    "value": "蓝牙耳机",
                },
            ],
        ),
        state,
        catalog,
    )

    assert result.intent == "new_search"
    assert result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="蓝牙耳机",
    )
    _assert_input_unchanged(state, before)


def test_category_and_subcategory_can_be_cleared_together(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="蓝牙耳机")
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {"slot": "category", "operation": "clear"},
                {"slot": "sub_category", "operation": "clear"},
            ]
        ),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert result.snapshot.category is None
    assert result.snapshot.sub_category is None
    _assert_input_unchanged(state, before)


def test_focus_price_is_the_cheaper_baseline(catalog: ProductCatalog) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[
            _candidate("earphone-apple", 1, 399),
            _candidate("phone-xiaomi-512", 2, 459),
            _candidate("phone-xiaomi-1tb", 3, 529),
        ],
        focus="phone-xiaomi-512",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(_turn(relative_price="cheaper"), state, catalog)

    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 458.99
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("direction", "field", "expected"),
    [
        ("cheaper", "max_price", 398.99),
        ("more_expensive", "min_price", 529.01),
    ],
)
def test_latest_batch_extreme_is_relative_price_baseline(
    catalog: ProductCatalog,
    direction: str,
    field: str,
    expected: float,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[
            _candidate("earphone-apple", 1, 399),
            _candidate("phone-xiaomi-512", 2, 459),
            _candidate("phone-xiaomi-1tb", 3, 529),
        ],
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(relative_price=direction),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert getattr(result.snapshot.constraints, field) == expected
    _assert_input_unchanged(state, before)


def test_resolved_product_takes_precedence_over_focus(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[
            _candidate("earphone-apple", 1, 399),
            _candidate("phone-xiaomi-512", 2, 459),
        ],
        focus="earphone-apple",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(relative_price="cheaper"),
        state,
        catalog,
        resolved_product_id="phone-xiaomi-512",
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 458.99
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    ("direction", "slot", "value", "field", "expected"),
    [
        ("cheaper", "constraints.max_price", 450, "max_price", 450),
        ("more_expensive", "constraints.min_price", 600, "min_price", 600),
    ],
)
def test_explicit_applicable_boundary_overrides_relative_price(
    catalog: ProductCatalog,
    direction: str,
    slot: str,
    value: float,
    field: str,
    expected: float,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[_candidate("phone-xiaomi-512", 1, 459)],
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            relative_price=direction,
            slot_operations=[
                {"slot": slot, "operation": "replace", "value": value}
            ],
        ),
        state,
        catalog,
    )

    assert result.snapshot is not None
    assert getattr(result.snapshot.constraints, field) == expected
    _assert_input_unchanged(state, before)


def test_relative_price_without_baseline_returns_clarification(
    catalog: ProductCatalog,
) -> None:
    state = _state(QuerySnapshot(category="数码电子", sub_category="智能手机"))
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(relative_price="more_expensive"),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.clarification_message
    assert result.parsed_intent is None
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize("intent", ["refine_search", "more_results"])
def test_refine_and_more_without_snapshot_return_missing_context(
    catalog: ProductCatalog,
    intent: str,
) -> None:
    state = _state()
    before = state.model_copy(deep=True)

    result = merge_turn_query(_turn(intent), state, catalog)

    assert result.intent == intent
    assert result.needs_clarification is True
    assert result.clarification_message
    assert result.snapshot is None
    assert result.parsed_intent is None
    _assert_input_unchanged(state, before)


def test_price_conflict_returns_clarification_without_parsed_intent(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(max_price=500),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {
                    "slot": "constraints.min_price",
                    "operation": "replace",
                    "value": 600,
                }
            ]
        ),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.clarification_message
    assert result.parsed_intent is None
    assert result.state == state
    _assert_input_unchanged(state, before)


def test_more_results_preserves_snapshot_candidates_focus_and_seen_ids(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(max_price=800),
        ),
        recent=[
            _candidate("phone-xiaomi-512", 1, 459),
            _candidate("phone-xiaomi-1tb", 2, 529),
        ],
        focus="phone-xiaomi-512",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(_turn("more_results"), state, catalog)

    assert result.intent == "more_results"
    assert result.snapshot == state.query_snapshot
    assert result.state == state
    assert result.state is not state
    assert result.parsed_intent is not None
    _assert_input_unchanged(state, before)


def test_more_results_with_price_operation_becomes_refinement(
    catalog: ProductCatalog,
) -> None:
    snapshot = QuerySnapshot(
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(max_price=800),
    )
    state = _state(snapshot)

    result = merge_turn_query(
        _turn(
            "more_results",
            slot_operations=[
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 100,
                }
            ],
        ),
        state,
        catalog,
    )

    assert result.intent == "refine_search"
    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 100
    assert result.state.recent_candidates == []
    assert result.state.focused_product_id is None
    assert result.state.seen_product_ids == []


def test_more_results_with_semantic_operation_becomes_refinement(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            semantic_terms=["通勤"],
        ),
        recent=[_candidate("phone-xiaomi-512", 1, 459)],
        focus="phone-xiaomi-512",
    )

    result = merge_turn_query(
        _turn(
            "more_results",
            semantic_term_operations=[{"operation": "add", "value": "拍照"}],
        ),
        state,
        catalog,
    )

    assert result.intent == "refine_search"
    assert result.result_strategy == "full_rerank"
    assert result.snapshot is not None
    assert result.snapshot.semantic_terms == ["通勤", "拍照"]
    assert result.state.recent_candidates == state.recent_candidates
    assert result.state.seen_product_ids == state.seen_product_ids


def test_more_results_with_relative_price_becomes_refinement(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[
            _candidate("phone-xiaomi-512", 1, 459),
            _candidate("phone-xiaomi-1tb", 2, 529),
        ],
    )

    result = merge_turn_query(
        _turn("more_results", relative_price="cheaper"),
        state,
        catalog,
    )

    assert result.intent == "refine_search"
    assert result.result_strategy == "stable_refine"
    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 458.99
    assert result.state.recent_candidates == state.recent_candidates
    assert result.state.seen_product_ids == state.seen_product_ids


def test_resolved_brand_is_included_and_removed_from_exclusions(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(exclude_brands=["小米"]),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(),
        state,
        catalog,
        resolved_brand="小米",
    )

    assert result.snapshot is not None
    assert result.snapshot.constraints.include_brands == ["小米"]
    assert result.snapshot.constraints.exclude_brands == []
    _assert_input_unchanged(state, before)


def test_more_results_with_resolved_brand_becomes_full_rerank_refinement(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(exclude_brands=["小米"]),
        ),
        recent=[
            _candidate("phone-xiaomi-512", 1, 459),
            _candidate("phone-xiaomi-1tb", 2, 529),
        ],
        focus="phone-xiaomi-512",
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn("more_results"),
        state,
        catalog,
        resolved_brand="小米",
    )

    assert result.intent == "refine_search"
    assert result.result_strategy == "full_rerank"
    assert result.snapshot is not None
    assert result.snapshot.constraints.include_brands == ["小米"]
    assert result.snapshot.constraints.exclude_brands == []
    assert result.state.recent_candidates == state.recent_candidates
    assert result.state.focused_product_id == state.focused_product_id
    assert result.state.seen_product_ids == state.seen_product_ids
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    "slot_operation",
    [
        {"slot": "category", "operation": "replace", "value": "不存在类目"},
        {
            "slot": "sub_category",
            "operation": "replace",
            "value": "跑步鞋",
        },
        {
            "slot": "constraints.include_brands",
            "operation": "add",
            "value": "不存在品牌",
        },
        {
            "slot": "constraints.sku_constraints",
            "operation": "add",
            "sku_key": "size",
            "value": "42码",
        },
        {
            "slot": "constraints.sku_constraints",
            "operation": "add",
            "sku_key": "storage",
            "value": "2TB",
        },
    ],
)
def test_invalid_taxonomy_values_take_user_safe_clarification_path(
    catalog: ProductCatalog,
    slot_operation: dict[str, object],
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机")
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(slot_operations=[slot_operation]),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.clarification_message
    assert result.parsed_intent is None
    assert result.state == state
    _assert_input_unchanged(state, before)


@pytest.mark.parametrize(
    "slot_operation",
    [
        {
            "slot": "constraints.include_brands",
            "operation": "remove",
            "value": "不存在品牌",
        },
        {
            "slot": "constraints.sku_constraints",
            "operation": "remove",
            "sku_key": "storage",
            "value": "2TB",
        },
        {
            "slot": "constraints.sku_constraints",
            "operation": "clear",
            "sku_key": "size",
        },
    ],
)
def test_invalid_remove_or_clear_operation_cannot_bypass_taxonomy_validation(
    catalog: ProductCatalog,
    slot_operation: dict[str, object],
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(
                include_brands=["小米"],
                sku_constraints={"storage": ["512GB"]},
            ),
        )
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(slot_operations=[slot_operation]),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.clarification_message
    assert result.parsed_intent is None
    assert result.state == state
    _assert_input_unchanged(state, before)


def test_switch_validates_sku_operation_against_new_pair_after_resetting_old_slots(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(
                max_price=500,
                sku_constraints={"color": ["黑色"]},
            ),
        ),
        recent=[_candidate("earphone-apple", 1, 399)],
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {"slot": "category", "operation": "replace", "value": "数码电子"},
                {
                    "slot": "sub_category",
                    "operation": "replace",
                    "value": "智能手机",
                },
                {
                    "slot": "constraints.sku_constraints",
                    "operation": "clear",
                    "sku_key": "storage",
                },
            ]
        ),
        state,
        catalog,
    )

    assert result.needs_clarification is False
    assert result.intent == "switch_category"
    assert result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="智能手机",
    )
    assert result.parsed_intent is not None
    assert result.state.recent_candidates == []
    assert result.state.seen_product_ids == []
    _assert_input_unchanged(state, before)


def test_hard_constraint_tightening_uses_stable_refinement_and_keeps_batch_context(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机"),
        recent=[
            _candidate("phone-xiaomi-512", 1, 459),
            _candidate("phone-xiaomi-1tb", 2, 529),
        ],
    )
    before = state.model_copy(deep=True)

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {
                    "slot": "constraints.exclude_brands",
                    "operation": "add",
                    "value": "小米",
                }
            ]
        ),
        state,
        catalog,
    )

    assert result.result_strategy == "stable_refine"
    assert result.state.recent_candidates == state.recent_candidates
    assert result.state.seen_product_ids == state.seen_product_ids
    _assert_input_unchanged(state, before)


def test_hard_constraint_relaxation_uses_full_rerank(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            constraints=SearchConstraints(exclude_brands=["小米"]),
        ),
        recent=[_candidate("phone-xiaomi-512", 1, 459)],
    )

    result = merge_turn_query(
        _turn(
            slot_operations=[
                {
                    "slot": "constraints.exclude_brands",
                    "operation": "remove",
                    "value": "小米",
                }
            ]
        ),
        state,
        catalog,
    )

    assert result.result_strategy == "full_rerank"
    assert result.snapshot is not None
    assert result.snapshot.constraints.exclude_brands == []


def test_soft_preference_prioritize_moves_term_to_front_and_uses_full_rerank(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            semantic_terms=["拍照", "轻薄"],
        )
    )

    result = merge_turn_query(
        _turn(
            semantic_term_operations=[
                {"operation": "prioritize", "value": "轻薄"}
            ]
        ),
        state,
        catalog,
    )

    assert result.result_strategy == "full_rerank"
    assert result.snapshot is not None
    assert result.snapshot.semantic_terms == ["轻薄", "拍照"]


@pytest.mark.parametrize(
    ("approximate_price", "expected_min", "expected_max"),
    [
        (
            {"mode": "target", "amount": 5000},
            4500,
            5500,
        ),
        (
            {"mode": "budget_cap", "amount": 5000},
            None,
            5500,
        ),
        (
            {
                "mode": "target",
                "amount": 5000,
                "tolerance_kind": "absolute",
                "tolerance_value": 300,
            },
            4700,
            5300,
        ),
    ],
)
def test_approximate_price_compiles_deterministic_bounds(
    catalog: ProductCatalog,
    approximate_price: dict[str, object],
    expected_min: float | None,
    expected_max: float,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机")
    )

    result = merge_turn_query(
        _turn(approximate_price=approximate_price),
        state,
        catalog,
    )

    assert result.result_strategy == "stable_refine"
    assert result.snapshot is not None
    assert result.snapshot.constraints.min_price == expected_min
    assert result.snapshot.constraints.max_price == expected_max


def test_more_results_with_approximate_price_becomes_refinement(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机")
    )

    result = merge_turn_query(
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "more_results",
                "approximate_price": {
                    "mode": "budget_cap",
                    "amount": 5000,
                },
            }
        ),
        state,
        catalog,
    )

    assert result.intent == "refine_search"
    assert result.result_strategy == "stable_refine"
    assert result.snapshot is not None
    assert result.snapshot.constraints.max_price == 5500


def test_large_finite_approximate_price_compiles_without_decimal_failure(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机")
    )

    result = merge_turn_query(
        _turn(
            approximate_price={
                "mode": "target",
                "amount": 1e29,
            }
        ),
        state,
        catalog,
    )

    assert result.needs_clarification is False
    assert result.snapshot is not None
    assert result.snapshot.constraints.min_price == 9e28
    assert result.snapshot.constraints.max_price == 1.1e29


def test_approximate_price_overflow_becomes_safe_clarification(
    catalog: ProductCatalog,
) -> None:
    state = _state(
        QuerySnapshot(category="数码电子", sub_category="智能手机")
    )

    result = merge_turn_query(
        _turn(
            approximate_price={
                "mode": "target",
                "amount": 1e308,
                "tolerance_kind": "absolute",
                "tolerance_value": 1e308,
            }
        ),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.clarification_message == INVALID_CONDITION_MESSAGE
