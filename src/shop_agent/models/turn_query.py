"""Pydantic contracts for one parsed shopping turn."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shop_agent.models.query import CanonicalSkuKey, NumericConstraint


TurnIntent = Literal[
    "new_search",
    "refine_search",
    "switch_category",
    "more_results",
    "product_question",
    "clarification_answer",
    "non_shopping",
]
ReferenceTarget = Literal["product", "brand"]
ReferenceKind = Literal["ordinal", "demonstrative", "brand", "product_name"]
RelativePriceDirection = Literal["cheaper", "more_expensive"]
StructuredFactField = Literal["title", "brand", "category", "display_price", "sku"]
SlotOperationKind = Literal["replace", "add", "remove", "clear"]
SlotName = Literal[
    "category",
    "sub_category",
    "constraints.min_price",
    "constraints.max_price",
    "constraints.price_preference",
    "constraints.include_brands",
    "constraints.exclude_brands",
    "constraints.required_features",
    "constraints.excluded_features",
    "constraints.sku_constraints",
    "constraints.numeric_constraints",
]

_SCALAR_SLOTS = frozenset(
    {
        "category",
        "sub_category",
        "constraints.min_price",
        "constraints.max_price",
        "constraints.price_preference",
    }
)
_LIST_SLOTS = frozenset(
    {
        "constraints.include_brands",
        "constraints.exclude_brands",
        "constraints.required_features",
        "constraints.excluded_features",
    }
)


class TurnCandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    product_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    brand: str = Field(min_length=1)


class ProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: ReferenceTarget
    surface_text: str = Field(min_length=1)
    kind: ReferenceKind
    ordinal: int | None = Field(default=None, ge=1)
    brand: str | None = None
    product_name: str | None = None

    @model_validator(mode="after")
    def validate_reference_clue(self) -> "ProductReference":
        if (self.kind == "ordinal") != (self.ordinal is not None):
            raise ValueError("ordinal is required only for ordinal references")
        if (self.kind == "brand") != (self.brand is not None):
            raise ValueError("brand is required only for brand references")
        if (self.kind == "product_name") != (self.product_name is not None):
            raise ValueError("product_name is required only for product_name references")
        return self


class ProductQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    kind: Literal["structured", "semantic"]
    field: StructuredFactField | None = None

    @model_validator(mode="after")
    def validate_question_field(self) -> "ProductQuestion":
        if self.kind == "structured" and self.field is None:
            raise ValueError("structured product questions require a field")
        if self.kind == "semantic" and self.field is not None:
            raise ValueError("semantic product questions cannot specify a field")
        return self


class SemanticTermOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "remove", "clear"]
    value: str | None = None

    @field_validator("value")
    @classmethod
    def strip_value(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_value(self) -> "SemanticTermOperation":
        if self.operation == "clear" and self.value is not None:
            raise ValueError("clear semantic operations cannot include a value")
        if self.operation != "clear" and not self.value:
            raise ValueError("semantic add and remove operations require a value")
        return self


class SlotOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: SlotName
    operation: SlotOperationKind
    value: str | float | NumericConstraint | None = None
    sku_key: CanonicalSkuKey | None = None

    @model_validator(mode="after")
    def validate_slot_contract(self) -> "SlotOperation":
        if self.operation == "clear":
            if self.value is not None:
                raise ValueError("clear slot operations cannot include a value")
        elif self.value is None:
            raise ValueError("slot operations require a value")

        if self.slot in _SCALAR_SLOTS:
            if self.operation not in {"replace", "clear"}:
                raise ValueError("scalar slots only accept replace or clear")
            if self.sku_key is not None:
                raise ValueError("only SKU slots accept sku_key")
        elif self.slot in _LIST_SLOTS:
            if self.operation not in {"add", "remove", "clear"}:
                raise ValueError("list slots only accept add, remove, or clear")
            if self.sku_key is not None:
                raise ValueError("only SKU slots accept sku_key")
        elif self.slot == "constraints.sku_constraints":
            if self.operation not in {"add", "remove", "clear"}:
                raise ValueError("SKU slots only accept add, remove, or clear")
            if self.sku_key is None:
                raise ValueError("SKU slot operations require sku_key")
        else:
            if self.operation not in {"add", "remove", "clear"}:
                raise ValueError("numeric slots only accept add, remove, or clear")
            if self.sku_key is not None:
                raise ValueError("only SKU slots accept sku_key")
        return self


class TurnQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    intent: TurnIntent
    reference: ProductReference | None = None
    semantic_term_operations: list[SemanticTermOperation] = Field(default_factory=list)
    slot_operations: list[SlotOperation] = Field(default_factory=list)
    relative_price: RelativePriceDirection | None = None
    product_question: ProductQuestion | None = None
    cancel_pending: bool = False

    @model_validator(mode="after")
    def validate_turn_contract(self) -> "TurnQuery":
        if (self.intent == "product_question") != (self.product_question is not None):
            raise ValueError("product_question is required only for product_question intent")

        scalar_slots: set[str] = set()
        cleared_collection_slots: set[str] = set()
        for operation in self.slot_operations:
            if operation.slot in _SCALAR_SLOTS:
                if operation.slot in scalar_slots:
                    raise ValueError("a scalar slot can appear only once per turn")
                scalar_slots.add(operation.slot)
                continue
            if operation.operation == "clear":
                if operation.slot in cleared_collection_slots:
                    raise ValueError("a collection slot clear cannot be repeated")
                cleared_collection_slots.add(operation.slot)
            elif operation.slot in cleared_collection_slots:
                raise ValueError("a collection slot clear cannot coexist with another operation")

        for slot in cleared_collection_slots:
            operation_count = sum(
                operation.slot == slot for operation in self.slot_operations
            )
            if operation_count != 1:
                raise ValueError("a collection slot clear cannot coexist with another operation")
        return self
