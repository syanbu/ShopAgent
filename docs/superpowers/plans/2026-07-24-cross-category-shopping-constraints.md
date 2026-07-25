# Cross-Category Shopping Constraints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有单轮推荐链路中加入跨品类 SKU 约束、可比较数值约束和宽松语义证据准入，使商品只有在同一个 SKU 同时满足价格与规格时才可返回，同时保留语义证据为 `unknown` 的候选。

**Architecture:** `ParsedIntent` 继续作为单次模型调用的权威输出，`SearchConstraints` 新增 SKU 与数值条件。Catalog 通过一个独立的 SKU 属性规范化模块构建 `sku_taxonomy` 和只读规范化视图，并执行同一 SKU 联合匹配；Qdrant 继续只做现有类目、品牌、粗价格召回。EvidenceService 只淘汰结构化硬条件失败或语义条件明确 `contradicted` 的候选，`unknown` 状态保留给后续排序功能使用。

**Tech Stack:** Python 3.11、Pydantic 2、LangGraph、FastAPI、Qdrant、pytest、pytest-asyncio、ruff、mypy

## Global Constraints

- 开始任何实现任务前先阅读 `docs/README.md` 和 `docs/features/cross-category-shopping-constraints.md`。
- `ecommerce_agent_dataset` 中的商品 JSON 是唯一事实源；不得修改原始 JSON 来适配代码。
- 对外仍返回商品卡片；SKU 只用于候选准入、`matched_skus` 和 `display_price`。
- 价格、SKU 离散条件以及可结构化的 SKU 数值条件必须在同一个 SKU 上联合满足。
- 语义条件的 `unknown` 保留候选，`contradicted` 淘汰候选；本计划不实现 supported/unknown 的新排序权重。
- 保持一次意图模型调用，不增加“先识别类目、再解析约束”的第二次调用。
- `ParsedIntent.schema_version` 保持为 `1`；新增约束字段提供空默认值，保持旧请求和测试数据兼容。
- 不增加新的运行时依赖。
- 不执行任何 Git 命令或 Git 操作。每个任务以测试通过和人工复核作为检查点，不包含暂存或提交步骤。

---

## File Structure

### Create

- `src/shop_agent/sku_attributes.py`：维护原始 SKU key 到规范 key 的映射、规范化 SKU 属性、单位解析和 `sku_taxonomy` 构建。
- `tests/unit/test_sku_attributes.py`：验证59种原始 key 的覆盖、上下文映射、去重目录和数值单位归一化。

### Modify

- `src/shop_agent/models/query.py`：新增 `CanonicalSkuKey`、`NumericConstraint`、`EvidenceCondition`，并扩展 `SearchConstraints`。
- `src/shop_agent/catalog.py`：暴露 SKU taxonomy，执行价格、离散 SKU 条件和结构化 SKU 数值条件的联合匹配。
- `src/shop_agent/services/dashscope_chat.py`：把 `sku_taxonomy` 注入单次意图提示词，校验模型输出，并让证据模型消费显式条件 ID。
- `src/shop_agent/services/ports.py`：将 `EvidenceMapper.map_conditions` 的输入从完整约束改为显式证据条件列表。
- `src/shop_agent/services/evidence.py`：结构化条件硬过滤；语义 `unknown` 保留；只对未结构化的数值条件做证据验证。
- `src/shop_agent/api/dependencies.py`：创建意图解析器时注入 Catalog 生成的 SKU taxonomy。
- `src/shop_agent/workflow/dependencies.py`：同步端口类型导入和方法签名。
- `tests/unit/test_query_models.py`：覆盖新约束模型和冲突校验。
- `tests/unit/test_catalog.py`：覆盖同一 SKU 联合匹配和未结构化数值条件。
- `tests/unit/test_model_gateways.py`：覆盖 taxonomy 提示词、意图输出校验和证据条件协议。
- `tests/unit/test_evidence_service.py`：把 unknown 的旧淘汰断言改为保留，并覆盖 contradicted 淘汰。
- `tests/unit/workflow_fakes.py`：同步新端口并支持 SKU 条件测试数据。
- `tests/unit/test_workflow_stream.py`：覆盖匹配 SKU 与展示价格的端到端事件。
- `docs/features/cross-category-shopping-constraints.md`：实现完成后更新真实代码入口、验证结果和状态。
- `docs/README.md`：实现完成后把功能状态从“提议”改为“已完成”。

---

### Task 1: Define the layered query contract

**Files:**
- Modify: `src/shop_agent/models/query.py`
- Test: `tests/unit/test_query_models.py`

**Interfaces:**
- Produces: `CanonicalSkuKey`、`NumericConstraint.condition_id()`、`EvidenceCondition`、`SearchConstraints.sku_constraints`、`SearchConstraints.numeric_constraints`、`build_evidence_conditions(constraints)`。
- Consumes: 现有 `ParsedIntent`、`SearchConstraints` 和价格区间验证。

- [ ] **Step 1: Write failing model tests**

在 `tests/unit/test_query_models.py` 增加以下测试，固定新字段的默认兼容性、合法形态、非法空值和语义冲突：

```python
from shop_agent.models.query import (
    NumericConstraint,
    SearchConstraints,
    build_evidence_conditions,
)


def test_constraints_default_new_layers_to_empty() -> None:
    constraints = SearchConstraints()

    assert constraints.sku_constraints == {}
    assert constraints.numeric_constraints == []


def test_constraints_accept_canonical_sku_and_numeric_conditions() -> None:
    constraints = SearchConstraints(
        sku_constraints={"storage": ["512GB"], "color": ["黑色"]},
        numeric_constraints=[
            NumericConstraint(
                field="battery_capacity",
                operator=">=",
                value=5000,
                unit="mAh",
            )
        ],
    )

    assert constraints.sku_constraints["storage"] == ["512GB"]
    assert constraints.numeric_constraints[0].condition_id() == (
        "numeric:battery_capacity:>=:5000:mAh"
    )


def test_constraints_reject_empty_sku_values() -> None:
    with pytest.raises(ValidationError, match="sku constraint values cannot be empty"):
        SearchConstraints(sku_constraints={"size": []})


def test_constraints_reject_same_required_and_excluded_feature() -> None:
    with pytest.raises(ValidationError, match="feature cannot be both required and excluded"):
        SearchConstraints(
            required_features=["防水"],
            excluded_features=["防水"],
        )


def test_build_evidence_conditions_uses_stable_unique_ids() -> None:
    constraints = SearchConstraints(
        required_features=["防水"],
        excluded_features=["入耳式"],
        numeric_constraints=[
            NumericConstraint(
                field="battery_capacity",
                operator=">=",
                value=5000,
                unit="mAh",
            )
        ],
    )

    conditions = build_evidence_conditions(constraints)

    assert [condition.condition_id for condition in conditions] == [
        "required:防水",
        "excluded:入耳式",
        "numeric:battery_capacity:>=:5000:mAh",
    ]
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
uv run pytest tests/unit/test_query_models.py -q
```

Expected: FAIL because the new types and fields do not exist.

- [ ] **Step 3: Add the new Pydantic contract**

在 `src/shop_agent/models/query.py` 中增加稳定的规范 key、数值条件和证据条件。保留现有价格字段与 `schema_version=1`：

```python
CanonicalSkuKey = Literal[
    "accent_color",
    "add_on_service",
    "capacity",
    "charging_case",
    "chip",
    "color",
    "custom_service",
    "fit",
    "flavor",
    "gender",
    "hat_adjustment",
    "hat_fit",
    "memory",
    "memory_configuration",
    "network_version",
    "package_count",
    "package_type",
    "pants_length",
    "product_type",
    "screen_size",
    "shade",
    "shoe_last",
    "size",
    "specification",
    "storage",
    "target_audience",
    "version",
]
NumericOperator = Literal["==", ">", ">=", "<", "<="]
EvidenceConditionKind = Literal[
    "required_feature",
    "excluded_feature",
    "numeric",
]


class NumericConstraint(BaseModel):
    field: str = Field(min_length=1)
    operator: NumericOperator
    value: float
    unit: str = Field(min_length=1)

    def condition_id(self) -> str:
        value = format(self.value, "g")
        return f"numeric:{self.field}:{self.operator}:{value}:{self.unit}"


class EvidenceCondition(BaseModel):
    condition_id: str
    kind: EvidenceConditionKind
    expression: str
    numeric_constraint: NumericConstraint | None = None


def build_evidence_conditions(
    constraints: "SearchConstraints",
) -> list[EvidenceCondition]:
    conditions = [
        EvidenceCondition(
            condition_id=f"required:{feature}",
            kind="required_feature",
            expression=f"商品具备：{feature}",
        )
        for feature in constraints.required_features
    ]
    conditions.extend(
        EvidenceCondition(
            condition_id=f"excluded:{feature}",
            kind="excluded_feature",
            expression=f"商品不具备：{feature}",
        )
        for feature in constraints.excluded_features
    )
    conditions.extend(
        EvidenceCondition(
            condition_id=item.condition_id(),
            kind="numeric",
            expression=(
                f"{item.field} {item.operator} "
                f"{format(item.value, 'g')} {item.unit}"
            ),
            numeric_constraint=item,
        )
        for item in constraints.numeric_constraints
    )
    return conditions
```

在 `SearchConstraints` 中加入字段与校验：

```python
    sku_constraints: dict[CanonicalSkuKey, list[str]] = Field(
        default_factory=dict,
        description="按规范 SKU key 表达的离散规格硬条件。",
    )
    numeric_constraints: list[NumericConstraint] = Field(
        default_factory=list,
        description="带比较符和单位的数值条件。",
    )

    @model_validator(mode="after")
    def validate_constraint_consistency(self) -> "SearchConstraints":
        for values in self.sku_constraints.values():
            if not values:
                raise ValueError("sku constraint values cannot be empty")
            if any(not value.strip() for value in values):
                raise ValueError("sku constraint values cannot be blank")
        overlap = set(self.required_features).intersection(self.excluded_features)
        if overlap:
            raise ValueError("feature cannot be both required and excluded")
        numeric_ids = [item.condition_id() for item in self.numeric_constraints]
        if len(numeric_ids) != len(set(numeric_ids)):
            raise ValueError("numeric constraints cannot be duplicated")
        return self
```

将该校验与现有价格区间校验合并在同一个 `model_validator` 中，避免同一模型分散维护两个 after validator。

- [ ] **Step 4: Run model tests**

Run:

```powershell
uv run pytest tests/unit/test_query_models.py tests/unit/test_query_compiler.py -q
```

Expected: PASS；旧的价格编译测试继续通过。

- [ ] **Step 5: Run static checks for the contract**

Run:

```powershell
uv run ruff check src/shop_agent/models/query.py tests/unit/test_query_models.py
uv run mypy src/shop_agent/models/query.py
```

Expected: both commands exit 0.

---

### Task 2: Build the canonical SKU attribute registry and taxonomy

**Files:**
- Create: `src/shop_agent/sku_attributes.py`
- Create: `tests/unit/test_sku_attributes.py`
- Modify: `src/shop_agent/catalog.py`

**Interfaces:**
- Consumes: `CanonicalSkuKey`、`Product`、`Sku`。
- Produces: `canonical_sku_key(category, sub_category, raw_key)`、`normalize_sku_properties(product, sku)`、`build_sku_taxonomy(products)`、`parse_quantity(text)`、`ProductCatalog.sku_taxonomy()`。

- [ ] **Step 1: Write registry coverage and taxonomy tests**

创建 `tests/unit/test_sku_attributes.py`：

```python
from pathlib import Path

from shop_agent.catalog import ProductCatalog
from shop_agent.sku_attributes import (
    canonical_sku_key,
    normalize_sku_properties,
    parse_quantity,
)


def test_repository_sku_keys_are_mapped_or_explicitly_ignored() -> None:
    catalog = ProductCatalog.load(Path("ecommerce_agent_dataset"))
    unresolved: set[tuple[str, str, str]] = set()

    for product in catalog.all():
        for sku in product.skus:
            for raw_key in sku.properties:
                if canonical_sku_key(
                    product.category,
                    product.sub_category,
                    raw_key,
                ) is None:
                    unresolved.add((product.category, product.sub_category, raw_key))

    assert unresolved == set()


def test_normalize_sku_properties_collapses_aliases(sample_product) -> None:
    product = sample_product.model_copy(
        update={"category": "数码电子", "sub_category": "智能手机"}
    )
    sku = product.skus[0].model_copy(
        update={"properties": {"存储配置": "512GB", "机身颜色": "黑色"}}
    )

    assert normalize_sku_properties(product, sku) == {
        "storage": "512GB",
        "color": "黑色",
    }


def test_catalog_taxonomy_is_deduplicated_and_scoped_by_category_pair() -> None:
    catalog = ProductCatalog.load(Path("ecommerce_agent_dataset"))

    taxonomy = catalog.sku_taxonomy()

    assert "storage" in taxonomy["数码电子/智能手机"]
    assert "512GB" in taxonomy["数码电子/智能手机"]["storage"]
    assert "size" in taxonomy["服饰运动/跑步鞋"]
    assert "42码" in taxonomy["服饰运动/跑步鞋"]["size"]
    assert "size" not in taxonomy["食品饮料/碳酸饮料"]


def test_parse_quantity_converts_compatible_units() -> None:
    assert parse_quantity("1TB") == (1024.0, "GB")
    assert parse_quantity("500ml") == (500.0, "ml")
    assert parse_quantity("1.5L") == (1500.0, "ml")
    assert parse_quantity("30小时") == (30.0, "h")
    assert parse_quantity("14英寸") == (14.0, "in")
```

- [ ] **Step 2: Run the new tests and confirm failure**

Run:

```powershell
uv run pytest tests/unit/test_sku_attributes.py -q
```

Expected: FAIL because `shop_agent.sku_attributes` and `sku_taxonomy()` do not exist.

- [ ] **Step 3: Implement the complete raw-key registry**

创建 `src/shop_agent/sku_attributes.py`。使用一个完整映射覆盖当前59种原始 key；需要上下文差异时优先查询 `CONTEXT_OVERRIDES`：

```python
import re
from collections.abc import Iterable

from shop_agent.models.product import Product, Sku
from shop_agent.models.query import CanonicalSkuKey


RAW_KEY_ALIASES: dict[str, CanonicalSkuKey] = {
    "logo配色": "accent_color",
    "版本": "version",
    "包装": "package_type",
    "包装规格": "package_type",
    "包装类型": "package_type",
    "包装数量": "package_count",
    "产品版本": "version",
    "产品规格": "specification",
    "产品类型": "product_type",
    "尺寸": "screen_size",
    "尺码": "size",
    "充电盒类型": "charging_case",
    "刺绣logo配色": "accent_color",
    "存储": "storage",
    "存储规格": "storage",
    "存储配置": "storage",
    "存储容量": "storage",
    "单盒容量": "capacity",
    "单条净含量": "capacity",
    "定制服务": "custom_service",
    "附加服务": "add_on_service",
    "固态存储": "storage",
    "固态硬盘容量": "storage",
    "规格": "specification",
    "机身存储": "storage",
    "机身颜色": "color",
    "口味": "flavor",
    "裤长": "pants_length",
    "款式": "fit",
    "款型": "fit",
    "帽身颜色": "color",
    "帽围调节方式": "hat_adjustment",
    "帽围类型": "hat_fit",
    "每箱数量": "package_count",
    "内存": "memory",
    "内存容量": "memory",
    "内存组合": "memory_configuration",
    "内含条数": "package_count",
    "配色": "color",
    "屏幕尺寸": "screen_size",
    "容量": "capacity",
    "色号": "shade",
    "色号规格": "shade",
    "适用人群": "target_audience",
    "适用性别": "gender",
    "数量": "package_count",
    "网络版本": "network_version",
    "箱规": "package_count",
    "鞋码": "size",
    "鞋楦": "shoe_last",
    "鞋楦类型": "shoe_last",
    "芯片": "chip",
    "芯片型号": "chip",
    "颜色": "color",
    "运行内存": "memory",
    "整箱规格": "package_count",
    "整箱盒数": "package_count",
    "整箱数量": "package_count",
    "总袋数": "package_count",
}
CONTEXT_OVERRIDES: dict[tuple[str, str, str], CanonicalSkuKey] = {
    ("服饰运动", "背包", "容量"): "capacity",
    ("数码电子", "笔记本电脑", "尺寸"): "screen_size",
}


def canonical_sku_key(
    category: str,
    sub_category: str,
    raw_key: str,
) -> CanonicalSkuKey | None:
    return CONTEXT_OVERRIDES.get(
        (category, sub_category, raw_key),
        RAW_KEY_ALIASES.get(raw_key),
    )


def normalize_sku_properties(
    product: Product,
    sku: Sku,
) -> dict[CanonicalSkuKey, str]:
    normalized: dict[CanonicalSkuKey, str] = {}
    for raw_key, value in sku.properties.items():
        key = canonical_sku_key(product.category, product.sub_category, raw_key)
        if key is None:
            continue
        if key in normalized and normalized[key] != value:
            raise ValueError(
                f"SKU {sku.sku_id} maps conflicting values to {key}"
            )
        normalized[key] = value.strip()
    return normalized


def build_sku_taxonomy(
    products: Iterable[Product],
) -> dict[str, dict[CanonicalSkuKey, list[str]]]:
    collected: dict[str, dict[CanonicalSkuKey, set[str]]] = {}
    for product in products:
        pair = f"{product.category}/{product.sub_category}"
        pair_values = collected.setdefault(pair, {})
        for sku in product.skus:
            for key, value in normalize_sku_properties(product, sku).items():
                pair_values.setdefault(key, set()).add(value)
    return {
        pair: {
            key: sorted(values)
            for key, values in sorted(attributes.items())
        }
        for pair, attributes in sorted(collected.items())
    }
```

同文件实现受控单位解析；只支持当前设计需要的存储、容量、重量和时间单位，其他字符串返回 `None`：

```python
_QUANTITY = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+|毫升|升|克|千克|小时|英寸|寸)\s*$"
)
_UNIT_CONVERSIONS = {
    "tb": (1024.0, "GB"),
    "gb": (1.0, "GB"),
    "mb": (1 / 1024.0, "GB"),
    "l": (1000.0, "ml"),
    "升": (1000.0, "ml"),
    "ml": (1.0, "ml"),
    "毫升": (1.0, "ml"),
    "kg": (1000.0, "g"),
    "千克": (1000.0, "g"),
    "g": (1.0, "g"),
    "克": (1.0, "g"),
    "h": (1.0, "h"),
    "小时": (1.0, "h"),
    "英寸": (1.0, "in"),
    "寸": (1.0, "in"),
}


def parse_quantity(text: str) -> tuple[float, str] | None:
    match = _QUANTITY.fullmatch(text)
    if match is None:
        return None
    number = float(match.group(1))
    conversion = _UNIT_CONVERSIONS.get(match.group(2).lower())
    if conversion is None:
        return None
    factor, base_unit = conversion
    return number * factor, base_unit
```

- [ ] **Step 4: Expose the immutable taxonomy through Catalog**

在 `ProductCatalog.__init__` 中调用 `build_sku_taxonomy(products.values())`，并增加返回深拷贝的方法，避免调用方修改内部目录：

```python
from copy import deepcopy

from shop_agent.models.query import CanonicalSkuKey
from shop_agent.sku_attributes import build_sku_taxonomy


    def sku_taxonomy(
        self,
    ) -> dict[str, dict[CanonicalSkuKey, list[str]]]:
        return deepcopy(self._sku_taxonomy)
```

- [ ] **Step 5: Run registry, Catalog and static checks**

Run:

```powershell
uv run pytest tests/unit/test_sku_attributes.py tests/unit/test_catalog.py -q
uv run ruff check src/shop_agent/sku_attributes.py src/shop_agent/catalog.py tests/unit/test_sku_attributes.py
uv run mypy src/shop_agent/sku_attributes.py src/shop_agent/catalog.py
```

Expected: all commands exit 0；仓库数据的原始 SKU key 覆盖测试报告空集合。

---

### Task 3: Match price, discrete attributes and structured numbers on the same SKU

**Files:**
- Modify: `src/shop_agent/catalog.py`
- Modify: `src/shop_agent/sku_attributes.py`
- Test: `tests/unit/test_catalog.py`

**Interfaces:**
- Consumes: `SearchConstraints`、`NumericConstraint`、规范化 SKU 属性和 `parse_quantity`。
- Produces: 扩展后的 `ProductCatalog.matched_skus(product_id, constraints)`、`ProductCatalog.unresolved_numeric_constraints(product_id, constraints)`。

- [ ] **Step 1: Write failing same-SKU tests**

在 `tests/unit/test_catalog.py` 增加：

```python
from shop_agent.models.query import NumericConstraint


def test_catalog_requires_price_and_size_on_same_sku(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    product = sample_product.model_copy(
        update={
            "category": "服饰运动",
            "sub_category": "跑步鞋",
            "skus": [
                sample_product.skus[0].model_copy(
                    update={"properties": {"鞋码": "42码"}, "price": 799}
                ),
                sample_product.skus[1].model_copy(
                    update={"properties": {"鞋码": "43码"}, "price": 699}
                ),
            ],
        }
    )
    catalog = ProductCatalog(
        sample_dataset_root,
        {product.product_id: product},
        {product.product_id: "data/product.json"},
    )

    matched = catalog.matched_skus(
        product.product_id,
        SearchConstraints(max_price=700, sku_constraints={"size": ["42码"]}),
    )

    assert matched == []


def test_catalog_returns_only_512gb_sku_inside_budget(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    product = sample_product.model_copy(
        update={
            "category": "数码电子",
            "sub_category": "智能手机",
            "skus": [
                sample_product.skus[0].model_copy(
                    update={"properties": {"存储": "256GB"}, "price": 6999}
                ),
                sample_product.skus[1].model_copy(
                    update={"properties": {"存储配置": "512GB"}, "price": 7999}
                ),
            ],
        }
    )
    catalog = ProductCatalog(
        sample_dataset_root,
        {product.product_id: product},
        {product.product_id: "data/product.json"},
    )

    matched = catalog.matched_skus(
        product.product_id,
        SearchConstraints(max_price=8000, sku_constraints={"storage": ["512GB"]}),
    )

    assert [sku.price for sku in matched] == [7999]


def test_catalog_compares_structured_numeric_sku_values(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    product = sample_product.model_copy(
        update={
            "category": "数码电子",
            "sub_category": "智能手机",
            "skus": [
                sample_product.skus[0].model_copy(
                    update={"properties": {"存储": "256GB"}}
                ),
                sample_product.skus[1].model_copy(
                    update={"properties": {"存储": "1TB"}}
                ),
            ],
        }
    )
    catalog = ProductCatalog(
        sample_dataset_root,
        {product.product_id: product},
        {product.product_id: "data/product.json"},
    )
    constraints = SearchConstraints(
        numeric_constraints=[
            NumericConstraint(
                field="storage", operator=">=", value=512, unit="GB"
            )
        ]
    )

    assert [sku.sku_id for sku in catalog.matched_skus(product.product_id, constraints)] == [
        sample_product.skus[1].sku_id
    ]
    assert catalog.unresolved_numeric_constraints(product.product_id, constraints) == []


def test_catalog_leaves_text_only_numeric_condition_unresolved(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    numeric = NumericConstraint(
        field="battery_capacity", operator=">=", value=5000, unit="mAh"
    )
    constraints = SearchConstraints(numeric_constraints=[numeric])

    assert catalog.matched_skus("p_digital_001", constraints)
    assert catalog.unresolved_numeric_constraints(
        "p_digital_001", constraints
    ) == [numeric]
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
uv run pytest tests/unit/test_catalog.py -q
```

Expected: FAIL because `matched_skus` only checks price and the unresolved numeric API is missing.

- [ ] **Step 3: Implement conjunctive SKU matching**

在 `ProductCatalog` 中缓存每个 SKU 的规范属性，并用以下私有逻辑替换当前只有价格的列表推导：

```python
from operator import eq, ge, gt, le, lt

from shop_agent.models.query import NumericConstraint
from shop_agent.sku_attributes import normalize_sku_properties, parse_quantity


_NUMERIC_OPERATORS = {
    "==": eq,
    ">": gt,
    ">=": ge,
    "<": lt,
    "<=": le,
}


    def matched_skus(
        self,
        product_id: str,
        constraints: SearchConstraints,
    ) -> list[Sku]:
        product = self.get(product_id)
        structured_numeric_fields = self._structured_numeric_fields(product_id)
        return [
            sku
            for sku in product.skus
            if self._sku_matches(
                product,
                sku,
                constraints,
                structured_numeric_fields,
            )
        ]

    def unresolved_numeric_constraints(
        self,
        product_id: str,
        constraints: SearchConstraints,
    ) -> list[NumericConstraint]:
        fields = self._structured_numeric_fields(product_id)
        return [item for item in constraints.numeric_constraints if item.field not in fields]

    def _structured_numeric_fields(self, product_id: str) -> set[str]:
        product = self.get(product_id)
        fields: set[str] = set()
        for sku in product.skus:
            for key, value in normalize_sku_properties(product, sku).items():
                if parse_quantity(value) is not None:
                    fields.add(key)
        return fields

    def _sku_matches(
        self,
        product: Product,
        sku: Sku,
        constraints: SearchConstraints,
        structured_numeric_fields: set[str],
    ) -> bool:
        if constraints.min_price is not None and sku.price < constraints.min_price:
            return False
        if constraints.max_price is not None and sku.price > constraints.max_price:
            return False
        properties = normalize_sku_properties(product, sku)
        for key, allowed_values in constraints.sku_constraints.items():
            if properties.get(key) not in allowed_values:
                return False
        for item in constraints.numeric_constraints:
            if item.field not in structured_numeric_fields:
                continue
            raw_value = properties.get(item.field)
            if raw_value is None or not _numeric_matches(raw_value, item):
                return False
        return True
```

在模块级增加完整的单位兼容比较；约束单位也通过 `parse_quantity(f"{value}{unit}")` 转到基础单位：

```python
def _numeric_matches(raw_value: str, constraint: NumericConstraint) -> bool:
    actual = parse_quantity(raw_value)
    expected = parse_quantity(f"{constraint.value:g}{constraint.unit}")
    if actual is None or expected is None or actual[1] != expected[1]:
        return False
    return _NUMERIC_OPERATORS[constraint.operator](actual[0], expected[0])
```

- [ ] **Step 4: Run Catalog and price compilation regression tests**

Run:

```powershell
uv run pytest tests/unit/test_catalog.py tests/unit/test_query_compiler.py tests/unit/test_evidence_service.py -q
```

Expected: PASS；现有纯价格筛选仍返回相同 SKU。

---

### Task 4: Inject and validate SKU taxonomy in the single intent call

**Files:**
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Modify: `src/shop_agent/api/dependencies.py`
- Test: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: `ProductCatalog.sku_taxonomy()` 和扩展后的 `ParsedIntent`。
- Produces: `_build_intent_system_prompt(..., sku_taxonomy=...)`、`DashScopeIntentParser(..., sku_taxonomy=...)`，并在 `_validate_intent` 中拒绝跨子类 key 和不存在的离散值。

- [ ] **Step 1: Write failing prompt and validator tests**

在 `tests/unit/test_model_gateways.py` 增加：

```python
def test_intent_prompt_contains_compact_sku_taxonomy(settings: Settings) -> None:
    parser = DashScopeIntentParser(
        settings,
        categories=["数码电子", "服饰运动"],
        sub_categories=["智能手机", "跑步鞋"],
        category_pairs=[("数码电子", "智能手机"), ("服饰运动", "跑步鞋")],
        sku_taxonomy={
            "数码电子/智能手机": {
                "storage": ["256GB", "512GB"],
                "color": ["黑色"],
            },
            "服饰运动/跑步鞋": {"size": ["42码"]},
        },
    )

    prompt = parser._system_prompt

    assert '"sku_taxonomy"' in prompt
    assert '"storage":["256GB","512GB"]' in prompt
    assert "SKU 条件只能使用已识别子类开放的规范 key" in prompt
    assert len(prompt) < 100_000


def test_intent_validator_rejects_cross_subcategory_sku_key(
    settings: Settings,
) -> None:
    parser = DashScopeIntentParser(
        settings,
        category_pairs=[("数码电子", "智能手机")],
        sku_taxonomy={
            "数码电子/智能手机": {"storage": ["512GB"]},
        },
    )
    content = ParsedIntent(
        schema_version=1,
        intent="product_search",
        retrieval_query="手机",
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(sku_constraints={"size": ["42码"]}),
    ).model_dump_json()

    with pytest.raises(ValueError, match="SKU keys"):
        parser._validate_intent(content)


def test_intent_validator_rejects_unknown_sku_value(settings: Settings) -> None:
    parser = DashScopeIntentParser(
        settings,
        category_pairs=[("数码电子", "智能手机")],
        sku_taxonomy={
            "数码电子/智能手机": {"storage": ["512GB"]},
        },
    )
    content = ParsedIntent(
        schema_version=1,
        intent="product_search",
        retrieval_query="手机",
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(sku_constraints={"storage": ["2TB"]}),
    ).model_dump_json()

    with pytest.raises(ValueError, match="SKU values"):
        parser._validate_intent(content)
```

- [ ] **Step 2: Run gateway tests and confirm failure**

Run:

```powershell
uv run pytest tests/unit/test_model_gateways.py -q
```

Expected: FAIL because the parser does not accept or validate `sku_taxonomy`.

- [ ] **Step 3: Extend the prompt without adding a second model call**

给 `_build_intent_system_prompt` 和 `DashScopeIntentParser.__init__` 增加只读 taxonomy 参数：

```python
from collections.abc import Mapping

from shop_agent.models.query import CanonicalSkuKey

SkuTaxonomy = Mapping[str, Mapping[CanonicalSkuKey, Sequence[str]]]
```

将 taxonomy JSON 扩展为：

```python
        {
            "categories": list(categories),
            "sub_categories": list(sub_categories),
            "category_pairs": [list(pair) for pair in category_pairs],
            "brands": list(brands),
            "sku_taxonomy": {
                pair: {
                    key: sorted(set(values))
                    for key, values in sorted(attributes.items())
                }
                for pair, attributes in sorted(sku_taxonomy.items())
            },
        }
```

在提示词规则中明确：

```python
        "7. SKU 条件只能使用已识别子类开放的规范 key 和候选值；离散的尺码、"
        "颜色、存储版本、口味等写入 sku_constraints。带至少、大于、小于等比较"
        "关系的条件写入 numeric_constraints，不得同时复制到 required_features。\n"
```

原第7、8条顺延。增加两个例子：“42码跑步鞋”和“512GB手机”，每个例子都同时包含完整的默认约束字段，避免模型遗漏新字段。

- [ ] **Step 4: Validate output against the selected category pair**

在 `_validate_intent` 完成 Pydantic 和品牌校验后加入：

```python
        if parsed.intent != "product_search":
            return parsed
        if parsed.category is None or parsed.sub_category is None:
            if parsed.constraints.sku_constraints:
                raise ValueError("SKU constraints require category and sub_category")
            return parsed
        pair = f"{parsed.category}/{parsed.sub_category}"
        allowed = self._sku_taxonomy.get(pair, {})
        unknown_keys = sorted(set(parsed.constraints.sku_constraints) - set(allowed))
        if unknown_keys:
            raise ValueError(f"SKU keys {unknown_keys} are not available for {pair}")
        invalid_values = {
            key: sorted(set(values) - set(allowed[key]))
            for key, values in parsed.constraints.sku_constraints.items()
            if set(values) - set(allowed[key])
        }
        if invalid_values:
            raise ValueError(f"SKU values {invalid_values} are not available for {pair}")
```

保存 taxonomy 时复制为 tuple，防止外部可变对象改变已经构建的提示词。

- [ ] **Step 5: Wire Catalog taxonomy into API dependencies**

在 `build_api_dependencies` 创建 `DashScopeIntentParser` 时增加：

```python
                sku_taxonomy=catalog.sku_taxonomy(),
```

- [ ] **Step 6: Run gateway and dependency regression tests**

Run:

```powershell
uv run pytest tests/unit/test_model_gateways.py tests/unit/test_workflow_routes.py -q
uv run ruff check src/shop_agent/services/dashscope_chat.py src/shop_agent/api/dependencies.py tests/unit/test_model_gateways.py
```

Expected: all commands exit 0；旧的价格、品牌和性价比提示词契约继续通过。

---

### Task 5: Keep unknown semantic candidates and reject only contradictions

**Files:**
- Modify: `src/shop_agent/services/ports.py`
- Modify: `src/shop_agent/services/dashscope_chat.py`
- Modify: `src/shop_agent/services/evidence.py`
- Modify: `src/shop_agent/models/retrieval.py`
- Modify: `tests/unit/test_evidence_service.py`
- Modify: `tests/unit/test_model_gateways.py`

**Interfaces:**
- Consumes: `EvidenceCondition`、`ProductCatalog.unresolved_numeric_constraints()` 和现有召回证据。
- Produces: `EvidenceMapper.map_conditions(product_id, conditions, evidence)`；`EvidenceCheck.condition` 存储稳定 condition ID；EvidenceService 将 `unknown` 视为 eligible，将任何 `contradicted` 视为不合格。

- [ ] **Step 1: Replace old unknown-rejection tests with candidate-retention tests**

在 `tests/unit/test_evidence_service.py` 将两个旧测试改为：

```python
@pytest.mark.asyncio
async def test_required_unknown_feature_keeps_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition="required:防水", status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(required_features=["防水"]),
    )

    assert validated[0].eligible is True
    assert validated[0].rejection_reasons == []


@pytest.mark.asyncio
async def test_excluded_unknown_feature_keeps_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition="excluded:入耳式", status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(excluded_features=["入耳式"]),
    )

    assert validated[0].eligible is True


@pytest.mark.asyncio
async def test_contradicted_feature_rejects_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[
            EvidenceCheck(
                condition="required:防水",
                status="contradicted",
                conflicting_evidence_ids=["p1:summary"],
            )
        ],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(required_features=["防水"]),
    )

    assert validated[0].eligible is False
    assert validated[0].rejection_reasons == ["semantic_condition_contradicted"]
```

更新 `FakeEvidenceMapper.calls`，记录 `list[EvidenceCondition]` 而不是 `SearchConstraints`。

- [ ] **Step 2: Add tests for exact condition coverage and unresolved numeric evidence**

增加：

```python
@pytest.mark.asyncio
async def test_missing_evidence_condition_is_parse_failure() -> None:
    product = _product("p1")
    mapper = FakeEvidenceMapper(
        {"p1": EvidenceAssessment(product_id="p1", checks=[])}
    )
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with pytest.raises(ServiceError, match="evidence conditions do not match request"):
        await service.validate_candidates(
            [_candidate(product, 0.8)],
            SearchConstraints(required_features=["防水"]),
        )


@pytest.mark.asyncio
async def test_text_only_numeric_constraint_is_sent_to_evidence_mapper() -> None:
    product = _product("p1")
    numeric = NumericConstraint(
        field="battery_capacity", operator=">=", value=5000, unit="mAh"
    )
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition=numeric.condition_id(), status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(numeric_constraints=[numeric]),
    )

    assert validated[0].eligible is True
    assert [condition.condition_id for condition in mapper.calls[0][1]] == [
        numeric.condition_id()
    ]
```

- [ ] **Step 3: Run evidence tests and confirm failure**

Run:

```powershell
uv run pytest tests/unit/test_evidence_service.py tests/unit/test_model_gateways.py -q
```

Expected: FAIL because unknown is still rejected and the mapper still consumes `SearchConstraints`.

- [ ] **Step 4: Change the mapper port to explicit evidence conditions**

在 `src/shop_agent/services/ports.py`：

```python
from shop_agent.models.query import EvidenceCondition


class EvidenceMapper(Protocol):
    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        raise NotImplementedError
```

在 `DashScopeEvidenceMapper.map_conditions` 中发送：

```python
"conditions": [condition.model_dump(mode="json") for condition in conditions]
```

系统提示词明确要求：

- 每个输入 `condition_id` 必须恰好返回一次，并原样写入 `EvidenceCheck.condition`。
- `supported` 必须有决定性 `evidence_ids`。
- `unknown` 表示证据不足，不允许因为未提及而推断满足。
- `contradicted` 表示证据明确违反用户条件。
- 对 `excluded_feature`，证据明确证明“不包含”才是 `supported`。

- [ ] **Step 5: Make EvidenceService distinguish hard filters from evidence checks**

用显式条件列表替换 `semantic_checks_pass`：

```python
from shop_agent.models.query import build_evidence_conditions


def semantic_conditions_allow_candidate(assessment: EvidenceAssessment) -> bool:
    return all(check.status != "contradicted" for check in assessment.checks)
```

在每个候选完成结构化校验后：

```python
unresolved_numeric = self._catalog.unresolved_numeric_constraints(
    product.product_id,
    constraints,
)
evidence_constraints = constraints.model_copy(
    update={"numeric_constraints": unresolved_numeric}
)
conditions = build_evidence_conditions(evidence_constraints)
if conditions and not rejection_reasons:
    assessment = await self._mapper.map_conditions(
        product.product_id,
        conditions,
        candidate.evidence,
    )
    self._validate_assessment(candidate, assessment, conditions)
    self._log_conflicts(candidate, assessment)
    if not semantic_conditions_allow_candidate(assessment):
        rejection_reasons.append("semantic_condition_contradicted")
```

扩展 `_validate_assessment`，要求返回 condition ID 集合与请求完全一致：

```python
expected = {condition.condition_id for condition in conditions}
returned = {check.condition for check in assessment.checks}
if returned != expected:
    raise ServiceError(
        "EVIDENCE_PARSE_FAILED",
        "evidence conditions do not match request",
        retryable=False,
    )
```

将结构化 SKU 失败原因统一为 `no_matching_sku`，因为失败可能来自价格、离散属性或结构化数值，而不再只是 `price_out_of_range`。

- [ ] **Step 6: Preserve semantic status without implementing new ranking weights**

`select_candidates` 继续按现有 `rerank_score` 排序。生成 `decision_reasons` 时根据 assessment 写入：

```python
statuses = {check.status for check in item.assessment.checks}
if "unknown" in statuses:
    decision_reasons.append("semantic_conditions_unknown")
elif statuses:
    decision_reasons.append("semantic_conditions_supported")
```

将 `_decisive_evidence_ids` 改成只依赖已经严格校验过的 assessment，并同步修改调用处：

```python
    @staticmethod
    def _decisive_evidence_ids(
        assessment: EvidenceAssessment,
    ) -> list[str]:
        selected: list[str] = []
        for check in assessment.checks:
            if check.status != "supported":
                continue
            for evidence_id in check.evidence_ids:
                if evidence_id not in selected:
                    selected.append(evidence_id)
        return selected
```

不要将 unknown 或 contradicted 的证据写入最终推荐事实。

- [ ] **Step 7: Run evidence and gateway tests**

Run:

```powershell
uv run pytest tests/unit/test_evidence_service.py tests/unit/test_model_gateways.py -q
uv run ruff check src/shop_agent/services/ports.py src/shop_agent/services/dashscope_chat.py src/shop_agent/services/evidence.py
uv run mypy src/shop_agent/services/ports.py src/shop_agent/services/evidence.py
```

Expected: all commands exit 0；unknown 两个测试 eligible 为 true，contradicted 测试为 false。

---

### Task 6: Verify workflow output, compatibility and feature documentation

**Files:**
- Modify: `tests/unit/workflow_fakes.py`
- Modify: `tests/unit/test_workflow_stream.py`
- Modify: `src/shop_agent/workflow/dependencies.py`
- Modify: `docs/features/cross-category-shopping-constraints.md`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: 完成后的意图、Catalog 和 EvidenceService 接口。
- Produces: 商品事件只携带联合匹配的 SKU，`display_price` 来自这些 SKU；完整测试与文档验收记录。

- [ ] **Step 1: Synchronize workflow fakes with the new ports**

给 `FakeIntentParser` 增加一个显式的意图覆盖入口：

```python
class FakeIntentParser:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.intent: ParsedIntent | None = None

    async def parse(self, message: str) -> ParsedIntent:
        self.calls.append(message)
        if self.intent is not None:
            return self.intent
        if message == "你好":
            return ParsedIntent(
                schema_version=1,
                intent="non_shopping",
                retrieval_query=None,
                category=None,
                sub_category=None,
            )
        return ParsedIntent(
            schema_version=1,
            intent="product_search",
            retrieval_query=message,
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(
                max_price=None if "性价比" in message else 500,
                price_preference="value" if "性价比" in message else None,
            ),
        )
```

给 `FakeEvidenceService` 注入 Catalog，并让选择结果使用真实的联合 SKU 匹配：

```python
class FakeEvidenceService:
    def __init__(self, *, catalog: ProductCatalog, eligible: bool) -> None:
        self.catalog = catalog
        self.eligible = eligible
        self.validate_calls: list[
            tuple[list[ProductCandidate], SearchConstraints, str | None, str | None]
        ] = []
        self.select_calls: list[
            tuple[list[ValidatedCandidate], int, SearchConstraints]
        ] = []

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]:
        self.select_calls.append((list(validated), limit, constraints))
        selected: list[SelectedProduct] = []
        for item in validated:
            if not item.eligible:
                continue
            product_id = item.candidate.product.product_id
            matched = self.catalog.matched_skus(product_id, constraints)
            if not matched:
                continue
            selected.append(
                SelectedProduct(
                    product_id=product_id,
                    rerank_score=item.candidate.rerank_score or 0,
                    evidence_ids=[item.candidate.evidence[0].chunk_id],
                    decision_reasons=["rerank_selected"],
                    matched_sku_ids=[sku.sku_id for sku in matched],
                )
            )
        return selected[:limit]
```

在 `build_harness` 中创建 Catalog 后传入：

```python
evidence=FakeEvidenceService(catalog=catalog, eligible=eligible)
```

- [ ] **Step 2: Write a workflow event test for exact matched SKU price**

在 `tests/unit/test_workflow_stream.py` 增加以下完整工作流测试：

```python
@pytest.mark.asyncio
async def test_product_event_uses_only_512gb_sku_price(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, product_count=1)
    product = harness.catalog.all()[0]
    product.skus[0].properties = {"存储": "256GB"}
    product.skus[0].price = 6999
    product.skus[1].properties = {"存储配置": "512GB"}
    product.skus[1].price = 7999
    harness.parser.intent = ParsedIntent(
        schema_version=1,
        intent="product_search",
        retrieval_query="512GB手机",
        category="数码电子",
        sub_category="智能手机",
        constraints=SearchConstraints(
            max_price=8000,
            sku_constraints={"storage": ["512GB"]},
        ),
    )

    parts = await _drain(_graph(harness), "推荐8000元以内的512GB手机")
    product_data = parts[0]["data"]["data"]

    assert product_data["display_price"] == 7999
    assert [sku["properties"] for sku in product_data["matched_skus"]] == [
        {"存储配置": "512GB"}
    ]
```

该测试同时覆盖意图约束向工作流传递、Catalog 联合匹配、`matched_sku_ids`、商品事件和 `display_price`，不在 fake 中写死第一个 SKU。

- [ ] **Step 3: Run workflow tests and fix only interface regressions**

Run:

```powershell
uv run pytest tests/unit/test_workflow_routes.py tests/unit/test_workflow_stream.py -q
```

Expected: PASS；商品事件顺序不变，新增用例的 `display_price` 为7999。

- [ ] **Step 4: Run the complete deterministic test suite**

Run:

```powershell
uv run pytest tests/unit tests/integration -q
uv run ruff check .
uv run mypy src
```

Expected:

- pytest exits 0 with no failed tests；依赖真实外部服务的 live tests 不在此命令中。
- ruff exits 0。
- mypy exits 0。

如果 integration 测试因为 Docker/Qdrant 未运行而 skip，必须在验证记录中写明 skip，不能报告为真实 Qdrant 通过。

- [ ] **Step 5: Run targeted repository-data acceptance tests**

增加或运行覆盖真实数据的测试：

```powershell
uv run pytest tests/unit/test_sku_attributes.py::test_repository_sku_keys_are_mapped_or_explicitly_ignored tests/unit/test_sku_attributes.py::test_catalog_taxonomy_is_deduplicated_and_scoped_by_category_pair tests/unit/test_catalog.py::test_repository_dataset_contains_100_products -q
```

Expected: 3 passed；确认4个一级类目、37个子类、100个商品和59种原始 key 均可加载并规范化。

- [ ] **Step 6: Update the feature document with actual implementation evidence**

仅在上述验证通过后修改 `docs/features/cross-category-shopping-constraints.md`：

- 状态从“提议”改为“已完成”。
- `代码入口` 改为实际创建和修改的核心文件。
- 在“代码与验证”中把“计划代码入口”“计划验证”改成实际行为和实际命令结果。
- 记录任何 skip 的外部集成测试，不把 skip 写成 pass。
- 保留“排序权重暂不包含”的边界。

同步修改 `docs/README.md` 对应索引行状态为“已完成”，并把代码入口更新为：

```text
src/shop_agent/models/query.py, src/shop_agent/sku_attributes.py,
src/shop_agent/catalog.py, src/shop_agent/services/dashscope_chat.py,
src/shop_agent/services/evidence.py, src/shop_agent/workflow/
```

- [ ] **Step 7: Re-run documentation and final focused verification**

Run:

```powershell
rg -n "跨品类商品约束与 SKU 匹配|状态：已完成|排序权重" docs/README.md docs/features/cross-category-shopping-constraints.md
uv run pytest tests/unit/test_query_models.py tests/unit/test_sku_attributes.py tests/unit/test_catalog.py tests/unit/test_model_gateways.py tests/unit/test_evidence_service.py tests/unit/test_workflow_stream.py -q
```

Expected: 文档索引和功能文档均显示“已完成”；focused suite exits 0。

---

## Final Acceptance Checklist

- [ ] 旧的 `ParsedIntent` JSON 不提供新字段时仍能解析。
- [ ] 当前59种原始 SKU key 全部映射或被显式说明；不能静默遗漏。
- [ ] `sku_taxonomy` 按 `category/sub_category` 隔离 key 和值。
- [ ] 意图模型不能输出跨子类 key 或目录外离散值。
- [ ] 价格与42码、512GB等条件在同一个 SKU 上联合匹配。
- [ ] SKU 结构化数值由代码比较；文本数值条件进入证据三态验证。
- [ ] required/excluded feature 为 unknown 时候选保留。
- [ ] 任一语义条件 contradicted 时候选淘汰。
- [ ] unknown 不被作为已验证事实写入最终推荐文本。
- [ ] 本计划没有实现新的 supported/unknown 排序权重。
- [ ] 商品事件仍先于文本事件，`display_price` 来自 `matched_skus`。
- [ ] 单元测试、静态检查和实际运行过的集成测试结果被准确记录。
- [ ] 功能文档和项目索引与实际代码状态一致。
- [ ] 未执行任何 Git 操作。
