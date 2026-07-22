from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client.http import models

from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.query import SearchConstraints
from shop_agent.services.qdrant_store import QdrantStore


def _settings() -> Settings:
    return Settings(dashscope_api_key="test-key", retrieval_chunk_limit=17)


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
        ],
    }


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
