import pytest
from pydantic import ValidationError

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
