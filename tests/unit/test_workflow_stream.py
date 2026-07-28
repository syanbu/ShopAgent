from pathlib import Path
from typing import Any

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.errors import ServiceError
from shop_agent.models.conversation import (
    CandidateReference,
    ConversationRecord,
    ConversationState,
)
from shop_agent.models.turn_query import (
    ProductQuestion,
    ProductReference,
    ReferenceCandidateMatch,
    TurnQuery,
)
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.unit.workflow_fakes import build_harness, initial_state


def _graph(harness: Any) -> Any:
    return build_graph(
        WorkflowDependencies(
            turn_query_parser=harness.parser,
            conversation_repository=harness.repository,
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
async def test_product_event_uses_only_512gb_sku_price(tmp_path: Path) -> None:
    harness = build_harness(
        tmp_path,
        product_count=1,
        product_pairs=[("数码电子", "智能手机")],
    )
    product = harness.catalog.all()[0]
    product.skus[0].properties = {"存储": "256GB"}
    product.skus[0].price = 6999
    product.skus[1].properties = {"存储配置": "512GB"}
    product.skus[1].price = 7999
    harness.catalog = ProductCatalog(
        tmp_path,
        {product.product_id: product},
        {product.product_id: f"data/{product.product_id}.json"},
    )
    harness.evidence.catalog = harness.catalog
    harness.parser.turn = TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "new_search",
            "slot_operations": [
                {
                    "slot": "category",
                    "operation": "replace",
                    "value": "数码电子",
                },
                {
                    "slot": "sub_category",
                    "operation": "replace",
                    "value": "智能手机",
                },
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 8000,
                },
                {
                    "slot": "constraints.sku_constraints",
                    "operation": "add",
                    "value": "512GB",
                    "sku_key": "storage",
                },
            ],
        }
    )

    parts = await _drain(_graph(harness), "推荐8000元以内的512GB手机")
    product_data = parts[0]["data"]["data"]

    assert product_data["display_price"] == 7999
    assert [sku["properties"] for sku in product_data["matched_skus"]] == [
        {"存储配置": "512GB"}
    ]


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


@pytest.mark.asyncio
async def test_product_question_stream_contains_text_deltas_only(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    harness.repository.record = ConversationRecord(
        state=ConversationState(
            schema_version=1,
            conversation_id="conversation-fixed",
            recent_candidates=[
                CandidateReference(
                    rank=index,
                    product_id=f"p{index}",
                    display_price=399 + index,
                )
                for index in range(1, 4)
            ],
            seen_product_ids=["p1", "p2", "p3"],
        ),
        version=2,
    )
    harness.parser.turn = TurnQuery(
        schema_version=1,
        intent="product_question",
        reference=ProductReference(
            target_type="product",
            surface_text="第二个",
            kind="ordinal",
            ordinal=2,
            candidate_matches=[
                ReferenceCandidateMatch(product_id="p1", matches=False),
                ReferenceCandidateMatch(product_id="p2", matches=True),
                ReferenceCandidateMatch(product_id="p3", matches=False),
            ],
        ),
        product_question=ProductQuestion(
            text="第二个多少钱",
            kind="structured",
            field="display_price",
        ),
    )

    parts = await _drain(_graph(harness), "第二个多少钱")

    assert [part["data"]["event"] for part in parts] == [
        "text_delta",
        "text_delta",
    ]
    assert harness.repository.record.state.focused_product_id == "p2"
    assert harness.retrieval.retrieve_calls == []
    assert harness.retrieval.fetch_product_calls == []


async def _drain(graph: Any, message: str) -> list[dict[str, Any]]:
    return [
        part
        async for part in graph.astream(
            initial_state(message), stream_mode="custom", version="v2"
        )
    ]
