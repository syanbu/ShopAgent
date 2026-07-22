import asyncio
import json
from collections.abc import Sequence
from typing import Any

from qdrant_client.http import models

from shop_agent.catalog import ProductCatalog
from shop_agent.chunking import build_product_chunks
from shop_agent.config import Settings
from shop_agent.models.retrieval import EvidenceChunk
from shop_agent.services.dashscope_embedding import DashScopeEmbedder
from shop_agent.services.ports import Embedder
from shop_agent.services.qdrant_store import QdrantStore


INDEX_BATCH_SIZE = 20


async def index_catalog(
    settings: Settings,
    *,
    catalog: ProductCatalog | None = None,
    embedder: Embedder | None = None,
    store: QdrantStore | Any | None = None,
    chunks: Sequence[EvidenceChunk] | None = None,
) -> dict[str, int]:
    product_catalog = catalog or ProductCatalog.load(settings.dataset_root)
    document_embedder = embedder or DashScopeEmbedder(settings)
    qdrant_store = store or QdrantStore(settings)
    products = product_catalog.all()
    all_chunks = (
        list(chunks)
        if chunks is not None
        else [
            chunk
            for product in products
            for chunk in build_product_chunks(
                product, product_catalog.source_path(product.product_id)
            )
        ]
    )

    await qdrant_store.ensure_collection()
    upserted_points = 0
    for offset in range(0, len(all_chunks), INDEX_BATCH_SIZE):
        batch = all_chunks[offset : offset + INDEX_BATCH_SIZE]
        vectors = await document_embedder.embed_documents(
            [chunk.text for chunk in batch]
        )
        if len(vectors) != len(batch):
            raise ValueError("embedding count does not match chunk count")
        points: list[models.PointStruct] = []
        for chunk, vector in zip(batch, vectors, strict=True):
            product = product_catalog.get(chunk.product_id)
            sku_prices = [sku.price for sku in product.skus]
            points.append(
                models.PointStruct(
                    id=chunk.point_id,
                    vector=vector,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "product_id": chunk.product_id,
                        "chunk_type": chunk.chunk_type,
                        "text": chunk.text,
                        "category": product.category,
                        "sub_category": product.sub_category,
                        "brand": product.brand,
                        "min_sku_price": min(sku_prices),
                        "max_sku_price": max(sku_prices),
                        "source_path": chunk.source_path,
                    },
                )
            )
        await qdrant_store.upsert(points)
        upserted_points += len(points)

    return {
        "products": len(products),
        "chunks": len(all_chunks),
        "upserted_points": upserted_points,
    }


async def _run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    summary = await index_catalog(settings)
    print(json.dumps(summary, ensure_ascii=False))


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
