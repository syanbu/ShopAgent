import pytest
from pydantic import ValidationError

import shop_agent.models.query as query_models
from shop_agent.models.query import ParsedIntent


def test_product_search_intent_accepts_explicit_constraints() -> None:
    intent = ParsedIntent.model_validate(
        {
            "schema_version": 1,
            "intent": "product_search",
            "retrieval_query": "适合运动的蓝牙耳机",
            "category": "数码电子",
            "sub_category": "蓝牙耳机",
            "constraints": {
                "min_price": None,
                "max_price": 500,
                "include_brands": [],
                "exclude_brands": [],
                "required_features": ["适合运动"],
                "excluded_features": ["入耳式"],
            },
        }
    )
    assert intent.constraints.max_price == 500
    assert intent.retrieval_query == "适合运动的蓝牙耳机"


def test_non_shopping_intent_rejects_retrieval_query() -> None:
    with pytest.raises(ValidationError):
        ParsedIntent.model_validate(
            {
                "schema_version": 1,
                "intent": "non_shopping",
                "retrieval_query": "蓝牙耳机",
                "category": None,
                "sub_category": None,
                "constraints": {},
            }
        )


def test_value_price_preference_is_a_separate_semantic_constraint() -> None:
    intent = ParsedIntent.model_validate(
        {
            "schema_version": 1,
            "intent": "product_search",
            "retrieval_query": "手机",
            "category": "数码电子",
            "sub_category": "智能手机",
            "constraints": {"price_preference": "value"},
        }
    )

    assert intent.constraints.price_preference == "value"
    assert intent.constraints.required_features == []


def test_constraints_default_new_layers_to_empty() -> None:
    constraints = query_models.SearchConstraints()

    assert constraints.model_dump().get("sku_constraints") == {}
    assert constraints.model_dump().get("numeric_constraints") == []


def test_constraints_accept_canonical_sku_and_numeric_conditions() -> None:
    numeric_type = getattr(query_models, "NumericConstraint", None)
    assert numeric_type is not None
    constraints = query_models.SearchConstraints(
        sku_constraints={"storage": ["512GB"], "color": ["黑色"]},
        numeric_constraints=[
            numeric_type(
                field="battery_capacity",
                operator=">=",
                value=5000,
                unit="mAh",
            )
        ],
    )

    assert constraints.sku_constraints["storage"] == ["512GB"]
    assert constraints.numeric_constraints[0].condition_id() == (
        "numeric:battery_capacity:>=:5000:mAh"
    )


def test_constraints_reject_empty_sku_values() -> None:
    with pytest.raises(ValidationError, match="sku constraint values cannot be empty"):
        query_models.SearchConstraints(sku_constraints={"size": []})


def test_constraints_reject_same_required_and_excluded_feature() -> None:
    with pytest.raises(
        ValidationError,
        match="feature cannot be both required and excluded",
    ):
        query_models.SearchConstraints(
            required_features=["防水"],
            excluded_features=["防水"],
        )


def test_build_evidence_conditions_uses_stable_unique_ids() -> None:
    numeric_type = getattr(query_models, "NumericConstraint", None)
    build_conditions = getattr(query_models, "build_evidence_conditions", None)
    assert numeric_type is not None
    assert callable(build_conditions)
    constraints = query_models.SearchConstraints(
        required_features=["防水"],
        excluded_features=["入耳式"],
        numeric_constraints=[
            numeric_type(
                field="battery_capacity",
                operator=">=",
                value=5000,
                unit="mAh",
            )
        ],
    )

    conditions = build_conditions(constraints)

    assert [condition.condition_id for condition in conditions] == [
        "required:防水",
        "excluded:入耳式",
        "numeric:battery_capacity:>=:5000:mAh",
    ]
