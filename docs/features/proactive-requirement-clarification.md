# Agent 主动需求澄清

> 状态：开发中
>
> 代码入口：
> `src/shop_agent/services/proactive_clarification.py`、
> `src/shop_agent/models/turn_query.py`、
> `src/shop_agent/models/conversation.py`、
> `src/shop_agent/services/dashscope_chat.py`、
> `src/shop_agent/workflow/nodes.py`、`src/shop_agent/workflow/graph.py`

## 功能目标

当用户只明确了一个可用的商品子品类、目录中存在超过当前展示上限的多个差异化候选，
但没有表达预算、品牌、使用场景、功能、规格或其他决策信息时，Agent 在检索前主动提出
一次与该子品类真实商品属性相关的问题，引导用户补充偏好或硬约束，再复用现有多轮
Query 编译、检索、重排和证据验证链路完成推荐。

典型对话：

```text
用户：推荐一款手机
Agent：你更看重拍照、续航、性能还是性价比？也可以补充预算。
用户：拍照优先，预算 4000
Agent：将“拍照优先”作为软偏好，将“预算 4000”作为最高价格硬约束，继续推荐。
```

本功能解决“可以直接检索，但条件过少导致推荐缺少决策依据”的需求发现问题。它不同于
现有阻塞型澄清：品类歧义、商品指代歧义、价格冲突、缺少价格基准和缺少对比维度仍由
现有澄清链路处理。

## 范围

本功能包含：

- 只在 `new_search` 或 `switch_category` 的已合并完整查询上判断是否主动反问。
- 只对 Catalog 唯一解析出的 `category + sub_category` 生效。
- 仅当完整查询除类目外没有预算、性价比、品牌、语义偏好、feature、SKU、数值或排除
  条件时，认为缺少决策信息。
- 仅当该子品类商品数大于 `Settings.final_product_limit` 且存在已审核问题策略时反问。
- 反问内容来自按子品类维护的确定性白名单，不由模型临时生成。
- 一次购物搜索最多主动反问一次；用户回答不充分时不继续盘问，按原始类目搜索。
- 用户可以在首次请求或反问回答中表达“直接推荐”“先看看”“不用问”等跳过意图。
- 用户回答继续由 `DashScopeTurnQueryParser` 解析为现有软偏好和硬约束操作。
- 使用 SQLite `pending_clarification` 保存暂停的新搜索，并在下一轮恢复。
- 保持现有 `POST /api/v1/chat/stream` 和 SSE 事件类型不变。

本功能不包含：

- 根据任意缺失字段逐项盘问，或连续进行问卷式访谈。
- 让 LLM 自行判断“信息是否足够”或自由生成问题与选项。
- 对已有明确偏好或约束的查询再次要求用户补充信息。
- 为目录中不超过三个商品的子品类增加无筛选价值的反问。
- 基于用户画像、点击、成交或长期历史生成个性化问题。
- 新增数据库表、Redis、外部问卷服务或新的模型调用。
- 改变现有阻塞型澄清、商品指代、商品对比或检索证据语义。

## 外部行为

### 主动反问触发门槛

主动反问必须同时满足以下条件：

1. 编译后的搜索意图是 `new_search` 或 `switch_category`。
2. 品类已经唯一绑定到 Catalog 中的精确 `category + sub_category`。
3. 合并后的 `QuerySnapshot` 除类目外没有任何决策信息：
   - `semantic_terms` 为空；
   - `min_price`、`max_price` 和 `price_preference` 为空；
   - 指定和排除品牌为空；
   - 必需和排除 feature 为空；
   - `sku_constraints` 和 `numeric_constraints` 为空。
4. 子品类商品数大于 `final_product_limit`；当前默认上限为三款。
5. 子品类存在下表中的已审核问题策略。
6. 本轮没有显式跳过主动反问。

判断依据是 `merge_query_snapshot` 产生的权威完整快照，不是本轮模型原始输出字段数量。
因此用户本轮只提到品类、但完整会话快照已经含有预算或偏好时，不触发主动反问。

### 首批子品类问题策略

首批策略只覆盖当前数据集中商品数超过三、且商品资料能够支持差异化回答的子品类：

| Category / Sub-category | 固定问题 |
|---|---|
| 数码电子 / 智能手机 | 你更看重拍照、续航、性能还是性价比？也可以补充预算。 |
| 数码电子 / 真无线耳机 | 你更看重降噪、音质、佩戴体验还是续航？也可以补充预算。 |
| 数码电子 / 笔记本电脑 | 主要用于办公学习、便携出行还是内容创作？也可以补充预算。 |
| 数码电子 / 平板电脑 | 主要用于学习办公、影音娱乐还是绘画创作？也可以补充预算。 |
| 美妆护肤 / 精华 | 你更关注修护、提亮、淡纹抗老还是控油？也可以补充预算。 |
| 服饰运动 / 跑步鞋 | 你更偏向日常训练、长距离缓震还是轻量竞速？也可以补充预算和尺码。 |
| 食品饮料 / 咖啡 | 你更偏好黑咖啡、奶咖口感还是冷萃便捷？也可以补充预算。 |
| 食品饮料 / 方便食品 | 你更偏好哪种口味，以及杯装还是袋装？也可以补充数量或预算。 |

策略白名单是事实边界。新增子品类即使商品数超过三，只要没有经过商品资料审核并加入
策略表，就直接执行普通推荐，不能由模型套用其他品类的问题。例如跑步鞋不得询问
“需要多少分辨率”，手机也不得询问鞋码。

### 不触发场景

- “推荐拍照好的手机”：已经包含“拍照”软偏好，直接检索。
- “推荐 4000 元以内的手机”：已经包含价格硬约束，直接检索。
- “先随便推荐几款手机”：解析为显式跳过，直接检索。
- “推荐面霜”：当前目录只有三款，能够一次完整展示，直接检索。
- “推荐鞋”：对应多个子品类，继续走现有 `ambiguous_category` 阻塞型澄清。
- “这三款哪个好”：继续走缺少比较维度的商品对比澄清。

### 回答与恢复

主动反问使用现有文本 SSE 顺序，不发送商品卡片：

```text
message_start -> text_delta -> message_end
```

发送反问前必须先保存 `pending_clarification`，其 `kind` 为
`missing_preferences`，并保存被暂停的 `TurnQuery`。下一轮：

- “拍照优先，预算 4000”：模型输出 `clarification_answer`，将“拍照优先”解析为
  `semantic_term_operations`，将预算解析为 `constraints.max_price`，再与暂停的新搜索合并。
- “先看看”：模型输出主动反问跳过标记，恢复原始搜索但不添加偏好或约束。
- 回答无法形成任何有效操作：清除 pending，并按原始搜索继续；不进行第二次主动反问。
- “算了”：沿用 `cancel_pending`，取消本次搜索。
- 明确发起其他新搜索：废弃旧 pending，执行新搜索。

### 与阻塞型澄清的优先级

阻塞型澄清优先于主动反问。品类无法唯一解析、条件冲突或缺少必要上下文时，不运行
主动反问判断。只有现有 `merge_query_snapshot` 已成功产生可执行快照后，才判断是否值得
主动收集偏好。

## 接口与数据

### `TurnQuery`

已增加内部布尔标记 `skip_preference_question`：

- 首次请求中的“直接推荐”“先看看”“不用问”将其设为 `true`。
- `missing_preferences` pending 下的“先看看”同样将其设为 `true`。
- 它不进入 `QuerySnapshot`，不参与检索文本、筛选或排序。
- 与 `cancel_pending=true` 不能同时存在。

其他回答继续使用现有结构：普通倾向进入 `semantic_term_operations`，明确价格、品牌、
feature、SKU 和数值限制进入对应 `slot_operations` 或价格结构。

### `PendingClarification`

`PendingClarification.kind` 已增加 `missing_preferences`。不新增持久化字段；问题文本可由
暂停请求中的唯一品类和确定性策略表重新得到。SQLite 表结构不变，仍只保存
`ConversationState.state_json`。

新增 enum 值会使完全回退到旧二进制时无法读取仍处于该 pending 的会话。行为回滚时应先
关闭主动反问路由，但暂时保留新 enum 的读取与“直接恢复原搜索”兼容，待 pending 自然
清除后再删除兼容代码。商品数据和查询快照不需要迁移。

### 确定性策略

`src/shop_agent/services/proactive_clarification.py` 集中负责：

- 子品类问题白名单；
- 判断完整快照是否只有类目；
- 统计对应子品类商品数；
- 与 `final_product_limit` 比较；
- 返回固定问题或“不反问”。

该服务只读取内存 Catalog 和 Settings，不调用模型、Embedding、Qdrant 或 Rerank。

## 工作流

现有搜索路径已经增加一个确定性节点：

```text
resolve_category_reference
  -> route_turn
  -> merge_query_snapshot
       -> needs_clarification -> persist_clarification -> END
       -> decide_proactive_clarification
            -> ask -> persist_clarification -> END
            -> continue -> compile_effective_query
                 -> retrieve_chunks -> aggregate_products
                 -> semantic_rerank -> validate_evidence
                 -> decide_candidates -> existing response path
```

`decide_proactive_clarification` 不修改已编译条件；只有选择 `ask` 时构造
`missing_preferences` pending 和固定文本。回答恢复后继续经过同一个
`merge_query_snapshot`，因此“拍照优先”与“预算 4000”复用现有软偏好与硬约束逻辑。

## 关键决策

### 以完整快照而不是模型字段数判断

多轮查询的事实来源是合并后的 `QuerySnapshot`。直接查看本轮 LLM 是否只输出类目会忽略
历史预算和偏好，并可能对已经足够具体的请求重复提问。

### 确定性白名单，不让模型生成问题

错误的跨品类问题会直接破坏用户信任。问题数量很小且与数据集强绑定，使用代码审查的
固定策略比增加一次开放式模型判断更稳定、可测试，也不会增加延迟和成本。

### 只问一次，允许直接跳过

主动反问不是完成搜索的必要条件。用户不给信息时仍应得到推荐，因此不复用阻塞型澄清的
两次失败退出策略，也不将用户困在澄清循环中。

### 候选不超过展示上限时直接推荐

当前一次最多发送三款商品。如果一个子品类最多只有三款，主动反问不能减少结果集合，
只会增加交互摩擦。

### 回答复用现有 Query 编译

本功能只负责决定是否提问和保存暂停状态，不创建第二套偏好、预算或筛选模型。
软偏好仍进入 `semantic_terms`，硬约束仍进入 `SearchConstraints`，下游检索和证据规则不变。

## 代码与验证

代码入口：

- `src/shop_agent/services/proactive_clarification.py`：策略表和确定性判断。
- `src/shop_agent/models/turn_query.py`：跳过标记及结构校验。
- `src/shop_agent/models/conversation.py`：`missing_preferences` pending 类型。
- `src/shop_agent/services/dashscope_chat.py`：首次跳过、回答偏好和跳过回答的解析规则。
- `src/shop_agent/workflow/nodes.py`：主动判断、pending 构造和回答恢复。
- `src/shop_agent/workflow/graph.py`：检索前分支。

验证范围：

- 策略表中的问题与精确子品类绑定，未知子品类不提问。
- 商品数大于三且只有类目时提问；不超过三时直接检索。
- 已有软偏好、硬约束或显式跳过时直接检索。
- 提问轮不调用 Embedding、Qdrant、Rerank 或证据模型。
- pending 在文本发送前保存，并可跨 SQLite repository 重建恢复。
- 回答中的软偏好和硬约束合并后走现有完整推荐链路。
- 跳过、无有效回答、取消和新搜索覆盖均不会形成追问循环。
- HTTP/SSE 事件顺序保持兼容。
- 真实模型能够稳定解析“拍照优先，预算 4000”和“先看看”。

详细执行顺序见
`docs/superpowers/plans/2026-08-03-proactive-requirement-clarification.md`。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-08-04 | 实现确定性问题策略、跳过合同、`missing_preferences` 持久化恢复、LangGraph 分支和 HTTP/SSE 集成 | 让只有类目的宽泛搜索在检索前最多收集一次子品类相关偏好，并完整复用既有查询编译链路 |
| 2026-08-03 | 创建设计提议，确定完整快照门槛、子品类问题白名单、一次提问与现有 Query 编译复用边界 | 补齐课题中的 Agent 主动反问能力，同时避免跨品类错误问题和无价值追问 |
