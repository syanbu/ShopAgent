from asyncio import CancelledError
from collections.abc import Hashable, Sequence
from math import ceil
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from shop_agent.models.query import SearchConstraints
from shop_agent.models.retrieval import EvidenceChunk, RetrievedChunk


_PRODUCT_KNOWLEDGE_MESSAGE = "product knowledge unavailable"


class QdrantStore:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._settings = settings
        client_options: dict[str, Any] = {}
        if urlparse(settings.qdrant_url).hostname in {"127.0.0.1", "::1", "localhost"}:
            client_options["trust_env"] = False
        self._client = client or AsyncQdrantClient(
            url=settings.qdrant_url,
            timeout=ceil(settings.qdrant_timeout_seconds),
            **client_options,
        )

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self._settings.qdrant_collection):
            await self._client.create_collection(
                collection_name=self._settings.qdrant_collection,
                vectors_config=models.VectorParams(
                    size=self._settings.embedding_dimension,
                    distance=models.Distance.COSINE,
                ),
            )
        schemas = {
            "product_id": models.PayloadSchemaType.KEYWORD,
            "category": models.PayloadSchemaType.KEYWORD,
            "sub_category": models.PayloadSchemaType.KEYWORD,
            "brand": models.PayloadSchemaType.KEYWORD,
            "chunk_type": models.PayloadSchemaType.KEYWORD,
            "min_sku_price": models.PayloadSchemaType.FLOAT,
            "max_sku_price": models.PayloadSchemaType.FLOAT,
        }
        for field_name, field_schema in schemas.items():
            await self._client.create_payload_index(
                collection_name=self._settings.qdrant_collection,
                field_name=field_name,
                field_schema=field_schema,
            )

    async def collection_ready(self) -> bool:
        collection_name = self._settings.qdrant_collection
        if not await self._client.collection_exists(collection_name):
            return False
        info = await self._client.get_collection(collection_name)
        vectors = info.config.params.vectors
        return (
            (info.points_count or 0) > 0
            and isinstance(vectors, models.VectorParams)
            and vectors.size == self._settings.embedding_dimension
            and vectors.distance == models.Distance.COSINE
        )

    @staticmethod
    def build_filter(
        *,
        category: str | None,
        sub_category: str | None,
        constraints: SearchConstraints,
        excluded_product_ids: Sequence[str] = (),
    ) -> models.Filter:
        must: list[models.Condition] = []
        must_not: list[models.Condition] = []
        if category:
            must.append(
                models.FieldCondition(
                    key="category", match=models.MatchValue(value=category)
                )
            )
        if sub_category:
            must.append(
                models.FieldCondition(
                    key="sub_category", match=models.MatchValue(value=sub_category)
                )
            )
        if constraints.include_brands:
            must.append(
                models.FieldCondition(
                    key="brand",
                    match=models.MatchAny(any=constraints.include_brands),
                )
            )
        if constraints.exclude_brands:
            must_not.append(
                models.FieldCondition(
                    key="brand",
                    match=models.MatchAny(any=constraints.exclude_brands),
                )
            )
        normalized_exclusions = list(
            dict.fromkeys(
                product_id.strip()
                for product_id in excluded_product_ids
                if product_id.strip()
            )
        )
        if normalized_exclusions:
            must_not.append(
                models.FieldCondition(
                    key="product_id",
                    match=models.MatchAny(any=normalized_exclusions),
                )
            )
        if constraints.max_price is not None:
            must.append(
                models.FieldCondition(
                    key="min_sku_price",
                    range=models.Range(lte=constraints.max_price),
                )
            )
        if constraints.min_price is not None:
            must.append(
                models.FieldCondition(
                    key="max_sku_price",
                    range=models.Range(gte=constraints.min_price),
                )
            )
        return models.Filter(must=must, must_not=must_not)

    async def search(
        self,
        query_vector: list[float],
        *,
        category: str | None,
        sub_category: str | None,
        constraints: SearchConstraints,
        excluded_product_ids: Sequence[str] = (),
    ) -> list[RetrievedChunk]:
        try:
            response = await self._client.query_points(
                collection_name=self._settings.qdrant_collection,
                query=query_vector,
                query_filter=self.build_filter(
                    category=category,
                    sub_category=sub_category,
                    constraints=constraints,
                    excluded_product_ids=excluded_product_ids,
                ),
                with_payload=True,
                limit=self._settings.retrieval_chunk_limit,
            )
        except Exception as exc:
            raise ServiceError(
                "RETRIEVAL_UNAVAILABLE",
                "Qdrant transport error",
                retryable=True,
            ) from exc

        return self._validate_search_points(response.points)

    @staticmethod
    def _validate_search_points(
        points: Sequence[models.ScoredPoint],
    ) -> list[RetrievedChunk]:
        results: list[RetrievedChunk] = []
        for point in points:
            try:
                if not isinstance(point.payload, dict):
                    raise ValueError("payload is not an object")
                results.append(
                    RetrievedChunk.model_validate(
                        {
                            **point.payload,
                            "point_id": str(point.id),
                            "score": point.score,
                        }
                    )
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise ServiceError(
                    "RETRIEVAL_UNAVAILABLE",
                    "invalid Qdrant payload",
                    retryable=False,
                ) from exc
        return results

    async def fetch_product_chunks(self, product_id: str) -> list[EvidenceChunk]:
        normalized_product_id = _validate_product_id(product_id)
        offset: int | str | UUID | None = None
        forwarded_offsets: set[int | str | UUID] = set()
        chunks: list[EvidenceChunk] = []
        while True:
            if offset is not None:
                try:
                    forwarded_offsets.add(offset)
                except (ServiceError, CancelledError):
                    raise
                except Exception as exc:
                    raise ServiceError(
                        "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                        _PRODUCT_KNOWLEDGE_MESSAGE,
                        retryable=False,
                    ) from exc
            try:
                response = await self._client.scroll(
                    collection_name=self._settings.qdrant_collection,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="product_id",
                                match=models.MatchValue(value=normalized_product_id),
                            )
                        ]
                    ),
                    limit=64,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except (ServiceError, CancelledError):
                raise
            except Exception as exc:
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=True,
                ) from exc
            try:
                points, next_offset = response
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=False,
                ) from exc
            chunks.extend(self._validate_product_points(points))
            if next_offset is None:
                return chunks
            if isinstance(next_offset, bool) or not isinstance(
                next_offset, (int, str, UUID)
            ):
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=False,
                )
            if not isinstance(next_offset, Hashable):
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=False,
                )
            try:
                repeated_offset = next_offset in forwarded_offsets
            except (ServiceError, CancelledError):
                raise
            except Exception as exc:
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=False,
                ) from exc
            if repeated_offset:
                raise ServiceError(
                    "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                    _PRODUCT_KNOWLEDGE_MESSAGE,
                    retryable=False,
                )
            offset = next_offset

    @staticmethod
    def _validate_product_points(
        points: object,
    ) -> list[EvidenceChunk]:
        if isinstance(points, (str, bytes, bytearray)) or not isinstance(
            points, Sequence
        ):
            raise ServiceError(
                "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                _PRODUCT_KNOWLEDGE_MESSAGE,
                retryable=False,
            )
        chunks: list[EvidenceChunk] = []
        try:
            for point in points:
                if not isinstance(point.payload, dict):
                    raise ValueError("payload is not an object")
                chunks.append(
                    EvidenceChunk.model_validate(
                        {
                            **point.payload,
                            "point_id": str(point.id),
                        }
                    )
                )
        except (ServiceError, CancelledError):
            raise
        except Exception as exc:
            raise ServiceError(
                "PRODUCT_KNOWLEDGE_UNAVAILABLE",
                _PRODUCT_KNOWLEDGE_MESSAGE,
                retryable=False,
            ) from exc
        return chunks

    async def upsert(self, points: Sequence[models.PointStruct]) -> None:
        await self._client.upsert(
            collection_name=self._settings.qdrant_collection,
            points=points,
            wait=True,
        )


def _validate_product_id(product_id: str) -> str:
    if not isinstance(product_id, str) or not product_id.strip():
        raise ValueError("product_id must be a non-empty string")
    return product_id.strip()
