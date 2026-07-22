import asyncio
from collections.abc import Sequence
from http import HTTPStatus
from math import isfinite
from numbers import Real
from typing import Any

import dashscope  # type: ignore[import-untyped]

from shop_agent.config import Settings
from shop_agent.errors import ServiceError


class DashScopeReranker:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        dashscope.base_http_api_url = settings.dashscope_sdk_base_url

    async def rerank(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        try:
            response: Any = await asyncio.to_thread(
                dashscope.TextReRank.call,
                api_key=self._settings.dashscope_api_key,
                model=self._settings.rerank_model,
                query=query,
                documents=list(documents),
                top_n=len(documents),
                return_documents=False,
                instruct="Rank products by how well they satisfy the shopping request.",
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "RERANK_UNAVAILABLE",
                "upstream reranking error",
                retryable=True,
            ) from exc
        try:
            if response.status_code != HTTPStatus.OK:
                raise ServiceError(
                    "RERANK_UNAVAILABLE",
                    str(response.message),
                    retryable=True,
                )
            results = response.output["results"]
            if not isinstance(results, list) or len(results) != len(documents):
                raise ValueError("rerank result count mismatch")
            seen_indexes: set[int] = set()
            ranking: list[tuple[int, float]] = []
            for item in results:
                if not isinstance(item, dict):
                    raise TypeError("rerank item is not an object")
                index = item["index"]
                score = item["relevance_score"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or index < 0
                    or index >= len(documents)
                    or index in seen_indexes
                ):
                    raise ValueError("invalid rerank index")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, Real)
                    or not isfinite(float(score))
                ):
                    raise ValueError("invalid rerank score")
                seen_indexes.add(index)
                ranking.append((index, float(score)))
            return ranking
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "RERANK_UNAVAILABLE",
                "invalid rerank response",
                retryable=True,
            ) from exc
