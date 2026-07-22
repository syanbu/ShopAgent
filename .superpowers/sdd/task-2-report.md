# Task 2: Product Catalog and Image Access Report

## Status

Completed Task 2 only. No Git commands or Git operations were performed.

## TDD evidence

### RED

Command:

```powershell
uv run pytest tests/unit/test_catalog.py -q
```

Output before `src/shop_agent/catalog.py` existed:

```text
==================================== ERRORS ====================================
_________________ ERROR collecting tests/unit/test_catalog.py _________________
ImportError while importing test module 'D:\\Desktop\\engine\\ShopAgent\\tests\\unit\\test_catalog.py'.
...
tests\\unit\\test_catalog.py:5: in <module>
    from shop_agent.catalog import ProductCatalog
E   ModuleNotFoundError: No module named 'shop_agent.catalog'
=========================== short test summary info ===========================
ERROR tests/unit/test_catalog.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.43s
```

### GREEN and static verification

```powershell
uv run pytest tests/unit/test_catalog.py -q
```

```text
...                                                                      [100%]
3 passed in 0.18s
```

```powershell
uv run ruff check src/shop_agent/catalog.py tests/conftest.py tests/unit/test_catalog.py
```

```text
All checks passed!
```

```powershell
uv run mypy src/shop_agent/catalog.py
```

```text
Success: no issues found in 1 source file
```

## Files

- Created `src/shop_agent/catalog.py`: loads and validates product JSON, indexes products and relative source paths, resolves image paths within the dataset root, and filters SKU prices by `SearchConstraints`.
- Created `tests/conftest.py`: fixture product JSON matches the Task 1 `Product` schema and creates an image at the same dataset-relative path.
- Created `tests/unit/test_catalog.py`: verifies fixture catalog loading/image resolution, max-price SKU selection, and the repository's 100-product dataset.
- Updated `docs/features/text-shopping-workflow.md`: registered the real catalog entry point and an in-progress catalog/image-access change.
- Updated `docs/README.md`: retained `开发中` and added the catalog entry point to the feature index.

## Self-review

- Scope is limited to Task 2; no chunking, Qdrant indexing, API, or workflow code was added.
- The loader treats product JSON as the source of truth, validates each document through `Product`, rejects duplicate IDs and empty datasets, and preserves source paths relative to the dataset root.
- Image resolution requires the resolved image path to remain inside the resolved dataset root; no image bytes enter application state.
- Budget filtering uses inclusive `min_price`/`max_price` checks against actual SKU prices.
- The fixture and repository dataset both use `image_path` values relative to their dataset roots. The sampled production JSON includes all Task 1 `Product` fields, so no schema change was needed.

## Review follow-up

- Updated `docs/features/text-shopping-workflow.md` to state accurately that the base configuration, schemas, and product catalog are created, while indexing, workflow, and API work remain for later tasks.
- Verified the feature remains marked `开发中` and its documented code entry includes `src/shop_agent/catalog.py`.
