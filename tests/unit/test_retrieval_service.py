from collections.abc import Sequence
from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.product import Product
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import RetrievedChunk
from shop_agent.services.retrieval import RetrievalService


class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("document embedding is not used during retrieval")

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.25] * 1024


class FakeStore:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        query_vector: list[float],
        *,
        category: str | None,
        sub_category: str | None,
        constraints: SearchConstraints,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_vector": query_vector,
                "category": category,
                "sub_category": sub_category,
                "constraints": constraints,
            }
        )
        return self.chunks


class FakeReranker:
    def __init__(self, ranking: list[tuple[int, float]]) -> None:
        self.ranking = ranking
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, float]]:
        self.calls.append((query, list(documents)))
        return self.ranking


def _settings(dataset_root: Path, *, product_limit: int = 10) -> Settings:
    return Settings(
        dashscope_api_key="test-key",
        dataset_root=dataset_root,
        rerank_product_limit=product_limit,
    )


def _intent() -> ParsedIntent:
    return ParsedIntent(
        schema_version=1,
        intent="product_search",
        retrieval_query="适合通勤的蓝牙耳机",
        category="数码电子",
        sub_category="蓝牙耳机",
        constraints=SearchConstraints(max_price=500),
    )


def _chunk(
    product_id: str,
    index: int,
    score: float,
    *,
    text: str | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{product_id}:chunk:{index}",
        point_id=f"00000000-0000-0000-0000-{index:012d}",
        product_id=product_id,
        chunk_type="product_summary" if index == 0 else "user_review",
        text=text or f"证据 {index}",
        source_path=f"2_数码电子/data/{product_id}.json",
        score=score,
    )


def _service(
    *,
    settings: Settings,
    catalog: ProductCatalog,
    chunks: list[RetrievedChunk],
    ranking: list[tuple[int, float]] | None = None,
) -> tuple[RetrievalService, FakeEmbedder, FakeStore, FakeReranker]:
    embedder = FakeEmbedder()
    store = FakeStore(chunks)
    reranker = FakeReranker(ranking or [])
    service = RetrievalService(
        settings=settings,
        catalog=catalog,
        embedder=embedder,
        store=store,
        reranker=reranker,
    )
    return service, embedder, store, reranker


@pytest.mark.asyncio
async def test_retrieve_chunks_embeds_query_and_forwards_structured_filters(
    sample_dataset_root: Path,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    service, embedder, store, _ = _service(
        settings=_settings(sample_dataset_root), catalog=catalog, chunks=[]
    )

    chunks = await service.retrieve_chunks(_intent())

    assert chunks == []
    assert embedder.queries == ["适合通勤的蓝牙耳机"]
    assert store.calls == [
        {
            "query_vector": [0.25] * 1024,
            "category": "数码电子",
            "sub_category": "蓝牙耳机",
            "constraints": SearchConstraints(max_price=500),
        }
    ]


def test_retrieval_groups_chunks_and_keeps_top_five_per_product(
    sample_dataset_root: Path,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    chunks = [
        _chunk("p_digital_001", index, score)
        for index, score in enumerate((0.20, 0.95, 0.60, 0.80, 0.50, 0.70))
    ]
    service, _, _, _ = _service(
        settings=_settings(sample_dataset_root), catalog=catalog, chunks=[]
    )

    candidates = service.aggregate_products(chunks)

    assert len(candidates) == 1
    assert candidates[0].product.product_id == "p_digital_001"
    assert [chunk.score for chunk in candidates[0].evidence] == [
        0.95,
        0.80,
        0.70,
        0.60,
        0.50,
    ]


def test_aggregate_products_rejects_unknown_catalog_product(
    sample_dataset_root: Path,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    service, _, _, _ = _service(
        settings=_settings(sample_dataset_root), catalog=catalog, chunks=[]
    )

    with pytest.raises(ServiceError) as error:
        service.aggregate_products([_chunk("missing-product", 0, 0.9)])

    assert error.value.code == "RETRIEVAL_UNAVAILABLE"
    assert error.value.message == "retrieval returned an unknown product"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_rerank_candidates_applies_indexes_and_builds_catalog_documents(
    sample_dataset_root: Path,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    service, _, _, reranker = _service(
        settings=_settings(sample_dataset_root),
        catalog=catalog,
        chunks=[],
        ranking=[(0, 0.73)],
    )
    candidates = service.aggregate_products(
        [_chunk("p_digital_001", 0, 0.9, text="通勤佩戴舒适")]
    )

    ranked = await service.rerank_candidates("通勤耳机", candidates)

    assert ranked[0].rerank_score == 0.73
    assert reranker.calls[0][0] == "通勤耳机"
    document = reranker.calls[0][1][0]
    assert all(
        value in document
        for value in (
            "测试蓝牙耳机",
            "测试品牌",
            "数码电子",
            "蓝牙耳机",
            "399.0",
            "599.0",
            "通勤佩戴舒适",
        )
    )


@pytest.mark.asyncio
async def test_rerank_candidates_returns_reranker_score_order(
    sample_dataset_root: Path,
    sample_product_data: dict[str, object],
) -> None:
    second = {**sample_product_data, "product_id": "p_digital_002", "title": "第二款"}
    products = {
        "p_digital_001": ProductCatalog.load(sample_dataset_root).get("p_digital_001"),
        "p_digital_002": Product.model_validate(second),
    }
    catalog = ProductCatalog(
        sample_dataset_root,
        products,
        {
            "p_digital_001": "2_数码电子/data/p_digital_001.json",
            "p_digital_002": "2_数码电子/data/p_digital_002.json",
        },
    )
    service, _, _, _ = _service(
        settings=_settings(sample_dataset_root),
        catalog=catalog,
        chunks=[],
        ranking=[(1, 0.91), (0, 0.52)],
    )
    candidates = service.aggregate_products(
        [_chunk("p_digital_001", 0, 0.9), _chunk("p_digital_002", 0, 0.8)]
    )

    ranked = await service.rerank_candidates("耳机", candidates)

    assert [candidate.product.product_id for candidate in ranked] == [
        "p_digital_002",
        "p_digital_001",
    ]
    assert [candidate.rerank_score for candidate in ranked] == [0.91, 0.52]
