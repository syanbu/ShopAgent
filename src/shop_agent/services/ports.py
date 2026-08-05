from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonProductMaterial,
)
from shop_agent.models.conversation import PendingClarification, QuerySnapshot
from shop_agent.models.query import EvidenceCondition, ParsedIntent
from shop_agent.models.retrieval import EvidenceAssessment, EvidenceChunk
from shop_agent.models.scenario import ScenarioSnapshot
from shop_agent.models.turn_query import TurnCandidateSummary, TurnQuery


class TurnContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_snapshot: QuerySnapshot | None = None
    active_task: Literal["product_search", "scenario_recommendation"] | None = None
    scenario_snapshot: ScenarioSnapshot | None = None
    recent_candidates: list[TurnCandidateSummary] = Field(default_factory=list)
    focused_product_id: str | None = None
    pending_clarification: PendingClarification | None = None


@runtime_checkable
class TurnQueryParser(Protocol):
    async def parse(self, message: str, context: TurnContext) -> TurnQuery:
        raise NotImplementedError


class IntentParser(Protocol):
    async def parse(self, message: str) -> ParsedIntent:
        raise NotImplementedError


class Embedder(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class Reranker(Protocol):
    async def rerank(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, float]]:
        raise NotImplementedError


class EvidenceMapper(Protocol):
    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        raise NotImplementedError


class ComparisonAssessor(Protocol):
    async def assess(
        self,
        question: str,
        dimension: str,
        materials: Sequence[ComparisonProductMaterial],
    ) -> ComparisonAssessment:
        raise NotImplementedError


class ResponseGenerator(Protocol):
    def stream(self, prompt: str) -> AsyncIterator[str]:
        raise NotImplementedError
