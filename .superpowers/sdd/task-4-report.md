# Task 4 Report: DashScope / Qdrant Gateways and Offline Indexer

## Status

`DONE_WITH_CONCERNS`

- Task 4 production code, unit tests, documentation, Compose definition, and deterministic local verification are complete.
- Local-Qdrant integration is **skipped / not verified** because the Docker Desktop Linux daemon is not running. This report does not count the skip as a passing integration test.
- No Git command or Git operation was executed.
- Task 5 retrieval/evidence workflow and later API/workflow tasks were not implemented.

## TDD RED

Command:

```text
uv run pytest tests/unit/test_model_gateways.py tests/unit/test_qdrant_filters.py -q
```

First result: exit code `1`, expected collection failure before production implementation.

```text
ERROR tests/unit/test_model_gateways.py
ModuleNotFoundError: No module named 'shop_agent.cli'

ERROR tests/unit/test_qdrant_filters.py
ModuleNotFoundError: No module named 'shop_agent.services.qdrant_store'

Interrupted: 2 errors during collection
```

The RED failure was caused by the missing Task 4 modules, not by a typo or unrelated environment error.

## GREEN and deterministic verification

Prescribed Task 4 unit command, final result:

```text
.................                                                        [100%]
17 passed in 3.41s
EXIT_CODE=0
```

The first GREEN attempt produced `16 passed, 1 failed`. The single root cause was that source-priority rules were present only in the system message while the test required the evidence input prompt itself to carry the same rules. The minimal fix put `official_faq > product_summary > user_review` and the user-review restriction in the evidence input prompt. The next run passed 17/17.

Full unit regression suite:

```text
............................                                             [100%]
28 passed in 5.26s
EXIT_CODE=0
```

Prescribed Ruff scope, followed by an expanded check including the integration test:

```text
All checks passed!
EXIT_CODE=0
```

Prescribed mypy scope:

```text
Success: no issues found in 8 source files
MYPY_EXIT=0
```

Compose syntax validation:

```text
docker compose config -q
EXIT_CODE=0
```

## Installed SDK contracts inspected

Installed versions in the locked `uv` environment:

- `dashscope 1.26.4`
- `openai 2.46.0`
- `qdrant-client 1.18.0`

Inspected signatures:

```text
TextEmbedding.call(model, input, workspace=None, api_key=None,
                   text_type=None, dimension=None, output_type=None,
                   instruct=None, **kwargs)

TextReRank.call(model, query, documents, return_documents=None,
                top_n=None, api_key=None, instruct=None, **kwargs)

AsyncQdrantClient.query_points(collection_name, query=..., query_filter=...,
                               limit=..., with_payload=...) -> QueryResponse
```

No runtime signature mismatch with the Task 4 plan was found. DashScope 1.26.4 has no `py.typed` marker, so its two import boundaries carry targeted `# type: ignore[import-untyped]`; request shapes are protected by unit tests against complete fake response structures.

Implemented gateway contract:

- `AsyncOpenAI` is constructed with the configured base URL, API key, and timeout.
- Structured chat calls use `qwen3.7-max`, JSON object mode, and `enable_thinking=False`.
- Structured output is validated by Pydantic, with no more than one correction retry containing the original output and validation error.
- Intent/evidence upstream failures map to retryable parse errors; streaming failures are logged and exposed as the fixed retryable generation error.
- Document/query embeddings use distinct `text_type` values, 1024-dimensional dense output, and SDK sync calls run through `asyncio.to_thread`.
- Reranking uses `qwen3-rerank`, `top_n=len(documents)`, `return_documents=False`, and the specified instruction.
- DashScope SDK base URL is assigned once in each embedding/reranking gateway constructor, not per request.

Implemented Qdrant/index contract:

- Versioned production collection default: `product_text_chunks_v1`.
- Vectors: 1024 dimensions, Cosine distance.
- Keyword indexes: `product_id`, `category`, `sub_category`, `brand`, `chunk_type`.
- Float indexes: `min_sku_price`, `max_sku_price`.
- Existing collections are not deleted or recreated.
- Filters implement category/subcategory, include/exclude brand, and SKU price-overlap rules.
- Search uses `with_payload=True` and the configured limit. Every payload is validated into `RetrievedChunk`; an invalid payload fails the whole search as non-retryable `RETRIEVAL_UNAVAILABLE`.
- Offline embedding/upsert batches are capped at 20 points. Point IDs are the deterministic chunk UUIDs, so repeated upsert is idempotent.
- Payload includes evidence text metadata, category, subcategory, brand, SKU price bounds, and the source JSON relative path.
- CLI prints JSON counts for products, chunks, and upserted points.
- Compose contains only Qdrant, exposes port 6333, uses the named `qdrant_data` volume, and checks `/healthz`.

## Docker and integration raw result

`docker compose up -d qdrant` was attempted once inside the sandbox and once with the required escalation. Both returned exit code `1` with the same output:

```text
unable to get image 'qdrant/qdrant:latest': failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the
daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find
the file specified.
```

No attempt was made to start the Docker Desktop GUI.

The first integration invocation reached `http://127.0.0.1:6333` and received an upstream `502 Bad Gateway`. It also exposed a test-cleanup defect: `pytest.skip` was being masked because `finally` queried `collection_exists` even though no collection had been created. The cleanup was narrowed to run only after this test has successfully created its unique collection. It verifies both the `test_product_text_chunks_` prefix and inequality with `product_text_chunks_v1` before deletion.

Final integration command:

```text
uv run pytest tests/integration/test_qdrant_store.py -q
```

Final result:

```text
s                                                                        [100%]
1 skipped in 4.96s
EXIT_CODE=0
```

Interpretation: collection creation, deterministic vector upsert, brand/price filtering, and cleanup against a real local Qdrant remain **blocked and unverified**. No collection was created or deleted in this run, and the production collection was never targeted.

## Files

Created:

- `src/shop_agent/services/__init__.py`
- `src/shop_agent/services/ports.py`
- `src/shop_agent/services/dashscope_chat.py`
- `src/shop_agent/services/dashscope_embedding.py`
- `src/shop_agent/services/dashscope_rerank.py`
- `src/shop_agent/services/qdrant_store.py`
- `src/shop_agent/cli/__init__.py`
- `src/shop_agent/cli/index_products.py`
- `compose.yaml`
- `tests/unit/test_model_gateways.py`
- `tests/unit/test_qdrant_filters.py`
- `tests/integration/test_qdrant_store.py`

Updated:

- `docs/features/text-shopping-workflow.md`
- `docs/README.md`

## Documentation and self-review

- The feature remains marked `开发中`; it was not prematurely marked complete.
- The document index now lists the service gateways, offline indexer, and Compose entry points.
- The feature document states that gateway/Qdrant/index infrastructure exists while online retrieval, evidence workflow, and API work remain pending.
- No secrets were added.
- No production collection delete/recreate path exists.
- The Compose image currently follows `qdrant/qdrant:latest`, as the Task 4 plan did not prescribe a server version. This is a reproducibility concern to resolve when a supported deployment version is chosen.
- Real DashScope calls were not made in Task 4; SDK calls were verified with fakes as required. Real Qdrant integration remains blocked by the missing daemon.

## 2026-07-23 Important review remediation

Status remains `DONE_WITH_CONCERNS`: both review findings are fixed and locally verified, while real Qdrant integration remains skipped / not verified because the Docker daemon is unavailable. No Git command or operation was executed.

### A. DashScope successful-response validation

Focused RED command:

```text
uv run pytest tests/unit/test_model_gateways.py -q -k "restore_input_order or malformed_success or preserves_normalized"
```

RED result:

```text
25 failed, 2 passed, 11 deselected in 3.07s
EXIT_CODE=1
```

The two immediately passing cases were behaviors already covered by the previous implementation: a short embedding result and an embedding with the wrong dimension. The 25 failures demonstrated the missing behavior:

- reversed embedding `text_index` values were returned in response-array order;
- missing successful-response fields leaked `AttributeError`, `KeyError`, or `ValueError`;
- duplicate/out-of-range indexes and bool/non-numeric/NaN/Inf values were accepted;
- pre-normalized `ServiceError` instances raised at the SDK boundary were wrapped again.

Minimal production changes:

- `dashscope_embedding.py` now requires a list whose length exactly matches the request, validates each item and a unique integer `text_index` covering `0..n-1`, validates exactly 1024 finite real values with bool explicitly rejected, converts values to float, and returns vectors in input order.
- `dashscope_rerank.py` now requires exactly one result per document, validates unique in-range integer indexes, validates finite real scores with bool explicitly rejected, and preserves the response ranking order.
- Missing/malformed HTTP-200 embedding results map to retryable `ServiceError("EMBEDDING_UNAVAILABLE", "invalid embedding response")`.
- Missing/malformed HTTP-200 rerank results map to retryable `ServiceError("RERANK_UNAVAILABLE", "invalid rerank response")`.
- Both SDK call boundaries re-raise an existing `ServiceError` unchanged before their general exception mapping.

Focused GREEN result:

```text
...........................                                              [100%]
27 passed, 11 deselected in 2.35s
EXIT_CODE=0
```

### B. Integration collection cleanup

A tests-only cleanup helper was introduced; no production delete capability was added.

Cleanup helper RED:

```text
uv run pytest tests/unit/test_qdrant_cleanup.py -q

ModuleNotFoundError: No module named 'tests'
1 error during collection
EXIT_CODE=1
```

Cleanup helper GREEN:

```text
.....                                                                    [100%]
5 passed in 0.04s
EXIT_CODE=0
```

The integration lifecycle now:

- always closes the Qdrant client in `finally`;
- does not probe collection state if the initial server reachability check failed, so cleanup cannot mask the Qdrant-unavailable skip;
- after the server has been reachable, checks and deletes the unique test collection even if `ensure_collection()` failed partway through;
- validates the `test_product_text_chunks_` prefix and rejects the production name before any delete;
- suppresses cleanup/close failures only while an original exception or pytest skip is already in flight, so cleanup never replaces the primary result;
- propagates cleanup failures when the test body otherwise succeeded, keeping leaked test resources visible.

Files added for tests-only lifecycle support:

- `tests/__init__.py`
- `tests/qdrant_cleanup.py`
- `tests/unit/test_qdrant_cleanup.py`

Files updated by the review remediation:

- `src/shop_agent/services/dashscope_embedding.py`
- `src/shop_agent/services/dashscope_rerank.py`
- `tests/unit/test_model_gateways.py`
- `tests/integration/test_qdrant_store.py`

### Final review verification

Task 4 planned unit scope:

```text
............................................                             [100%]
44 passed in 2.45s
EXIT_CODE=0
```

Full unit suite:

```text
............................................................             [100%]
60 passed in 2.69s
EXIT_CODE=0
```

Ruff across Task 4 code and all related tests:

```text
All checks passed!
EXIT_CODE=0
```

mypy:

```text
Success: no issues found in 8 source files
MYPY_EXIT=0
```

Integration command:

```text
uv run pytest tests/integration/test_qdrant_store.py -q
```

Integration result:

```text
s                                                                        [100%]
1 skipped in 4.00s
EXIT_CODE=0
```

The integration result is a skip, not a pass. Real collection creation/upsert/filter/delete behavior remains unverified until the local Docker/Qdrant daemon is available.
