from pathlib import Path
from typing import Any

import pytest

from shop_agent.models.turn_query import TurnQuery
from shop_agent.models.retrieval import SelectedProduct
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from shop_agent.workflow.nodes import build_nodes, route_selection
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
    assert harness.retrieval.retrieve_calls[0].intent.constraints.max_price == expected_cap
    assert harness.evidence.validate_calls[0][1].max_price == expected_cap
    assert harness.evidence.select_calls[0][2].max_price == expected_cap


@pytest.mark.asyncio
async def test_value_price_without_sub_category_short_circuits_to_clarification(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
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
                    "slot": "constraints.price_preference",
                    "operation": "replace",
                    "value": "value",
                },
            ],
        }
    )

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
        turn_query_parser=harness.parser,
        conversation_repository=harness.repository,
        retrieval_service=harness.retrieval,
        evidence_service=harness.evidence,
        response_generator=harness.response,
        catalog=harness.catalog,
        settings=harness.settings,
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
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。"
    )
    assert harness.response.prompts == []


@pytest.mark.asyncio
async def test_evidence_empty_skips_candidate_decision(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, eligible=False)

    events = await _drain(_graph(harness), "推荐蓝牙耳机")

    assert len(harness.retrieval.retrieve_calls) == 1
    assert len(harness.retrieval.aggregate_calls) == 1
    assert len(harness.retrieval.rerank_calls) == 1
    assert len(harness.evidence.validate_calls) == 1
    assert harness.evidence.select_calls == []
    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == (
        "找到了一些候选商品，但现有信息不足以确认它们符合要求，"
        "建议您调整筛选条件。"
    )
    assert harness.response.prompts == []


@pytest.mark.asyncio
async def test_fixed_no_result_response_requires_reason(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    nodes = build_nodes(_dependencies(harness))
    events: list[dict[str, object]] = []

    with pytest.raises(
        RuntimeError,
        match="no-result response requires a reason",
    ):
        await nodes.emit_no_results_response(
            {"response_mode": "no_results"},
            events.append,
        )

    assert events == []


def test_selection_route_distinguishes_empty_and_nonempty_products() -> None:
    assert route_selection({"selected_products": []}) == "no_products"

    selected = SelectedProduct(
        product_id="p1",
        rerank_score=0.9,
        evidence_ids=["p1:summary"],
        decision_reasons=["test"],
        matched_sku_ids=["p1-black"],
    )
    assert route_selection({"selected_products": [selected]}) == "has_products"


@pytest.mark.asyncio
async def test_shopping_route_calls_each_stage_once(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    await _drain(_graph(harness), "推荐蓝牙耳机")

    assert [message for message, _ in harness.parser.calls] == ["推荐蓝牙耳机"]
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
            turn_query_parser=harness.parser,
            conversation_repository=harness.repository,
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
