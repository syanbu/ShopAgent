import pytest
from pydantic import ValidationError

import shop_agent.models as public_models
from shop_agent.models.query import NumericConstraint
from shop_agent.models.turn_query import (
    ApproximatePrice,
    CategoryCandidate,
    CategoryReference,
    ProductQuestion,
    ProductReference,
    ReferenceCandidateMatch,
    SemanticTermOperation,
    SlotOperation,
    TurnCandidateSummary,
    TurnQuery,
)


def test_turn_query_accepts_a_category_reference_with_exact_candidates() -> None:
    reference = CategoryReference(
        surface_text="耳机",
        candidates=[
            CategoryCandidate(
                category="数码电子",
                sub_category="真无线耳机",
            )
        ],
    )

    query = TurnQuery(
        schema_version=1,
        intent="new_search",
        category_reference=reference,
    )

    assert query.category_reference == reference


def test_category_reference_rejects_duplicate_scopes() -> None:
    with pytest.raises(ValidationError, match="category candidate scopes"):
        CategoryReference(
            surface_text="耳机",
            candidates=[
                CategoryCandidate(
                    category="数码电子",
                    sub_category="真无线耳机",
                ),
                CategoryCandidate(
                    category="数码电子",
                    sub_category="真无线耳机",
                ),
            ],
        )


@pytest.mark.parametrize("surface_text", ["", "   ", "\t"])
def test_category_reference_rejects_blank_surface_text(surface_text: str) -> None:
    with pytest.raises(ValidationError):
        CategoryReference(surface_text=surface_text)


def test_category_reference_candidate_defaults_are_independent() -> None:
    first = CategoryReference(surface_text="耳机")
    second = CategoryReference(surface_text="手机")

    first.candidates.append(
        CategoryCandidate(
            category="数码电子",
            sub_category="真无线耳机",
        )
    )

    assert second.candidates == []


@pytest.mark.parametrize("slot", ["category", "sub_category"])
def test_category_reference_cannot_coexist_with_direct_category_slots(
    slot: str,
) -> None:
    with pytest.raises(ValidationError, match="category_reference"):
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "category_reference": {
                    "surface_text": "耳机",
                    "candidates": [
                        {
                            "category": "数码电子",
                            "sub_category": "真无线耳机",
                        }
                    ],
                },
                "slot_operations": [
                    {
                        "slot": slot,
                        "operation": "replace",
                        "value": (
                            "数码电子" if slot == "category" else "真无线耳机"
                        ),
                    }
                ],
            }
        )


def test_turn_candidate_summary_is_exported_from_models_package() -> None:
    assert public_models.TurnCandidateSummary is TurnCandidateSummary
    assert "TurnCandidateSummary" in public_models.__all__


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
        {
            "slot": "constraints.include_brands",
            "operation": "replace",
            "value": "小米",
        },
        {
            "slot": "constraints.max_price",
            "operation": "clear",
            "value": 300,
        },
        {
            "slot": "constraints.sku_constraints",
            "operation": "add",
            "value": "512GB",
        },
    ],
)
def test_slot_operation_rejects_invalid_contract(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SlotOperation.model_validate(payload)


def test_slot_operation_accepts_valid_sku_and_numeric_operations() -> None:
    sku_operation = SlotOperation(
        slot="constraints.sku_constraints",
        operation="add",
        sku_key="storage",
        value="512GB",
    )
    numeric = NumericConstraint(
        field="battery_capacity",
        operator=">=",
        value=5000,
        unit="mAh",
    )
    numeric_operation = SlotOperation(
        slot="constraints.numeric_constraints",
        operation="add",
        value=numeric,
    )

    assert sku_operation.sku_key == "storage"
    assert numeric_operation.value == numeric


@pytest.mark.parametrize("value", ["most_expensive", "cheapest", 1])
def test_price_preference_rejects_values_other_than_value(value: object) -> None:
    with pytest.raises(ValidationError, match="price preference"):
        SlotOperation(
            slot="constraints.price_preference",
            operation="replace",
            value=value,
        )


def test_price_preference_accepts_value_or_clear() -> None:
    replacement = SlotOperation(
        slot="constraints.price_preference",
        operation="replace",
        value="value",
    )
    clearing = SlotOperation(
        slot="constraints.price_preference",
        operation="clear",
    )

    assert replacement.value == "value"
    assert clearing.value is None


@pytest.mark.parametrize(
    "reference",
    [
        {"target_type": "product", "surface_text": "第二个", "kind": "ordinal"},
        {
            "target_type": "product",
            "surface_text": "这个",
            "kind": "demonstrative",
            "ordinal": 1,
        },
        {"target_type": "product", "surface_text": "小米的", "kind": "brand"},
        {
            "target_type": "product",
            "surface_text": "耳机",
            "kind": "product_name",
        },
        {
            "target_type": "product",
            "surface_text": "这个",
            "kind": "demonstrative",
            "brand": "小米",
        },
    ],
)
def test_product_reference_rejects_mismatched_kind_fields(
    reference: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProductReference.model_validate(reference)


def test_product_reference_accepts_kind_specific_clues() -> None:
    ordinal = ProductReference(
        target_type="product",
        surface_text="第二个",
        kind="ordinal",
        ordinal=2,
    )
    brand = ProductReference(
        target_type="product",
        surface_text="小米的",
        kind="brand",
        brand="小米",
    )

    assert ordinal.ordinal == 2
    assert brand.brand == "小米"


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


@pytest.mark.parametrize(
    "question",
    [
        {"text": "多少钱", "kind": "structured"},
        {"text": "防水吗", "kind": "semantic", "field": "sku"},
    ],
)
def test_product_question_rejects_invalid_field_contract(
    question: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProductQuestion.model_validate(question)


def test_turn_query_requires_product_question_if_and_only_if_routed_to_product_question() -> None:
    question = ProductQuestion(text="多少钱", kind="structured", field="display_price")

    with pytest.raises(ValidationError):
        TurnQuery(schema_version=1, intent="product_question")
    with pytest.raises(ValidationError):
        TurnQuery(
            schema_version=1,
            intent="refine_search",
            product_question=question,
        )

    query = TurnQuery(
        schema_version=1,
        intent="product_question",
        product_question=question,
    )
    assert query.product_question == question


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "clear", "value": "通勤"},
        {"operation": "add"},
        {"operation": "remove"},
    ],
)
def test_semantic_term_operation_requires_the_correct_value_contract(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SemanticTermOperation.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "add", "value": ""},
        {"operation": "add", "value": "   "},
        {"operation": "remove", "value": "\t"},
    ],
)
def test_semantic_term_add_and_remove_reject_blank_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SemanticTermOperation.model_validate(payload)


def test_semantic_term_add_strips_surrounding_whitespace() -> None:
    operation = SemanticTermOperation(operation="add", value="  通勤  ")

    assert operation.value == "通勤"


def test_semantic_term_prioritize_requires_and_normalizes_a_value() -> None:
    operation = SemanticTermOperation(operation="prioritize", value="  续航  ")

    assert operation.value == "续航"

    with pytest.raises(ValidationError):
        SemanticTermOperation(operation="prioritize")


@pytest.mark.parametrize(
    ("payload", "expected_kind", "expected_value"),
    [
        (
            {"mode": "target", "amount": 5000},
            "percent",
            10,
        ),
        (
            {
                "mode": "budget_cap",
                "amount": 5000,
                "tolerance_kind": "absolute",
                "tolerance_value": 300,
            },
            "absolute",
            300,
        ),
    ],
)
def test_approximate_price_accepts_default_and_explicit_tolerance(
    payload: dict[str, object],
    expected_kind: str,
    expected_value: float,
) -> None:
    approximate = ApproximatePrice.model_validate(payload)

    assert approximate.tolerance_kind == expected_kind
    assert approximate.tolerance_value == expected_value


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "target", "amount": -1},
        {"mode": "target", "amount": 5000, "tolerance_value": 0},
        {
            "mode": "target",
            "amount": 5000,
            "tolerance_kind": "percent",
            "tolerance_value": 100,
        },
    ],
)
def test_approximate_price_rejects_invalid_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ApproximatePrice.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "target", "amount": float("inf")},
        {
            "mode": "target",
            "amount": 5000,
            "tolerance_kind": "absolute",
            "tolerance_value": float("inf"),
        },
    ],
)
def test_approximate_price_rejects_non_finite_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="finite"):
        ApproximatePrice.model_validate(payload)


@pytest.mark.parametrize(
    "extra",
    [
        {
            "slot_operations": [
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 5500,
                }
            ]
        },
        {"relative_price": "cheaper"},
    ],
)
def test_approximate_price_cannot_coexist_with_other_price_operations(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="approximate_price"):
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "refine_search",
                "approximate_price": {
                    "mode": "target",
                    "amount": 5000,
                },
                **extra,
            }
        )


def test_turn_query_rejects_conflicting_slot_operations() -> None:
    with pytest.raises(ValidationError):
        TurnQuery(
            schema_version=1,
            intent="refine_search",
            slot_operations=[
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 300,
                },
                {
                    "slot": "constraints.max_price",
                    "operation": "clear",
                },
            ],
        )
    with pytest.raises(ValidationError):
        TurnQuery(
            schema_version=1,
            intent="refine_search",
            slot_operations=[
                {
                    "slot": "constraints.include_brands",
                    "operation": "clear",
                },
                {
                    "slot": "constraints.include_brands",
                    "operation": "add",
                    "value": "小米",
                },
            ],
        )


def test_turn_query_defaults_preference_question_skip_to_false() -> None:
    query = TurnQuery(schema_version=1, intent="new_search")

    assert query.skip_preference_question is False


@pytest.mark.parametrize(
    "intent",
    ["new_search", "switch_category", "clarification_answer"],
)
def test_turn_query_allows_preference_question_skip_on_supported_intents(
    intent: str,
) -> None:
    query = TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": intent,
            "skip_preference_question": True,
        }
    )

    assert query.skip_preference_question is True


@pytest.mark.parametrize(
    "intent",
    [
        "refine_search",
        "more_results",
        "product_question",
        "product_comparison",
        "non_shopping",
    ],
)
def test_turn_query_rejects_preference_question_skip_on_other_intents(
    intent: str,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "intent": intent,
        "skip_preference_question": True,
    }
    if intent == "product_question":
        payload["product_question"] = {
            "text": "怎么样",
            "kind": "semantic",
        }
    if intent == "product_comparison":
        payload["product_comparison"] = {
            "question": "这两个哪个好",
            "surface_text": "这两个",
        }

    with pytest.raises(ValidationError, match="skip_preference_question"):
        TurnQuery.model_validate(payload)


def test_turn_query_rejects_skip_and_cancel_together() -> None:
    with pytest.raises(ValidationError, match="cannot coexist"):
        TurnQuery(
            schema_version=1,
            intent="clarification_answer",
            skip_preference_question=True,
            cancel_pending=True,
        )


def test_scenario_intent_requires_scenario_request() -> None:
    query = TurnQuery(
        schema_version=1,
        intent="scenario_recommendation",
        scenario_request={
            "surface_text": "三亚度假，从防晒到穿搭",
            "recipe_id": "beach_vacation",
            "unmapped_requirements": [],
        },
    )

    assert query.scenario_request is not None
    assert query.scenario_request.recipe_id == "beach_vacation"

    with pytest.raises(ValidationError, match="scenario_request"):
        TurnQuery(schema_version=1, intent="scenario_recommendation")


def test_scenario_request_is_exclusive_to_scenario_intent() -> None:
    with pytest.raises(ValidationError, match="scenario_request"):
        TurnQuery(
            schema_version=1,
            intent="new_search",
            scenario_request={
                "surface_text": "三亚度假",
                "recipe_id": "beach_vacation",
            },
        )


def test_scenario_intent_rejects_single_category_mutations_and_references() -> None:
    with pytest.raises(ValidationError, match="scenario recommendations"):
        TurnQuery(
            schema_version=1,
            intent="scenario_recommendation",
            scenario_request={
                "surface_text": "三亚度假",
                "recipe_id": "beach_vacation",
            },
            semantic_term_operations=[
                {"operation": "add", "value": "便宜"}
            ],
        )
