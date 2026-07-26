import os
import sys
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from shop_agent.config import Settings
from shop_agent.models.query import SearchConstraints
from shop_agent.services.qdrant_store import QdrantStore
from tests.qdrant_cleanup import cleanup_qdrant_test_collection


@pytest.mark.asyncio
async def test_qdrant_store_upserts_and_filters_by_brand_and_price() -> None:
    collection_name = f"test_product_text_chunks_{uuid4().hex}"
    assert collection_name.startswith("test_product_text_chunks_")
    assert collection_name != "product_text_chunks_v1"
    client = AsyncQdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        check_compatibility=False,
    )
    server_reachable = False
    try:
        try:
            await client.get_collections()
            server_reachable = True
        except Exception as exc:
            pytest.skip(f"local Qdrant unavailable: {exc}")

        settings = Settings(
            dashscope_api_key="test-key",
            qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_collection=collection_name,
            retrieval_chunk_limit=10,
        )
        store = QdrantStore(settings, client=client)
        await store.ensure_collection()
        points = [
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000001",
                vector=[1.0] + [0.0] * 1023,
                payload={
                    "chunk_id": "p1:summary",
                    "product_id": "p1",
                    "chunk_type": "product_summary",
                    "text": "品牌A耳机",
                    "source_path": "p1.json",
                    "category": "数码电子",
                    "sub_category": "蓝牙耳机",
                    "brand": "品牌A",
                    "min_sku_price": 399.0,
                    "max_sku_price": 599.0,
                },
            ),
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000002",
                vector=[0.9, 0.1] + [0.0] * 1022,
                payload={
                    "chunk_id": "p2:summary",
                    "product_id": "p2",
                    "chunk_type": "product_summary",
                    "text": "品牌B耳机",
                    "source_path": "p2.json",
                    "category": "数码电子",
                    "sub_category": "蓝牙耳机",
                    "brand": "品牌B",
                    "min_sku_price": 699.0,
                    "max_sku_price": 899.0,
                },
            ),
            models.PointStruct(
                id="00000000-0000-0000-0000-000000000003",
                vector=[0.95, 0.05] + [0.0] * 1022,
                payload={
                    "chunk_id": "p1:review:0",
                    "product_id": "p1",
                    "chunk_type": "user_review",
                    "text": "品牌A耳机评价",
                    "source_path": "p1.json",
                    "category": "数码电子",
                    "sub_category": "蓝牙耳机",
                    "brand": "品牌A",
                    "min_sku_price": 399.0,
                    "max_sku_price": 599.0,
                },
            ),
        ]
        await store.upsert(points)

        results = await store.search(
            [1.0] + [0.0] * 1023,
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(include_brands=["品牌A"], max_price=500),
        )

        assert results
        assert {result.product_id for result in results} == {"p1"}

        product_chunks = await store.fetch_product_chunks("p1")

        assert [chunk.chunk_id for chunk in product_chunks] == [
            "p1:summary",
            "p1:review:0",
        ]
        assert all(not hasattr(chunk, "score") for chunk in product_chunks)
    finally:
        await cleanup_qdrant_test_collection(
            client,
            collection_name,
            server_reachable=server_reachable,
            suppress_errors=sys.exc_info()[0] is not None,
        )
