from typing import Literal

from pydantic import BaseModel

from shop_agent.errors import ErrorCode
from shop_agent.models.product import Sku


class MessageStartData(BaseModel):
    request_id: str
    conversation_id: str


class ProductEventData(BaseModel):
    rank: int
    product_id: str
    title: str
    brand: str
    base_price: float
    display_price: float
    matched_skus: list[Sku]
    image_url: str | None


class TextDeltaData(BaseModel):
    delta: str


class ErrorData(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool


class MessageEndData(BaseModel):
    request_id: str
    status: Literal["completed", "partial", "failed"]
