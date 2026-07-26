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
from shop_agent.models.retrieval import EvidenceChunk
from shop_agent.models.turn_query import TurnQuery
from shop_agent.services.conversation_repository import SqliteConversationRepository
from tests.integration.api_fakes import (
    FakeGraph,
    FakeReadinessProbe,
    FailingConversationRepository,
    FailingTurnQueryParser,
    SequencedResponseGenerator,
    compiled_chat_dependencies,
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
async def test_compiled_http_generation_failure_persists_candidates_for_follow_up_ordinal(
    tmp_path: Path,
) -> None:
    """A failure after emitted products must not discard the reference domain."""
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "第二个",
                    "kind": "ordinal",
                    "ordinal": 2,
                },
                "product_question": {"text": "第二个防水吗", "kind": "semantic"},
            }
        ),
    ]
    dependencies, parser, repository, retrieval, _ = compiled_chat_dependencies(
        tmp_path,
        turns=turns,
        response_generator=SequencedResponseGenerator(
            [
                ServiceError("GENERATION_FAILED", "upstream failed", retryable=True),
                "第二个商品的已验证说明",
            ]
        ),
    )

    async def fetch_product_chunks(product_id: str) -> list[EvidenceChunk]:
        return [
            EvidenceChunk(
                chunk_id=f"{product_id}:summary",
                point_id=f"point-{product_id}",
                product_id=product_id,
                chunk_type="product_summary",
                text="已验证的商品知识。",
                source_path=f"data/{product_id}.json",
            )
        ]

    retrieval.fetch_product_chunks = fetch_product_chunks  # type: ignore[method-assign]

    failed = await _post(
        dependencies,
        {"conversation_id": "generation-persistence", "message": "展示三款"},
    )
    followed_up = await _post(
        dependencies,
        {"conversation_id": "generation-persistence", "message": "第二个防水吗"},
    )

    failed_events = parse_sse(failed.text)
    assert [event.name for event in failed_events] == [
        "message_start",
        "product",
        "product",
        "product",
        "error",
        "message_end",
    ]
    assert failed_events[-2].data == {
        "code": "GENERATION_FAILED",
        "message": "upstream failed",
        "retryable": True,
    }
    assert failed_events[-1].data["status"] == "partial"

    follow_up_events = parse_sse(followed_up.text)
    assert [event.name for event in follow_up_events] == [
        "message_start",
        "text_delta",
        "message_end",
    ]
    assert follow_up_events[-1].data["status"] == "completed"
    assert parser.contexts[1].recent_candidates[1].product_id == "p2"
    assert parser.contexts[1].focused_product_id is None
    assert len(retrieval.calls) == 1
    saved = await repository.load("generation-persistence")
    assert saved is not None
    assert saved.version == 2
    assert saved.state.focused_product_id == "p2"
    assert [item.product_id for item in saved.state.recent_candidates] == [
        "p1",
        "p2",
        "p3",
    ]
    assert saved.state.seen_product_ids == ["p1", "p2", "p3"]


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
async def test_compiled_graph_persists_refinement_and_isolates_conversations(
    tmp_path: Path,
) -> None:
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "refine_search",
                "slot_operations": [
                    {
                        "slot": "constraints.max_price",
                        "operation": "replace",
                        "value": 300,
                    }
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
    ]
    dependencies, parser, repository, retrieval, _ = compiled_chat_dependencies(
        tmp_path, turns=turns
    )

    first = await _post(
        dependencies,
        {"conversation_id": "c1", "message": "推荐蓝牙耳机"},
    )
    refined = await _post(
        dependencies,
        {"conversation_id": "c1", "message": "预算改成300"},
    )
    isolated = await _post(
        dependencies,
        {"conversation_id": "c2", "message": "推荐蓝牙耳机"},
    )

    for response, conversation_id in ((first, "c1"), (refined, "c1"), (isolated, "c2")):
        events = parse_sse(response.text)
        names = [event.name for event in events]
        assert names[0] == "message_start"
        assert names[-1] == "message_end"
        assert names == [
            "message_start",
            *["product"] * names.count("product"),
            *["text_delta"] * names.count("text_delta"),
            "message_end",
        ]
        assert 0 <= names.count("product") <= 3
        assert names.count("text_delta") >= 1
        assert events[0].data["conversation_id"] == conversation_id
        assert events[-1].data["status"] == "completed"

    assert parser.calls == ["推荐蓝牙耳机", "预算改成300", "推荐蓝牙耳机"]
    assert parser.contexts[0].query_snapshot is None
    assert parser.contexts[1].query_snapshot is not None
    assert parser.contexts[1].query_snapshot.constraints.max_price is None
    assert parser.contexts[1].recent_candidates
    assert parser.contexts[2].query_snapshot is None
    assert parser.contexts[2].recent_candidates == []
    assert parser.contexts[2].focused_product_id is None
    assert retrieval.calls[1].max_price == 300
    assert retrieval.calls[1].excluded_product_ids == ()
    c1 = await repository.load("c1")
    c2 = await repository.load("c2")
    assert c1 is not None
    assert c2 is not None
    assert c1.state.query_snapshot is not None
    assert c1.state.query_snapshot.constraints.max_price == 300
    assert c1.state.recent_candidates
    assert c1.state.seen_product_ids
    assert c2.state.query_snapshot is not None
    assert c2.state.query_snapshot.constraints.max_price is None
    assert c2.state.recent_candidates
    assert c2.state.seen_product_ids


@pytest.mark.asyncio
async def test_compiled_http_dialogue_persists_focus_for_follow_up_pronoun(
    tmp_path: Path,
) -> None:
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "第二个",
                    "kind": "ordinal",
                    "ordinal": 2,
                },
                "product_question": {
                    "text": "第二个防水吗",
                    "kind": "semantic",
                },
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "它",
                    "kind": "demonstrative",
                },
                "product_question": {
                    "text": "它续航怎么样",
                    "kind": "semantic",
                },
            }
        ),
    ]
    dependencies, parser, repository, retrieval, _ = compiled_chat_dependencies(
        tmp_path,
        turns=turns,
    )
    focused_fetches: list[str] = []

    async def fetch_product_chunks(product_id: str) -> list[EvidenceChunk]:
        focused_fetches.append(product_id)
        return [
            EvidenceChunk(
                chunk_id=f"{product_id}:summary",
                point_id=f"point-{product_id}",
                product_id=product_id,
                chunk_type="product_summary",
                text="确定性的商品知识。",
                source_path=f"data/{product_id}.json",
            )
        ]

    retrieval.fetch_product_chunks = fetch_product_chunks  # type: ignore[method-assign]

    displayed = await _post(
        dependencies,
        {"conversation_id": "focus-dialogue", "message": "展示三款"},
    )
    ordinal = await _post(
        dependencies,
        {"conversation_id": "focus-dialogue", "message": "第二个防水吗"},
    )
    pronoun = await _post(
        dependencies,
        {"conversation_id": "focus-dialogue", "message": "它续航怎么样"},
    )

    displayed_events = parse_sse(displayed.text)
    ordinal_events = parse_sse(ordinal.text)
    pronoun_events = parse_sse(pronoun.text)
    assert [event.name for event in displayed_events] == [
        "message_start",
        "product",
        "product",
        "product",
        "text_delta",
        "message_end",
    ]
    for events in (ordinal_events, pronoun_events):
        assert [event.name for event in events] == [
            "message_start",
            "text_delta",
            "message_end",
        ]
        assert events[-1].data["status"] == "completed"
    assert focused_fetches == ["p2", "p2"]
    assert retrieval.calls.__len__() == 1
    assert parser.contexts[2].focused_product_id == "p2"
    saved = await repository.load("focus-dialogue")
    assert saved is not None
    assert saved.version == 3
    assert saved.state.focused_product_id == "p2"
    assert [item.product_id for item in saved.state.recent_candidates] == [
        "p1",
        "p2",
        "p3",
    ]
    assert saved.state.seen_product_ids == ["p1", "p2", "p3"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message", "retryable"),
    [
        ("CONVERSATION_UNAVAILABLE", "conversation storage unavailable", True),
        (
            "CONVERSATION_CONFLICT",
            "conversation state changed; retry the request",
            True,
        ),
        ("TURN_QUERY_PARSE_FAILED", "invalid structured output", True),
    ],
)
async def test_known_pre_product_errors_keep_public_sse_contract(
    sample_dataset_root: Path,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    graph = FakeGraph(
        [],
        error=ServiceError(code, message, retryable=retryable),  # type: ignore[arg-type]
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
    assert events[1].data == {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    assert events[-1].data["status"] == "failed"
    assert "upstream-model-secret" not in response.text
    assert "SELECT private_chunk" not in response.text
    assert "C:\\private\\chat.sqlite3" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_point", "error", "marker"),
    [
        (
            "load",
            ServiceError(
                "CONVERSATION_UNAVAILABLE",
                "conversation storage unavailable",
                retryable=True,
            ),
            "SELECT private_chunk FROM C:\\private\\chat.sqlite3",
        ),
        (
            "save",
            ServiceError(
                "CONVERSATION_CONFLICT",
                "conversation state changed; retry the request",
                retryable=True,
            ),
            "SQL conflict secret at C:\\private\\chat.sqlite3",
        ),
        (
            "parse",
            ServiceError(
                "TURN_QUERY_PARSE_FAILED",
                "invalid structured output",
                retryable=True,
            ),
            "model-response-secret: <untrusted-json>",
        ),
    ],
)
async def test_compiled_graph_pre_product_errors_are_safe_over_http(
    tmp_path: Path,
    failure_point: str,
    error: ServiceError,
    marker: str,
) -> None:
    parser = (
        FailingTurnQueryParser(error, marker) if failure_point == "parse" else None
    )
    repository = (
        FailingConversationRepository(
            SqliteConversationRepository(tmp_path / "chat.sqlite3"),
            load_error=error if failure_point == "load" else None,
            save_error=error if failure_point == "save" else None,
            marker=marker,
        )
        if failure_point in {"load", "save"}
        else None
    )
    dependencies, _, _, retrieval, evidence = compiled_chat_dependencies(
        tmp_path,
        turns=[
            TurnQuery.model_validate(
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
                            "value": "蓝牙耳机",
                        },
                    ],
                }
            )
        ],
        parser=parser,
        repository=repository,
    )

    response = await _post(
        dependencies,
        {"conversation_id": "c1", "message": "推荐蓝牙耳机"},
    )

    events = parse_sse(response.text)
    assert [event.name for event in events] == [
        "message_start",
        "error",
        "message_end",
    ]
    assert events[1].data == error.to_payload()
    assert events[-1].data["status"] == "failed"
    assert marker not in response.text
    assert "upstream-model-secret" not in response.text
    assert "SELECT private_chunk" not in response.text
    assert "C:\\private\\chat.sqlite3" not in response.text
    if failure_point == "save":
        assert retrieval.calls
        assert evidence.select_calls
    else:
        assert retrieval.calls == []
        assert evidence.select_calls == []


@pytest.mark.asyncio
async def test_compiled_graph_product_knowledge_error_ends_without_product_or_text(
    tmp_path: Path,
) -> None:
    turns = [
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "new_search",
                "slot_operations": [
                    {"slot": "category", "operation": "replace", "value": "数码电子"},
                    {
                        "slot": "sub_category",
                        "operation": "replace",
                        "value": "蓝牙耳机",
                    },
                ],
            }
        ),
        TurnQuery.model_validate(
            {
                "schema_version": 1,
                "intent": "product_question",
                "reference": {
                    "target_type": "product",
                    "surface_text": "第一个",
                    "kind": "ordinal",
                    "ordinal": 1,
                },
                "product_question": {
                    "text": "第一个防水吗",
                    "kind": "semantic",
                },
            }
        ),
    ]
    dependencies, _, _, _, _ = compiled_chat_dependencies(tmp_path, turns=turns)

    initial = await _post(
        dependencies,
        {"conversation_id": "c1", "message": "推荐蓝牙耳机"},
    )
    failed = await _post(
        dependencies,
        {"conversation_id": "c1", "message": "第一个防水吗"},
    )

    assert "product" in [event.name for event in parse_sse(initial.text)]
    events = parse_sse(failed.text)
    assert [event.name for event in events] == [
        "message_start",
        "error",
        "message_end",
    ]
    assert events[1].data == {
        "code": "PRODUCT_KNOWLEDGE_UNAVAILABLE",
        "message": "product knowledge unavailable",
        "retryable": False,
    }
    assert events[-1].data["status"] == "failed"
    assert "upstream-model-secret" not in failed.text
    assert "SELECT private_chunk" not in failed.text


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
