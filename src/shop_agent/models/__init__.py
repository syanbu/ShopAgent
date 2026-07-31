"""Pydantic schemas for the shopping workflow."""

from shop_agent.models.conversation import (
    CandidateReference,
    ConversationRecord,
    ConversationState,
    PendingClarification,
    QuerySnapshot,
)
from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonEvidence,
    ComparisonProductFinding,
    ComparisonProductMaterial,
)
from shop_agent.models.turn_query import (
    CategoryCandidate,
    CategoryReference,
    ComparisonCandidateMatch,
    ProductComparison,
    ProductQuestion,
    ProductReference,
    ReferenceCandidateMatch,
    SemanticTermOperation,
    SlotOperation,
    TurnCandidateSummary,
    TurnQuery,
)

__all__ = [
    "CandidateReference",
    "CategoryCandidate",
    "CategoryReference",
    "ComparisonAssessment",
    "ComparisonCandidateMatch",
    "ComparisonEvidence",
    "ComparisonProductFinding",
    "ComparisonProductMaterial",
    "ConversationRecord",
    "ConversationState",
    "PendingClarification",
    "ProductQuestion",
    "ProductComparison",
    "ProductReference",
    "ReferenceCandidateMatch",
    "QuerySnapshot",
    "SemanticTermOperation",
    "SlotOperation",
    "TurnCandidateSummary",
    "TurnQuery",
]
