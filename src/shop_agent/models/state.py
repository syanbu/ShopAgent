from typing import Literal, TypedDict

from shop_agent.errors import ErrorCode
from shop_agent.models.query import ParsedIntent, PriceCompilationReference, SearchConstraints
from shop_agent.models.retrieval import (
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)


class ShoppingState(TypedDict, total=False):
    request_id: str
    conversation_id: str
    user_message: str
    parsed_intent: ParsedIntent
    effective_constraints: SearchConstraints
    price_reference: PriceCompilationReference
    clarification_message: str
    retrieved_chunks: list[RetrievedChunk]
    candidates: list[ProductCandidate]
    validated_candidates: list[ValidatedCandidate]
    selected_products: list[SelectedProduct]
    response_mode: Literal["shopping", "non_shopping", "no_results", "clarification"]
    response_text: str
    error_code: ErrorCode
