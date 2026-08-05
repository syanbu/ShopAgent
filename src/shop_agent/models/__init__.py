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
from shop_agent.models.scenario import (
    CatalogScope,
    ScenarioBundleItem,
    ScenarioRequest,
    ScenarioSlotSpec,
    ScenarioSnapshot,
    SolutionRecipe,
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
    "CatalogScope",
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
    "ScenarioBundleItem",
    "ScenarioRequest",
    "ScenarioSlotSpec",
    "ScenarioSnapshot",
    "SemanticTermOperation",
    "SlotOperation",
    "SolutionRecipe",
    "TurnCandidateSummary",
    "TurnQuery",
]
