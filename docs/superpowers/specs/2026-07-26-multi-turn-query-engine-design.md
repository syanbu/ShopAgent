# 多轮 Query 编译与指代消解设计

## 背景

改造前的工作流将每条消息独立解析为完整 `ParsedIntent`。当时 `conversation_id` 只
关联 SSE 事件，图不读取历史条件、历史候选或澄清状态。因此“预算降到
300”“第二个怎么样”“它防水吗”“换一批”等输入无法在当前单轮假设下可靠处理。

多轮能力不能通过拼接历史消息解决。模型若直接重写完整历史意图，可能静默丢失预算、
错误继承跨品类条件或凭空选择商品。本设计将开放语言理解与确定性状态变化分离：模型
输出本轮增量 `TurnQuery`，代码在有限候选域内消解指代、合并槽位并选择工作流分支。

## 目标

- 把多轮碎片表达编译为可独立执行的 `QuerySnapshot`。
- 支持品类、预算、场景、品牌、SKU、必需特征和排除条件的继承与修改。
- 支持最近一轮候选中的序数、指示、品牌和商品名称指代。
- 指代不唯一时保存暂停操作并澄清，用户回答后恢复执行。
- 区分新搜索、条件细化、品类切换、换一批和指定商品问答。
- 最大程度复用现有 `ParsedIntent`、`SearchConstraints`、检索、重排和证据链路。
- 用 SQLite 持久化轻量领域状态，并为未来 MySQL 实现保留仓库接口。

## 非目标

- 不引用最近一轮之前的商品。
- 不保存或拼接完整对话历史，不建立长期用户画像。
- 不将商品数据迁入会话数据库。
- 不引入 Redis、分布式会话和多实例一致性。
- 不处理商品 JSON 热更新或历史商品 ID 失效。
- 不实现图片指代、商品对比、购物车和交易。

## 方案比较

### 模型直接生成完整快照

模型同时接收历史快照和本轮消息，并输出新的完整快照。实现代码较少，但历史条件为何
改变不可解释，容易发生静默丢槽，难以对每次状态变更做确定性测试，因此不采用。

### 模型生成 TurnQuery，代码确定性编译

一次结构化模型调用只提取本轮意图、指代线索和槽位操作。代码完成候选唯一性校验、
条件合并、品类切换、相对价格和路由。这与现有“模型理解、代码守事实”的系统边界
一致，是本设计采用的方案。

### 多模型分阶段解析

意图、指代和槽位分别调用模型，职责更窄，但增加延迟、成本和失败点。当前规模没有
证据表明需要拆分；后续只有在单次 TurnQuery 评测出现明确瓶颈时再考虑。

## 存储分层

```text
商品 JSON
  -> 启动时加载为内存 ProductCatalog
  -> 建索引时生成 Qdrant Chunk 与 payload

SQLite
  -> 只保存 QuerySnapshot、最近候选、焦点、已展示 ID 和待澄清状态
  -> 通过 product_id 引用 Catalog 与 Qdrant，不复制完整商品
```

Qdrant 中包含由商品事实生成的 summary、FAQ、评论和过滤 payload，但它是可重建且
可能陈旧的派生检索索引。商品名称、价格、SKU 和图片等最终事实仍以 JSON/Catalog
为准。

第一版使用 SQLite，因为状态按 `conversation_id` 单行读取、每轮写入一次，且项目为
单实例 Demo。`ConversationRepository` 隔离数据库细节；未来可以添加 MySQL 实现。
Redis 在模型和检索调用主导延迟的当前阶段没有收益，不引入缓存一致性和部署复杂度。

## 核心数据模型

### TurnQuery

`TurnQuery` 只描述本轮变化。模型输入只包含本轮原文和当前会话的紧凑结构化摘要：
QuerySnapshot、最近候选的 rank/商品名/品牌、焦点和待澄清状态；不传完整历史消息。

```json
{
  "schema_version": 1,
  "intent": "refine_search",
  "reference": null,
  "semantic_term_operations": [],
  "slot_operations": [
    {
      "slot": "constraints.max_price",
      "operation": "replace",
      "value": 300
    }
  ],
  "product_question": null
}
```

允许的意图是 `new_search`、`refine_search`、`switch_category`、`more_results`、
`product_question`、`clarification_answer` 和 `non_shopping`。

槽位操作为：

- `replace`：替换预算等标量。
- `add`：增加品牌、场景、SKU 或 feature。
- `remove`：取消已有条件。
- `clear`：清空整个槽位。

标量同一轮最多一个最终操作。列表槽位的 `clear` 不能与同槽位的其他操作共存。
模型输出仍经过 JSON Schema、Pydantic 和一次自动纠错。

最终增量模型固定为：

```text
SemanticTermOperation
  operation: "add" | "remove" | "clear"
  value: str | null

SlotOperation
  slot: "category" | "sub_category" |
        "constraints.min_price" | "constraints.max_price" |
        "constraints.price_preference" |
        "constraints.include_brands" | "constraints.exclude_brands" |
        "constraints.required_features" | "constraints.excluded_features" |
        "constraints.sku_constraints" | "constraints.numeric_constraints"
  operation: "replace" | "add" | "remove" | "clear"
  value: str | float | NumericConstraint | null
  sku_key: CanonicalSkuKey | null
```

语义词的 `add/remove` 携带非空字符串，`clear` 不带值。标量只接受
`replace/clear`；品牌与 feature 列表只接受 `add/remove/clear`。SKU 操作始终通过
`sku_key` 指定规范属性，离散值只用于 `add/remove`，`clear` 清空该 key。数值条件的
`add/remove` 携带完整 `NumericConstraint`，`clear` 不带值并清空数值条件列表。

### ProductReference

```json
{
  "target_type": "product",
  "surface_text": "那个小米的",
  "kind": "brand",
  "ordinal": null,
  "brand": "小米",
  "product_name": null
}
```

模型只输出用户表面表达和可校验线索。`resolved_product_id` 不属于模型协议，只能由
解析器根据最近候选与 Catalog 生成。

### ProductQuestion

商品追问保留原问题文本，并将可直接读取的结构化事实限制为名称、品牌、类目、展示
价格和 SKU。模型可以输出结构化字段枚举或 `semantic`，但不能输出事实答案。代码仅
对允许字段直接读取 Catalog；其余问题按 `product_id` 读取文本知识。

```text
ProductQuestion
  text: str
  kind: "structured" | "semantic"
  field: "title" | "brand" | "category" | "display_price" | "sku" | null
```

`structured` 必须携带 `field`；`semantic` 的 `field` 必须为 `null`。

### QuerySnapshot

```json
{
  "category": "数码电子",
  "sub_category": "蓝牙耳机",
  "semantic_terms": ["适合通勤", "佩戴轻便"],
  "constraints": {
    "min_price": null,
    "max_price": 300,
    "price_preference": null,
    "include_brands": [],
    "exclude_brands": [],
    "required_features": ["适合通勤", "佩戴轻便"],
    "excluded_features": ["入耳式"],
    "sku_constraints": {},
    "numeric_constraints": []
  }
}
```

快照保存结构化语义词而不是不可拆分的历史检索字符串。`semantic_terms` 表示用于
召回但不要求硬验证的正向场景和描述；`required_features` 表示需要证据判断的明确
条件。执行前由代码去重并使用子品类、`semantic_terms` 和必需特征生成
`retrieval_query`，再构造下游兼容的
`ParsedIntent`。

### ConversationState

```text
ConversationState
  schema_version: 1
  conversation_id
  query_snapshot: QuerySnapshot | null
  recent_candidates: CandidateReference[]
  focused_product_id: str | null
  seen_product_ids: list[str]
  pending_clarification: PendingClarification | null

ConversationRecord
  state: ConversationState
  version: int

CandidateReference
  rank
  product_id
  display_price

PendingClarification
  kind
  candidate_product_ids
  suspended_turn_query
  attempt_count
```

`recent_candidates` 是唯一商品指代域。`seen_product_ids` 只用于换一批排重，不能
用于序数、品牌或指示消解。`display_price` 是已经发给客户端的结果快照，只服务于
相对价格编译。SQL 乐观并发版本只保存在 `ConversationRecord`，不会序列化进
`ConversationState.state_json`。

## 指代解析

解析器按以下顺序运行：

1. 序数在 `recent_candidates` 中按 rank 精确查找。
2. 商品名称或品牌在最近候选对应的 Catalog 商品上过滤。
3. 裸指代优先使用 `focused_product_id`。
4. 没有焦点但最近候选只有一个时使用该商品。
5. 结果不是恰好一个时进入澄清，不按模型置信度猜测。

用户明确选择某商品后更新焦点。焦点必须属于最近候选；新搜索、品类切换或换一批
清空焦点。

品牌对象与商品对象分开处理。“这个牌子的还有吗”可以先解析唯一焦点商品，再读取
Catalog 的真实品牌并转为品牌细化；“那个小米的怎么样”若最近候选中有多个小米商品，
仍需澄清具体商品。

## 槽位合并

### 新搜索与品类切换

`new_search` 从空快照开始。若本轮识别出的有效 `category + sub_category` 与旧快照
不同，代码强制执行 `switch_category` 语义，不依赖模型标签，并重置所有旧条件、
候选、焦点、已展示集合和待澄清状态。只有本轮明确重新表达的条件进入新快照。

### 条件细化

`refine_search` 在旧快照上执行操作：

- 指定品牌和排除品牌不能含相同品牌；最新加入的一侧移除另一侧同值。
- 必需 feature 和排除 feature 不能含相同表达；最新加入的一侧移除另一侧同值。
- `clear` 独占该槽位。
- 合并后最低价高于最高价时进入澄清。
- 条件发生变化后清空 `seen_product_ids`，从全库重新检索。

### 相对价格

有焦点时以焦点商品最近展示价格为基准。没有焦点时，便宜以最近候选最低展示价为
基准，贵以最高展示价为基准。没有最近候选时澄清预算。明确金额优先于相对表达。

当前价格以两位小数表示：编译器使用 `Decimal("0.01")` 完成一步运算，最后才在
Pydantic 边界转成两位小数 `float`。更便宜编译为
`max_price = reference - Decimal("0.01")`，更贵编译为
`min_price = reference + Decimal("0.01")`。这保证结果严格跨过已展示价格，而不引入
任意降幅。例如无焦点且最近价格为 `[399, 459, 529]` 时，更贵的最低价为 `529.01`。
早期实现计划写成 `530.01` 是算术笔误；没有已确认的 `530.00` 价格基准。

### Retrieval Query

多轮合并完成后，代码从快照重新生成检索文本；各节点不得继续使用本轮孤立消息作为
完整检索需求。现有性价比价格编译在快照合并后执行，并继续生成
`effective_constraints` 与 `price_reference`。

## 意图路由

| 意图 | 路由行为 |
|---|---|
| `new_search` | 清空历史后完整检索 |
| `refine_search` | 合并条件后从全库重新检索 |
| `switch_category` | 重置旧场景后检索新类目 |
| `more_results` | 保持快照并排除当前搜索的全部 `seen_product_ids` |
| `product_question` | 绑定最近候选中的商品，读取 Catalog，必要时读取该商品 Qdrant Chunk |
| `clarification_answer` | 解析回答并恢复暂停的 TurnQuery |
| `non_shopping` | 正常回答，不修改购物状态 |

连续换一批时，每批替换 `recent_candidates` 并累积 `seen_product_ids`。因此第二批之后
“第二个”只指第二批第二项，下一次换一批仍会排除第一批和第二批全部商品。

## 指定商品知识读取

结构化问题通过 Catalog 的允许字段集合回答，例如商品名称、品牌、类目、展示价格和
匹配 SKU。开放语义问题使用新增的 `fetch_product_chunks(product_id)`：Qdrant 按
已索引的 `product_id` keyword payload 精确 scroll，关闭向量返回并读取该商品全部
Chunk。

该接口返回 `EvidenceChunk`，不复用带相似度分数的 `RetrievedChunk`。最终回答模型只
接收目标商品的 Catalog 事实和真实 Chunk；没有证据时说明未知，不使用常识补全。

普通商品检索增加请求级 `excluded_product_ids` 参数，通过 Qdrant 的
`must_not product_id MatchAny` 排除当前查询已经展示的商品。该运行时排重条件不进入
`SearchConstraints`，因为它不是用户商品约束。

## 工作流改造

```text
START
  -> load_conversation
  -> parse_turn_query
  -> pending? -> resume_pending_action -> resolve_reference / END
  -> resolve_reference
       -> persist_clarification（保存后直接发送澄清文本）-> END
       -> route_turn
            -> search
                 -> merge_query_snapshot -> compile_effective_query
                 -> retrieve_chunks -> aggregate_products -> semantic_rerank
                 -> validate_evidence -> decide_candidates
                 -> persist_search_result -> emit_product_events -> generate_response
                 -> persist_no_results -> generate_response
            -> product_question
                 -> load_product_facts
                 -> fetch_product_knowledge（仅 semantic）
                 -> persist_focus -> generate_product_response -> END
            -> non_shopping -> generate_response -> END
```

`parse_turn_query` 已替换旧单轮解析入口。`TurnQuery` 是该节点输出，不是额外的
历史消息。只有搜索路由进入 `merge_query_snapshot`；商品问答和非购物输入不修改查询
快照。合并结果转成现有检索链路可接受的 `ParsedIntent`。
实际 `compile_effective_query` 节点仍只负责性价比等生效约束。

`decide_candidates` 已是纯选择节点，`emit_product_events` 独立发送商品事件。会话先由
`persist_search_result`、`persist_no_results` 或 `persist_focus` 持久化，再发商品事件
或生成文案，使文案失败时卡片引用状态仍存在。第一版不引入网络事件与数据库之间的
exactly-once 协议。

## SQLite 会话仓库

```text
conversation_state
  conversation_id TEXT PRIMARY KEY
  version INTEGER NOT NULL
  state_json TEXT NOT NULL
  updated_at TEXT NOT NULL
```

仓库读取返回状态与版本；保存必须携带 `expected_version`。更新使用：

```sql
UPDATE conversation_state
SET version = :next_version,
    state_json = :state_json,
    updated_at = :updated_at
WHERE conversation_id = :conversation_id
  AND version = :expected_version;
```

更新零行表示同会话已有请求抢先写入，返回可重试的 `CONVERSATION_CONFLICT`。新会话
使用插入并处理主键竞争。SQLite 只保存领域快照，不保存 LangGraph 的召回 Chunk、
重排结果、证据模型响应或最终文案。第一版不设置 TTL 和自动清理。

## 澄清状态机

`PendingClarification` 保存候选、暂停 TurnQuery 和 `attempt_count`：

- 可唯一解析的回答恢复暂停动作，并清空 pending。
- “算了”等取消表达清空 pending，不执行暂停动作。
- 明确的新搜索清空 pending，按新请求执行。
- 初始指代无法解析时创建 `attempt_count=1` 的 pending 并追问。
- 下一轮澄清答案仍不明确时计为 `attempt_count=2`，立即取消暂停动作、清空 pending，
  并请用户重新完整描述；不保存等待第 3 次回答的状态。

澄清是正常业务分支，不发送 SSE `error`。

## API 与 SSE

`POST /api/v1/chat/stream` 请求体保持不变。`conversation_id` 从关联标识升级为会话状态
主键；未提供时仍生成并在 `message_start` 返回。

现有事件名称与基本顺序保持：

```text
message_start
product * 0..3
text_delta * 1..N
message_end
```

商品问答和澄清通常不发送 product 事件。新增服务错误继续使用现有 `error` 事件。

## 错误与一致性

- SQLite 读取失败返回 `CONVERSATION_UNAVAILABLE`，不能假装没有历史。
- 乐观写冲突返回可重试的 `CONVERSATION_CONFLICT`。
- TurnQuery 两次校验失败返回 `TURN_QUERY_PARSE_FAILED`。
- 商品文本知识读取失败返回 `PRODUCT_KNOWLEDGE_UNAVAILABLE`。
- 所有未唯一解析的指代、相对基准缺失和条件冲突都进入澄清，不进入错误事件。
- Qdrant 失败时只有 Catalog 明确字段可以回答；语义问题不得自由生成。

第一版固定商品数据：商品 JSON 在会话生命周期内不删除、不重命名，服务重启加载同一
数据集，不设计商品 ID 失效清理或状态迁移。

## 可观测性

在现有单行 JSON 日志基础上增加：

- `turn_query`：本轮结构化增量，不记录模型推理文本。
- `reference_resolution`：指代线索、候选数量、焦点和结果，不记录完整商品正文。
- `query_snapshot_compiled`：旧快照摘要、槽位操作和新快照摘要。
- `conversation_persisted`：conversation_id、旧/新版本和状态类型。
- `turn_route`：最终路由和澄清原因。

用户文本和 conversation_id 继续使用现有单行安全编码，避免日志注入。

## 验证

### 指代解析

- 合法序数解析最近候选，越界澄清。
- 单候选裸指代成功，多候选无焦点澄清。
- 显式选择更新焦点，后续“它”复用焦点。
- 品牌唯一命中成功，多商品同品牌澄清，无命中说明最近结果不存在目标。
- `seen_product_ids` 中但不在最近候选中的商品不可引用。

### 合并和相对价格

- 标量 replace/clear 和列表 add/remove/clear 正确。
- 指定与排除品牌、必需与排除 feature 保持互斥。
- 品类切换重置全部旧条件和引用状态。
- 条件细化保留未修改条件并清空已展示集合。
- 焦点价格、最近最低价和最高价分别生成正确相对预算。
- 无价格基准进入澄清，明确金额覆盖相对操作。
- QuerySnapshot 稳定生成 ParsedIntent 与 effective constraints。

### 工作流路由

- 细化走全库检索，不能只过滤最近三个商品。
- 商品结构化问题不调用 Embedding 和 Qdrant。
- 商品语义问题只读取目标 product_id 的 Chunk。
- 指代歧义不调用检索、重排和证据模型。
- 连续换一批排除全部已展示商品，序数只引用最新一批。
- 非购物输入保留购物状态，新品类不继承旧条件。

### 澄清恢复

- 暂停问题在唯一回答后继续执行。
- 取消表达删除 pending。
- 新搜索覆盖 pending。
- 两次不明确后退出澄清状态。

### SQLite

- 相同 conversation_id 可在仓库对象重建后恢复。
- 不同会话隔离。
- 版本递增和并发冲突正确。
- QuerySnapshot、候选、焦点、seen IDs 和 pending JSON 往返不丢失。

### API

- 同 conversation_id 连续请求继承条件与焦点。
- 不同 conversation_id 不共享状态。
- SSE 事件顺序保持兼容。
- 文案失败后已保存候选仍可在下一轮引用。

真实模型测试保持 opt-in。指代、合并、路由和持久化行为全部使用 Fake 构造确定性测试。

当前实现文件与名称以 `src/shop_agent/models/turn_query.py` 的 `TurnQuery`、
`src/shop_agent/models/conversation.py` 的 `QuerySnapshot`/`ConversationState`/
`ConversationRecord`、`src/shop_agent/services/conversation_repository.py` 的
`SqliteConversationRepository`、`src/shop_agent/workflow/nodes.py` 的上述节点和
`src/shop_agent/workflow/graph.py` 的生产编译图为准。HTTP 生产装配位于
`src/shop_agent/api/dependencies.py`，真实 SQLite 多请求验收位于
`tests/integration/test_chat_api.py`。

## 已确认取舍

- 指代范围只包含最近一轮候选，不回溯更早结果。
- 无法唯一解析时追问，不默认选择第一项。
- 品类切换默认重置全部旧条件，只有显式表达才继承。
- 商品问答绑定 product_id 后可以读取该商品全部 Qdrant 知识，但不执行全库商品搜索。
- 商品保持 JSON/Catalog 事实源，SQLite 只存会话状态。
- 第一版 SQLite、未来可换 MySQL，不引入 Redis。
- 商品数据固定，不设计商品 ID 失效处理。
