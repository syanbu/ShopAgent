from pathlib import Path
from typing import Any

import pytest

from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.unit.workflow_fakes import build_harness, initial_state


async def _drain(graph: Any, message: str) -> list[dict[str, Any]]:
    return [
        part
        async for part in graph.astream(
            initial_state(message), stream_mode="custom", version="v2"
        )
    ]


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
async def test_non_shopping_skips_all_retrieval_services(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    events = await _drain(_graph(harness), "你好")

    assert harness.retrieval.retrieve_calls == []
    assert harness.retrieval.aggregate_calls == []
    assert harness.retrieval.rerank_calls == []
    assert harness.evidence.validate_calls == []
    assert harness.evidence.select_calls == []
    assert [part["data"]["event"] for part in events] == [
        "text_delta",
        "text_delta",
    ]


@pytest.mark.asyncio
async def test_no_hits_skips_rerank_validation_and_decision(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, return_hits=False)

    events = await _drain(_graph(harness), "不存在的商品")

    assert len(harness.retrieval.retrieve_calls) == 1
    assert harness.retrieval.aggregate_calls == []
    assert harness.retrieval.rerank_calls == []
    assert harness.evidence.validate_calls == []
    assert harness.evidence.select_calls == []
    assert [part["data"]["event"] for part in events] == [
        "text_delta",
        "text_delta",
    ]
    assert "没有召回到商品" in harness.response.prompts[0]
    assert all(
        forbidden in harness.response.prompts[0]
        for forbidden in ("库存", "优惠", "优惠券", "购买链接", "不得")
    )


@pytest.mark.asyncio
async def test_evidence_empty_skips_candidate_decision(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, eligible=False)

    events = await _drain(_graph(harness), "推荐蓝牙耳机")

    assert len(harness.retrieval.retrieve_calls) == 1
    assert len(harness.retrieval.aggregate_calls) == 1
    assert len(harness.retrieval.rerank_calls) == 1
    assert len(harness.evidence.validate_calls) == 1
    assert harness.evidence.select_calls == []
    assert [part["data"]["event"] for part in events] == [
        "text_delta",
        "text_delta",
    ]
    assert "没有通过证据校验的商品" in harness.response.prompts[0]


@pytest.mark.asyncio
async def test_shopping_route_calls_each_stage_once(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    await _drain(_graph(harness), "推荐蓝牙耳机")

    assert harness.parser.calls == ["推荐蓝牙耳机"]
    assert len(harness.retrieval.retrieve_calls) == 1
    assert len(harness.retrieval.aggregate_calls) == 1
    assert len(harness.retrieval.rerank_calls) == 1
    assert len(harness.evidence.validate_calls) == 1
    assert len(harness.evidence.select_calls) == 1
    assert len(harness.response.prompts) == 1


@pytest.mark.asyncio
async def test_missing_correlation_ids_use_injected_factory(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    ids = iter(("request-generated", "conversation-generated"))
    graph = build_graph(
        WorkflowDependencies(
            intent_parser=harness.parser,
            retrieval_service=harness.retrieval,
            evidence_service=harness.evidence,
            response_generator=harness.response,
            catalog=harness.catalog,
            settings=harness.settings,
            id_factory=lambda: next(ids),
        )
    )

    result = await graph.ainvoke({"user_message": "你好"}, version="v2")

    assert result.value["request_id"] == "request-generated"
    assert result.value["conversation_id"] == "conversation-generated"
