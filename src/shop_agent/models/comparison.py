"""Contracts for evidence-grounded comparison of recent product candidates."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ComparisonOutcome = Literal[
    "winner",
    "tie",
    "context_dependent",
    "insufficient_evidence",
]
ComparisonSourceType = Literal[
    "structured_facts",
    "product_summary",
    "official_faq",
    "user_review",
]


def _normalize_non_blank(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be blank")
    return normalized


class ComparisonEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    source_type: ComparisonSourceType
    content: str = Field(min_length=1)

    @field_validator("evidence_id", "content", mode="before")
    @classmethod
    def normalize_text(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "comparison evidence field")
        return _normalize_non_blank(value, field_name)


class ComparisonProductMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    evidence: list[ComparisonEvidence] = Field(min_length=1)

    @field_validator("product_id", "title", mode="before")
    @classmethod
    def normalize_text(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "comparison product field")
        return _normalize_non_blank(value, field_name)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "ComparisonProductMaterial":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("comparison evidence IDs must be unique per product")
        return self


class ComparisonProductFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    supported_summary: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("product_id", "supported_summary", mode="before")
    @classmethod
    def normalize_text(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "comparison finding field")
        return _normalize_non_blank(value, field_name)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def normalize_evidence_ids(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("comparison evidence IDs must be a list")
        normalized = [
            _normalize_non_blank(item, "comparison evidence ID") for item in value
        ]
        if len(normalized) != len(set(normalized)):
            raise ValueError("comparison evidence IDs must be unique")
        return normalized

    @field_validator("limitations", mode="before")
    @classmethod
    def normalize_limitations(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("comparison limitations must be a list")
        return [_normalize_non_blank(item, "comparison limitation") for item in value]


class ComparisonAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    products: list[ComparisonProductFinding] = Field(min_length=2, max_length=3)
    outcome: ComparisonOutcome
    winner_product_id: str | None = None
    reason: str = Field(min_length=1)
    response_text: str = Field(min_length=1)

    @field_validator(
        "dimension",
        "winner_product_id",
        "reason",
        "response_text",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object, info: object) -> object:
        if value is None and getattr(info, "field_name", None) == "winner_product_id":
            return None
        field_name = getattr(info, "field_name", "comparison assessment field")
        return _normalize_non_blank(value, field_name)

    @model_validator(mode="after")
    def validate_winner_and_products(self) -> "ComparisonAssessment":
        product_ids = [item.product_id for item in self.products]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("comparison finding product IDs must be unique")
        if self.outcome == "winner":
            if self.winner_product_id not in set(product_ids):
                raise ValueError("winner must be one of the compared products")
            if any(not finding.evidence_ids for finding in self.products):
                raise ValueError(
                    "winner comparisons require evidence for every product"
                )
        elif self.winner_product_id is not None:
            raise ValueError("only winner outcomes may include a winner product")
        return self
