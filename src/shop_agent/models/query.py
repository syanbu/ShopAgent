from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SearchConstraints(BaseModel):
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    include_brands: list[str] = Field(default_factory=list)
    exclude_brands: list[str] = Field(default_factory=list)
    required_features: list[str] = Field(default_factory=list)
    excluded_features: list[str] = Field(default_factory=list)


class ParsedIntent(BaseModel):
    schema_version: Literal[1]
    intent: Literal["product_search", "non_shopping"]
    retrieval_query: str | None
    category: str | None
    sub_category: str | None
    constraints: SearchConstraints = Field(default_factory=SearchConstraints)

    @model_validator(mode="after")
    def validate_route_fields(self) -> "ParsedIntent":
        if self.intent == "product_search" and not self.retrieval_query:
            raise ValueError("product_search requires retrieval_query")
        if self.intent == "non_shopping" and self.retrieval_query is not None:
            raise ValueError("non_shopping cannot include retrieval_query")
        return self
