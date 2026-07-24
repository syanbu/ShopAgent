# 性价比价格偏好与子品类价格基准设计

## 背景

当前单轮意图能够提取明确的价格上下限，但无法稳定处理“性价比高”这类模糊价格
表达。如果把“性价比高”放入 `required_features`，系统会要求商品描述、官方问答
或用户评价为它提供语义证据，这与本项目希望采用的价格规则不一致。让模型直接填写
价格上限也不可行，模型不知道当前商品目录的价格分布，生成的金额无法验证。

本阶段为每个 `category + sub_category` 计算价格基准。模型只识别用户是否表达
“性价比高”，具体价格由后端根据商品目录确定。

## 目标

- 服务启动时计算每个子品类的最低 SKU 价格中位数。
- 将中位数的 `120%` 作为“性价比高”的动态价格上限。
- 在意图中保留用户明确表达的预算和价格偏好。
- 在后端生成一份独立的生效约束，供检索、SKU 校验和候选选择共同使用。
- 为后续多轮 Query 编译保留原始语义和价格计算依据。

## 范围

本阶段包含：

- 子品类价格统计及内存缓存。
- 单轮输入中“性价比高”的语义识别。
- 明确预算与动态价格上限的合并。
- 缺少子品类时的澄清回复。
- 原始意图、价格基准和生效约束的日志。

本阶段不包含：

- 多轮历史、上下文快照和 Query 合并。
- “便宜一点”“贵一点”等相对价格调整。
- 序数、指示或品牌指代消解。
- 配置、口碑、销量和价格的综合评分。
- 一级类目价格中位数或跨子品类价格回退。

## 语义定义

“性价比高”在本阶段表示价格不超过当前商品目录中同一子品类主流价格的上沿。它是
项目针对 mock 数据定义的价格策略，不代表完整的配置价格比或市场价值判断。

模型输出新的价格偏好字段：

```json
{
  "constraints": {
    "min_price": null,
    "max_price": null,
    "price_preference": "value",
    "include_brands": [],
    "exclude_brands": [],
    "required_features": [],
    "excluded_features": []
  }
}
```

`price_preference` 的可用值为：

- `"value"`：用户明确要求“性价比高”或语义等价的表达。
- `null`：用户没有表达该价格偏好。

模型只负责语义判断，不能填写统计中位数、倍率或生效价格。“性价比高”不能同时进入
`required_features`、`excluded_features` 或 `retrieval_query`。

## 子品类价格基准

### 数据来源

价格统计使用 `ProductCatalog` 已加载的原始商品 JSON。每个商品只贡献一个价格样本：

```text
商品价格样本 = min(product.skus[*].price)
```

一个商品包含多少个 SKU 都不会改变其统计权重。

### 分组与计算

商品按 `(category, sub_category)` 分组。每组计算：

- `sample_count`：该组商品数量。
- `median_min_sku_price`：该组商品最低 SKU 价格的中位数。
- `value_price_cap`：`median_min_sku_price × 1.2`，保留两位小数。

奇数样本取排序后的中间值，偶数样本取中间两个值的平均数。只有一个商品时，该商品的
最低 SKU 价格就是中位数。当前数据为 mock 数据，不设置最小样本数门槛。

建议使用以下只读记录表示一个价格基准：

```text
CategoryPriceReference
  category
  sub_category
  sample_count
  median_min_sku_price
  value_price_cap
```

`ProductCatalog` 在加载完全部商品后一次性构建映射，服务运行期间不重新计算。商品
JSON 变化后需要重启服务；价格或 Chunk 数据变化时仍按现有规则重新构建 Qdrant
索引。

### 当前数据示例

| 类目 | 子品类 | 样本数 | 中位数 | 性价比上限 |
|---|---|---:|---:|---:|
| 数码电子 | 智能手机 | 10 | 7249.00 | 8698.80 |
| 服饰运动 | 短袖T恤 | 3 | 129.00 | 154.80 |

这些数值是设计时对当前数据集的验收基准。商品数据扩充后，中位数和上限应随目录重新
计算，不能写成业务常量。

## 原始意图与生效约束分离

`ParsedIntent` 保存模型提取的原始语义。用户明确表达的 `min_price`、`max_price`
和 `price_preference` 都保留在这里，不被编译过程覆盖。

图状态新增后端生成的 `effective_constraints`。Qdrant 过滤、Catalog SKU 筛选、
证据校验和候选选择必须读取同一份生效约束，不能在各节点重复计算价格。

价格编译同时保存 `price_reference`，用于日志和以后多轮重新编译：

```text
PriceCompilationReference
  category
  sub_category
  sample_count
  median_min_sku_price
  multiplier
  computed_price_cap
  applied
  skip_reason
```

`multiplier` 本阶段固定为 `1.2`。`skip_reason` 只在没有应用统计上限时使用。

## 约束编译规则

对 `product_search` 意图按以下顺序编译：

1. 复制用户明确表达的价格、品牌和属性约束。
2. `price_preference` 不是 `"value"` 时，不应用子品类价格基准。
3. 缺少 `category` 或 `sub_category` 时，停止编译并进入澄清回复。
4. Catalog 中没有对应价格基准时，停止编译并进入澄清回复。
5. 用户没有明确 `max_price` 时，使用 `value_price_cap`。
6. 用户明确 `max_price` 时，暂定上限为二者较小值。
7. 如果用户明确的 `min_price` 高于暂定上限，明确数字优先，放弃本次统计上限。

第 7 条中，如果用户没有明确上限，生效约束只保留 `min_price`；如果用户同时给出
有效的明确价格区间，则恢复该明确区间。编译前必须校验用户明确的
`min_price <= max_price`；如果明确区间本身无效，则进入意图解析失败链路，不能
通过放弃统计上限修复用户自身矛盾的数字条件。

示例：

| 用户表达 | 明确价格 | 统计上限 | 生效价格 |
|---|---|---:|---|
| 性价比高的手机 | 无 | 8698.80 | `max_price=8698.80` |
| 8000 元以内、性价比高的手机 | `max=8000` | 8698.80 | `max_price=8000` |
| 10000 元以内、性价比高的手机 | `max=10000` | 8698.80 | `max_price=8698.80` |
| 至少 9000 元、性价比高的手机 | `min=9000` | 8698.80 | `min_price=9000`，不应用统计上限 |

## 工作流位置

价格编译作为独立节点放在购物意图路由之后、检索之前：

```text
structure_intent
  -> route_intent
       -> non_shopping -> generate_response
       -> product_search -> compile_query
            -> compiled -> retrieve_chunks
            -> needs_clarification -> generate_clarification
```

这样不会提前引入多轮状态，也为后续上下文快照、槽位合并和相对价格操作保留明确入口。

`compile_query` 只做确定性转换，不调用模型、Embedding 或 Qdrant。编译成功后，后续
节点统一使用 `effective_constraints`：

- Qdrant 根据生效的品牌和价格字段粗筛。
- Catalog 根据生效价格逐个筛选 SKU。
- 证据校验使用相同约束判断商品是否合格。
- 候选选择使用相同约束生成 `matched_sku_ids`。

## 澄清行为

当 `price_preference="value"` 但缺少有效的 `category + sub_category` 时，系统
跳过 Embedding、Qdrant、重排序和证据校验，通过现有 SSE 文本事件返回：

```text
请明确想购买的商品类型，例如手机、T恤或耳机。
```

本阶段不保存这次澄清的上下文。用户需要重新提交完整需求；连续澄清在多轮 Query
编译功能中实现。

## 日志与可观测性

现有 `parsed_intent` 日志继续记录模型输出的原始意图。价格编译完成后增加一条单行
JSON 日志，记录：

- 原始 `min_price` 和 `max_price`。
- `price_preference`。
- 子品类中位数、样本数和 `1.2` 倍率。
- 计算出的价格上限。
- 最终生效的 `min_price` 和 `max_price`。
- 是否应用统计上限及未应用原因。

日志沿用现有单行 JSON 编码规则，中文保持可读，用户输入不能注入额外日志行。

## API 与存储影响

- `POST /api/v1/chat/stream` 请求和 SSE 事件结构不变。
- `ParsedIntent.constraints` 增加可选的 `price_preference`。
- 图状态增加 `effective_constraints` 和可选的 `price_reference`。
- 不新增数据库、checkpointer、配置项或环境变量。
- 不修改 Qdrant payload 和 collection schema。
- 当前 Qdrant 已有 `min_sku_price`、`max_sku_price`，本功能不要求单独重建索引。

## 验证

### 价格统计

- 一个商品只贡献最低 SKU 价格。
- 奇数样本、中位数为小数的偶数样本和单商品样本计算正确。
- 同一商品的多个 SKU 不会增加统计权重。
- 价格基准按 `category + sub_category` 隔离。
- 金额保留两位小数。
- 当前数据得到智能手机 `7249.00 / 8698.80`，短袖T恤
  `129.00 / 154.80`。

### 意图提取

- “推荐性价比高的手机”输出 `price_preference="value"`。
- “性价比高”不进入 `required_features`、`excluded_features` 或
  `retrieval_query`。
- 没有相关语义时 `price_preference=null`。
- JSON Schema、提示词规则和代表性示例包含该字段。

### 约束编译

- 没有明确预算时使用统计上限。
- 明确上限更低时保留明确上限。
- 明确上限更高时使用统计上限。
- 明确最低价与统计上限冲突时，保留明确数字并记录跳过原因。
- 缺少子品类或价格基准时进入澄清分支。
- 非购物意图不执行价格编译。

### 工作流

- 检索、证据校验、候选选择收到同一份 `effective_constraints`。
- 澄清分支不调用 Embedding、Qdrant、重排序和证据模型。
- 商品卡片只包含符合生效价格上限的 SKU。
- SSE 事件顺序和现有错误语义保持不变。

## 风险与后续

当前“性价比高”只表达相对价格上限，没有评价配置、性能或口碑。回复文案不能把它
描述为经过综合评分的性价比结论。后续若引入可解释评分，需要重新定义
`price_preference="value"` 对筛选与排序的作用。

价格基准反映当前 mock catalog，不代表市场价格。样本较少时仍按现有商品计算，并
通过 `sample_count` 保留统计背景。扩充数据后无需修改规则，重启服务即可重新计算。

后续多轮 Query 编译继续复用原始 `price_preference`、`effective_constraints` 和
`price_reference`，但“便宜一点”“贵一点”需要新的相对价格操作和参照物，不能扩展
`price_preference` 枚举来代替。
