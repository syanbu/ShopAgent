from typing import Any


async def cleanup_qdrant_test_collection(
    client: Any,
    collection_name: str,
    *,
    server_reachable: bool,
    suppress_errors: bool,
) -> None:
    cleanup_error: Exception | None = None
    if server_reachable:
        try:
            if (
                not collection_name.startswith("test_product_text_chunks_")
                or collection_name == "product_text_chunks_v1"
            ):
                raise AssertionError("unsafe test collection name")
            if await client.collection_exists(collection_name):
                await client.delete_collection(collection_name)
        except Exception as exc:
            cleanup_error = exc
    try:
        await client.close()
    except Exception as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if cleanup_error is not None and not suppress_errors:
        raise cleanup_error
