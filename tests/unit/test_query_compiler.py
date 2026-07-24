import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.services.query_compiler import compile_query


def _intent(*, min_price: float | None = None, max_price: float | None = None) -> ParsedIntent:
    return ParsedIntent(
        schema_version=1,
        intent="product_search",
        retrieval_query="手机",
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(
            min_price=min_price,
            max_price=max_price,
            price_preference="value",
        ),
    )


@pytest.fixture
def catalog() -> ProductCatalog:
    return ProductCatalog.load(__import__("pathlib").Path("ecommerce_agent_dataset"))


@pytest.mark.parametrize(
    ("intent", "expected_min", "expected_max", "applied", "skip_reason"),
    [
        (_intent(), None, 8698.8, True, None),
        (_intent(max_price=8000), None, 8000, True, None),
        (_intent(max_price=10000), None, 8698.8, True, None),
        (_intent(min_price=9000), 9000, None, False, "explicit_min_exceeds_computed_cap"),
        (
            _intent(min_price=9000, max_price=10000),
            9000,
            10000,
            False,
            "explicit_min_exceeds_computed_cap",
        ),
    ],
)
def test_compile_query_merges_explicit_and_value_price_constraints(
    catalog: ProductCatalog,
    intent: ParsedIntent,
    expected_min: float | None,
    expected_max: float | None,
    applied: bool,
    skip_reason: str | None,
) -> None:
    result = compile_query(intent, catalog)

    assert result.needs_clarification is False
    assert result.effective_constraints.min_price == expected_min
    assert result.effective_constraints.max_price == expected_max
    assert result.price_reference is not None
    assert result.price_reference.applied is applied
    assert result.price_reference.skip_reason == skip_reason


def test_compile_query_requires_category_pair_for_value_preference(
    catalog: ProductCatalog,
) -> None:
    intent = _intent().model_copy(update={"sub_category": None})

    result = compile_query(intent, catalog)

    assert result.needs_clarification is True
    assert result.clarification_message == "请明确想购买的商品类型，例如手机、T恤或耳机。"


def test_compile_query_preserves_constraints_without_value_preference(
    catalog: ProductCatalog,
) -> None:
    constraints = SearchConstraints(max_price=5000, include_brands=["Apple 苹果"])
    intent = _intent().model_copy(update={"constraints": constraints})

    result = compile_query(intent, catalog)

    assert result.effective_constraints == constraints
    assert result.price_reference is None


def test_explicit_invalid_price_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_price cannot exceed max_price"):
        SearchConstraints(min_price=10, max_price=5)
