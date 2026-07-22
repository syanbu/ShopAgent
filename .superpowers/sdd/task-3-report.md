# Task 3: Deterministic Evidence Chunking Report

## Status

Completed.

## RED

- Added `tests/unit/test_chunking.py` before production code.
- Ran `uv run pytest tests/unit/test_chunking.py -q`.
- Confirmed collection failed for the expected reason: `ModuleNotFoundError: No module named 'shop_agent.chunking'`.

## GREEN

- Added `src/shop_agent/chunking.py`.
- `build_product_chunks(product, source_path)` creates one product-summary chunk, one chunk per official FAQ, and one chunk per user review.
- Point IDs use `uuid5(NAMESPACE_URL, chunk_id)` and remain deterministic for the same chunk ID.
- Chunk text includes product summary, FAQ question/answer, review rating/content, and preserves the supplied source path.

## Verification

- `uv run pytest tests/unit/test_chunking.py -q` — 2 passed.
- `uv run ruff check src/shop_agent/chunking.py tests/unit/test_chunking.py` — passed.
- `uv run mypy src/shop_agent/chunking.py` — passed with no issues.

## Files

- Created: `src/shop_agent/chunking.py`
- Created: `tests/unit/test_chunking.py`
- Updated: `docs/README.md`
- Updated: `docs/features/text-shopping-workflow.md`

## Documentation

- Kept the text-shopping workflow marked as development in progress.
- Added the concrete `chunking.py` code entry and a development change-log record for deterministic evidence chunking.

## Self-review

- Scope is limited to Task 3; no gateway, Qdrant, or later workflow work was added.
- No Git commands or Git operations were performed.
- The requested tests cover chunk count, summary identity, UUID5 point ID generation, product identity, and deterministic IDs.

## Review Follow-up: Test Coverage

- Expanded `tests/unit/test_chunking.py` without modifying production code.
- Added exact ordered assertions for the summary, each FAQ, and each review: chunk ID, chunk type, text, and complete source-path propagation.
- Added direct UUID5 namespace assertions for every generated point ID while retaining the repeated-call determinism test.
- Added a deep-copied whitespace fixture case to verify all generated chunk text is stripped at its outer boundaries.

## Review Follow-up Verification

- `uv run pytest tests/unit/test_chunking.py -q` — 4 passed.
- `uv run ruff check src/shop_agent/chunking.py tests/unit/test_chunking.py` — passed.
- `uv run mypy src/shop_agent/chunking.py` — passed with no issues.
- No Git commands or Git operations were performed.
