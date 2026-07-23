from collections.abc import Sequence
from typing import Protocol

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.product import Product
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import ProductCandidate, RetrievedChunk
from shop_agent.services.ports import Embedder, Reranker


class RetrievalStore(Protocol):
    async def search(
        self,
        query_vector: list[float],
        *,
        category: str | None,
        sub_category: str | None,
        constraints: SearchConstraints,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError


class RetrievalService:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: ProductCatalog,
        embedder: Embedder,
        store: RetrievalStore,
        reranker: Reranker,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    async def retrieve_chunks(self, intent: ParsedIntent) -> list[RetrievedChunk]:
        if intent.intent != "product_search" or intent.retrieval_query is None:
            raise ServiceError(
                "RETRIEVAL_UNAVAILABLE",
                "product search intent required",
                retryable=False,
            )
        query_vector = await self._embedder.embed_query(intent.retrieval_query)
        return await self._store.search(
            query_vector,
            category=intent.category,
            sub_category=intent.sub_category,
            constraints=intent.constraints,
        )

    def aggregate_products(
        self, chunks: Sequence[RetrievedChunk]
    ) -> list[ProductCandidate]:
        grouped: dict[str, list[RetrievedChunk]] = {}
        products: dict[str, Product] = {}
        for chunk in chunks:
            if chunk.product_id not in products:
                try:
                    products[chunk.product_id] = self._catalog.get(chunk.product_id)
                except KeyError as exc:
                    raise ServiceError(
                        "RETRIEVAL_UNAVAILABLE",
                        "retrieval returned an unknown product",
                        retryable=False,
                    ) from exc
            grouped.setdefault(chunk.product_id, []).append(chunk)

        candidates = [
            ProductCandidate(
                product=products[product_id],
                evidence=sorted(
                    evidence,
                    key=lambda chunk: (-chunk.score, chunk.chunk_id),
                )[:5],
            )
            for product_id, evidence in grouped.items()
        ]
        return candidates[: self._settings.rerank_product_limit]

    async def rerank_candidates(
        self, query: str, candidates: Sequence[ProductCandidate]
    ) -> list[ProductCandidate]:
        if not candidates:
            return []
        documents = [self._candidate_document(candidate) for candidate in candidates]
        ranking = await self._reranker.rerank(query, documents)
        ranked = [
            candidates[index].model_copy(update={"rerank_score": score})
            for index, score in ranking
        ]
        return sorted(
            ranked,
            key=lambda candidate: (
                -(candidate.rerank_score or 0.0),
                candidate.product.product_id,
            ),
        )

    @staticmethod
    def _candidate_document(candidate: ProductCandidate) -> str:
        product = candidate.product
        prices = [sku.price for sku in product.skus]
        evidence = "\n".join(chunk.text for chunk in candidate.evidence)
        return (
            f"title: {product.title}\n"
            f"brand: {product.brand}\n"
            f"category: {product.category}\n"
            f"sub_category: {product.sub_category}\n"
            f"sku_price_range: {min(prices)} - {max(prices)}\n"
            f"evidence:\n{evidence}"
        )
