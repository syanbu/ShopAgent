import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from shop_agent.api.dependencies import ApiDependencies
from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonProductFinding,
    ComparisonProductMaterial,
)
from shop_agent.models.conversation import ConversationRecord, ConversationState
from shop_agent.models.product import Product
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceAssessment,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.models.state import ShoppingState
from shop_agent.models.turn_query import TurnQuery
from shop_agent.services.conversation_repository import (
    ConversationRepository,
    SqliteConversationRepository,
)
from shop_agent.services.ports import TurnContext, TurnQueryParser
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph


class FakeGraph:
    def __init__(
        self,
        events: Sequence[dict[str, Any]],
        *,
        error: ServiceError | BaseException | None = None,
    ) -> None:
        self.events = list(events)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def astream(
        self,
        state: ShoppingState,
        *,
        stream_mode: Literal["custom"],
        version: Literal["v2"],
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(
            {"state": state, "stream_mode": stream_mode, "version": version}
        )
        for event in self.events:
            yield {"type": "custom", "ns": (), "data": event}
        if self.error is not None:
            raise self.error


class FakeReadinessProbe:
    def __init__(self, ready: bool = True, *, error: Exception | None = None) -> None:
        self.is_ready = ready
        self.error = error
        self.calls = 0

    async def collection_ready(self) -> bool:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.is_ready


@dataclass(frozen=True)
class ParsedSseEvent:
    name: str
    data: dict[str, Any]


def parse_sse(body: str) -> list[ParsedSseEvent]:
    events: list[ParsedSseEvent] = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, _, value = line.partition(":")
            fields[name] = value.lstrip()
        if "event" in fields:
            events.append(
                ParsedSseEvent(
                    name=fields["event"],
                    data=json.loads(fields["data"]),
                )
            )
    return events


def product_event(product_id: str = "p1", *, price: float = 400) -> dict[str, Any]:
    return {
        "event": "product",
        "data": {
            "rank": 1,
            "product_id": product_id,
            "title": "测试耳机",
            "brand": "测试品牌",
            "base_price": price,
            "display_price": price,
            "matched_skus": [],
            "image_url": f"http://test/api/v1/products/{product_id}/image",
        },
    }


class SequencedTurnQueryParser:
    def __init__(self, turns: Sequence[TurnQuery]) -> None:
        self._turns = iter(turns)
        self.calls: list[str] = []
        self.contexts: list[TurnContext] = []

    async def parse(self, message: str, context: TurnContext) -> TurnQuery:
        self.calls.append(message)
        self.contexts.append(context)
        return next(self._turns)


class FailingTurnQueryParser:
    def __init__(self, error: ServiceError, marker: str) -> None:
        self._error = error
        self._marker = marker

    async def parse(self, message: str, context: TurnContext) -> TurnQuery:
        raise self._error from RuntimeError(self._marker)


class FailingConversationRepository:
    def __init__(
        self,
        delegate: SqliteConversationRepository,
        *,
        load_error: ServiceError | None = None,
        save_error: ServiceError | None = None,
        marker: str,
    ) -> None:
        self._delegate = delegate
        self._load_error = load_error
        self._save_error = save_error
        self._marker = marker

    async def load(self, conversation_id: str) -> ConversationRecord | None:
        if self._load_error is not None:
            raise self._load_error from RuntimeError(self._marker)
        return await self._delegate.load(conversation_id)

    async def save(
        self,
        state: ConversationState,
        *,
        expected_version: int | None,
    ) -> ConversationRecord:
        if self._save_error is not None:
            raise self._save_error from RuntimeError(self._marker)
        return await self._delegate.save(state, expected_version=expected_version)


@dataclass(frozen=True)
class RetrievalCall:
    max_price: float | None
    excluded_product_ids: tuple[str, ...]


class DeterministicRetrievalService:
    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog
        self.calls: list[RetrievalCall] = []

    async def retrieve_chunks(
        self,
        intent: ParsedIntent,
        *,
        excluded_product_ids: Sequence[str] = (),
    ) -> list[RetrievedChunk]:
        exclusions = tuple(excluded_product_ids)
        self.calls.append(
            RetrievalCall(
                max_price=intent.constraints.max_price,
                excluded_product_ids=exclusions,
            )
        )
        return [
            RetrievedChunk(
                chunk_id=f"{product.product_id}:summary",
                point_id=f"00000000-0000-0000-0000-{index:012d}",
                product_id=product.product_id,
                chunk_type="product_summary",
                text=f"{product.title} 适合通勤",
                source_path=f"data/{product.product_id}.json",
                score=1 - index / 10,
            )
            for index, product in enumerate(self._catalog.all(), start=1)
            if product.product_id not in exclusions
            and (intent.category is None or product.category == intent.category)
            and (
                intent.sub_category is None
                or product.sub_category == intent.sub_category
            )
        ]

    async def fetch_product_chunks(self, product_id: str) -> list[Any]:
        raise ServiceError(
            "PRODUCT_KNOWLEDGE_UNAVAILABLE",
            "product knowledge unavailable",
            retryable=False,
        )

    def aggregate_products(
        self,
        chunks: Sequence[RetrievedChunk],
        *,
        max_evidence_chunks: int | None = 5,
    ) -> list[ProductCandidate]:
        by_product: dict[str, list[RetrievedChunk]] = {}
        for chunk in chunks:
            by_product.setdefault(chunk.product_id, []).append(chunk)
        return [
            ProductCandidate(
                product=product,
                evidence=(
                    by_product[product.product_id]
                    if max_evidence_chunks is None
                    else by_product[product.product_id][:max_evidence_chunks]
                ),
            )
            for product in self._catalog.all()
            if product.product_id in by_product
        ]

    async def rerank_candidates(
        self, query: str, candidates: Sequence[ProductCandidate]
    ) -> list[ProductCandidate]:
        return [
            candidate.model_copy(update={"rerank_score": 1 - index / 10})
            for index, candidate in enumerate(candidates)
        ]


class DeterministicEvidenceService:
    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog
        self.select_calls: list[SearchConstraints] = []

    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]:
        return [
            ValidatedCandidate(
                candidate=candidate,
                assessment=EvidenceAssessment(
                    product_id=candidate.product.product_id,
                    checks=[],
                ),
                eligible=(
                    (category is None or candidate.product.category == category)
                    and (
                        sub_category is None
                        or candidate.product.sub_category == sub_category
                    )
                    and (
                        not constraints.include_brands
                        or candidate.product.brand in constraints.include_brands
                    )
                    and candidate.product.brand not in constraints.exclude_brands
                    and bool(
                        self._catalog.matched_skus(
                            candidate.product.product_id,
                            constraints,
                        )
                    )
                ),
                rejection_reasons=(
                    []
                    if (
                        (category is None or candidate.product.category == category)
                        and (
                            sub_category is None
                            or candidate.product.sub_category == sub_category
                        )
                        and (
                            not constraints.include_brands
                            or candidate.product.brand
                            in constraints.include_brands
                        )
                        and candidate.product.brand
                        not in constraints.exclude_brands
                        and bool(
                            self._catalog.matched_skus(
                                candidate.product.product_id,
                                constraints,
                            )
                        )
                    )
                    else ["structured_conditions_not_satisfied"]
                ),
            )
            for candidate in candidates
        ]

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]:
        self.select_calls.append(constraints)
        selected: list[SelectedProduct] = []
        for candidate in validated:
            product_id = candidate.candidate.product.product_id
            matched = self._catalog.matched_skus(product_id, constraints)
            if candidate.eligible and matched:
                selected.append(
                    SelectedProduct(
                        product_id=product_id,
                        rerank_score=candidate.candidate.rerank_score or 0,
                        evidence_ids=[candidate.candidate.evidence[0].chunk_id],
                        decision_reasons=["test_selected"],
                        matched_sku_ids=[sku.sku_id for sku in matched],
                    )
                )
        return selected[:limit]


class DeterministicResponseGenerator:
    async def stream(self, prompt: str) -> AsyncIterator[str]:
        yield "测试回复"


class SequencedResponseGenerator:
    """Test-only response outcomes for exercising post-product failures."""

    def __init__(self, outcomes: Sequence[str | ServiceError]) -> None:
        self._outcomes = iter(outcomes)

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        outcome = next(self._outcomes)
        if isinstance(outcome, ServiceError):
            raise outcome
        yield outcome


class DeterministicComparisonAssessor:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, list[ComparisonProductMaterial]]
        ] = []

    async def assess(
        self,
        question: str,
        dimension: str,
        materials: Sequence[ComparisonProductMaterial],
    ) -> ComparisonAssessment:
        copied = [material.model_copy(deep=True) for material in materials]
        self.calls.append((question, dimension, copied))
        return ComparisonAssessment(
            dimension=dimension,
            products=[
                ComparisonProductFinding(
                    product_id=material.product_id,
                    evidence_ids=[material.evidence[0].evidence_id],
                    supported_summary=f"{material.title} 的{dimension}资料",
                    limitations=[],
                )
                for material in materials
            ],
            outcome="winner",
            winner_product_id=materials[0].product_id,
            reason="第一款在现有资料中更有优势。",
            response_text="第一款在现有资料中更有优势。",
        )


def compiled_chat_dependencies(
    tmp_path: Path,
    *,
    turns: Sequence[TurnQuery] = (),
    parser: TurnQueryParser | None = None,
    repository: ConversationRepository | None = None,
    response_generator: DeterministicResponseGenerator | SequencedResponseGenerator | None = None,
    catalog_override: ProductCatalog | None = None,
) -> tuple[
    ApiDependencies,
    SequencedTurnQueryParser,
    SqliteConversationRepository,
    DeterministicRetrievalService,
    DeterministicEvidenceService,
]:
    catalog = catalog_override or _catalog(tmp_path)
    settings = Settings(
        dashscope_api_key="test-key",
        dataset_root=tmp_path,
        conversation_db_path=tmp_path / "chat.sqlite3",
        final_product_limit=3,
        public_base_url="http://test",
    )
    sequenced_parser = SequencedTurnQueryParser(turns)
    sqlite_repository = SqliteConversationRepository(settings.conversation_db_path)
    retrieval = DeterministicRetrievalService(catalog)
    evidence = DeterministicEvidenceService(catalog)
    graph = build_graph(
        WorkflowDependencies(
            turn_query_parser=parser or sequenced_parser,
            conversation_repository=repository or sqlite_repository,
            retrieval_service=retrieval,
            evidence_service=evidence,
            response_generator=response_generator or DeterministicResponseGenerator(),
            catalog=catalog,
            settings=settings,
            comparison_assessor=DeterministicComparisonAssessor(),
        )
    )
    return (
        ApiDependencies(
            graph=graph,
            catalog=catalog,
            settings=settings,
            readiness_probe=FakeReadinessProbe(),
            id_factory=lambda: "generated-conversation-id",
        ),
        sequenced_parser,
        sqlite_repository,
        retrieval,
        evidence,
    )


def _catalog(
    root: Path,
    *,
    product_count: int = 3,
    category: str = "数码电子",
    sub_category: str = "蓝牙耳机",
) -> ProductCatalog:
    products = [
        Product.model_validate(
            {
                "product_id": f"p{index}",
                "title": f"测试蓝牙耳机 {index}",
                "brand": f"测试品牌 {index}",
                "category": category,
                "sub_category": sub_category,
                "base_price": 200.0 + index,
                "image_path": f"images/p{index}.jpg",
                "skus": [
                    {
                        "sku_id": f"p{index}-black",
                        "properties": {"颜色": "黑色"},
                        "price": 200.0 + index,
                    }
                ],
                "rag_knowledge": {
                    "marketing_description": "测试商品",
                    "official_faq": [],
                    "user_reviews": [],
                },
            }
        )
        for index in range(1, product_count + 1)
    ]
    return ProductCatalog(
        root,
        {product.product_id: product for product in products},
        {product.product_id: f"data/{product.product_id}.json" for product in products},
    )
