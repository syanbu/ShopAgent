from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SearchConstraints(BaseModel):
    min_price: float | None = Field(
        default=None,
        ge=0,
        description="用户明确表达的最低可接受 SKU 价格；未表达最低价时为 null。",
    )
    max_price: float | None = Field(
        default=None,
        ge=0,
        description="用户明确表达的最高可接受 SKU 价格；未表达最高价时为 null。",
    )
    price_preference: Literal["value"] | None = Field(
        default=None,
        description="用户明确表达性价比价格偏好时为 value，否则为 null。",
    )
    include_brands: list[str] = Field(
        default_factory=list,
        description="用户明确要求包含的品牌；未指定品牌时为空数组。",
    )
    exclude_brands: list[str] = Field(
        default_factory=list,
        description="用户明确排除的品牌；未排除品牌时为空数组。",
    )
    required_features: list[str] = Field(
        default_factory=list,
        description="商品必须具备的场景、功能或属性；未提出时为空数组。",
    )
    excluded_features: list[str] = Field(
        default_factory=list,
        description="商品不得具备的场景、功能或属性；未提出时为空数组。",
    )

    @model_validator(mode="after")
    def validate_price_range(self) -> "SearchConstraints":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        return self


class CategoryPriceReference(BaseModel):
    category: str
    sub_category: str
    sample_count: int = Field(ge=1)
    median_min_sku_price: float = Field(ge=0)
    value_price_cap: float = Field(ge=0)


class PriceCompilationReference(BaseModel):
    category: str
    sub_category: str
    sample_count: int = Field(ge=1)
    median_min_sku_price: float = Field(ge=0)
    multiplier: float = Field(gt=0)
    computed_price_cap: float = Field(ge=0)
    applied: bool
    skip_reason: str | None = None


class QueryCompilationResult(BaseModel):
    effective_constraints: SearchConstraints
    price_reference: PriceCompilationReference | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None


class ParsedIntent(BaseModel):
    schema_version: Literal[1] = Field(description="固定为 1。")
    intent: Literal["product_search", "non_shopping"] = Field(
        description="商品搜索使用 product_search，其他输入使用 non_shopping。"
    )
    retrieval_query: str | None = Field(
        description=(
            "面向向量检索的商品、场景和正向需求；不重复价格、品牌或排除条件。"
            "product_search 时必须非空，non_shopping 时必须为 null。"
        )
    )
    category: str | None = Field(
        description="商品一级类目；无法映射到可用目录时为 null。"
    )
    sub_category: str | None = Field(
        description="商品二级类目；无法映射到可用目录时为 null。"
    )
    constraints: SearchConstraints = Field(
        default_factory=SearchConstraints,
        description="用户明确表达的结构化搜索约束。",
    )

    @model_validator(mode="after")
    def validate_route_fields(self) -> "ParsedIntent":
        if self.intent == "product_search" and not self.retrieval_query:
            raise ValueError("product_search requires retrieval_query")
        if self.intent == "non_shopping" and self.retrieval_query is not None:
            raise ValueError("non_shopping cannot include retrieval_query")
        return self
