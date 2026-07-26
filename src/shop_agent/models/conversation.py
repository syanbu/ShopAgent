"""Persistent, product-reference-only conversation models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.turn_query import TurnQuery


def _normalize_product_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("product IDs must be strings")
    normalized = value.strip()
    if not normalized:
        raise ValueError("product IDs cannot be blank")
    return normalized


def _normalize_product_ids(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("product ID collections must be lists or tuples")
    normalized = [_normalize_product_id(value) for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError("product IDs must be unique")
    return normalized


class QuerySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    sub_category: str | None = None
    semantic_terms: list[str] = Field(default_factory=list)
    constraints: SearchConstraints = Field(default_factory=SearchConstraints)

    def to_parsed_intent(self) -> ParsedIntent:
        terms = list(
            dict.fromkeys(
                [
                    self.sub_category or self.category or "商品",
                    *self.semantic_terms,
                    *self.constraints.required_features,
                ]
            )
        )
        return ParsedIntent(
            schema_version=1,
            intent="product_search",
            retrieval_query="、".join(terms),
            category=self.category,
            sub_category=self.sub_category,
            constraints=self.constraints,
        )


class CandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    product_id: str = Field(min_length=1)
    display_price: float = Field(ge=0)

    @field_validator("product_id", mode="before")
    @classmethod
    def normalize_product_id(cls, value: object) -> str:
        return _normalize_product_id(value)


class PendingClarification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ambiguous_reference", "missing_context", "condition_conflict"]
    candidate_product_ids: tuple[str, ...] = ()
    suspended_turn_query: TurnQuery
    attempt_count: int = Field(default=1, ge=1, le=2)

    @field_validator("candidate_product_ids", mode="before")
    @classmethod
    def normalize_candidate_product_ids(
        cls,
        value: list[str] | tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(_normalize_product_ids(value))


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    conversation_id: str = Field(min_length=1, max_length=128)
    query_snapshot: QuerySnapshot | None = None
    recent_candidates: list[CandidateReference] = Field(default_factory=list)
    focused_product_id: str | None = None
    seen_product_ids: list[str] = Field(default_factory=list)
    pending_clarification: PendingClarification | None = None

    @field_validator("focused_product_id", mode="before")
    @classmethod
    def normalize_focused_product_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_product_id(value)

    @field_validator("seen_product_ids", mode="before")
    @classmethod
    def normalize_seen_product_ids(cls, value: object) -> list[str]:
        try:
            return _normalize_product_ids(value)
        except ValueError as error:
            if str(error) == "product IDs must be unique":
                raise ValueError("seen product IDs must be unique") from error
            raise

    @model_validator(mode="after")
    def validate_persisted_references(self) -> "ConversationState":
        ranks = [candidate.rank for candidate in self.recent_candidates]
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("recent candidate ranks must be unique and contiguous")
        candidate_ids = [candidate.product_id for candidate in self.recent_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("recent candidate product IDs must be unique")
        candidate_id_set = set(candidate_ids)
        if not candidate_id_set.issubset(set(self.seen_product_ids)):
            raise ValueError("recent candidates must be included in seen product IDs")
        if (
            self.focused_product_id is not None
            and self.focused_product_id not in candidate_id_set
        ):
            raise ValueError("focused product must be in recent candidates")
        clarification = self.pending_clarification
        if clarification is not None and not set(
            clarification.candidate_product_ids
        ).issubset(candidate_id_set):
            raise ValueError("clarification candidates must be recent candidates")
        return self


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ConversationState
    version: int = Field(ge=1)
