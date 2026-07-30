# 多商品对比决策

> 状态：提议
>
> 代码入口：尚未创建

## 功能目标

在现有多轮商品推荐之后，支持用户从最近一轮展示的最多三款商品中选择两到三款，
围绕一个明确维度进行对比，并根据商品 JSON 中的真实资料给出有边界的选择建议。

典型对话：

```text
用户：推荐几款面霜
Agent：展示 A、B、C 三款商品
用户：A 和 B 哪个更保湿？
Agent：对比 A、B 的保湿证据，说明差异并给出推荐；证据不足时不强行判断。
```

本功能解决的是已知小候选集中的购买决策，不是从全量商品中重新召回候选，也不是基于
用户行为预测点击或购买概率的个性化推荐系统。

## 范围

本功能包含：

- 新增独立的多商品对比意图和 Agent 工作流分支。
- 对比对象严格限定为 SQLite 会话中 `recent_candidates` 保存的最近一轮商品。
- 支持选择最近候选中的两款或全部三款，包括序数、位置、标题和品牌等自然语言引用。
- 从内存 `ProductCatalog` 按可信 `product_id` 读取完整商品 JSON。
- 使用商品标题、结构化字段、SKU、商品详情、官方问答和用户评论生成对比材料。
- 一次模型调用在统一上下文中完成所有目标商品的证据分析和对比结论。
- 对模型返回的目标商品 ID 和证据引用进行确定性校验。
- 支持明确胜出、平局、依赖使用场景和证据不足四类结论。
- 唯一胜出商品可成为会话焦点，供后续单商品追问使用。

本功能不包含：

- 引用最近一轮之前展示的商品。
- 根据任意商品名称重新执行全库搜索。
- 在目标商品已经确定后重新执行向量召回或语义重排。
- 协同过滤、双塔召回、Learning to Rank、用户画像或行为预测。
- AHP、TOPSIS、Copeland 等额外评分或排名聚合算法。
- 生成数据集中不存在的“保湿度”“修护指数”等数值分数。
- 把用户评论当作官方规格、成分或功效证明。
- 商品对比历史、完整商品 JSON 或模型对比结果的额外持久化。
- 新增客户端专用的结构化对比卡片或 SSE 事件；第一版沿用流式文本回复。

本功能复用 [多轮 Query 编译、稳定条件细化与指代消解](multi-turn-query-engine.md) 的
会话、候选域、指代校验、焦点商品和澄清机制。商品 JSON 继续是唯一权威事实源，
Qdrant 不参与已经确定目标商品的对比取数。

## 外部行为

### 目标选择

对比请求只允许从 `recent_candidates` 中选择商品。模型负责判断当前用户原文与每个最近
候选之间的语言关系，代码负责校验候选矩阵并生成可信的目标商品集合。模型不能直接
生成或选择可信 `product_id`。

第一版需要覆盖：

- “第一款和第二款哪个更保湿”
- “前两个对比一下”
- “第一款和第三款”
- “这三个哪个续航最好”
- “小米和三星这两款拍照哪个好”
- 使用客户端已经展示的 A、B、C 标签选择商品

目标不足两款、品牌引用命中多款但无法确定选择范围，或引用超出最近候选域时，进入
现有可恢复澄清流程，不执行商品取数和回答模型调用。

### 比较维度

用户明确提出“保湿”“续航”“重量”“价格”等维度时，系统只围绕该维度比较。若用户
只说“哪个好”且当前消息没有明确维度，第一版追问：

```text
你更想比较哪方面，例如价格、规格还是使用体验？
```

系统不根据常识自动选择评价维度，也不把当前目录中缺少证据的维度替换成其他维度。

### 商品材料

目标商品确定后，系统从 `ProductCatalog` 直接读取对应原始商品，构造带稳定来源标识的
对比材料：

1. 结构化商品字段与 SKU。
2. 官方问答。
3. 商品详情描述。
4. 用户评论。

事实冲突时沿用现有优先级。用户评论只代表个人体验；多条评论互相冲突时，回答需要
说明存在个体差异，不能用简单多数票覆盖官方资料。

商品材料来自本地 JSON，不执行 Embedding、Qdrant 搜索或 Rerank。实现时可以复用现有
Chunk 构造规则生成稳定证据 ID，以便模型输出和代码校验，但不得把派生 Chunk 视为新的
事实源。

### 对比结论

模型在一次调用中同时接收比较问题和全部目标商品材料，输出结构化判断和面向用户的
自然语言回复。后端完成结构校验和证据白名单校验后，才通过现有 SSE 协议发送回复。
允许的结论为：

| 结论 | 含义 |
|---|---|
| `winner` | 现有资料对某一商品形成明确的相对优势证据 |
| `tie` | 目标商品在当前维度没有可支持的明确高下 |
| `context_dependent` | 优势取决于肤质、季节、具体 SKU 或其他资料中明确存在的场景 |
| `insufficient_evidence` | 至少一个目标商品缺少完成比较所需的关键资料 |

只有 `winner` 可以携带唯一 `winner_product_id`。其他结论不得为了生成推荐而指定
胜出商品。模型不得把文本相似度、Rerank 分数或商品描述篇幅解释为属性强弱。

回答需要包含：

- 每款商品与比较维度直接相关的证据摘要。
- 能够由现有资料支持的主要差异。
- 明确结论及其适用条件。
- 资料不足、评论冲突或 SKU 差异等限制。

例如比较面霜保湿能力时，若一款商品只有用户评论提到“比较润”，另一款有官方问答明确
说明锁水和适用肤质，系统可以说明后者证据更充分，但不能据此生成未经提供的功效数值
或宣称适合所有肤质。

### 会话状态

商品对比不修改 `query_snapshot`、`recent_candidates` 或 `seen_product_ids`。

- `winner`：保存唯一胜出商品为 `focused_product_id`，支持后续“它适合敏感肌吗”等
  单商品追问。
- `tie`、`context_dependent` 或 `insufficient_evidence`：清空旧焦点，避免后续裸指代
  错误落到比较前的商品。
- 对比解析或生成失败：不修改现有会话状态。

### 失败行为

- 商品 ID 不在最近候选域：按非法模型输出处理并纠正一次。
- 证据 ID 不属于对应目标商品：按非法模型输出处理并纠正一次。
- 目标商品已不在 Catalog：返回安全的商品资料不可用错误，不使用模型常识补全。
- 结构化输出纠正后仍非法：进入统一模型解析错误链路。
- 对比模型失败：发送现有安全错误事件，不生成无证据的兜底结论。

## 接口与数据

### 对外接口

第一版继续使用：

```text
POST /api/v1/chat/stream
```

请求体和 SSE 事件类型保持兼容。对比回复使用
`message_start -> text_delta* -> message_end`，不重复发送已经展示的商品卡片，也不新增
客户端必须解析的事件。

### 本轮意图

`TurnIntent` 增加 `product_comparison`。本轮结构需要表达：

```text
ProductComparison
  question: str
  dimension: str
  reference:
    surface_text: str
    candidate_matches[]:
      product_id
      selected
```

`candidate_matches` 必须按 `recent_candidates.rank` 顺序完整覆盖最近候选。模型输出的
`product_id` 只是对服务端候选的逐项复制，解析器先验证完整、有序、无重复且无域外 ID，
再由代码选择 `selected=true` 的两到三款商品。

若比较维度缺失，解析结果进入澄清，不调用对比模型。被暂停的对比请求需要保存目标选择，
用户补充维度后恢复原操作，不要求重新选择商品。

### 模型输出

对比模型使用受 Pydantic 校验的结构化输出：

```text
ComparisonAssessment
  dimension
  products[]:
    product_id
    evidence_ids[]
    supported_summary
    limitations[]
  outcome: winner | tie | context_dependent | insufficient_evidence
  winner_product_id: str | null
  reason
  response_text
```

代码必须校验：

- `products` 与可信目标集合完全一致且顺序稳定。
- 所有 `evidence_ids` 属于对应目标商品的本次材料。
- `winner_product_id` 只在 `outcome=winner` 时存在并属于目标集合。
- `tie`、`context_dependent` 和 `insufficient_evidence` 不携带胜出商品。
- `response_text` 必须遵守和结构化结论相同的胜出结果与资料边界，不得加入未在
  `products` 和 `reason` 中表达的新事实。

结构化结果只存在于单次 LangGraph 状态，不写入 SQLite。SQLite 继续只保存现有会话状态，
不需要表结构迁移。

## 工作流

在现有 `resolve_reference` 和品类解析之后增加对比路由：

```text
parse_turn_query
  -> resolve_comparison_targets
       -> persist_clarification -> END
       -> load_comparison_materials
       -> assess_comparison
       -> persist_comparison_focus
       -> emit_comparison_response
       -> END
```

`load_comparison_materials` 对本地 Catalog 做确定性读取，不触发检索、聚合、重排和现有
单商品证据验证链路。`assess_comparison` 只负责基于白名单材料生成结构化判断；
`emit_comparison_response` 只发送已经校验的 `response_text`，不再调用模型或重新决定
胜负。第一版因此只有一次对比模型调用，不为三款商品拆分成多次成对判断。

## 关键决策

### 不引入复杂推荐算法

候选集合已经由最近一轮结果限定为最多三款，问题是基于商品资料进行购买决策，而不是
从海量商品中预测用户行为。协同过滤、双塔、Learning to Rank 等算法既缺少训练数据，
也不能直接证明“更保湿”或“续航更好”。

### 不使用 Copeland 或人为综合分

第一版使用一次模型调用统一比较全部目标商品，避免多次成对调用产生判断尺度漂移。
最多三款商品不需要额外排名聚合。没有统一量纲和标注数据时也不生成属性分数，避免
制造虚假精度。

### 目标绑定与语义判断分离

模型理解“A 和 B”“前两个”等自然语言关系，代码将判断限制在最近候选域并生成可信目标
集合。模型不能通过生成商品 ID 扩大对比范围。

### 商品 JSON 是唯一事实源

SQLite 只提供最近候选 ID，完整商品内容从 `ProductCatalog` 读取。目标已确定时不再使用
Qdrant；这减少外部依赖和延迟，也避免把检索相似度误当作比较结论。

### 允许没有冠军

数据不完整是当前 Demo 的已知边界。`context_dependent` 和
`insufficient_evidence` 是正常业务结果，不是生成失败。能明确表达资料边界比强行推荐
更符合项目的防幻觉要求。

## 代码与验证

预计主要代码入口：

- `src/shop_agent/models/turn_query.py`：新增多商品对比意图和候选选择结构。
- `src/shop_agent/models/comparison.py`：对比材料和结构化判断模型。
- `src/shop_agent/models/state.py`：单次对比状态。
- `src/shop_agent/services/dashscope_chat.py`：对比解析规则、结构化判断和纠正校验。
- `src/shop_agent/workflow/nodes.py`：目标消解、材料加载、焦点持久化和回复生成。
- `src/shop_agent/workflow/graph.py`：新增对比分支。
- `tests/unit/`：模型、目标消解、证据白名单和工作流测试。
- `tests/integration/test_chat_api.py`：SSE 顺序、会话状态与错误行为。
- `tests/live/test_live_shopping_flow.py`：真实模型多目标引用和对比稳定性。

验证至少覆盖：

- 最近两款和第一、第三款的明确选择。
- 三款全部比较。
- 序数、位置、标题、品牌和 A/B/C 标签引用。
- 目标不足、引用歧义、域外商品和缺少比较维度的可恢复澄清。
- 结构化字段、官方问答、详情和评论的证据优先级。
- SKU 不同导致 `context_dependent`。
- 商品资料不足时返回 `insufficient_evidence`，不强行生成胜出商品。
- 评论冲突时说明个体差异，不把评论升级为官方事实。
- 非法商品 ID、非法证据 ID 和不一致胜出结论的纠正与安全失败。
- 对比请求不调用 Embedding、Qdrant、Rerank 和普通候选选择。
- `winner` 保存焦点，非唯一结论清空焦点，失败不修改会话。
- SSE 保持现有接口兼容顺序。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-07-30 | 创建多商品对比决策提议 | 将参赛 Demo 的对比能力限定为最近候选中的证据驱动决策，明确不引入缺少数据支撑的复杂推荐算法 |
