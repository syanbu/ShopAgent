import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from qdrant_client.http import models

from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.query import SearchConstraints
from shop_agent.models.retrieval import EvidenceChunk
from shop_agent.services.qdrant_store import QdrantStore


class _BrokenPointSequence(Sequence[models.Record]):
    def __getitem__(self, index: int) -> models.Record:
        raise RuntimeError("private malformed page")

    def __len__(self) -> int:
        return 1


class _UnhashableInt(int):
    __hash__ = None  # type: ignore[assignment]


def _settings() -> Settings:
    return Settings(dashscope_api_key="test-key", retrieval_chunk_limit=17)


def test_local_qdrant_client_ignores_environment_proxy() -> None:
    with patch("shop_agent.services.qdrant_store.AsyncQdrantClient") as client_type:
        QdrantStore(_settings())

    assert client_type.call_args.kwargs["trust_env"] is False


def test_build_filter_maps_category_brand_and_price_constraints() -> None:
    query_filter = QdrantStore.build_filter(
        category="数码电子",
        sub_category="蓝牙耳机",
        constraints=SearchConstraints(
            min_price=200,
            max_price=500,
            include_brands=["品牌A", "品牌B"],
            exclude_brands=["品牌C"],
        ),
        excluded_product_ids=["p1", "p2", "p1"],
    )

    assert query_filter.model_dump(exclude_none=True) == {
        "must": [
            {"key": "category", "match": {"value": "数码电子"}},
            {"key": "sub_category", "match": {"value": "蓝牙耳机"}},
            {"key": "brand", "match": {"any": ["品牌A", "品牌B"]}},
            {"key": "min_sku_price", "range": {"lte": 500.0}},
            {"key": "max_sku_price", "range": {"gte": 200.0}},
        ],
        "must_not": [
            {"key": "brand", "match": {"any": ["品牌C"]}},
            {"key": "product_id", "match": {"any": ["p1", "p2"]}},
        ],
    }


def test_build_filter_omits_empty_product_exclusions() -> None:
    query_filter = QdrantStore.build_filter(
        category=None,
        sub_category=None,
        constraints=SearchConstraints(),
        excluded_product_ids=[],
    )

    assert query_filter.model_dump(exclude_none=True) == {
        "must": [],
        "must_not": [],
    }


def test_build_filter_ignores_blank_ids_and_preserves_case_and_order() -> None:
    query_filter = QdrantStore.build_filter(
        category=None,
        sub_category=None,
        constraints=SearchConstraints(),
        excluded_product_ids=[" p1 ", "", "P1", "   ", "p1"],
    )

    assert query_filter.model_dump(exclude_none=True)["must_not"] == [
        {"key": "product_id", "match": {"any": ["p1", "P1"]}}
    ]


@pytest.mark.asyncio
async def test_ensure_collection_creates_cosine_vectors_and_payload_indexes() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = False
    store = QdrantStore(_settings(), client=client)

    await store.ensure_collection()

    vector_config = client.create_collection.await_args.kwargs["vectors_config"]
    assert vector_config == models.VectorParams(
        size=1024, distance=models.Distance.COSINE
    )
    assert client.create_payload_index.await_count == 7
    schemas = {
        (call.kwargs["field_name"], call.kwargs["field_schema"])
        for call in client.create_payload_index.await_args_list
    }
    assert schemas == {
        ("product_id", models.PayloadSchemaType.KEYWORD),
        ("category", models.PayloadSchemaType.KEYWORD),
        ("sub_category", models.PayloadSchemaType.KEYWORD),
        ("brand", models.PayloadSchemaType.KEYWORD),
        ("chunk_type", models.PayloadSchemaType.KEYWORD),
        ("min_sku_price", models.PayloadSchemaType.FLOAT),
        ("max_sku_price", models.PayloadSchemaType.FLOAT),
    }


@pytest.mark.asyncio
async def test_ensure_collection_does_not_recreate_existing_collection() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True

    await QdrantStore(_settings(), client=client).ensure_collection()

    client.create_collection.assert_not_awaited()
    client.delete_collection.assert_not_called()


@pytest.mark.asyncio
async def test_collection_ready_requires_nonempty_matching_vector_config() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value = SimpleNamespace(
        points_count=1,
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=models.VectorParams(
                    size=1024,
                    distance=models.Distance.COSINE,
                )
            )
        ),
    )

    ready = await QdrantStore(_settings(), client=client).collection_ready()

    assert ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("points_count", "dimension"), [(0, 1024), (1, 768)])
async def test_collection_ready_rejects_empty_or_incompatible_collection(
    points_count: int,
    dimension: int,
) -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value = SimpleNamespace(
        points_count=points_count,
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                )
            )
        ),
    )

    ready = await QdrantStore(_settings(), client=client).collection_ready()

    assert ready is False


@pytest.mark.asyncio
async def test_search_validates_payload_and_uses_configured_limit() -> None:
    client = AsyncMock()
    client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                id="00000000-0000-0000-0000-000000000001",
                score=0.82,
                payload={
                    "chunk_id": "p1:summary",
                    "product_id": "p1",
                    "chunk_type": "product_summary",
                    "text": "商品摘要",
                    "source_path": "data/p1.json",
                },
            )
        ]
    )
    store = QdrantStore(_settings(), client=client)

    results = await store.search(
        [0.1] * 1024,
        category="数码电子",
        sub_category=None,
        constraints=SearchConstraints(max_price=500),
    )

    assert results[0].point_id == "00000000-0000-0000-0000-000000000001"
    assert results[0].score == 0.82
    kwargs = client.query_points.await_args.kwargs
    assert kwargs["with_payload"] is True
    assert kwargs["limit"] == 17


@pytest.mark.asyncio
async def test_search_rejects_invalid_payload_instead_of_skipping() -> None:
    client = AsyncMock()
    client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(id="point", score=0.9, payload={"product_id": "p1"})]
    )

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).search(
            [0.1] * 1024,
            category=None,
            sub_category=None,
            constraints=SearchConstraints(),
        )

    assert error.value.code == "RETRIEVAL_UNAVAILABLE"
    assert error.value.message == "invalid Qdrant payload"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_search_wraps_transport_failure_as_retryable() -> None:
    client = AsyncMock()
    client.query_points.side_effect = RuntimeError("connection refused")

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).search(
            [0.1] * 1024,
            category=None,
            sub_category=None,
            constraints=SearchConstraints(),
        )

    assert error.value.code == "RETRIEVAL_UNAVAILABLE"
    assert error.value.retryable is True


def _scroll_record(
    point_id: UUID,
    *,
    chunk_id: str,
    product_id: str = "p1",
) -> models.Record:
    return models.Record(
        id=point_id,
        payload={
            "chunk_id": chunk_id,
            "product_id": product_id,
            "chunk_type": "product_summary",
            "text": f"{chunk_id} 商品资料",
            "source_path": f"data/{product_id}.json",
        },
        vector=None,
    )


@pytest.mark.asyncio
async def test_fetch_product_chunks_scrolls_all_pages_in_order_without_scores() -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000001")
    second_id = UUID("00000000-0000-0000-0000-000000000002")
    next_offset = second_id
    client = AsyncMock()
    client.scroll.side_effect = [
        ([_scroll_record(first_id, chunk_id="p1:summary")], next_offset),
        ([_scroll_record(second_id, chunk_id="p1:faq:0")], None),
    ]
    store = QdrantStore(_settings(), client=client)

    results = await store.fetch_product_chunks("p1")

    assert all(isinstance(result, EvidenceChunk) for result in results)
    assert [result.chunk_id for result in results] == ["p1:summary", "p1:faq:0"]
    assert [result.point_id for result in results] == [str(first_id), str(second_id)]
    assert all(not hasattr(result, "score") for result in results)
    assert client.scroll.await_count == 2
    expected_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="product_id",
                match=models.MatchValue(value="p1"),
            )
        ]
    )
    first_call, second_call = client.scroll.await_args_list
    for call in (first_call, second_call):
        assert call.kwargs["collection_name"] == _settings().qdrant_collection
        assert call.kwargs["scroll_filter"] == expected_filter
        assert call.kwargs["limit"] == 64
        assert call.kwargs["with_payload"] is True
        assert call.kwargs["with_vectors"] is False
    assert first_call.kwargs["offset"] is None
    assert second_call.kwargs["offset"] is next_offset
    client.query_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_product_chunks_returns_empty_first_page() -> None:
    client = AsyncMock()
    client.scroll.return_value = ([], None)

    results = await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert results == []
    assert client.scroll.await_count == 1


@pytest.mark.asyncio
async def test_fetch_product_chunks_rejects_blank_product_id_before_scroll() -> None:
    client = AsyncMock()

    with pytest.raises(ValueError, match="product_id must be a non-empty string"):
        await QdrantStore(_settings(), client=client).fetch_product_chunks("   ")

    client.scroll.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_product_chunks_normalizes_malformed_payload_safely() -> None:
    client = AsyncMock()
    client.scroll.return_value = (
        [models.Record(id=1, payload={"product_id": "p1", "text": "private"})],
        None,
    )

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_fetch_product_chunks_normalizes_transport_failure_safely() -> None:
    client = AsyncMock()
    client.scroll.side_effect = RuntimeError("private Qdrant endpoint refused")

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is True
    assert "endpoint" not in str(error.value)


@pytest.mark.asyncio
async def test_fetch_product_chunks_rejects_repeated_forwarded_offset() -> None:
    client = AsyncMock()
    client.scroll.side_effect = [([], "cursor-a"), ([], "cursor-a")]

    with pytest.raises(ServiceError) as error:
        await asyncio.wait_for(
            QdrantStore(_settings(), client=client).fetch_product_chunks("p1"),
            timeout=0.25,
        )

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False
    assert client.scroll.await_count == 2


@pytest.mark.asyncio
async def test_fetch_product_chunks_rejects_offset_cycle() -> None:
    client = AsyncMock()
    client.scroll.side_effect = [
        ([], "cursor-a"),
        ([], "cursor-b"),
        ([], "cursor-a"),
    ]

    with pytest.raises(ServiceError) as error:
        await asyncio.wait_for(
            QdrantStore(_settings(), client=client).fetch_product_chunks("p1"),
            timeout=0.25,
        )

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False
    assert client.scroll.await_count == 3


@pytest.mark.asyncio
async def test_fetch_product_chunks_rejects_illegal_next_offset_type() -> None:
    client = AsyncMock()
    client.scroll.side_effect = [([], ["unhashable-offset"])]

    with pytest.raises(ServiceError) as error:
        await asyncio.wait_for(
            QdrantStore(_settings(), client=client).fetch_product_chunks("p1"),
            timeout=0.25,
        )

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False
    assert client.scroll.await_count == 1


@pytest.mark.asyncio
async def test_fetch_product_chunks_rejects_unhashable_typed_offset() -> None:
    client = AsyncMock()
    client.scroll.side_effect = [([], _UnhashableInt(7))]

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False
    assert client.scroll.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("points", [None, iter([]), _BrokenPointSequence()])
async def test_fetch_product_chunks_normalizes_malformed_points_container(
    points: object,
) -> None:
    client = AsyncMock()
    client.scroll.return_value = (points, None)

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert error.value.code == "PRODUCT_KNOWLEDGE_UNAVAILABLE"
    assert error.value.message == "product knowledge unavailable"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_fetch_product_chunks_preserves_existing_service_error() -> None:
    original = ServiceError("INTERNAL_ERROR", "already normalized", retryable=False)
    client = AsyncMock()
    client.scroll.side_effect = original

    with pytest.raises(ServiceError) as error:
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")

    assert error.value is original


@pytest.mark.asyncio
async def test_fetch_product_chunks_preserves_cancellation() -> None:
    client = AsyncMock()
    client.scroll.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await QdrantStore(_settings(), client=client).fetch_product_chunks("p1")
