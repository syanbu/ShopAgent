import json
import logging
from pathlib import Path
from typing import Any

import pytest

from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from shop_agent.workflow.nodes import build_nodes
from tests.unit.workflow_fakes import build_harness, initial_state


async def _drain(graph: Any, message: str) -> list[dict[str, Any]]:
    return [
        part
        async for part in graph.astream(
            initial_state(message), stream_mode="custom", version="v2"
        )
    ]


@pytest.mark.asyncio
async def test_value_price_is_compiled_once_and_shared_by_downstream_nodes(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)

    await _drain(_graph(harness), "推荐性价比高的蓝牙耳机")

    expected_cap = 481.2
    assert harness.retrieval.retrieve_calls[0].constraints.max_price == expected_cap
    assert harness.evidence.validate_calls[0][1].max_price == expected_cap
    assert harness.evidence.select_calls[0][2].max_price == expected_cap


@pytest.mark.asyncio
async def test_value_price_without_sub_category_short_circuits_to_clarification(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    original_parse = harness.parser.parse

    async def parse_without_sub_category(message: str):
        parsed = await original_parse(message)
        return parsed.model_copy(update={"sub_category": None})

    harness.parser.parse = parse_without_sub_category  # type: ignore[method-assign]

    events = await _drain(_graph(harness), "推荐性价比高的商品")

    assert harness.retrieval.retrieve_calls == []
    assert harness.evidence.validate_calls == []
    assert harness.response.prompts == []
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == "请明确想购买的商品类型，例如手机、T恤或耳机。"


def _graph(harness: Any) -> Any:
    return build_graph(_dependencies(harness))


def _dependencies(harness: Any) -> WorkflowDependencies:
    return WorkflowDependencies(
        intent_parser=harness.parser,
        retrieval_service=harness.retrieval,
        evidence_service=harness.evidence,
        response_generator=harness.response,
        catalog=harness.catalog,
        settings=harness.settings,
    )


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("推荐蓝牙耳机", "product_search"),
        ("你好", "non_shopping"),
    ],
)
@pytest.mark.asyncio
async def test_structure_intent_logs_final_json_for_every_intent(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    message: str,
    expected_intent: str,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await nodes.structure_intent(initial_state(message))

    records = [
        record
        for record in caplog.records
        if record.name == "uvicorn.error"
        and record.getMessage().startswith("parsed_intent ")
    ]
    assert len(records) == 1
    log_message = records[0].getMessage()
    payload = json.loads(log_message.removeprefix("parsed_intent "))
    assert payload["request_id"] == "request-fixed"
    assert payload["conversation_id"] == "conversation-fixed"
    assert payload["intent"]["intent"] == expected_intent
    if expected_intent == "product_search":
        assert payload["intent"]["constraints"]["max_price"] == 500


@pytest.mark.asyncio
async def test_structure_intent_keeps_chinese_readable_in_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await nodes.structure_intent(initial_state("推荐蓝牙耳机"))

    log_message = next(
        record.getMessage()
        for record in caplog.records
        if record.name == "uvicorn.error"
        and record.getMessage().startswith("parsed_intent ")
    )
    assert '"retrieval_query":"推荐蓝牙耳机"' in log_message
    assert '"category":"数码电子"' in log_message
    assert "\\u63a8" not in log_message


@pytest.mark.asyncio
async def test_structure_intent_escapes_log_line_separators(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))
    conversation_id = "ok\nINFO forged=true\u0085next\u2028next\u2029next"
    state = initial_state("你好")
    state["conversation_id"] = conversation_id

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await nodes.structure_intent(state)

    records = [
        record
        for record in caplog.records
        if record.name == "uvicorn.error"
        and record.getMessage().startswith("parsed_intent ")
    ]
    assert len(records) == 1
    log_message = records[0].getMessage()
    assert "\n" not in log_message
    assert "\u0085" not in log_message
    assert "\u2028" not in log_message
    assert "\u2029" not in log_message
    payload = json.loads(log_message.removeprefix("parsed_intent "))
    assert payload["conversation_id"] == conversation_id


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
