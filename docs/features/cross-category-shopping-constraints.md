# 跨品类商品约束与 SKU 匹配

> 状态：开发中
>
> 代码入口：`src/shop_agent/models/query.py`、`src/shop_agent/sku_attributes.py`、`src/shop_agent/catalog.py`、`src/shop_agent/services/dashscope_chat.py`、`src/shop_agent/services/evidence.py`、`src/shop_agent/workflow/`

## 功能目标

在现有单轮文本商品推荐工作流之上，将用户提出的商品条件拆分为可执行、可验证的约束，覆盖当前商品目录中的全部一级类目和子类目。

系统应当：

- 使用原始商品 JSON 中真实存在的类目、子类目、商品、SKU、价格和商品证据。
- 区分类目范围、价格与品牌、SKU 属性、数值条件和语义特征。
- 在同一个 SKU 上联合检查价格与 SKU 属性，避免使用其他规格的最低价错误匹配用户需要的规格。
- 对原始数据没有提供充分证据的语义特征标记为 `unknown`，保留候选但降低后续排序优先级，而不是直接淘汰。
- 不允许模型根据常识补充原始商品数据中不存在的能力、规格或属性。

## 范围

本功能包含：

- 购物意图约束结构的拆层设计。
- 跨类目稳定的 SKU 规范属性 key。
- 原始 SKU 属性 key 到规范 key 的上下文映射。
- 每个 `category + sub_category` 可用 SKU 属性及取值的目录视图。
- 价格条件与 SKU 条件在同一个 SKU 上的联合匹配。
- 语义特征的 `supported`、`unknown`、`contradicted` 三态验证语义。
- 将匹配 SKU 传递给商品卡片价格计算和候选决策。

本功能暂不包含：

- `supported` 与 `unknown` 的具体排序权重、打分公式和评测阈值。
- 多轮条件继承、条件修改和指代消解。
- 修改原始商品 JSON 的字段名称或数据结构。
- 从商品描述自动回填新的结构化商品规格。
- 建立覆盖所有自然语言特征的固定枚举或商品知识本体。
- 库存、下单或交易能力。

本功能与 [单轮文本商品推荐工作流](text-shopping-workflow.md) 隔离记录。原文档描述基础单轮工作流；本文描述已经接入该工作流的跨品类约束扩展。

## 数据集边界

当前 `ecommerce_agent_dataset` 是唯一商品事实源，包含：

- 4 个一级类目：美妆护肤、数码电子、服饰运动、食品饮料。
- 37 个二级类目。
- 100 个商品。
- 59 种原始 SKU 属性 key。

原始 SKU 属性存在同义命名，例如：

| 语义 | 原始 key 示例 |
|---|---|
| 存储 | `存储`、`存储容量`、`存储配置`、`机身存储`、`固态存储`、`固态硬盘容量` |
| 颜色 | `颜色`、`配色`、`机身颜色`、`帽身颜色` |
| 尺码 | `尺码`、`鞋码` |
| 包装数量 | `数量`、`每箱数量`、`整箱数量`、`整箱盒数`、`总袋数` |
| 鞋楦 | `鞋楦`、`鞋楦类型` |

同一个原始 key 在不同子类中也可能表达不同含义。例如食品的`容量`通常表示净含量，背包的`容量`表示内部容积，美妆的`容量`表示销售规格。因此不能只按原始 key 做全局字符串替换。

## 外部行为

### 约束处理流程

```text
用户原文
  -> Catalog 为意图提示词提供紧凑的子类 SKU 属性目录
  -> 单次意图调用同时识别 category / sub_category 和分层购物约束
  -> 代码按 Catalog 校验规范 SKU key 与候选值
  -> 按类目、品牌和可执行价格条件召回候选
  -> 对每个商品逐个检查 SKU
       -> 在同一个 SKU 上联合应用价格和 sku_constraints
       -> 保存 matched_skus
       -> matched_skus 为空时淘汰商品
  -> 验证 required_features / excluded_features
       -> supported：明确满足条件
       -> unknown：证据不足，保留候选并降低后续排序优先级
       -> contradicted：明确违反条件，淘汰候选
  -> 排序并返回商品卡片
```

具体排序权重在后续独立设计中确定。本功能只固定候选准入语义：`unknown` 不是硬过滤条件。

### 同一 SKU 联合匹配

用户请求“700 元以内、42 码的跑步鞋”时，价格和尺码必须由同一个 SKU 同时满足。

假设某商品有：

| SKU | 尺码 | 价格 |
|---|---:|---:|
| `sku-a` | 42码 | 799 |
| `sku-b` | 43码 | 699 |

该商品不能匹配用户需求。虽然商品最低 SKU 价格为 699 元，但 42 码 SKU 超出预算。

手机存储同理。用户请求“8000 元以内、512GB 的手机”时，不能使用 256GB SKU 的最低价证明 512GB 版本符合预算。

返回结果仍然以商品为单位：

- 商品至少存在一个匹配 SKU 时才进入候选。
- `matched_skus` 只包含同时满足价格和 SKU 条件的规格。
- `display_price` 取 `matched_skus` 中的最低价格。
- `base_price` 继续保留原始商品事实。

## 接口与数据

### 分层约束

建议的意图结构如下：

```json
{
  "schema_version": 1,
  "intent": "product_search",
  "retrieval_query": "适合拍演唱会、续航好的手机",
  "category": "数码电子",
  "sub_category": "智能手机",
  "constraints": {
    "min_price": null,
    "max_price": 8000,
    "price_preference": null,
    "include_brands": [],
    "exclude_brands": [],
    "sku_constraints": {
      "storage": ["512GB"],
      "color": ["黑色"]
    },
    "numeric_constraints": [
      {
        "field": "battery_capacity",
        "operator": ">=",
        "value": 5000,
        "unit": "mAh"
      }
    ],
    "required_features": [
      "适合拍演唱会",
      "续航好"
    ],
    "excluded_features": [
      "曲面屏"
    ]
  }
}
```

各层职责：

| 层 | 含义 | 执行方式 |
|---|---|---|
| `category` / `sub_category` | 商品检索范围 | 只接受 Catalog 中真实存在的精确值 |
| `constraints.min_price`、`max_price`、品牌字段 | 价格、性价比偏好和品牌 | 代码执行确定性约束 |
| `constraints.sku_constraints` | 尺码、颜色、存储、版本、口味等离散 SKU 属性 | 在同一个 SKU 上做精确或规范化后的离散匹配 |
| `constraints.numeric_constraints` | 续航、重量、容量等需要比较符的数值条件 | 结构化数值由代码比较，文本证据中的数值按三态验证；不得从常识补值 |
| `constraints.required_features` | 商品必须具备的场景、功能或语义属性 | 映射到商品证据并生成三态结果 |
| `constraints.excluded_features` | 商品不得具备的场景、功能或语义属性 | 映射到商品证据并生成三态结果 |

“512GB版本”属于离散 SKU 选择，进入 `sku_constraints`；“存储至少512GB”表达比较关系，进入 `numeric_constraints`。无论条件来自哪一层，只要它约束 SKU 属性，就必须和价格条件在同一个 SKU 上计算。

`numeric_constraints` 按事实位置分两条执行路径：

- 数值来自 `skus[].properties` 时，代码规范化数值与单位，并在同一个 SKU 上执行比较。例如“存储至少512GB”和该 SKU 的价格必须同时满足。
- 数值只存在于商品描述或官方问答时，不能转成结构化硬过滤。证据模型判断条件为 `supported`、`unknown` 或 `contradicted`；证据不足时按 `unknown` 保留候选。

### SKU 规范 key

意图协议使用稳定的英文机器标识，不直接暴露59种原始 key。第一版规范 key 至少包括：

| 规范 key | 含义 | 原始 key 示例 |
|---|---|---|
| `size` | 服装或鞋类尺码 | `尺码`、`鞋码` |
| `color` | 商品主体颜色 | `颜色`、`配色`、`机身颜色`、`帽身颜色` |
| `accent_color` | Logo 或刺绣配色 | `logo配色`、`刺绣logo配色` |
| `storage` | 持久化存储 | `存储`、`存储容量`、`存储配置`、`机身存储`、`固态硬盘容量` |
| `memory` | 运行内存 | `内存`、`内存容量`、`运行内存` |
| `screen_size` | 屏幕尺寸 | `屏幕尺寸`，以及特定子类中的`尺寸` |
| `chip` | 芯片型号 | `芯片`、`芯片型号` |
| `network_version` | 网络版本 | `网络版本` |
| `version` | 商品版本 | `版本`、`产品版本` |
| `capacity` | 净含量或规格容量 | `容量`、`单盒容量`、`单条净含量` |
| `package_count` | 包装内数量 | `数量`、`每箱数量`、`整箱盒数`、`总袋数` |
| `package_type` | 包装方式 | `包装`、`包装类型`、`包装规格` |
| `flavor` | 食品饮料口味 | `口味` |
| `shade` | 美妆色号 | `色号`、`色号规格` |
| `specification` | 美妆或普通销售规格 | `规格`、`产品规格` |
| `gender` | 适用性别 | `适用性别`，以及特定子类中的款型值 |
| `fit` | 款式或版型 | `款式`、特定子类中的`款型` |
| `shoe_last` | 鞋楦 | `鞋楦`、`鞋楦类型` |
| `pants_length` | 裤长 | `裤长` |
| `hat_fit` | 帽围规格或调节方式 | `帽围类型`、`帽围调节方式` |
| `charging_case` | 耳机充电盒类型 | `充电盒类型` |

该列表是协议层规范名称，不是让所有子类都接受所有 key。

### 上下文映射

原始属性到规范属性的映射必须包含类目上下文：

```text
(category, sub_category, raw_key) -> canonical_key
```

示例：

```json
[
  {
    "category": "数码电子",
    "sub_category": "智能手机",
    "raw_key": "存储配置",
    "canonical_key": "storage"
  },
  {
    "category": "服饰运动",
    "sub_category": "跑步鞋",
    "raw_key": "鞋码",
    "canonical_key": "size"
  },
  {
    "category": "食品饮料",
    "sub_category": "碳酸饮料",
    "raw_key": "容量",
    "canonical_key": "capacity"
  }
]
```

Catalog 加载原始 JSON 时保留原始 SKU，并构建只读规范化视图。规范化视图只服务于意图约束校验和 SKU 匹配，不修改或替代原始事实源。

### 子类属性目录

每个 `category + sub_category` 只开放该子类商品实际存在的规范 key 和候选值。例如：

```json
{
  "数码电子/智能手机": {
    "storage": ["256GB", "512GB", "1TB"],
    "color": ["黑色", "白色"],
    "version": ["全网通版"]
  },
  "服饰运动/跑步鞋": {
    "size": ["39码", "40码", "42码"],
    "shoe_last": ["标准楦", "宽楦"]
  },
  "食品饮料/碳酸饮料": {
    "capacity": ["330ml", "480ml"],
    "flavor": ["白桃味"],
    "package_count": ["单瓶", "15瓶整箱"]
  }
}
```

可用 key 和值从当前 Catalog 生成；原始 key 到规范 key 的语义映射需要显式维护和测试。意图模型只能输出当前子类允许的规范 key，不得自由创造 key。

第一版保持现有单次意图模型调用。Catalog 将37个子类的允许 key 和去重后的候选值压缩成 `sku_taxonomy` 注入提示词，模型在一次输出中同时返回类目和约束。代码随后按已识别的 `category + sub_category` 校验 key 与值。无效 key、跨子类 key 或目录中不存在且无法规范化的 SKU 值不能进入硬过滤。实现时需要测试提示词大小，防止完整 SKU 重复值造成不必要的上下文膨胀。

### 语义证据状态

状态针对“用户条件是否得到满足”，而不是仅判断某个词是否在文本中出现：

| 状态 | 含义 | 候选行为 |
|---|---|---|
| `supported` | 商品证据明确证明满足用户条件 | 保留，后续排序优先 |
| `unknown` | 原始数据没有足够证据判断 | 保留，后续排序降级 |
| `contradicted` | 商品证据明确证明违反用户条件 | 淘汰 |

`supported` 和 `contradicted` 都必须携带决定性的 `evidence_ids`；只有
`unknown` 可以在没有决定性证据时返回。`conflicting_evidence_ids` 只记录与最终
判断相反一侧的证据，不能代替触发保留或淘汰的决定性证据。

对于排除条件，“不含咖啡因”有明确官方证据时为 `supported`；商品完全没有提到咖啡因时为 `unknown`；明确含咖啡因时为 `contradicted`。

用户表达的条件应完整保留。意图阶段不能因为商品目录可能没有对应证据而删除 feature；证据验证阶段负责产生三态结果。

## 关键决策

### 返回商品，使用 SKU 判断商品是否可推荐

外部结果继续返回商品卡片，不把响应改成 SKU 列表。SKU 约束用于判断商品下是否存在可购买的匹配规格，并决定 `matched_skus` 和 `display_price`。

### 规范 key 与原始事实源分离

规范 key 使用英文稳定标识，避免模型和接口依赖原始数据中的同义中文字段。原始 JSON 仍是唯一事实源，返回卡片时保留原始 SKU 属性和值。

### 一个通用模型覆盖全部子类

不为37个子类创建37套固定 Pydantic 模型。通用约束结构负责协议稳定性，子类属性目录负责限制每个子类可使用的 key 和值。

### unknown 不作为硬过滤条件

语义特征缺少证据时不能宣称满足，也不直接淘汰商品。候选保留并在后续排序中低于明确 `supported` 的候选。具体排序权重和补位策略留给后续设计。

### 不把所有用户表达都预定义为 feature 枚举

`required_features` 和 `excluded_features` 保持开放文本，以承载“适合通勤”“不甜腻”“穿着不贴身”等跨品类自然语言需求。开放文本只影响条件表达，不降低事实要求；商品是否满足仍必须由原始 JSON 证据判断。

## 代码与验证

### 已知问题

- 复合商品短语同时包含多个合法子类词时，意图模型缺少稳定的中心词选择规则。例如“防晒精华”当前可能被解析为 `sub_category=防晒`、`required_features=["精华"]`，而不是以“精华”为商品子类。

以上问题尚未修复，因此本功能保持“开发中”状态；当前实现是可验证的阶段性检查点。

已实现代码入口：

- `src/shop_agent/models/query.py`：分层约束、SKU 约束和数值约束模型。
- `src/shop_agent/sku_attributes.py`：59种原始 key 的规范映射、taxonomy 和数值单位归一化。
- `src/shop_agent/catalog.py`：规范属性目录、SKU 规范化视图和同一 SKU 联合匹配。
- `src/shop_agent/services/dashscope_chat.py`：向意图模型提供当前子类允许的规范 key 与值，并在证据结构化调用的自动纠错范围内校验 condition ID 完整覆盖。
- `src/shop_agent/services/evidence.py`：执行结构化硬过滤和语义三态候选准入。
- `src/shop_agent/workflow/`：在检索、证据验证和候选决策之间传递新约束与匹配 SKU。

已验证行为：

- Catalog 能加载当前4个一级类目、37个子类和100个商品，并为全部原始 SKU key 建立有效映射或明确豁免。
- 同义原始 key 在正确类目上下文中映射到相同规范 key。
- 同名原始 key 在不同子类中可以映射到不同语义。
- 意图模型不能为当前子类输出未开放的 SKU key。
- 单次意图调用能够同时输出类目和受对应子类目录约束的 SKU 条件。
- `sku_taxonomy` 使用去重值且提示词大小处于配置的安全边界内。
- 42码与预算必须由同一个鞋类 SKU 同时满足。
- 512GB与预算必须由同一个手机 SKU 同时满足。
- SKU 中存在的数值条件由代码比较，只存在于文本证据中的数值条件走三态验证。
- `matched_skus` 只包含满足所有适用硬条件的 SKU。
- `display_price` 来自过滤后的 `matched_skus`，不使用其他规格的最低价。
- `supported` 候选保留，`unknown` 候选也保留，`contradicted` 候选淘汰。
- `supported` 和 `contradicted` 缺少决定性 `evidence_ids` 时，证据响应校验失败，不能据此保留或淘汰商品。
- 证据模型遗漏、改写或重复 condition ID 时，第一次响应校验失败并进入现有的一次自动纠错；正常响应仍只调用一次证据模型。
- 商品 JSON 没有证据时，模型不能将语义条件判定为 `supported`。
- 原有无 SKU 属性条件的价格检索行为保持兼容。

验证命令与结果：

- `uv run pytest tests/unit tests/integration -q -rs`：170 passed；本次本地 Qdrant 集成测试实际执行并通过。
- `uv run ruff check .`：通过。
- `uv run mypy src`：33个源文件通过。
- 真实数据专项测试：3 passed，确认4个一级类目、37个子类、100个商品及全部59种原始 SKU key 均可加载和规范化。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-07-24 | 创建跨品类约束、SKU 规范化和语义三态设计 | 将新约束能力与已完成的单轮推荐工作流隔离记录，并固定当前已确认的设计边界 |
| 2026-07-25 | 完成约束模型、taxonomy、同一 SKU 匹配和 unknown 准入 | 实现已确认设计，并记录确定性测试与未通过的外部 Qdrant 验证边界 |
| 2026-07-25 | 要求 contradicted 携带决定性证据 | 防止证据模型在没有原始商品证据时淘汰候选，并明确冲突证据不能替代决定性证据 |
| 2026-07-25 | 状态调整为开发中并记录真实请求缺陷 | 复合子类短语仍可能误解析，证据条件覆盖错误仍会终止整个流；同时更新本地 Qdrant 已实际通过的验证结果 |
| 2026-07-25 | 将证据 condition ID 覆盖校验纳入结构化调用纠错 | 缺失、改写或重复 ID 时允许模型纠正一次，避免首次可纠正输出直接终止 SSE |
