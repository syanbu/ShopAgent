from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import EvidenceAssessment, EvidenceChunk


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
        constraints: SearchConstraints,
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        raise NotImplementedError


class ResponseGenerator(Protocol):
    def stream(self, prompt: str) -> AsyncIterator[str]:
        raise NotImplementedError
