from shop_agent.catalog import ProductCatalog
from shop_agent.models.query import (
    ParsedIntent,
    PriceCompilationReference,
    QueryCompilationResult,
    SearchConstraints,
)


CLARIFICATION_MESSAGE = "请明确想购买的商品类型，例如手机、T恤或耳机。"
VALUE_PRICE_MULTIPLIER = 1.2


def compile_query(
    intent: ParsedIntent,
    catalog: ProductCatalog,
) -> QueryCompilationResult:
    constraints = intent.constraints.model_copy(deep=True)
    if intent.intent != "product_search" or constraints.price_preference != "value":
        return QueryCompilationResult(effective_constraints=constraints)
    if intent.category is None or intent.sub_category is None:
        return _clarification(constraints)
    reference = catalog.price_reference(intent.category, intent.sub_category)
    if reference is None:
        return _clarification(constraints)

    computed_cap = reference.value_price_cap
    explicit_max = constraints.max_price
    provisional_cap = (
        computed_cap if explicit_max is None else min(explicit_max, computed_cap)
    )
    applied = True
    skip_reason = None
    if constraints.min_price is not None and constraints.min_price > provisional_cap:
        applied = False
        skip_reason = "explicit_min_exceeds_computed_cap"
    else:
        constraints = constraints.model_copy(update={"max_price": provisional_cap})

    price_reference = PriceCompilationReference(
        category=reference.category,
        sub_category=reference.sub_category,
        sample_count=reference.sample_count,
        median_min_sku_price=reference.median_min_sku_price,
        multiplier=VALUE_PRICE_MULTIPLIER,
        computed_price_cap=computed_cap,
        applied=applied,
        skip_reason=skip_reason,
    )
    return QueryCompilationResult(
        effective_constraints=constraints,
        price_reference=price_reference,
    )


def _clarification(constraints: SearchConstraints) -> QueryCompilationResult:
    return QueryCompilationResult(
        effective_constraints=constraints,
        needs_clarification=True,
        clarification_message=CLARIFICATION_MESSAGE,
    )
