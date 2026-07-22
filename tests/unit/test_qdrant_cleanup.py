from unittest.mock import AsyncMock

import pytest

from tests.qdrant_cleanup import cleanup_qdrant_test_collection


@pytest.mark.asyncio
async def test_cleanup_deletes_safe_collection_and_always_closes() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    collection_name = "test_product_text_chunks_unique"

    await cleanup_qdrant_test_collection(
        client,
        collection_name,
        server_reachable=True,
        suppress_errors=False,
    )

    client.collection_exists.assert_awaited_once_with(collection_name)
    client.delete_collection.assert_awaited_once_with(collection_name)
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_does_not_probe_when_server_was_never_reachable() -> None:
    client = AsyncMock()

    await cleanup_qdrant_test_collection(
        client,
        "test_product_text_chunks_unique",
        server_reachable=False,
        suppress_errors=True,
    )

    client.collection_exists.assert_not_awaited()
    client.delete_collection.assert_not_awaited()
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_error_does_not_override_primary_error() -> None:
    client = AsyncMock()
    client.collection_exists.side_effect = RuntimeError("cleanup failed")

    await cleanup_qdrant_test_collection(
        client,
        "test_product_text_chunks_unique",
        server_reachable=True,
        suppress_errors=True,
    )

    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_error_is_raised_without_primary_error() -> None:
    client = AsyncMock()
    expected = RuntimeError("cleanup failed")
    client.collection_exists.side_effect = expected

    with pytest.raises(RuntimeError) as error:
        await cleanup_qdrant_test_collection(
            client,
            "test_product_text_chunks_unique",
            server_reachable=True,
            suppress_errors=False,
        )

    assert error.value is expected
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_rejects_unsafe_collection_name_but_still_closes() -> None:
    client = AsyncMock()

    with pytest.raises(AssertionError, match="unsafe test collection name"):
        await cleanup_qdrant_test_collection(
            client,
            "product_text_chunks_v1",
            server_reachable=True,
            suppress_errors=False,
        )

    client.collection_exists.assert_not_awaited()
    client.delete_collection.assert_not_awaited()
    client.close.assert_awaited_once_with()
