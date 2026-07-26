from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceChunk,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.services.conversation_repository import ConversationRepository
from shop_agent.services.ports import ResponseGenerator, TurnQueryParser


class RetrievalOperations(Protocol):
    async def retrieve_chunks(
        self,
        intent: ParsedIntent,
        *,
        excluded_product_ids: Sequence[str] = (),
    ) -> list[RetrievedChunk]: ...

    async def fetch_product_chunks(self, product_id: str) -> list[EvidenceChunk]: ...

    def aggregate_products(
        self, chunks: Sequence[RetrievedChunk]
    ) -> list[ProductCandidate]: ...

    async def rerank_candidates(
        self, query: str, candidates: Sequence[ProductCandidate]
    ) -> list[ProductCandidate]: ...


class EvidenceOperations(Protocol):
    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]: ...

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]: ...


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class WorkflowDependencies:
    turn_query_parser: TurnQueryParser
    conversation_repository: ConversationRepository
    retrieval_service: RetrievalOperations
    evidence_service: EvidenceOperations
    response_generator: ResponseGenerator
    catalog: ProductCatalog
    settings: Settings
    id_factory: Callable[[], str] = _new_id
