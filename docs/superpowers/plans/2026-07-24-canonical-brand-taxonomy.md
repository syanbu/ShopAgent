# Canonical Brand Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize duplicate brand spellings in the source catalog, constrain structured intent brands to exact catalog values, and rebuild the derived Qdrant collection from the updated JSON.

**Architecture:** The source JSON remains the only product fact source. `ProductCatalog` exposes its canonical brand vocabulary; the intent prompt places that vocabulary in both taxonomy metadata and the JSON Schema array item enums, while a runtime validator retries one out-of-vocabulary model response and rejects a second. The derived `product_text_chunks_v1` collection is deleted explicitly and rebuilt from all current source products.

**Tech Stack:** Python 3.11+, Pydantic v2, DashScope JSON mode, Qdrant, pytest.

## Global Constraints

- Canonicalize Apple as `Apple 苹果`, Nike as `Nike 耐克`, and The North Face as `北面`.
- Do not change product titles, product IDs, SKU data, category data, or Chunk IDs.
- Never silently drop an explicit out-of-vocabulary brand constraint.
- Delete only the `product_text_chunks_v1` Qdrant collection, not the Docker volume or unrelated collections.
- Do not execute Git commands without separate authorization.

---

### Task 1: Normalize the source catalog

**Files:**
- Modify: `ecommerce_agent_dataset/2_数码电子/data/p_digital_013.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_003.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_006.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_007.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_011.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_018.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_019.json`
- Modify: `ecommerce_agent_dataset/3_服饰运动/data/p_clothes_021.json`
- Test: `tests/unit/test_catalog.py`

**Interfaces:**
- Consumes: product JSON `brand: str`.
- Produces: one canonical brand string per real-world brand.

- [x] Add a failing catalog test that expects sorted, unique canonical brands.
- [x] Run the focused catalog test and verify it fails because `ProductCatalog.brands()` does not exist.
- [x] Add `ProductCatalog.brands() -> list[str]`.
- [x] Replace the eight legacy `brand` values with the approved canonical values.
- [x] Verify all 100 JSON files parse and the legacy exact values are absent.

### Task 2: Constrain structured intent brands

**Files:**
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Modify: `src/shop_agent/api/dependencies.py`
- Modify: `tests/live/test_live_shopping_flow.py`
- Modify: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: `brands: Sequence[str]` from `ProductCatalog.brands()`.
- Produces: `ParsedIntent.constraints.include_brands` and `exclude_brands` containing only exact catalog brand values.

- [x] Add failing prompt tests for brand taxonomy and JSON Schema item enums.
- [x] Add a failing parser test where the first response uses `苹果` and the corrected response uses `Apple 苹果`.
- [x] Add a failing parser test where two responses remain outside the catalog and produce `INTENT_PARSE_FAILED`.
- [x] Run the focused gateway tests and verify the expected failures.
- [x] Pass `brands` through `_build_intent_system_prompt()` and `DashScopeIntentParser`.
- [x] Validate both brand constraint arrays inside the structured-call validator; retry once on a `ValueError`.
- [x] Supply `catalog.brands()` from API and live-test dependency assembly.
- [x] Run focused gateway, catalog, filter, and workflow tests.

### Task 3: Document and verify behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/features/text-shopping-workflow.md`
- Modify: `docs/superpowers/plans/2026-07-24-canonical-brand-taxonomy.md`

**Interfaces:**
- Produces: documented canonical-brand and Qdrant rebuild behavior.

- [x] Document that brand constraints use exact source-catalog values and are runtime-validated.
- [x] Document that indexed brand or Chunk facts require a full Qdrant reindex.
- [x] Add a 2026-07-24 behavior-change record.
- [x] Run Ruff formatting and lint, mypy, and all non-live tests.
- [x] Confirm the dataset still produces exactly 992 Chunks.

### Task 4: Rebuild and perform live acceptance

**Interfaces:**
- Deletes and recreates: Qdrant collection `product_text_chunks_v1`.
- Consumes: all current JSON products and DashScope embeddings.
- Produces: 992 Qdrant Points with canonical `brand` payloads.

- [x] Start local Qdrant and delete only `product_text_chunks_v1`.
- [x] Run `python -m shop_agent.cli.index_products` to rebuild every Point.
- [x] Verify collection point count is 992.
- [x] Inspect payloads and confirm no exact legacy brand value remains.
- [x] Restart or invoke the application with the updated catalog.
- [x] Send “推荐手机，不要苹果” and confirm the parsed intent contains `exclude_brands:["Apple 苹果"]`.
- [x] Confirm returned product events contain no Apple products.
