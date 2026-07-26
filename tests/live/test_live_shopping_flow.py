import os
from collections.abc import Sequence

import httpx
import pytest

from shop_agent.api.app import create_app
from shop_agent.api.dependencies import ApiDependencies
from shop_agent.catalog import ProductCatalog
from shop_agent.cli.index_products import index_catalog
from shop_agent.config import Settings
from shop_agent.models.conversation import (
    CandidateReference,
    ConversationState,
    QuerySnapshot,
)
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceChunk,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.models.turn_query import TurnCandidateSummary
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
from shop_agent.services.multi_turn_query_compiler import merge_turn_query
from shop_agent.services.ports import TurnContext
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.integration.api_fakes import ParsedSseEvent, parse_sse


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1",
        reason="set RUN_LIVE_TESTS=1 to call real services",
    ),
]


class InstrumentedRetrievalService:
    def __init__(self, delegate: RetrievalService) -> None:
        self.delegate = delegate
        self.retrieve_calls = 0
        self.aggregate_calls = 0
        self.rerank_calls = 0

    async def retrieve_chunks(
        self,
        intent: ParsedIntent,
        *,
        excluded_product_ids: Sequence[str] = (),
    ) -> list[RetrievedChunk]:
        self.retrieve_calls += 1
        return await self.delegate.retrieve_chunks(
            intent,
            excluded_product_ids=excluded_product_ids,
        )

    async def fetch_product_chunks(self, product_id: str) -> list[EvidenceChunk]:
        return await self.delegate.fetch_product_chunks(product_id)

    def aggregate_products(
        self, chunks: Sequence[RetrievedChunk]
    ) -> list[ProductCandidate]:
        self.aggregate_calls += 1
        return self.delegate.aggregate_products(chunks)

    async def rerank_candidates(
        self, query: str, candidates: Sequence[ProductCandidate]
    ) -> list[ProductCandidate]:
        self.rerank_calls += 1
        return await self.delegate.rerank_candidates(query, candidates)

    def reset_calls(self) -> None:
        self.retrieve_calls = 0
        self.aggregate_calls = 0
        self.rerank_calls = 0


class CapturingEvidenceService:
    def __init__(self, delegate: EvidenceService) -> None:
        self.delegate = delegate
        self.last_validated: list[ValidatedCandidate] = []

    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]:
        self.last_validated = await self.delegate.validate_candidates(
            candidates,
            constraints,
            category=category,
            sub_category=sub_category,
        )
        return self.last_validated

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]:
        return self.delegate.select_candidates(
            validated,
            limit,
            constraints=constraints,
        )


@pytest.mark.asyncio
async def test_live_single_turn_shopping_flow() -> None:
    settings = Settings()  # type: ignore[call-arg]
    if not settings.dashscope_api_key.strip():
        pytest.fail("DASHSCOPE_API_KEY is required for live tests")
    catalog = ProductCatalog.load(settings.dataset_root)
    turn_query_parser = DashScopeTurnQueryParser(
        settings,
        categories=[product.category for product in catalog.all()],
        sub_categories=[product.sub_category for product in catalog.all()],
        category_pairs=[
            (product.category, product.sub_category) for product in catalog.all()
        ],
        brands=catalog.brands(),
        sku_taxonomy=catalog.sku_taxonomy(),
    )
    await _assert_live_turn_query_parser_contracts(turn_query_parser, catalog)
    store = QdrantStore(settings)

    embedder = DashScopeEmbedder(settings)
    await index_catalog(
        settings,
        catalog=catalog,
        embedder=embedder,
        store=store,
    )
    if not await store.collection_ready():
        pytest.fail("configured Qdrant collection is not ready after indexing")
    retrieval = InstrumentedRetrievalService(
        RetrievalService(
            settings=settings,
            catalog=catalog,
            embedder=embedder,
            store=store,
            reranker=DashScopeReranker(settings),
        )
    )
    evidence = CapturingEvidenceService(
        EvidenceService(
            catalog=catalog,
            mapper=DashScopeEvidenceMapper(settings),
        )
    )
    graph = build_graph(
        WorkflowDependencies(
            turn_query_parser=turn_query_parser,
            conversation_repository=SqliteConversationRepository(
                settings.conversation_db_path
            ),
            retrieval_service=retrieval,
            evidence_service=evidence,
            response_generator=DashScopeResponseGenerator(settings),
            catalog=catalog,
            settings=settings,
        )
    )
    app = create_app(
        ApiDependencies(
            graph=graph,
            catalog=catalog,
            settings=settings,
            readiness_probe=store,
        )
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=settings.public_base_url,
        timeout=120,
    ) as client:
        shopping = await _chat(client, "推荐一款蓝牙耳机")
        _assert_completed(shopping)
        products = _product_events(shopping)
        assert 1 <= len(products) <= 3
        assert any(event.name == "text_delta" for event in shopping)
        _assert_catalog_facts(products, catalog, settings)

        retrieval.reset_calls()
        non_shopping = await _chat(client, "你好")
        _assert_completed(non_shopping)
        assert _product_events(non_shopping) == []
        assert (
            retrieval.retrieve_calls,
            retrieval.aggregate_calls,
            retrieval.rerank_calls,
        ) == (0, 0, 0)

        constrained = await _chat(client, "500 元以内，不要入耳式")
        _assert_completed(constrained)
        constrained_products = _product_events(constrained)
        assert all(event.data["display_price"] <= 500 for event in constrained_products)
        _assert_exclusion_evidence(constrained_products, evidence.last_validated)


async def _assert_live_turn_query_parser_contracts(
    parser: DashScopeTurnQueryParser,
    catalog: ProductCatalog,
) -> None:
    earphones = [
        product for product in catalog.all() if product.sub_category == "真无线耳机"
    ][:3]
    assert len(earphones) == 3
    snapshot = QuerySnapshot(
        category="数码电子",
        sub_category="真无线耳机",
        constraints=SearchConstraints(include_brands=["小米"], max_price=500),
    )
    candidates = [
        CandidateReference(
            rank=rank,
            product_id=product.product_id,
            display_price=min(sku.price for sku in product.skus),
        )
        for rank, product in enumerate(earphones, start=1)
    ]
    state = ConversationState(
        schema_version=1,
        conversation_id="live-parser-contract",
        query_snapshot=snapshot,
        recent_candidates=candidates,
        seen_product_ids=[product.product_id for product in earphones],
    )
    context = TurnContext(
        query_snapshot=snapshot,
        recent_candidates=[
            TurnCandidateSummary(
                rank=rank,
                product_id=product.product_id,
                title=product.title,
                brand=product.brand,
            )
            for rank, product in enumerate(earphones, start=1)
        ],
    )

    budget = await parser.parse("预算改成300", context)
    assert budget.intent == "refine_search"
    budget_result = merge_turn_query(budget, state, catalog)
    assert budget_result.needs_clarification is False
    assert budget_result.snapshot is not None
    assert budget_result.snapshot.constraints.max_price == 300

    exclude = await parser.parse("不要小米了", context)
    brand_mutations = [
        (operation.slot, operation.operation, operation.value)
        for operation in exclude.slot_operations
        if operation.slot
        in {"constraints.exclude_brands", "constraints.include_brands"}
    ]
    assert brand_mutations in (
        [("constraints.exclude_brands", "add", "小米")],
        [("constraints.include_brands", "remove", "小米")],
    )
    exclude_result = merge_turn_query(exclude, state, catalog)
    assert exclude_result.needs_clarification is False
    assert exclude_result.snapshot is not None
    assert "小米" not in exclude_result.snapshot.constraints.include_brands

    ordinal = await parser.parse("第二个怎么样", context)
    assert ordinal.intent == "product_question"
    assert ordinal.reference is not None
    assert ordinal.reference.kind == "ordinal"
    assert ordinal.reference.ordinal == 2

    brand = await parser.parse("那个小米的", context)
    assert brand.reference is not None
    assert brand.reference.kind == "brand"
    assert brand.reference.brand == "小米"

    switch = await parser.parse("再看看手机", context)
    assert any(
        operation.slot == "sub_category"
        and operation.operation == "replace"
        and operation.value == "智能手机"
        for operation in switch.slot_operations
    )
    switch_result = merge_turn_query(switch, state, catalog)
    assert switch_result.intent == "switch_category"
    assert switch_result.needs_clarification is False
    assert switch_result.snapshot == QuerySnapshot(
        category="数码电子",
        sub_category="智能手机",
    )


async def _chat(client: httpx.AsyncClient, message: str) -> list[ParsedSseEvent]:
    response = await client.post("/api/v1/chat/stream", json={"message": message})
    assert response.status_code == 200
    return parse_sse(response.text)


def _assert_completed(events: Sequence[ParsedSseEvent]) -> None:
    assert events[0].name == "message_start"
    assert events[-1].name == "message_end"
    assert events[-1].data["status"] == "completed"
    assert sum(event.name == "message_end" for event in events) == 1


def _product_events(events: Sequence[ParsedSseEvent]) -> list[ParsedSseEvent]:
    return [event for event in events if event.name == "product"]


def _assert_catalog_facts(
    events: Sequence[ParsedSseEvent],
    catalog: ProductCatalog,
    settings: Settings,
) -> None:
    for event in events:
        data = event.data
        product = catalog.get(data["product_id"])
        matched_skus = data["matched_skus"]
        assert data["title"] == product.title
        assert data["brand"] == product.brand
        assert data["base_price"] == product.base_price
        assert data["display_price"] == min(sku["price"] for sku in matched_skus)
        assert {sku["sku_id"] for sku in matched_skus}.issubset(
            {sku.sku_id for sku in product.skus}
        )
        assert data["image_url"] in {
            None,
            f"{settings.public_base_url.rstrip('/')}/api/v1/products/"
            f"{product.product_id}/image",
        }


def _assert_exclusion_evidence(
    events: Sequence[ParsedSseEvent],
    validated: Sequence[ValidatedCandidate],
) -> None:
    by_product = {
        item.candidate.product.product_id: item for item in validated if item.eligible
    }
    for event in events:
        item = by_product[event.data["product_id"]]
        known_ids = {chunk.chunk_id for chunk in item.candidate.evidence}
        checks = [
            check for check in item.assessment.checks if check.condition == "入耳式"
        ]
        assert len(checks) == 1
        assert checks[0].status == "supported"
        assert checks[0].evidence_ids
        assert set(checks[0].evidence_ids).issubset(known_ids)
