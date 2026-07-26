import pytest
from pydantic import ValidationError

from shop_agent.models.conversation import (
    CandidateReference,
    ConversationRecord,
    ConversationState,
    PendingClarification,
    QuerySnapshot,
)
from shop_agent.models.turn_query import ProductQuestion, TurnQuery


def _suspended_turn() -> TurnQuery:
    return TurnQuery(
        schema_version=1,
        intent="product_question",
        product_question=ProductQuestion(
            text="第二个多少钱",
            kind="structured",
            field="display_price",
        ),
    )


def _candidate(product_id: str, rank: int) -> CandidateReference:
    return CandidateReference(rank=rank, product_id=product_id, display_price=399)


def test_query_snapshot_converts_to_the_existing_parsed_intent_contract() -> None:
    snapshot = QuerySnapshot(
        category="数码电子",
        sub_category="蓝牙耳机",
        semantic_terms=["通勤", "轻量"],
        constraints={"required_features": ["轻量", "降噪"]},
    )

    intent = snapshot.to_parsed_intent()

    assert intent.intent == "product_search"
    assert intent.retrieval_query == "蓝牙耳机、通勤、轻量、降噪"
    assert intent.constraints is snapshot.constraints


def test_collection_defaults_are_independent() -> None:
    first_snapshot = QuerySnapshot()
    second_snapshot = QuerySnapshot()
    first_snapshot.semantic_terms.append("通勤")

    first_state = ConversationState(schema_version=1, conversation_id="c1")
    second_state = ConversationState(schema_version=1, conversation_id="c2")
    first_state.seen_product_ids.append("p1")

    assert second_snapshot.semantic_terms == []
    assert second_state.seen_product_ids == []


def test_conversation_requires_focus_to_belong_to_latest_candidates() -> None:
    with pytest.raises(ValidationError, match="focused product"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=[_candidate("p1", 1)],
            focused_product_id="p2",
            seen_product_ids=["p1"],
        )


def test_conversation_requires_recent_candidates_to_be_seen() -> None:
    with pytest.raises(ValidationError, match="recent candidates"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=[_candidate("p1", 1)],
            seen_product_ids=[],
        )


@pytest.mark.parametrize(
    "candidates",
    [
        [_candidate("p1", 1), _candidate("p2", 3)],
        [_candidate("p1", 1), _candidate("p2", 1)],
        [_candidate("p1", 1), _candidate("p1", 2)],
    ],
)
def test_conversation_rejects_invalid_candidate_ranks_or_ids(
    candidates: list[CandidateReference],
) -> None:
    with pytest.raises(ValidationError):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=candidates,
            seen_product_ids=["p1", "p2"],
        )


def test_conversation_rejects_duplicate_seen_ids() -> None:
    with pytest.raises(ValidationError, match="seen product IDs"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            seen_product_ids=["p1", "p1"],
        )


def test_product_ids_are_opaque_and_case_sensitive() -> None:
    state = ConversationState(
        schema_version=1,
        conversation_id="c1",
        recent_candidates=[_candidate(" P1 ", 1), _candidate("p1", 2)],
        seen_product_ids=[" P1 ", "p1"],
    )

    assert [candidate.product_id for candidate in state.recent_candidates] == ["P1", "p1"]
    assert state.seen_product_ids == ["P1", "p1"]


def test_product_id_collections_reject_raw_strings() -> None:
    with pytest.raises(ValidationError):
        PendingClarification(
            kind="missing_context",
            candidate_product_ids="P1",
            suspended_turn_query=_suspended_turn(),
        )
    with pytest.raises(ValidationError):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            seen_product_ids="P1",
        )


def test_product_ids_reject_non_string_values_as_validation_errors() -> None:
    with pytest.raises(ValidationError):
        CandidateReference(rank=1, product_id=1, display_price=399)


def test_pending_clarification_round_trip_retains_suspended_turn_and_attempt_count() -> None:
    pending = PendingClarification(
        kind="ambiguous_reference",
        candidate_product_ids=[" P1 ", "p1"],
        suspended_turn_query=_suspended_turn(),
        attempt_count=2,
    )
    state = ConversationState(
        schema_version=1,
        conversation_id="c1",
        recent_candidates=[_candidate("P1", 1), _candidate("p1", 2)],
        seen_product_ids=["P1", "p1"],
        pending_clarification=pending,
    )

    restored = ConversationState.model_validate_json(state.model_dump_json())

    assert restored.pending_clarification == pending
    assert restored.pending_clarification is not None
    assert restored.pending_clarification.attempt_count == 2
    assert restored.pending_clarification.suspended_turn_query.intent == "product_question"


def test_pending_clarification_candidates_are_immutable() -> None:
    pending = PendingClarification(
        kind="missing_context",
        candidate_product_ids=["p1"],
        suspended_turn_query=_suspended_turn(),
    )

    assert pending.candidate_product_ids == ("p1",)
    with pytest.raises(AttributeError):
        pending.candidate_product_ids.append("p2")


def test_conversation_rejects_clarification_candidates_outside_recent_candidates() -> None:
    pending = PendingClarification(
        kind="ambiguous_reference",
        candidate_product_ids=["p2"],
        suspended_turn_query=_suspended_turn(),
    )

    with pytest.raises(ValidationError, match="clarification candidates"):
        ConversationState(
            schema_version=1,
            conversation_id="c1",
            recent_candidates=[_candidate("p1", 1)],
            seen_product_ids=["p1"],
            pending_clarification=pending,
        )


def test_state_json_contains_only_persisted_domain_state() -> None:
    state = ConversationState(schema_version=1, conversation_id="c1")
    persisted = state.model_dump()

    assert set(persisted) == {
        "schema_version",
        "conversation_id",
        "query_snapshot",
        "recent_candidates",
        "focused_product_id",
        "seen_product_ids",
        "pending_clarification",
    }
    assert "product_body" not in state.model_dump_json()
    assert "sku_list" not in state.model_dump_json()
    assert "qdrant_chunk" not in state.model_dump_json()
    assert "model_response" not in state.model_dump_json()
    assert "generated_reply" not in state.model_dump_json()


def test_conversation_record_keeps_sql_version_outside_serialized_state() -> None:
    state = ConversationState(schema_version=1, conversation_id="c1")
    record = ConversationRecord(state=state, version=1)

    assert "version" not in state.model_dump()
    assert record.version == 1
