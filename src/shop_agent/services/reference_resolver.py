"""Deterministically resolve references within the latest candidate batch."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shop_agent.catalog import ProductCatalog
from shop_agent.models import CandidateReference, ConversationState, ProductReference
from shop_agent.models.product import Product


class ReferenceResolution(BaseModel):
    """The resolved product or brand, or a safe request for clarification."""

    model_config = ConfigDict(extra="forbid")

    product_id: str | None = None
    brand: str | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None
    candidate_product_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_resolution_state(self) -> "ReferenceResolution":
        has_resolution = self.product_id is not None or self.brand is not None
        if self.product_id is not None and self.brand is not None:
            raise ValueError("a resolution cannot contain both product and brand")
        if self.needs_clarification:
            if has_resolution:
                raise ValueError("a clarification cannot contain a resolution")
            if self.clarification_message is None:
                raise ValueError("a clarification requires a message")
        elif self.clarification_message is not None or self.candidate_product_ids:
            raise ValueError("only clarifications can include candidate details")
        return self


def resolve_reference(
    reference: ProductReference,
    state: ConversationState,
    catalog: ProductCatalog,
) -> ReferenceResolution:
    """Resolve only against products shown in the latest candidate batch."""
    latest_products = [
        (candidate, catalog.get(candidate.product_id))
        for candidate in state.recent_candidates
    ]

    if reference.target_type == "brand":
        return _resolve_brand_reference(reference, state, latest_products)

    matches = _resolve_product_matches(reference, state, latest_products)
    if len(matches) == 1:
        return ReferenceResolution(product_id=matches[0])
    return _clarification(latest_products)


def _resolve_brand_reference(
    reference: ProductReference,
    state: ConversationState,
    latest_products: list[tuple[CandidateReference, Product]],
) -> ReferenceResolution:
    if reference.kind == "brand" and reference.brand is not None:
        expected = _normalize_natural_language(reference.brand)
        brands = {
            product.brand
            for _, product in latest_products
            if _normalize_natural_language(product.brand) == expected
        }
    elif state.focused_product_id is not None:
        brands = {
            product.brand
            for candidate, product in latest_products
            if candidate.product_id == state.focused_product_id
        }
    else:
        brands = {product.brand for _, product in latest_products}

    if len(brands) == 1:
        return ReferenceResolution(brand=next(iter(brands)))
    return _clarification(latest_products)


def _resolve_product_matches(
    reference: ProductReference,
    state: ConversationState,
    latest_products: list[tuple[CandidateReference, Product]],
) -> list[str]:
    if reference.kind == "ordinal" and reference.ordinal is not None:
        return [
            candidate.product_id
            for candidate, _ in latest_products
            if candidate.rank == reference.ordinal
        ]
    if reference.kind == "brand" and reference.brand is not None:
        expected = _normalize_natural_language(reference.brand)
        return [
            product.product_id
            for _, product in latest_products
            if _normalize_natural_language(product.brand) == expected
        ]
    if reference.kind == "product_name" and reference.product_name is not None:
        expected = _normalize_natural_language(reference.product_name)
        return [
            product.product_id
            for _, product in latest_products
            if _normalize_natural_language(product.title) == expected
        ]
    if reference.kind == "demonstrative":
        if state.focused_product_id is not None:
            return [state.focused_product_id]
        if len(latest_products) == 1:
            return [latest_products[0][0].product_id]
    return []


def _clarification(
    latest_products: list[tuple[CandidateReference, Product]],
) -> ReferenceResolution:
    candidate_product_ids = [candidate.product_id for candidate, _ in latest_products]
    entries = [
        f"第{candidate.rank}款：{product.title}"
        for candidate, product in latest_products
    ]
    message = "请说明您想问的是哪款商品。"
    if entries:
        message = f"请确认您指的是哪款商品：{'；'.join(entries)}。"
    return ReferenceResolution(
        needs_clarification=True,
        clarification_message=message,
        candidate_product_ids=candidate_product_ids,
    )


def _normalize_natural_language(value: str) -> str:
    return value.strip().casefold()
