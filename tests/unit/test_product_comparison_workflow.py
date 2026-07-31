from pathlib import Path
from typing import Any

import pytest

from shop_agent.errors import ServiceError
from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonProductFinding,
)
from shop_agent.models.conversation import (
    CandidateReference,
    ConversationRecord,
    ConversationState,
    QuerySnapshot,
)
from shop_agent.models.query import SearchConstraints
from shop_agent.models.turn_query import TurnQuery
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.unit.workflow_fakes import (
    FakeConversationRepository,
    FakeTurnQueryParser,
    build_harness,
    initial_state,
)


def _conversation(*, focus: str | None = None) -> ConversationState:
    return ConversationState(
        schema_version=1,
        conversation_id="conversation-fixed",
        query_snapshot=QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(max_price=500),
        ),
        recent_candidates=[
            CandidateReference(
                rank=index,
                product_id=f"p{index}",
                display_price=399 + index,
            )
            for index in range(1, 4)
        ],
        focused_product_id=focus,
        seen_product_ids=["p1", "p2", "p3"],
    )


def _comparison_turn(
    *,
    selected: tuple[str, ...] = ("p1", "p2"),
    dimension: str | None = "保湿",
    question: str = "第一款和第二款哪个更保湿",
    surface_text: str = "第一款和第二款",
) -> TurnQuery:
    return TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "product_comparison",
            "product_comparison": {
                "question": question,
                "dimension": dimension,
                "surface_text": surface_text,
                "candidate_matches": [
                    {
                        "product_id": f"p{index}",
                        "selected": f"p{index}" in selected,
                    }
                    for index in range(1, 4)
                ],
            },
        }
    )


def _clarification_answer(
    *,
    question: str,
    dimension: str | None,
    surface_text: str | None,
    selected: tuple[str, ...] = (),
) -> TurnQuery:
    return TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "clarification_answer",
            "product_comparison": {
                "question": question,
                "dimension": dimension,
                "surface_text": surface_text,
                "candidate_matches": [
                    {
                        "product_id": f"p{index}",
                        "selected": f"p{index}" in selected,
                    }
                    for index in range(1, 4)
                ]
                if selected
                else [],
            },
        }
    )


def _dependencies(
    harness: Any,
    parser: FakeTurnQueryParser,
    repository: FakeConversationRepository,
) -> WorkflowDependencies:
    return WorkflowDependencies(
        turn_query_parser=parser,
        conversation_repository=repository,
        retrieval_service=harness.retrieval,
        evidence_service=harness.evidence,
        response_generator=harness.response,
        catalog=harness.catalog,
        settings=harness.settings,
        comparison_assessor=harness.comparison,
    )


async def _drain(
    graph: Any,
    message: str,
) -> list[dict[str, Any]]:
    return [
        part
        async for part in graph.astream(
            initial_state(message),
            stream_mode="custom",
            version="v2",
        )
    ]


def _finding(
    product_id: str, evidence_id: str | None = None
) -> ComparisonProductFinding:
    return ComparisonProductFinding(
        product_id=product_id,
        evidence_ids=[evidence_id or f"{product_id}:structured"],
        supported_summary=f"{product_id} 的保湿资料",
        limitations=[],
    )


@pytest.mark.asyncio
async def test_comparison_uses_recent_catalog_materials_without_retrieval(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    parser = FakeTurnQueryParser([_comparison_turn()])
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(), version=1)
    )

    events = await _drain(
        build_graph(_dependencies(harness, parser, repository)),
        "第一款和第二款哪个更保湿",
    )

    assert [part["data"]["event"] for part in events] == ["text_delta"]
    assert events[0]["data"]["data"]["delta"] == ("通勤耳机 1 在现有资料中更有优势。")
    assert harness.retrieval.retrieve_calls == []
    assert harness.retrieval.fetch_product_calls == []
    assert harness.retrieval.rerank_calls == []
    assert harness.evidence.validate_calls == []
    assert len(harness.comparison.calls) == 1
    question, dimension, materials = harness.comparison.calls[0]
    assert question == "第一款和第二款哪个更保湿"
    assert dimension == "保湿"
    assert [material.product_id for material in materials] == ["p1", "p2"]
    assert [item.source_type for item in materials[0].evidence] == [
        "structured_facts",
        "product_summary",
    ]
    assert repository.record is not None
    assert repository.record.state.focused_product_id == "p1"
    assert [item.product_id for item in repository.record.state.recent_candidates] == [
        "p1",
        "p2",
        "p3",
    ]


@pytest.mark.asyncio
async def test_missing_dimension_is_persisted_and_resumed(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    parser = FakeTurnQueryParser(
        [
            _comparison_turn(
                selected=("p1", "p2", "p3"),
                dimension=None,
                question="这三个哪个好",
                surface_text="这三个",
            ),
            _clarification_answer(
                question="保湿",
                dimension="保湿",
                surface_text=None,
            ),
        ]
    )
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(), version=1)
    )
    graph = build_graph(_dependencies(harness, parser, repository))

    first = await _drain(graph, "这三个哪个好")
    second = await _drain(graph, "保湿")

    assert first[0]["data"]["data"]["delta"] == (
        "你更想比较哪方面，例如价格、规格还是使用体验？"
    )
    assert second[0]["data"]["data"]["delta"] == ("通勤耳机 1 在现有资料中更有优势。")
    assert parser.calls[1][1].pending_clarification is not None
    assert (
        parser.calls[1][1].pending_clarification.kind == "missing_comparison_dimension"
    )
    assert len(harness.comparison.calls) == 1
    _, dimension, materials = harness.comparison.calls[0]
    assert dimension == "保湿"
    assert [material.product_id for material in materials] == ["p1", "p2", "p3"]
    assert repository.record is not None
    assert repository.record.state.pending_clarification is None


@pytest.mark.asyncio
async def test_ambiguous_targets_are_persisted_and_resumed(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    parser = FakeTurnQueryParser(
        [
            _comparison_turn(selected=("p1",)),
            _clarification_answer(
                question="前两个",
                dimension=None,
                surface_text="前两个",
                selected=("p1", "p2"),
            ),
        ]
    )
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(), version=1)
    )
    graph = build_graph(_dependencies(harness, parser, repository))

    first = await _drain(graph, "第一款和第二款哪个更保湿")
    second = await _drain(graph, "前两个")

    assert first[0]["data"]["data"]["delta"] == (
        "请选择最近展示的两到三款商品进行对比。"
    )
    assert second[0]["data"]["data"]["delta"] == ("通勤耳机 1 在现有资料中更有优势。")
    assert parser.calls[1][1].pending_clarification is not None
    assert (
        parser.calls[1][1].pending_clarification.kind == "ambiguous_comparison_targets"
    )
    assert len(harness.comparison.calls) == 1
    assert [material.product_id for material in harness.comparison.calls[0][2]] == [
        "p1",
        "p2",
    ]


@pytest.mark.asyncio
async def test_non_winner_comparison_clears_stale_focus(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    harness.comparison.assessment = ComparisonAssessment(
        dimension="保湿",
        products=[_finding("p1"), _finding("p2")],
        outcome="context_dependent",
        winner_product_id=None,
        reason="不同 SKU 和使用场景下各有优势。",
        response_text="两款各有侧重，需要结合使用场景选择。",
    )
    parser = FakeTurnQueryParser([_comparison_turn()])
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(focus="p3"), version=1)
    )

    await _drain(
        build_graph(_dependencies(harness, parser, repository)),
        "第一款和第二款哪个更保湿",
    )

    assert repository.record is not None
    assert repository.record.state.focused_product_id is None


@pytest.mark.asyncio
async def test_untrusted_comparison_evidence_fails_before_persisting(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    harness.comparison.assessment = ComparisonAssessment(
        dimension="保湿",
        products=[
            _finding("p1", evidence_id="p2:structured"),
            _finding("p2"),
        ],
        outcome="winner",
        winner_product_id="p1",
        reason="错误证据。",
        response_text="错误结论。",
    )
    parser = FakeTurnQueryParser([_comparison_turn()])
    repository = FakeConversationRepository(
        ConversationRecord(state=_conversation(), version=1)
    )

    with pytest.raises(ServiceError) as caught:
        await _drain(
            build_graph(_dependencies(harness, parser, repository)),
            "第一款和第二款哪个更保湿",
        )

    assert caught.value.code == "COMPARISON_PARSE_FAILED"
    assert repository.saves == []
