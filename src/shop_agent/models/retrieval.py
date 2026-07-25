from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shop_agent.models.product import Product


ChunkType = Literal["product_summary", "official_faq", "user_review"]


class EvidenceChunk(BaseModel):
    chunk_id: str
    point_id: str
    product_id: str
    chunk_type: ChunkType
    text: str
    source_path: str


class RetrievedChunk(EvidenceChunk):
    score: float


class ProductCandidate(BaseModel):
    product: Product
    evidence: list[RetrievedChunk]
    rerank_score: float | None = None


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str
    status: Literal["supported", "contradicted", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def decisive_status_requires_evidence(self) -> "EvidenceCheck":
        if self.status == "supported" and not self.evidence_ids:
            raise ValueError("supported check requires evidence")
        if self.status == "contradicted" and not self.evidence_ids:
            raise ValueError("contradicted check requires evidence")
        return self


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    checks: list[EvidenceCheck]


class ValidatedCandidate(BaseModel):
    candidate: ProductCandidate
    assessment: EvidenceAssessment
    eligible: bool
    rejection_reasons: list[str] = Field(default_factory=list)


class SelectedProduct(BaseModel):
    product_id: str
    rerank_score: float
    evidence_ids: list[str]
    decision_reasons: list[str]
    matched_sku_ids: list[str]
