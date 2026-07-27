# Digital Catalog Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Sub-agent execution is explicitly forbidden for this task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 mock smartphones, 8 mock true-wireless earbuds, and 12 matching AI-generated local product images to the repository dataset.

**Architecture:** Keep the existing static JSON files and local images as the sole source of truth. Add products using the current `Product` schema, allow the existing Catalog and taxonomy builders to discover them automatically, and upsert their text chunks through the existing index command. No runtime API, workflow, or storage schema changes are required.

**Tech Stack:** JSON product fixtures, local JPEG images, Pydantic Catalog loading, pytest, Qdrant indexing CLI, AI image generation.

## Global Constraints

- Work inline in the current session; do not use sub-agents.
- Do not run Git commands without a separate explicit authorization for this task.
- Add exactly 12 products: 4 `智能手机` and 8 `真无线耳机`.
- Use product IDs `p_digital_026` through `p_digital_037`.
- Use real brand names with fictional mock model names; document that these are not real release claims.
- Generate one original square AI image per product; do not download Google or manufacturer images.
- Generated images contain no brand logos, model names, specifications, or other text.
- Keep the existing product JSON schema and existing supported SKU keys.
- Update current-state documentation, but do not rewrite historical plans, status reports, or old design snapshots.

---

### Task 1: Lock the expanded Catalog contract with failing tests

**Files:**
- Modify: `tests/unit/test_catalog.py`

**Interfaces:**
- Consumes: `ProductCatalog.load(root)`, `ProductCatalog.all()`, and `ProductCatalog.price_reference(category, sub_category)`.
- Produces: repository-level assertions for 112 products, 14 smartphones, 10 true-wireless earbuds, and all 12 new product IDs.

- [ ] **Step 1: Replace the fixed dataset-size test**

Replace `test_repository_dataset_contains_100_products` with:

```python
def test_repository_dataset_contains_112_products() -> None:
    root = Path("ecommerce_agent_dataset")
    if not root.exists():
        pytest.skip("repository dataset is unavailable")
    catalog = ProductCatalog.load(root)
    products = catalog.all()

    assert len(products) == 112
    assert sum(product.sub_category == "智能手机" for product in products) == 14
    assert sum(product.sub_category == "真无线耳机" for product in products) == 10
    assert {
        f"p_digital_{index:03d}" for index in range(26, 38)
    }.issubset({product.product_id for product in products})

    brands = set(catalog.brands())
    assert {"Apple 苹果", "Nike 耐克", "北面"}.issubset(brands)
    assert brands.isdisjoint({"苹果", "Nike", "耐克", "The North Face"})
```

- [ ] **Step 2: Run the new test and verify the expected failure**

Run:

```bash
uv run pytest tests/unit/test_catalog.py::test_repository_dataset_contains_112_products -q
```

Expected: FAIL because the Catalog still contains 100 products.

- [ ] **Step 3: Defer the smartphone price-reference assertion**

Keep the current smartphone price-reference test unchanged until all four phone JSON files
exist. Its expected sample count and median will be updated from observed Catalog values in
Task 3, rather than manually duplicating the median calculation.

---

### Task 2: Add four smartphone fixtures

**Files:**
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_026.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_027.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_028.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_029.json`

**Interfaces:**
- Consumes: the existing `Product` JSON schema and supported SKU keys `存储`, `版本`, and `颜色`.
- Produces: four Catalog-loadable products with IDs `p_digital_026`–`p_digital_029`.

- [ ] **Step 1: Create the four JSON files using the approved product matrix**

Use this exact structured matrix:

| ID | Brand | Title/model | SKU storage and prices |
|---|---|---|---|
| `026` | `荣耀` | `荣耀 Magic 9 Pro 2K护眼屏潜望长焦旗舰5G智能手机` | 12GB+256GB ¥5499; 16GB+512GB ¥6199; 16GB+1TB ¥6999 |
| `027` | `Samsung 三星` | `Samsung Galaxy S27 AMOLED高刷屏AI影像旗舰5G智能手机` | 12GB+256GB ¥5999; 12GB+512GB ¥6799; 16GB+1TB ¥7599 |
| `028` | `一加 OnePlus` | `一加 OnePlus 16 高刷直屏高性能游戏5G智能手机` | 12GB+256GB ¥4299; 16GB+512GB ¥4999; 24GB+1TB ¥5799 |
| `029` | `Redmi 红米` | `Redmi K100 Pro 高性能长续航电竞5G智能手机` | 12GB+256GB ¥2999; 16GB+512GB ¥3499; 16GB+1TB ¥3999 |

For every file:

- Set `category` to `数码电子` and `sub_category` to `智能手机`.
- Set `base_price` to the first price in the matrix.
- Set `image_path` to `2_数码电子/images/<product_id>_live.jpg`.
- Create one SKU for every storage/price pair, using IDs
  `s_<product_id>_1` through `s_<product_id>_3`.
- Give each SKU the properties `存储`, `版本`, and `颜色`.
- Write one internally consistent marketing description, exactly three FAQ entries, and
  exactly five reviews with ratings covering both positive and negative experiences.
- Cover gaming, screen, camera, battery, charging, heat, storage selection, and realistic
  limitations only where the product's own description establishes those facts.

- [ ] **Step 2: Load the partial dataset**

Run:

```bash
uv run python -c 'from pathlib import Path; from shop_agent.catalog import ProductCatalog; c=ProductCatalog.load(Path("ecommerce_agent_dataset")); assert len(c.all()) == 104; assert sum(p.sub_category == "智能手机" for p in c.all()) == 14; print("smartphones: OK")'
```

Expected: `smartphones: OK`.

---

### Task 3: Add eight true-wireless earbud fixtures and update price baselines

**Files:**
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_030.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_031.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_032.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_033.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_034.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_035.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_036.json`
- Create: `ecommerce_agent_dataset/2_数码电子/data/p_digital_037.json`
- Modify: `tests/unit/test_catalog.py`

**Interfaces:**
- Consumes: the existing Product JSON schema and supported SKU keys `版本` and `颜色`.
- Produces: eight Catalog-loadable products and updated deterministic price-reference assertions.

- [ ] **Step 1: Create the earbud JSON files using the approved product matrix**

Use this exact structured matrix:

| ID | Brand | Title/model | Versions and starting price |
|---|---|---|---|
| `030` | `小米` | `Xiaomi Buds 6 Pro 自适应降噪高解析真无线蓝牙耳机` | 标准版/无线充版, from ¥999 |
| `031` | `vivo` | `vivo TWS 5 Pro 空间音频低延迟真无线蓝牙耳机` | 标准版/空间音频版, from ¥899 |
| `032` | `OPPO` | `OPPO Enco X4 深度降噪双单元真无线蓝牙耳机` | 标准版/礼盒版, from ¥1099 |
| `033` | `Sony 索尼` | `Sony WF-1000XM7 旗舰降噪高解析真无线蓝牙耳机` | 标准版, from ¥1999 |
| `034` | `Samsung 三星` | `Samsung Galaxy Buds4 Pro 智能降噪真无线蓝牙耳机` | 标准版, from ¥1499 |
| `035` | `荣耀` | `荣耀 Earbuds 5 Pro 智慧降噪长续航真无线蓝牙耳机` | 标准版/无线充版, from ¥799 |
| `036` | `一加 OnePlus` | `OnePlus Buds Pro 4 低延迟高解析真无线蓝牙耳机` | 标准版/电竞低延迟版, from ¥899 |
| `037` | `Redmi 红米` | `Redmi Buds 8 Pro 主动降噪长续航真无线蓝牙耳机` | 标准版/长续航版, from ¥399 |

For every file:

- Set `category` to `数码电子` and `sub_category` to `真无线耳机`.
- Set `base_price` to the listed starting price.
- Set `image_path` to `2_数码电子/images/<product_id>_live.jpg`.
- Create three colors for every version, with SKU IDs increasing from
  `s_<product_id>_1`; premium versions cost ¥100–¥300 more than the starting price.
- Write one internally consistent marketing description, exactly three FAQ entries, and
  exactly five reviews with mixed ratings.
- Cover ANC, transparency mode, codec compatibility, microphone quality, latency,
  battery life, fit, water resistance, and limitations only when supported by that
  product's own facts.

- [ ] **Step 2: Run the repository dataset contract**

Run:

```bash
uv run pytest tests/unit/test_catalog.py::test_repository_dataset_contains_112_products -q
```

Expected: PASS.

- [ ] **Step 3: Read the recalculated smartphone price reference**

Run:

```bash
uv run python -c 'from pathlib import Path; from shop_agent.catalog import ProductCatalog; r=ProductCatalog.load(Path("ecommerce_agent_dataset")).price_reference("数码电子", "智能手机"); print(r.sample_count, r.median_min_sku_price, r.value_price_cap)'
```

Expected: one line containing sample count `14` and the Catalog-computed median and cap.

- [ ] **Step 4: Update the baseline assertion with the exact observed values**

In `test_repository_price_reference_matches_design_baseline`, change the smartphone tuple
from sample count `10` to `14`, and replace `7249.0` and `8698.8` with the exact values
printed in Step 3. Keep the T-shirt assertions unchanged.

- [ ] **Step 5: Run all Catalog tests**

Run:

```bash
uv run pytest tests/unit/test_catalog.py -q
```

Expected: all tests pass.

---

### Task 4: Generate and validate twelve local product images

**Files:**
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_026_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_027_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_028_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_029_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_030_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_031_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_032_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_033_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_034_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_035_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_036_live.jpg`
- Create: `ecommerce_agent_dataset/2_数码电子/images/p_digital_037_live.jpg`

**Interfaces:**
- Consumes: each product's category, primary color, and form factor.
- Produces: one square RGB JPEG at the exact `image_path` referenced by each JSON file.

- [ ] **Step 1: Generate the four phone images**

For each phone, use an original prompt based on:

```text
Square premium ecommerce packshot of a fictional modern 5G smartphone, front and back
three-quarter view, [PRODUCT-SPECIFIC COLOR AND MATERIAL], clean pale studio background,
soft realistic shadow, centered product, photorealistic, no logo, no brand name, no text,
no watermark, no UI lettering, no accessories.
```

Use distinct finishes: blue glass for `026`, graphite metal for `027`, dark green matte
glass for `028`, and silver-black performance styling for `029`.

- [ ] **Step 2: Generate the eight earbud images**

For each earbud, use an original prompt based on:

```text
Square premium ecommerce packshot of fictional true-wireless in-ear earbuds with an open
charging case, [PRODUCT-SPECIFIC COLOR AND SHAPE], clean pale studio background, soft
realistic shadow, centered product, photorealistic, no logo, no brand name, no text,
no watermark, no extra accessories.
```

Use visually distinct combinations: ceramic white, navy blue, matte black, silver,
lavender, pearl white, forest green, and charcoal gray for IDs `030`–`037`.

- [ ] **Step 3: Normalize generated outputs**

Save or convert each generated output to the exact `.jpg` path above. Preserve a square
aspect ratio, use RGB color, and target at least 800×800 pixels.

- [ ] **Step 4: Validate paths and file types**

Run:

```bash
uv run python -c 'from pathlib import Path; from shop_agent.catalog import ProductCatalog; c=ProductCatalog.load(Path("ecommerce_agent_dataset")); ids=[f"p_digital_{i:03d}" for i in range(26,38)]; missing=[i for i in ids if not c.image_file(i).is_file()]; assert not missing, missing; print("images: OK")'
```

Expected: `images: OK`.

Run:

```bash
file ecommerce_agent_dataset/2_数码电子/images/p_digital_0{26,27,28,29,30,31,32,33,34,35,36,37}_live.jpg
```

Expected: twelve JPEG image lines, each reporting square dimensions of at least 800×800.

---

### Task 5: Update current-state documentation

**Files:**
- Modify: `docs/background.md`
- Modify: `docs/features/cross-category-shopping-constraints.md`
- Modify: `docs/features/text-shopping-workflow.md`
- Retain: `docs/README.md`

**Interfaces:**
- Consumes: final Catalog counts and computed smartphone price reference.
- Produces: current documentation consistent with the 112-product dataset.

- [ ] **Step 1: Update current dataset counts**

In `docs/background.md`, change the current dataset description from 100 to 112 products
and note that the four top-level categories remain unchanged.

In `docs/features/cross-category-shopping-constraints.md`, update current-state occurrences
of 100 products to 112 products. Keep 4 top-level categories and the actual Catalog-derived
sub-category count. Do not change historical changelog rows.

- [ ] **Step 2: Update the price baseline**

In `docs/features/text-shopping-workflow.md`, replace the smartphone sample count, median,
and 1.2× value-price cap with the exact values produced in Task 3. Keep the T-shirt
baseline unchanged.

- [ ] **Step 3: Keep the documentation index unchanged**

No new runtime user capability or feature document is introduced. Confirm that
`docs/README.md` already maps the Catalog and search behavior to
`docs/features/text-shopping-workflow.md` and
`docs/features/cross-category-shopping-constraints.md`; do not add a redundant feature
entry.

- [ ] **Step 4: Verify stale current-state counts are gone**

Run:

```bash
rg -n '100 个商品|100 条商品数据|确认4个一级类目、37个子类和100个商品' docs/background.md docs/features
```

Expected: no matches in current-state prose. Historical documents outside these paths are
intentionally unchanged.

---

### Task 6: Run final verification and update the derived index

**Files:**
- Verify: `ecommerce_agent_dataset/2_数码电子/data/*.json`
- Verify: `ecommerce_agent_dataset/2_数码电子/images/*.jpg`
- Verify: `tests/unit/test_catalog.py`
- Verify: current-state documentation changed in Task 5

**Interfaces:**
- Consumes: the complete expanded dataset and local Qdrant/DashScope configuration.
- Produces: verified local fixtures and, when external dependencies are available, indexed searchable products.

- [ ] **Step 1: Run focused offline validation**

Run:

```bash
uv run pytest tests/unit/test_catalog.py tests/unit/test_sku_attributes.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the full offline test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass, with only already-known environment-dependent skips.

- [ ] **Step 3: Index all product text chunks**

Run:

```bash
uv run python -m shop_agent.cli.index_products
```

Expected: the existing collection is updated idempotently and the command completes
without a Catalog, embedding, or Qdrant error. If Qdrant or the model endpoint is
unavailable, record that this external integration step was not completed; do not treat it
as an offline fixture failure.

- [ ] **Step 4: Exercise representative searches when services are available**

Run the existing chat client for these messages:

```text
推荐一款蓝牙耳机
还有别的吗？
推荐一款4000元以内的手机
推荐一款索尼耳机
```

Expected: newly added products can appear, continuation does not return no-results while
unseen eligible earbuds remain, and every returned price/SKU/image matches its source JSON.

- [ ] **Step 5: Review the final workspace without Git**

List the explicitly created and modified paths with `rg --files` and inspect the relevant
files directly. Do not run `git status`, `git diff`, `git add`, `git commit`, or `git push`
unless the user separately authorizes those operations.
