"""Deterministically resolve references within the latest candidate batch."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shop_agent.catalog import ProductCatalog
from shop_agent.models import (
    CandidateReference,
    CategoryCandidate,
    CategoryReference,
    ConversationState,
    ProductReference,
)
from shop_agent.models.product import Product
from shop_agent.models.turn_query import ReferenceTarget


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


class CategoryResolution(BaseModel):
    """A trusted Catalog scope or a deterministic non-search outcome."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["resolved", "ambiguous", "unsupported"]
    scope: CategoryCandidate | None = None
    candidate_scopes: list[CategoryCandidate] = Field(default_factory=list)
    message: str | None = None

    @model_validator(mode="after")
    def validate_resolution_state(self) -> "CategoryResolution":
        if self.outcome == "resolved":
            if (
                self.scope is None
                or self.candidate_scopes
                or self.message is not None
            ):
                raise ValueError("resolved category requires only one scope")
        elif self.outcome == "ambiguous":
            if (
                self.scope is not None
                or len(self.candidate_scopes) < 2
                or self.message is None
            ):
                raise ValueError(
                    "ambiguous category requires multiple scopes and a message"
                )
        elif (
            self.scope is not None
            or self.candidate_scopes
            or self.message is None
        ):
            raise ValueError("unsupported category requires only a message")
        return self


def resolve_category_reference(
    reference: CategoryReference,
    catalog: ProductCatalog,
    *,
    allowed_scopes: Sequence[CategoryCandidate] | None = None,
) -> CategoryResolution:
    """Resolve model-understood category candidates inside the Catalog domain."""
    products = catalog.all()
    valid_scopes = {
        *((product.category, None) for product in products),
        *((product.category, product.sub_category) for product in products),
    }
    submitted = [
        (candidate.category, candidate.sub_category)
        for candidate in reference.candidates
    ]
    if any(scope not in valid_scopes for scope in submitted):
        return _unsupported_category(reference.surface_text)

    allowed = (
        None
        if allowed_scopes is None
        else {
            (candidate.category, candidate.sub_category)
            for candidate in allowed_scopes
        }
    )
    candidates = [
        candidate.model_copy(deep=True)
        for candidate in reference.candidates
        if allowed is None
        or (candidate.category, candidate.sub_category) in allowed
    ]
    if len(candidates) == 1:
        return CategoryResolution(outcome="resolved", scope=candidates[0])
    if len(candidates) > 1:
        labels = [
            candidate.sub_category or candidate.category
            for candidate in candidates
        ]
        return CategoryResolution(
            outcome="ambiguous",
            candidate_scopes=candidates,
            message=f"你说的是{'、'.join(labels)}中的哪一种？",
        )
    return _unsupported_category(reference.surface_text)


def _unsupported_category(surface_text: str) -> CategoryResolution:
    return CategoryResolution(
        outcome="unsupported",
        message=f"当前商品目录暂不支持“{surface_text}”，请换一种商品类型。",
    )


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
    allowed_ids = {candidate.product_id for candidate, _ in allowed_products}
    matches = [product_id for product_id in matches if product_id in allowed_ids]
    if len(matches) == 1:
        return ReferenceResolution(product_id=matches[0])
    return _clarification(allowed_products)


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
