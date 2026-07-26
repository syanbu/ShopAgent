"""SQLite persistence for compact conversation state."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aiosqlite
from pydantic import ValidationError

from shop_agent.errors import ServiceError
from shop_agent.models import ConversationRecord, ConversationState


class ConversationRepository(Protocol):
    async def load(self, conversation_id: str) -> ConversationRecord | None:
        raise NotImplementedError

    async def save(
        self,
        state: ConversationState,
        *,
        expected_version: int | None,
    ) -> ConversationRecord:
        raise NotImplementedError


class SqliteConversationRepository:
    """Persist one versioned conversation state per SQLite row."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = asyncio.Lock()
        self._schema_initialized = False

    async def load(self, conversation_id: str) -> ConversationRecord | None:
        try:
            await self._ensure_schema()
            async with aiosqlite.connect(self._database_path) as connection:
                cursor = await connection.execute(
                    """
                    SELECT version, state_json
                    FROM conversation_state
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
        except aiosqlite.Error as error:
            raise self._unavailable_error() from error

        if row is None:
            return None
        try:
            return ConversationRecord(
                state=ConversationState.model_validate_json(row[1]),
                version=row[0],
            )
        except ValidationError:
            raise self._unavailable_error() from None

    async def save(
        self,
        state: ConversationState,
        *,
        expected_version: int | None,
    ) -> ConversationRecord:
        next_version = 1 if expected_version is None else expected_version + 1
        state_json = state.model_dump_json()
        updated_at = datetime.now(UTC).isoformat()

        try:
            await self._ensure_schema()
            async with aiosqlite.connect(self._database_path) as connection:
                if expected_version is None:
                    await self._insert_new_state(
                        connection,
                        state=state,
                        state_json=state_json,
                        updated_at=updated_at,
                    )
                else:
                    await self._update_existing_state(
                        connection,
                        state=state,
                        expected_version=expected_version,
                        next_version=next_version,
                        state_json=state_json,
                        updated_at=updated_at,
                    )
                await connection.commit()
        except ServiceError:
            raise
        except aiosqlite.Error as error:
            raise self._unavailable_error() from error

        return ConversationRecord(state=state, version=next_version)

    async def _ensure_schema(self) -> None:
        if self._schema_initialized:
            return

        async with self._schema_lock:
            if self._schema_initialized:
                return
            async with aiosqlite.connect(self._database_path) as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_state (
                        conversation_id TEXT PRIMARY KEY,
                        version INTEGER NOT NULL,
                        state_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                await connection.commit()
            self._schema_initialized = True

    async def _insert_new_state(
        self,
        connection: aiosqlite.Connection,
        *,
        state: ConversationState,
        state_json: str,
        updated_at: str,
    ) -> None:
        try:
            await connection.execute(
                """
                INSERT INTO conversation_state (
                    conversation_id, version, state_json, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (state.conversation_id, 1, state_json, updated_at),
            )
        except aiosqlite.IntegrityError as error:
            raise self._conflict_error() from error

    async def _update_existing_state(
        self,
        connection: aiosqlite.Connection,
        *,
        state: ConversationState,
        expected_version: int,
        next_version: int,
        state_json: str,
        updated_at: str,
    ) -> None:
        cursor = await connection.execute(
            """
            UPDATE conversation_state
            SET version = ?, state_json = ?, updated_at = ?
            WHERE conversation_id = ? AND version = ?
            """,
            (
                next_version,
                state_json,
                updated_at,
                state.conversation_id,
                expected_version,
            ),
        )
        try:
            if cursor.rowcount != 1:
                raise self._conflict_error()
        finally:
            await cursor.close()

    @staticmethod
    def _conflict_error() -> ServiceError:
        return ServiceError(
            "CONVERSATION_CONFLICT",
            "conversation state changed; retry the request",
            retryable=True,
        )

    @staticmethod
    def _unavailable_error() -> ServiceError:
        return ServiceError(
            "CONVERSATION_UNAVAILABLE",
            "conversation storage unavailable",
            retryable=True,
        )
