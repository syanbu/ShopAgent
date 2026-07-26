from pathlib import Path

import aiosqlite
import pytest

from shop_agent.errors import ServiceError
from shop_agent.models import CandidateReference, ConversationState, QuerySnapshot
from shop_agent.services.conversation_repository import SqliteConversationRepository


def _state(conversation_id: str) -> ConversationState:
    return ConversationState(
        schema_version=1,
        conversation_id=conversation_id,
        query_snapshot=QuerySnapshot(
            category="数码电子",
            sub_category="蓝牙耳机",
            semantic_terms=["通勤"],
            constraints={"max_price": 300},
        ),
        recent_candidates=[
            CandidateReference(rank=1, product_id="p1", display_price=299.0)
        ],
        seen_product_ids=["p1"],
    )


@pytest.mark.asyncio
async def test_save_new_state_creates_parent_and_survives_repository_recreation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "nested" / "conversations.sqlite3"
    repository = SqliteConversationRepository(db_path)

    record = await repository.save(_state("c1"), expected_version=None)
    restored = await SqliteConversationRepository(db_path).load("c1")

    assert db_path.parent.is_dir()
    assert record.version == 1
    assert restored is not None
    assert restored.version == 1
    assert restored.state == _state("c1")


@pytest.mark.asyncio
async def test_save_updates_only_the_expected_version(tmp_path: Path) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversations.sqlite3")
    state = _state("c1")
    await repository.save(state, expected_version=None)

    updated = await repository.save(state, expected_version=1)

    assert updated.version == 2


@pytest.mark.asyncio
async def test_stale_version_returns_retryable_conversation_conflict(tmp_path: Path) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversations.sqlite3")
    state = _state("c1")
    await repository.save(state, expected_version=None)
    await repository.save(state, expected_version=1)

    with pytest.raises(ServiceError) as captured:
        await repository.save(state, expected_version=1)

    assert captured.value.code == "CONVERSATION_CONFLICT"
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_insert_of_existing_conversation_returns_retryable_conflict(
    tmp_path: Path,
) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversations.sqlite3")
    await repository.save(_state("c1"), expected_version=None)

    with pytest.raises(ServiceError) as captured:
        await repository.save(_state("c1"), expected_version=None)

    assert captured.value.code == "CONVERSATION_CONFLICT"
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_conversations_remain_isolated(tmp_path: Path) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversations.sqlite3")
    await repository.save(_state("c1"), expected_version=None)
    second_state = _state("c2").model_copy(
        update={"query_snapshot": QuerySnapshot(sub_category="跑步鞋")}
    )
    await repository.save(second_state, expected_version=None)

    first_record = await repository.load("c1")
    second_record = await repository.load("c2")

    assert first_record is not None
    assert second_record is not None
    assert first_record.state.query_snapshot is not None
    assert second_record.state.query_snapshot is not None
    assert first_record.state.query_snapshot.sub_category == "蓝牙耳机"
    assert second_record.state.query_snapshot.sub_category == "跑步鞋"


@pytest.mark.asyncio
async def test_state_json_is_compact_domain_state_without_product_body(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversations.sqlite3"
    state = _state("c1")
    repository = SqliteConversationRepository(db_path)
    await repository.save(state, expected_version=None)

    async with aiosqlite.connect(db_path) as connection:
        cursor = await connection.execute(
            "SELECT state_json FROM conversation_state WHERE conversation_id = ?",
            ("c1",),
        )
        row = await cursor.fetchone()
        await cursor.close()

    assert row is not None
    assert row[0] == state.model_dump_json()
    assert "product_body" not in row[0]
    assert "sku_list" not in row[0]
    assert "qdrant_chunk" not in row[0]


@pytest.mark.asyncio
async def test_sqlite_errors_are_normalized_without_secret_or_path_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_path = str(tmp_path / "secret-path.sqlite3")
    repository = SqliteConversationRepository(secret_path)

    def fail_connect(*args: object, **kwargs: object) -> object:
        raise aiosqlite.OperationalError(f"secret path: {secret_path}")

    monkeypatch.setattr(aiosqlite, "connect", fail_connect)

    with pytest.raises(ServiceError) as captured:
        await repository.load("c1")

    assert captured.value.code == "CONVERSATION_UNAVAILABLE"
    assert captured.value.retryable is True
    assert captured.value.message == "conversation storage unavailable"
    assert secret_path not in captured.value.message
    assert "secret" not in captured.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_json",
    [
        "secret-marker malformed JSON",
        '{"schema_version": 2, "conversation_id": "secret-marker"}',
    ],
)
async def test_invalid_persisted_state_is_normalized_without_content_leakage(
    tmp_path: Path,
    state_json: str,
) -> None:
    repository = SqliteConversationRepository(tmp_path / "conversations.sqlite3")
    await repository.load("missing")

    async with aiosqlite.connect(tmp_path / "conversations.sqlite3") as connection:
        await connection.execute(
            """
            INSERT INTO conversation_state (
                conversation_id, version, state_json, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            ("c1", 1, state_json, "2026-07-26T00:00:00+00:00"),
        )
        await connection.commit()

    with pytest.raises(ServiceError) as captured:
        await repository.load("c1")

    assert captured.value.code == "CONVERSATION_UNAVAILABLE"
    assert captured.value.retryable is True
    assert captured.value.message == "conversation storage unavailable"
    assert "secret-marker" not in str(captured.value)
