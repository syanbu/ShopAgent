# Stable Multi-Turn Refinement Implementation Plan

> **For agentic workers:** Execute inline with TDD. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Make multi-turn product refinement preserve still-eligible products when
hard constraints tighten, fill only with products not yet shown in the current
shopping task, and use full reranking when preferences change or constraints
relax.

**Architecture:** Keep `TurnQuery` as the model-owned incremental input, but add
deterministic approximate-price and ordered-preference operations. After compiling
the new `QuerySnapshot`, compare old and new hard constraints to choose one of
three result strategies: stable refinement, full rerank, or more results. Stable
refinement excludes all previously seen products from the global retrieval while
injecting the latest displayed products into the same evidence-validation path;
the final selector keeps eligible old products in their original order and fills
remaining slots from ranked unseen products.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph, Qdrant, pytest,
pytest-asyncio, Ruff, mypy.

## Product Rules

- Explicit restrictive wording such as “不要”“只要”“必须”“以内”“至少” produces
  hard constraints. Expressions without restrictive wording are soft preferences.
- “5000 左右” is a target range of `4500..5500`; “预算大概 5000” is a flexible
  maximum of `5500`; strict bounds remain exact. The default tolerance is 10%,
  overridden by an explicit amount or percentage.
- Tightening hard constraints keeps eligible latest products in their original
  relative order, removes ineligible products, and fills up to three slots with
  products never shown in the current shopping task.
- If unseen fillers are exhausted, return fewer than three products. Do not reuse
  older products merely to fill the list.
- Relaxing or removing a hard constraint performs a full rerank.
- Adding, removing, or reprioritizing a soft preference performs a full rerank.
  Soft preferences accumulate in priority order.
- A mixed turn applies hard filters first and then performs a full rerank because
  the soft ranking objective changed.
- Every successful refinement re-emits the complete current product list. Product
  cards gain no “retained” or “new” labels.
- The assistant briefly acknowledges the applied refinement and states when fewer
  than three products are displayed, without claiming an exhaustive catalog count.
- Product cards continue to expose only matching SKUs, and `display_price` remains
  the minimum price among those matching SKUs.
- Query conditions and `seen_product_ids` persist throughout the same shopping
  task. A new search or category switch resets them; explicit inheritance in the
  new turn writes them again.

## Global Constraints

- Do not execute Git commands.
- Update `docs/features/multi-turn-query-engine.md` in the same behavior change.
- Keep `POST /api/v1/chat/stream` and all SSE event names backward compatible.
- Do not add card-label fields, a new database table, a new service, or a schema
  migration.
- Keep product references restricted to `recent_candidates`; accumulated
  `seen_product_ids` never expands the reference domain.
- Product JSON remains authoritative and Qdrant remains a derived retrieval index.

---

## Task 1: Extend the turn-query contract

**Files:**
- Modify: `src/shop_agent/models/turn_query.py`
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Modify: `tests/unit/test_turn_query_models.py`
- Modify: `tests/unit/test_model_gateways.py`

- [x] Add `prioritize` to semantic-term operations. It moves a preference to the
  front without duplicating it.
- [x] Add an `ApproximatePrice` contract with target/budget-cap modes and
  percent/absolute tolerances.
- [x] Reject approximate price combined with direct price-bound or relative-price
  operations in the same structured turn.
- [x] Update the parser prompt so only explicit restrictive wording produces hard
  feature/brand/SKU constraints, while ordinary preference wording produces
  ordered semantic operations.
- [x] Add prompt and model tests for default 10%, explicit tolerance, ordered
  preferences, and invalid mixed price representations.

## Task 2: Compile result strategy deterministically

**Files:**
- Modify: `src/shop_agent/models/state.py`
- Modify: `src/shop_agent/services/multi_turn_query_compiler.py`
- Modify: `tests/unit/test_multi_turn_query_compiler.py`

- [x] Compile approximate target price to symmetric min/max bounds and approximate
  budget to a maximum bound, using `Decimal` and two-decimal output.
- [x] Compare old and new hard constraints across prices, included/excluded brands,
  required/excluded features, SKU allowed values, and numeric conditions.
- [x] Select `stable_refine` only for a pure hard-constraint tightening.
- [x] Select `full_rerank` for soft mutations, hard relaxation, mixed
  tighten/relax changes, new searches, and category switches.
- [x] Preserve the in-flight latest candidates and accumulated seen IDs for
  refinements; reset task history only for a new search or category switch.
- [x] Add focused compiler tests for tightening, relaxation, mixed changes,
  ordered soft preferences, approximate prices, and input immutability.

## Task 3: Preserve eligible products and fill from unseen inventory

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `tests/unit/test_multi_turn_workflow.py`
- Modify: `tests/integration/test_chat_api.py`

- [x] During stable refinement, globally retrieve with every task-level seen ID
  excluded.
- [x] Fetch the latest displayed products by exact `product_id`, convert their
  chunks into the normal candidate stream, and deduplicate chunks before
  aggregation.
- [x] Run old and new products through the same rerank, structured SKU checks, and
  evidence validation.
- [x] Select eligible latest products in their original relative order, then
  append ranked unseen fillers up to the configured limit.
- [x] Persist the newly displayed batch as `recent_candidates` while accumulating
  task-level `seen_product_ids`.
- [x] For stable refinement with no qualifying products, persist an empty latest
  batch but retain task-level seen history.
- [x] Add workflow and HTTP regressions for survivor ordering, ineligible removal,
  unseen fill, exhausted fill, hard relaxation, soft rerank, latest-batch
  references, and matching-SKU prices.

## Task 4: Keep the response and documentation aligned

**Files:**
- Modify: `src/shop_agent/workflow/nodes.py`
- Modify: `docs/features/multi-turn-query-engine.md`
- Modify: `docs/README.md` only if its feature index or code-entry list changes

- [x] In refinement response prompts, require a short acknowledgement of the
  applied condition and an honest displayed count when fewer than three products
  are returned, without claiming a full-catalog count.
- [x] Document hard versus soft behavior, tightening versus relaxation, default
  price tolerance, task-level seen history, stable result ordering, no forced
  refill, and matching-SKU card behavior.
- [x] Add a dated feature change record.

## Task 5: Verification and review

- [x] Run focused model, compiler, workflow, and HTTP tests.
- [x] Run the complete pytest suite.
- [x] Run Ruff and mypy using the repository’s existing commands.
- [x] Review the no-Git before/after diff for scope drift, unknown identifiers,
  persistence invariants, and sibling result-selection paths.
- [x] Confirm no dependency, public SSE schema, database schema, or card-label
  change was introduced.

## Acceptance Scenarios

1. `小米 / OPPO / Samsung` followed by “不要小米” returns
   `OPPO / Samsung / <unseen filler>` in that order.
2. The same turn with no unseen filler returns only `OPPO / Samsung`.
3. “小米也可以” removes Xiaomi from `exclude_brands` and fully reranks all
   eligible products.
4. “拍照优先” fully reranks; “续航也重要” appends a lower-priority preference;
   “续航更重要” moves it to the highest priority.
5. “不要小米，拍照优先” excludes Xiaomi and fully reranks because the soft
   preference changed.
6. “5000 左右” compiles to `4500..5500`; “预算大概 5000” compiles to
   `max_price=5500`; explicit tolerance overrides 10%.
7. A product remains eligible when at least one SKU satisfies every hard
   constraint; its card contains only matching SKUs and uses their minimum price.
8. After a refinement, “第二个怎么样” resolves only against the complete list
   emitted by that refinement.
