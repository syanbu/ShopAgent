from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from fastapi import Depends, Request

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.models.state import ShoppingState
from shop_agent.services.dashscope_chat import (
    DashScopeEvidenceMapper,
    DashScopeResponseGenerator,
    DashScopeTurnQueryParser,
)
from shop_agent.services.conversation_repository import SqliteConversationRepository
from shop_agent.services.dashscope_embedding import DashScopeEmbedder
from shop_agent.services.dashscope_rerank import DashScopeReranker
from shop_agent.services.evidence import EvidenceService
from shop_agent.services.qdrant_store import QdrantStore
from shop_agent.services.retrieval import RetrievalService
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph


class GraphRunner(Protocol):
    def astream(
        self,
        state: ShoppingState,
        *,
        stream_mode: Literal["custom"],
        version: Literal["v2"],
    ) -> AsyncIterator[Any]: ...


class ReadinessProbe(Protocol):
    async def collection_ready(self) -> bool: ...


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    graph: GraphRunner
    catalog: ProductCatalog
    settings: Settings
    readiness_probe: ReadinessProbe
    id_factory: Callable[[], str] = _new_id


def get_dependencies(request: Request) -> ApiDependencies:
    return request.app.state.dependencies


Dependencies = Annotated[ApiDependencies, Depends(get_dependencies)]


def build_api_dependencies(settings: Settings | None = None) -> ApiDependencies:
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    catalog = ProductCatalog.load(resolved_settings.dataset_root)
    store = QdrantStore(resolved_settings)
    retrieval = RetrievalService(
        settings=resolved_settings,
        catalog=catalog,
        embedder=DashScopeEmbedder(resolved_settings),
        store=store,
        reranker=DashScopeReranker(resolved_settings),
    )
    evidence = EvidenceService(
        catalog=catalog,
        mapper=DashScopeEvidenceMapper(resolved_settings),
    )
    graph = build_graph(
        WorkflowDependencies(
            turn_query_parser=DashScopeTurnQueryParser(
                resolved_settings,
                categories=list(
                    dict.fromkeys(product.category for product in catalog.all())
                ),
                sub_categories=list(
                    dict.fromkeys(product.sub_category for product in catalog.all())
                ),
                category_pairs=list(
                    dict.fromkeys(
                        (product.category, product.sub_category)
                        for product in catalog.all()
                    )
                ),
                brands=catalog.brands(),
                sku_taxonomy=catalog.sku_taxonomy(),
            ),
            conversation_repository=SqliteConversationRepository(
                resolved_settings.conversation_db_path
            ),
            retrieval_service=retrieval,
            evidence_service=evidence,
            response_generator=DashScopeResponseGenerator(resolved_settings),
            catalog=catalog,
            settings=resolved_settings,
        )
    )
    return ApiDependencies(
        graph=graph,
        catalog=catalog,
        settings=resolved_settings,
        readiness_probe=store,
    )
