from pathlib import Path
from typing import Any

import pytest

from shop_agent.errors import ServiceError
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.unit.workflow_fakes import build_harness, initial_state


def _graph(harness: Any) -> Any:
    return build_graph(
        WorkflowDependencies(
            intent_parser=harness.parser,
            retrieval_service=harness.retrieval,
            evidence_service=harness.evidence,
            response_generator=harness.response,
            catalog=harness.catalog,
            settings=harness.settings,
        )
    )


@pytest.mark.asyncio
async def test_product_events_arrive_before_text_deltas(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    parts = [
        part
        async for part in _graph(harness).astream(
            initial_state("推荐一款蓝牙耳机"),
            stream_mode="custom",
            version="v2",
        )
    ]

    assert all(part["type"] == "custom" for part in parts)
    assert [part["data"]["event"] for part in parts] == [
        "product",
        "product",
        "product",
        "text_delta",
        "text_delta",
    ]


@pytest.mark.asyncio
async def test_product_event_uses_catalog_facts_and_matched_skus(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path, product_count=1)

    parts = [
        part
        async for part in _graph(harness).astream(
            initial_state("推荐一款蓝牙耳机"),
            stream_mode="custom",
            version="v2",
        )
    ]

    product_data = parts[0]["data"]["data"]
    assert product_data == {
        "rank": 1,
        "product_id": "p1",
        "title": "通勤耳机 1",
        "brand": "品牌 1",
        "base_price": 400.0,
        "display_price": 400.0,
        "matched_skus": [
            {
                "sku_id": "p1-black",
                "properties": {"颜色": "黑色"},
                "price": 400.0,
            }
        ],
        "image_url": "http://testserver/api/v1/products/p1/image",
    }


@pytest.mark.asyncio
async def test_verified_prompt_contains_only_selected_evidence(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, product_count=1)

    await _drain(_graph(harness), "推荐一款蓝牙耳机")

    prompt = harness.response.prompts[0]
    assert "推荐一款蓝牙耳机" in prompt
    assert "p1:summary" in prompt
    assert "通勤耳机 1 适合通勤" in prompt
    assert "p1-white" not in prompt
    assert "库存" in prompt
    assert "优惠" in prompt
    assert "优惠券" in prompt
    assert "购买链接" in prompt
    assert "不得" in prompt


@pytest.mark.asyncio
async def test_empty_model_chunks_are_not_emitted(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, product_count=1)
    harness.response.deltas = ["第一段", "", "第二段"]

    parts = await _drain(_graph(harness), "推荐一款蓝牙耳机")

    text_events = [
        part["data"]["data"]["delta"]
        for part in parts
        if part["data"]["event"] == "text_delta"
    ]
    assert text_events == ["第一段", "第二段"]


@pytest.mark.asyncio
async def test_empty_model_stream_raises_generation_failure(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, product_count=1)
    harness.response.deltas = []

    with pytest.raises(ServiceError) as error:
        await _drain(_graph(harness), "推荐一款蓝牙耳机")

    assert error.value.code == "GENERATION_FAILED"
    assert error.value.message == "model returned no response text"
    assert error.value.retryable is True


async def _drain(graph: Any, message: str) -> list[dict[str, Any]]:
    return [
        part
        async for part in graph.astream(
            initial_state(message), stream_mode="custom", version="v2"
        )
    ]
