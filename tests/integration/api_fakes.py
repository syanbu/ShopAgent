import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from shop_agent.errors import ServiceError
from shop_agent.models.state import ShoppingState


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
