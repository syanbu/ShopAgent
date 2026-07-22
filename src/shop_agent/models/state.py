from typing import Literal, TypedDict

from shop_agent.errors import ErrorCode
from shop_agent.models.query import ParsedIntent
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
    retrieved_chunks: list[RetrievedChunk]
    candidates: list[ProductCandidate]
    validated_candidates: list[ValidatedCandidate]
    selected_products: list[SelectedProduct]
    response_mode: Literal["shopping", "non_shopping", "no_results"]
    response_text: str
    error_code: ErrorCode
