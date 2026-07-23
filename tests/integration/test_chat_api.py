import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from shop_agent.api.app import create_app
from shop_agent.api.chat import _stream_events
from shop_agent.api.dependencies import ApiDependencies
from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.errors import ServiceError
from tests.integration.api_fakes import (
    FakeGraph,
    FakeReadinessProbe,
    parse_sse,
    product_event,
)


def _dependencies(
    sample_dataset_root: Path,
    graph: FakeGraph,
) -> ApiDependencies:
    return ApiDependencies(
        graph=graph,
        catalog=ProductCatalog.load(sample_dataset_root),
        settings=Settings(
            dashscope_api_key="test-key", dataset_root=sample_dataset_root
        ),
        readiness_probe=FakeReadinessProbe(),
        id_factory=iter(("request-fixed", "conversation-fixed")).__next__,
    )


async def _post(
    dependencies: ApiDependencies, payload: dict[str, Any]
) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(dependencies))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/chat/stream", json=payload)


@pytest.mark.asyncio
async def test_chat_stream_emits_start_products_text_and_end(
    sample_dataset_root: Path,
) -> None:
    graph = FakeGraph(
        [
            product_event("p1"),
            product_event("p2"),
            product_event("p3"),
            {"event": "text_delta", "data": {"delta": "推荐结果"}},
        ]
    )

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": "推荐耳机"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    assert [event.name for event in events] == [
        "message_start",
        "product",
        "product",
        "product",
        "text_delta",
        "message_end",
    ]
    assert events[0].data == {
        "request_id": "request-fixed",
        "conversation_id": "conversation-fixed",
    }
    assert events[-1].data == {
        "request_id": "request-fixed",
        "status": "completed",
    }
    assert graph.calls[0]["stream_mode"] == "custom"
    assert graph.calls[0]["version"] == "v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "   ", "x" * 4001])
async def test_chat_rejects_invalid_message(
    sample_dataset_root: Path, message: str
) -> None:
    graph = FakeGraph([])

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": message}
    )

    assert response.status_code == 422
    assert graph.calls == []


@pytest.mark.asyncio
async def test_chat_rejects_oversized_conversation_id(
    sample_dataset_root: Path,
) -> None:
    graph = FakeGraph([])

    response = await _post(
        _dependencies(sample_dataset_root, graph),
        {"conversation_id": "c" * 129, "message": "你好"},
    )

    assert response.status_code == 422
    assert graph.calls == []


@pytest.mark.asyncio
async def test_generation_failure_after_products_is_partial(
    sample_dataset_root: Path,
) -> None:
    graph = FakeGraph(
        [product_event()],
        error=ServiceError("GENERATION_FAILED", "upstream failed", retryable=True),
    )

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": "推荐耳机"}
    )

    events = parse_sse(response.text)
    assert [event.name for event in events] == [
        "message_start",
        "product",
        "error",
        "message_end",
    ]
    assert events[-2].data == {
        "code": "GENERATION_FAILED",
        "message": "upstream failed",
        "retryable": True,
    }
    assert events[-1].data["status"] == "partial"


@pytest.mark.asyncio
async def test_failure_before_products_is_failed(sample_dataset_root: Path) -> None:
    graph = FakeGraph(
        [],
        error=ServiceError("INTENT_PARSE_FAILED", "invalid intent", retryable=True),
    )

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": "推荐耳机"}
    )

    events = parse_sse(response.text)
    assert [event.name for event in events] == [
        "message_start",
        "error",
        "message_end",
    ]
    assert events[-2].data["code"] == "INTENT_PARSE_FAILED"
    assert events[-1].data["status"] == "failed"


@pytest.mark.asyncio
async def test_no_results_emits_no_product_and_one_end(
    sample_dataset_root: Path,
) -> None:
    graph = FakeGraph([{"event": "text_delta", "data": {"delta": "暂无结果"}}])

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": "不存在"}
    )

    names = [event.name for event in parse_sse(response.text)]
    assert "product" not in names
    assert names.count("message_end") == 1


@pytest.mark.asyncio
async def test_existing_conversation_id_is_forwarded(sample_dataset_root: Path) -> None:
    graph = FakeGraph([])

    response = await _post(
        _dependencies(sample_dataset_root, graph),
        {"conversation_id": "conversation-user", "message": "你好"},
    )

    events = parse_sse(response.text)
    assert events[0].data["conversation_id"] == "conversation-user"
    assert graph.calls[0]["state"]["conversation_id"] == "conversation-user"


@pytest.mark.asyncio
async def test_unhandled_graph_error_hides_details(sample_dataset_root: Path) -> None:
    graph = FakeGraph([], error=RuntimeError("secret absolute path"))

    response = await _post(
        _dependencies(sample_dataset_root, graph), {"message": "推荐耳机"}
    )

    events = parse_sse(response.text)
    assert events[-2].data == {
        "code": "INTERNAL_ERROR",
        "message": "internal service error",
        "retryable": False,
    }
    assert "secret absolute path" not in response.text


@pytest.mark.asyncio
async def test_client_cancellation_stops_without_end_event(
    sample_dataset_root: Path,
) -> None:
    dependencies = _dependencies(
        sample_dataset_root,
        FakeGraph([], error=asyncio.CancelledError()),
    )
    stream = _stream_events(
        dependencies,
        request_id="request-fixed",
        conversation_id="conversation-fixed",
        message="推荐耳机",
    )

    first = await anext(stream)
    assert first.event == "message_start"
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
