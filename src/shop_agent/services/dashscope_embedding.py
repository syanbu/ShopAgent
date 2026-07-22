import asyncio
from collections.abc import Sequence
from http import HTTPStatus
from math import isfinite
from numbers import Real
from typing import Any

import dashscope  # type: ignore[import-untyped]

from shop_agent.config import Settings
from shop_agent.errors import ServiceError


class DashScopeEmbedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        dashscope.base_http_api_url = settings.dashscope_sdk_base_url

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, text_type="document")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], text_type="query")
        return vectors[0]

    async def _embed(
        self, texts: Sequence[str], *, text_type: str
    ) -> list[list[float]]:
        if not texts:
            return []
        try:
            response: Any = await asyncio.to_thread(
                dashscope.TextEmbedding.call,
                api_key=self._settings.dashscope_api_key,
                model=self._settings.embedding_model,
                input=list(texts),
                dimension=self._settings.embedding_dimension,
                text_type=text_type,
                output_type="dense",
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "EMBEDDING_UNAVAILABLE",
                "upstream embedding error",
                retryable=True,
            ) from exc
        try:
            if response.status_code != HTTPStatus.OK:
                raise ServiceError(
                    "EMBEDDING_UNAVAILABLE",
                    str(response.message),
                    retryable=True,
                )
            embeddings = response.output["embeddings"]
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise ValueError("embedding count mismatch")
            vectors_by_index: dict[int, list[float]] = {}
            for item in embeddings:
                if not isinstance(item, dict):
                    raise TypeError("embedding item is not an object")
                text_index = item["text_index"]
                vector = item["embedding"]
                if (
                    not isinstance(text_index, int)
                    or isinstance(text_index, bool)
                    or text_index < 0
                    or text_index >= len(texts)
                    or text_index in vectors_by_index
                ):
                    raise ValueError("invalid text_index")
                if (
                    not isinstance(vector, list)
                    or len(vector) != self._settings.embedding_dimension
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, Real)
                        or not isfinite(float(value))
                        for value in vector
                    )
                ):
                    raise ValueError("invalid embedding vector")
                vectors_by_index[text_index] = [float(value) for value in vector]
            return [vectors_by_index[index] for index in range(len(texts))]
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "EMBEDDING_UNAVAILABLE",
                "invalid embedding response",
                retryable=True,
            ) from exc
