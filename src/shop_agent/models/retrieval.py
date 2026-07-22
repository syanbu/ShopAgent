from typing import Literal

from pydantic import BaseModel, Field

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
    condition: str
    status: Literal["supported", "contradicted", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)


class EvidenceAssessment(BaseModel):
    product_id: str
    checks: list[EvidenceCheck] = Field(default_factory=list)


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
