# 多轮 Query 编译与指代消解

> 状态：开发中
>
> 代码入口：`src/shop_agent/models/turn_query.py`、`src/shop_agent/models/conversation.py`、`src/shop_agent/services/ports.py`、`src/shop_agent/services/conversation_repository.py`、`src/shop_agent/services/reference_resolver.py`、`src/shop_agent/services/multi_turn_query_compiler.py`、`src/shop_agent/services/dashscope_chat.py`、`src/shop_agent/services/retrieval.py`、`src/shop_agent/services/qdrant_store.py`、`src/shop_agent/workflow/nodes.py`、`src/shop_agent/workflow/graph.py`、`src/shop_agent/api/dependencies.py`、`tests/unit/test_model_gateways.py`、`tests/unit/test_reference_resolver.py`、`tests/unit/test_multi_turn_workflow.py`、`tests/integration/test_chat_api.py`

## 功能目标

在现有单轮商品推荐工作流之上增加会话级 Query 编译能力。系统将用户多轮、碎片化的
表达编译为一份可独立执行的查询快照，支持继承、替换、删除和清空品类、预算、场景、
品牌偏好、SKU 条件、必需特征与避雷项。

系统只在最近一轮展示的商品中解析“第二个”“中间那个”“三星这个”“那个小米的”
“它”等指代。模型对自然语言与候选标题、品牌的关系做逐项判断，代码校验候选集合并
根据命中数量确定唯一对象或歧义。无法唯一确定对象时不猜测，而是保存被暂停的操作并
追问用户；用户明确目标后继续原操作。
商品问答解析出 `product_id` 后，从内存 `ProductCatalog` 读取结构化事实，必要时仅从
Qdrant 读取该商品的文本知识，不重新执行全库商品搜索。

## 范围

本功能包含：

- SQLite 会话状态和可替换的 `ConversationRepository`。
- 本轮增量 `TurnQuery`、完整 `QuerySnapshot` 和确定性槽位操作。
- 最近一轮商品候选、焦点商品与序数、指示、品牌、商品名称指代。
- 用户自然语言商品类型到 Catalog 规范品类的唯一绑定与歧义澄清。
- 指代歧义、条件冲突和缺少上下文时的可续接澄清。
- 新搜索、条件细化、品类切换、换一批、商品追问、澄清回答和非购物路由。
- “再便宜一点”“贵一点”等相对价格操作。
- 当前查询范围内的已展示商品去重。
- 按 `product_id` 读取 Qdrant 商品知识。
- 会话乐观并发控制和确定性单元、工作流、SQLite、API 测试。

本功能不包含：

- 引用最近一轮结果之前的商品。
- 将完整历史消息拼接给模型或实现长期用户画像。
- 将商品事实迁移到 SQLite、MySQL 或 Qdrant。
- Redis 缓存、分布式锁、跨实例会话协调和生产级高并发治理。
- MySQL 会话仓库实现；接口边界允许后续替换。
- 商品数据热更新、商品 ID 失效处理和会话状态迁移器。
- 图片指代、多模态输入、商品对比、购物车和交易操作。
- 任意比例的模糊降价策略；相对价格只使用明确的历史价格基准。

本功能依赖 [单轮文本商品推荐工作流](text-shopping-workflow.md) 的检索、重排、证据
校验和 SSE 链路，以及 [跨品类商品约束与 SKU 匹配](cross-category-shopping-constraints.md)
的分层约束与同一 SKU 判断。商品 JSON 继续作为唯一权威事实源，Qdrant 继续作为由
商品内容派生的检索索引。

## 外部行为

### 会话继承

客户端继续调用 `POST /api/v1/chat/stream` 并复用 `conversation_id`。服务按该 ID 读取
当前查询快照；未提供 ID 时仍由服务端生成。不同会话之间不共享条件、候选或焦点。

示例：

```text
用户：推荐跑步鞋
用户：要轻量的
用户：预算 500 以内
```

第三轮生效查询同时包含跑步鞋、轻量和 500 元预算。本轮模型只输出增量操作，历史
条件由代码合并，模型不能直接覆盖完整快照。

### 指代范围与焦点

指代候选域只包含最近一轮展示的商品：

- 显式指代由模型对每个最近候选输出一次 `product_id + matches`；解析器要求 ID 按
  `rank` 顺序完整覆盖最近候选，不能缺失、重复、重排或生成域外 ID。
- 序数、相对位置、标题和品牌措辞都使用该候选匹配矩阵。代码只接受唯一商品命中；
  多个商品命中时只用实际命中的子集追问，例如两款小米会追问“第一款还是第二款”，
  不会把无关的第三款放入澄清。
- 品牌条件本身可以从多个同品牌候选归并为一个 Catalog 品牌；但 `product_question`
  必须最终得到唯一 `product_id`，不能因为模型把“三星”标成品牌目标而进入商品知识
  失败。
- 裸指代词优先指向已明确的焦点商品。
- 没有焦点但最近只展示一个商品时，裸指代指向该商品。
- 没有焦点且最近展示多个商品时，裸指代触发澄清。
- 商品追问即使没有显式 `reference`，也按裸指代规则使用焦点或唯一最近候选；多候选
  且无焦点时必须进入澄清，不能直接落入商品知识失败。
- `ProductReference.surface_text` 必须逐字来自当前用户消息中的连续原文片段。模型不能
  把最近候选标题、品牌或焦点商品转写成本轮显式引用；缺少原文依据的引用按非法结构化
  输出纠正一次，避免它绕过已有焦点。
- 模型输出的候选 ID 只是从 `recent_candidates` 复制的非可信逐项判断，不是最终绑定；
  可信 `product_id` 仍由代码在校验后的候选域中按基数规则生成。
- 当前候选摘要只包含 `rank`、`product_id`、标题和品牌，因此支持序数、相对位置、标题
  与品牌语义；不支持“最便宜的”“续航最好的”等需要候选价格或 feature 的比较级指代。

用户明确选择某个候选后，该商品成为焦点。连续追问中的“它”可以指向该焦点。新
搜索、品类切换和成功返回新商品的换一批会清空旧焦点；纯 `more_results` 没有可展示
商品时不构成新的结果批次，保留上一批候选与焦点供后续追问。

### 澄清与恢复

无法唯一解析对象、相对价格缺少基准、条件冲突或细化时缺少基础品类，系统保存
`PendingClarification` 并跳过 Embedding、全库检索、重排和证据模型。待澄清状态保存
被暂停的结构化 `TurnQuery`，用户回答后恢复原操作，而不是要求重新描述完整问题。

用户说“算了”可以取消澄清；直接发起新搜索会废弃旧澄清。初始指代未解析时创建
`attempt_count=1` 的 pending；下一轮澄清答案仍无法解析时计为第 2 次，系统立即清空
pending、取消暂停操作并要求用户重新完整描述，避免澄清死循环。这保持“连续两次无法
解析即退出”的规则，不会保存一个等待第 3 次回答的状态。

歧义 pending 只保存本次真实命中的 `candidate_product_ids`。澄清回答即使输出了完整
最近候选矩阵，也必须与该不可变子集求交，不能借“第三个”等回答跳出上一轮“第一款
还是第二款”的选择范围。旧版已持久化且没有候选匹配矩阵的引用继续走原线索解析兼容
路径。

`missing_context` 同时覆盖“尚无查询快照”和“已有快照但缺少最近展示价格基准”。
澄清答案恢复时，只有前者将暂停操作转为 `new_search`；后者保留暂停的搜索意图，在
已有快照上合并明确预算，避免丢失品类与未修改条件。

### 品类切换

用户明确提出与当前不同的商品子品类时，代码将本轮视为品类切换并清空旧预算、品牌、
SKU、场景、必需特征、排除条件、最近候选、焦点和已展示集合。只有用户在新请求中
重新表达的条件才进入新快照；“还是这个预算”等显式继承表达由本轮槽位操作重新写入。

### 自然语言品类解析

用户不必逐字说出 Catalog 的规范分类名称。`TurnQuery.category_reference` 保存当前
消息中的商品类型原文，以及 LLM 判断的所有规范 Catalog 候选。例如“耳机”唯一对应
`数码电子 / 真无线耳机` 时，代码将该范围写入查询快照；“鞋”同时对应跑步鞋、篮球鞋
和徒步鞋时，代码保存候选与原查询操作并固定追问，不能让模型静默选择一种。

模型负责自然语言与 taxonomy 的语义关系，输出候选必须使用 Catalog 精确值。解析器
验证原文片段、Catalog 成员、组合、去重和稳定顺序；resolver 只按候选基数确定控制流：

- 一个候选生成可信 `category/sub_category`；
- 多个候选保存 `ambiguous_category` pending，回答只能在不可变候选集合内选择；
- 明确商品类型但候选为空时固定提示目录不支持，并跳过全库检索；
- 没有明确商品类型时 `category_reference=null`，继续允许“推荐适合送人的礼物”等
  categoryless 全库语义搜索。

品类澄清保留同轮预算、品牌、功能、SKU 和语义操作。例如“500元以内的鞋”回答
“跑步鞋”后得到 `服饰运动 / 跑步鞋 + max_price=500`。部署前已经错误保存为
categoryless 的旧快照不自动迁移，因为仅凭 `semantic_terms` 无法可靠区分遗漏品类与
合法全库查询；用户需要重新发起明确搜索或使用新的 `conversation_id`。

### 条件细化与换一批

条件细化在完整商品库上重新检索，不能只过滤上一次展示的三个商品。条件变化后清空
旧的已展示集合。

`recent_candidates` 只保存最近一批商品，服务于指代；`seen_product_ids` 累积当前
查询条件下已经展示的商品，只服务于“还有吗”“换一批”的排重。连续换一批时排除
全部 `seen_product_ids`，但用户不能通过序数引用更早批次的商品。

只有不含任何 query mutation 的纯 `more_results` 保持快照并携带 seen 排除。若同轮
还包含语义词、槽位/价格、相对价格或解析出的品牌变化，则确定性转为
`refine_search`（品类变化仍优先成为 `switch_category`），应用操作、清空旧 seen，
并从全库重新检索，不能静默丢弃已经抽取的条件。

### 无结果与结果耗尽

无结果响应由后端根据工作流事实选择固定文案，不调用回答模型：

- 纯 `more_results` 没有新商品时返回“当前条件下没有更多符合要求的商品了。”
- 新搜索、条件细化或品类切换零召回时返回“当前筛选条件下没有找到匹配商品，建议您
  放宽或修改筛选条件。”
- 有召回但证据校验或最终 SKU 选择没有可展示商品时返回“找到了一些候选商品，但现有
  信息不足以确认它们符合要求，建议您调整筛选条件。”

内部 `no_result_reason` 只存在于单次 `ShoppingState`，不进入 SQLite 或 SSE 数据。
所有无结果分支先执行 `persist_no_results`，保存成功后才发送一个固定
`text_delta`。失败的纯 `more_results` 保留 `seen_product_ids`、`recent_candidates`
和 `focused_product_id`；因为本轮没有展示新商品，上一批仍是最近可指代的商品批次。
新搜索、条件细化和品类切换无结果时继续清空这些旧展示状态。

### 相对价格

- 有焦点商品时，“再便宜一点”与“贵一点”以焦点商品的展示价格为基准。
- 没有焦点但存在最近候选时，便宜使用最近候选最低展示价，贵使用最高展示价。
- 没有最近候选时追问明确预算。
- 明确金额始终替代相对价格规则。
- 商品价格以分为最小精度；编译器使用 `Decimal("0.01")` 做一步运算，最后才在
  Pydantic 边界转成两位小数 `float`。“更便宜”编译为基准价减 `0.01` 的包含上限，
  “更贵”编译为基准价加 `0.01` 的包含下限，不使用任意百分比。例如最近价格为
  `[399, 459, 529]` 且没有焦点时，“更贵”的最低价是 `529.01`。早期实现计划中的
  `530.01` 是算术笔误；目录和候选中没有 `530.00` 的已确认基准。

### 指定商品问答

“第二个多少钱”解析出商品后直接从 `ProductCatalog` 读取价格、品牌、SKU 等结构化
事实。“第二个防水吗”若无法由结构化字段回答，则按 `product_id` 精确读取 Qdrant 中
该商品的 summary、FAQ 和评论 Chunk。第一版单商品 Chunk 数量较少，读取全部 Chunk，
不调用全库向量召回；没有证据时明确说明信息不足。回答模型将确定性代码解析出的目标
绑定视为可信结论，不能因为提示词只包含单个目标商品、未重复提供候选排名而重新质疑
“第二款”等指代是否成立。

## 接口与数据

### `TurnQuery`

`TurnQuery` 是一次模型结构化调用的输出，只描述本轮意图和增量。模型输入包含本轮
原文、当前快照摘要、最近候选摘要、焦点和待澄清摘要，不包含完整历史消息：

```json
{
  "schema_version": 1,
  "intent": "refine_search",
  "reference": null,
  "category_reference": null,
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

明确商品类型使用独立结构：

```json
{
  "surface_text": "耳机",
  "candidates": [
    {
      "category": "数码电子",
      "sub_category": "真无线耳机"
    }
  ]
}
```

`surface_text` 必须来自当前消息连续原文。候选允许 `sub_category=null` 表示用户明确
指向整个顶级类目；普通上位词不能借此扩大为包含无关商品的顶级类目。
`category_reference` 与本轮直接 `category/sub_category` 槽位操作不能并存。

第一版意图为：

- `new_search`
- `refine_search`
- `switch_category`
- `more_results`
- `product_question`
- `clarification_answer`
- `non_shopping`

槽位操作为 `replace`、`add`、`remove` 和 `clear`。预算等标量只接受
`replace/clear`；品牌、语义词和 feature 列表接受 `add/remove/clear`。同一标量一轮
最多一个最终操作，`clear` 不能与同槽位其他操作并存。

最终模型术语为：

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

语义词 `add/remove` 的 `value` 会先去除首尾空白并且必须非空，`clear` 不带值。`category`、
`sub_category`、价格边界与 `price_preference` 只接受 `replace/clear`；品牌和 feature
列表只接受 `add/remove/clear`。SKU 操作使用 `sku_key` 指定一个规范属性，离散值只在
`add/remove` 时提供；`clear` 清空该 key。数值条件的 `add/remove` 携带完整
`NumericConstraint`，`clear` 不带值并清空整个数值条件列表。

### `ProductReference`

```json
{
  "target_type": "product",
  "surface_text": "第二个",
  "kind": "ordinal",
  "ordinal": 2,
  "brand": null,
  "product_name": null,
  "candidate_matches": [
    {"product_id": "p1", "matches": false},
    {"product_id": "p2", "matches": true},
    {"product_id": "p3", "matches": false}
  ]
}
```

`target_type` 区分商品与品牌对象；`kind` 覆盖序数、指示、品牌和商品名称线索。
`ReferenceCandidateMatch` 的 `product_id` 必须逐字复制当前 `recent_candidates`；
`candidate_matches` 必须按候选 rank 顺序完整覆盖每个 ID 恰好一次。模型输出不包含
可信 `resolved_product_id`，解析器负责矩阵结构校验，resolver 负责候选域限制和唯一性
判断。字段默认空列表主要用于兼容升级前已持久化的 pending 和非模型测试替身；当
当前候选域本身为空时，空列表也是完整的空域判断。新模型在非空候选域输出引用时必须
通过完整矩阵校验。

商品追问使用独立结构，模型将问题分为允许直接读取的结构化字段（名称、品牌、类目、
展示价格和 SKU）或开放语义问题，并保留用户问题文本。代码只对允许的结构化字段直接
读取 Catalog；其他问题统一进入指定商品知识读取，不能相信模型自行给出的答案。

```text
ProductQuestion
  text: str
  kind: "structured" | "semantic"
  field: "title" | "brand" | "category" | "display_price" | "sku" | null
```

`structured` 必须带 `field`；`semantic` 的 `field` 必须为 `null`。

### `QuerySnapshot`

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

快照保存可独立执行的完整条件。`semantic_terms` 承载用于召回但不要求作为硬条件
验证的正向场景和描述；`required_features` 继续承载需要证据判断的明确条件。生成
检索文本时对二者去重。`retrieval_query` 不作为不可拆分的历史字符串保存，而是由
代码根据子品类、`semantic_terms` 和必需特征生成，再转换为现有
`ParsedIntent`。后端价格编译由实际的 `compile_effective_query` 节点和同名服务函数
继续执行，明确区分多轮快照合并与生效约束编译。

### 会话状态

```text
ConversationState
  schema_version = 1
  conversation_id
  query_snapshot
  recent_candidates[]
    rank
    product_id
    display_price
  focused_product_id
  seen_product_ids[]
  pending_clarification

ConversationRecord
  state: ConversationState
  version: int
```

`display_price` 是已经发送给客户端的结果快照，只用于相对价格基准；商品正文、完整
SKU 和文本证据不写入 SQLite。SQL 乐观并发版本只属于 `ConversationRecord`，不进入
序列化的 `ConversationState.state_json`。

`PendingClarification` 实际保存 `kind`、不可变的 `candidate_product_ids`、
不可变的 `candidate_category_scopes`、被暂停的 `suspended_turn_query` 与
`attempt_count`。商品歧义使用商品 ID，品类歧义使用规范品类范围。初次未解析写入 1；
下一次仍未解析立即退出，不保存等待第三次回答的状态。

### SQLite

第一版使用 `ConversationRepository` 协议和 `SqliteConversationRepository` 实现，
不把整个 LangGraph 临时状态、Qdrant 结果或模型输出持久化。实际表结构为：

```text
conversation_state
  conversation_id TEXT PRIMARY KEY
  version INTEGER NOT NULL
  state_json TEXT NOT NULL
  updated_at TEXT NOT NULL
```

仓库接口提供按 ID 读取和带 `expected_version` 的保存。更新使用乐观并发控制：版本
不匹配时返回可重试的 `CONVERSATION_CONFLICT`，不能覆盖新状态。第一版不配置 TTL
或自动清理。

SQLite 是当前单实例 Demo 的默认实现。接口不绑定具体数据库，后续可以增加 MySQL
实现；第一版不引入 Redis。

### Qdrant 商品知识读取

Qdrant 已为 `product_id` 建立 keyword payload 索引。新增按 ID 精确读取接口，使用
payload Filter 和 scroll 读取全部 Chunk，不请求向量。返回独立的 `EvidenceChunk`，
不为非语义检索结果伪造相似度分数。

普通检索接口增加请求级 `excluded_product_ids`，使用 `product_id` 的 `must_not`
过滤实现换一批排重。该列表不写入 `SearchConstraints`，避免把展示历史误当成用户
商品约束。

## 工作流

当前生产图以 `parse_turn_query` 多轮入口取代旧单轮解析入口，检索、重排和证据链路在生成完整
查询后继续复用：

```text
START
  -> load_conversation
  -> parse_turn_query
  -> pending? -> resume_pending_action -> resolve_reference / END
  -> resolve_reference
       -> persist_clarification（保存后直接发澄清文本）-> END
       -> resolve_category_reference
            -> persist_clarification（歧义或目录不支持）-> END
            -> route_turn
            -> search
                 -> merge_query_snapshot -> compile_effective_query
                 -> retrieve_chunks -> aggregate_products -> semantic_rerank
                 -> validate_evidence -> decide_candidates
                 -> persist_search_result -> emit_product_events -> generate_response
                 -> persist_no_results -> emit_no_results_response -> END
            -> product_question
                 -> load_product_facts
                 -> fetch_product_knowledge（仅 semantic）
                 -> persist_focus -> generate_product_response -> END
            -> non_shopping -> generate_response -> END
```

搜索路由进入 `merge_query_snapshot`；商品问答和非购物输入不经过无意义的快照合并。
品类是否切换由合并器比较新旧 `category + sub_category` 后确定，不能只相信模型路由。
商品选择与商品 SSE 事件拆成不同节点；会话保存发生在商品卡片发送和推荐文案生成之前，
使文案失败时最近候选仍可供下一轮引用。

## 条件合并规则

- 新搜索和品类切换从空快照开始。
- 条件细化在原快照上确定性执行槽位操作。
- 加入指定品牌时移除同品牌的排除条件；加入排除品牌时移除同品牌的指定条件。
- 加入必需 feature 时移除相同的排除 feature，反之亦然。
- 明确金额覆盖相对价格操作。
- 合并后 `min_price > max_price` 时进入业务澄清，不将其作为模型或服务错误。
- 条件修改后清空 `seen_product_ids`；连续换一批时累积它。
- 非购物输入不修改现有购物快照。

## 错误与一致性

指代歧义、缺少上下文和条件冲突是正常澄清，不发送 `error` 事件。新增服务错误码：

- `CONVERSATION_UNAVAILABLE`
- `CONVERSATION_CONFLICT`
- `TURN_QUERY_PARSE_FAILED`
- `PRODUCT_KNOWLEDGE_UNAVAILABLE`

SQLite 读取失败不能降级为无历史单轮请求。Qdrant 不可用时，Catalog 中明确存在的
结构化事实仍可回答；依赖文本证据的问题必须明确失败，不能使用模型常识。TurnQuery
非法时沿用一次结构化纠错，第二次失败进入统一 SSE 错误链路。

TurnQuery 的 taxonomy 后校验同时覆盖槽位品牌和
`ProductReference(kind="brand").brand`，均要求与 Catalog 品牌精确一致。引用品牌首轮
越界时沿用一次 structured correction；第二次仍越界时安全归一化为可重试的
`TURN_QUERY_PARSE_FAILED`，不向客户端泄露模型原始内容。

候选匹配矩阵同样进入一次 structured correction：缺项、重复、重排或域外 ID 都视为
非法结构化输出；纠正后仍不完整则返回 `TURN_QUERY_PARSE_FAILED`。完整矩阵中的
`matches` 是语言理解结果，后端不重新做字符串等值匹配，但会确定性执行目标类型覆盖、
命中基数判断、Catalog 品牌归并和 pending 候选子集求交。

品类候选进入相同的结构化纠错边界：原文不属于当前消息、候选不在 Catalog、组合非法、
重复、顺序错误或与直接品类槽位冲突时纠正一次。品类候选列表的语言完整性由真实模型
验收约束；“耳机”“手机”“鞋”“T恤”分别重复五次，任何一次遗漏都必须将实现升级为
完整 taxonomy 匹配矩阵，不能以不完整候选列表上线。

搜索结果由 `persist_search_result` 保存后才发送 `product` 和推荐文本；零结果由
`persist_no_results` 保存后才发送固定文本，且不调用回答模型；商品追问由
`persist_focus` 保存焦点和清除 pending 后才生成文本。保存失败时不发送商品或文本。HTTP 层保持
`message_start -> product* -> text_delta* -> message_end` 兼容顺序；商品发送前失败为
`failed`，已发送商品后生成失败为 `partial`，并通过安全 `error` 事件归一化公开错误。

第一版假设商品 JSON 在会话生命周期内不变，`product_id` 稳定，服务重启后加载相同
数据；不处理商品删除、重命名或数据集切换。

## 关键决策

### 模型输出增量，代码维护历史

一次结构化模型调用生成 `TurnQuery`。模型处理开放语言，代码负责对象唯一性、槽位
操作、品类切换、相对价格和路由，避免模型静默丢失或篡改历史条件。

### 模型理解指代语义，代码决定最终绑定

模型逐项判断用户原文是否指向每个最近候选，因而可以理解“中间那个”“三星这个”
以及标题或品牌的自然语言变体。模型不能直接返回最终 `resolved_product_id`；解析器
先验证候选矩阵与服务端上下文完全同域、有序且无重复，再由 resolver 按唯一商品、
唯一品牌或歧义规则产生可信结果。这样保留模型的语言理解能力，同时不把候选范围和
对象唯一性下放给模型。

### 最近结果限制指代范围

裸指代和序数不回溯整个会话。`seen_product_ids` 仅用于换一批排重，不能扩大引用域。
这以较小的记忆范围换取明确、可解释的消解规则。

### 确定性无结果使用固定响应

检索、证据校验和最终 SKU 选择已经能确定为什么没有可展示商品。工作流在失败来源记录
`no_result_reason`，持久化后直接发送对应固定文案，不再为确定性状态增加模型调用和
文案漂移。纯 `more_results` 只表示保持原条件继续取数，因此可以安全归类为结果耗尽；
携带条件修改的同轮输入会先归一化为 `refine_search`。

### 商品与会话存储分离

商品 JSON 是权威事实源，内存 Catalog 是运行时只读视图，Qdrant 是商品内容的派生
检索索引，SQLite 只保存会话状态和商品 ID 引用。第一版不复制完整商品到会话库。

### SQLite 通过仓库接口隔离

当前请求量和单实例部署不需要 MySQL 与 Redis。乐观版本控制处理同一会话并发写；
未来多实例部署可以替换仓库实现，而不改变 Query 编译和工作流节点。

## 代码与验证

实际代码入口：

- `src/shop_agent/models/turn_query.py`：`TurnQuery`、`TurnCandidateSummary`、引用、商品问题
  和增量操作模型；`src/shop_agent/services/ports.py` 定义 `TurnContext` 与解析器协议。
- `src/shop_agent/models/conversation.py`：`QuerySnapshot`、`CandidateReference`、
  `PendingClarification`、`ConversationState`、`ConversationRecord`。
- `src/shop_agent/services/reference_resolver.py`：最近候选与焦点上的确定性消解。
- `src/shop_agent/services/multi_turn_query_compiler.py`：槽位合并、品类切换和相对价格。
- `src/shop_agent/services/conversation_repository.py`：SQLite 仓库和乐观并发控制。
- `src/shop_agent/services/dashscope_chat.py`：单次结构化 `DashScopeTurnQueryParser`。
- `src/shop_agent/services/retrieval.py`、`src/shop_agent/services/qdrant_store.py`：普通
  检索排除和按 `product_id` 精确 scroll 的定向知识读取。
- `src/shop_agent/workflow/nodes.py`、`src/shop_agent/workflow/graph.py`：生产节点、路由、
  保存先于事件、Catalog 事实与 Qdrant 文本知识分支。
- `src/shop_agent/api/dependencies.py`、`src/shop_agent/api/chat.py`：生产依赖装配与兼容
  SSE 边界。
- `tests/unit/test_multi_turn_workflow.py`、`tests/integration/test_chat_api.py`、
  `tests/live/test_live_shopping_flow.py`：确定性对话、真实 SQLite HTTP 和 opt-in 解析器验收。

### 覆盖矩阵

| 要求 | 具体证据 |
|---|---|
| 最近一批中的序数、指示、品牌和商品名；旧批次不可引用 | `tests/unit/test_reference_resolver.py::test_resolver_uses_the_expected_product_reference_branch`、`test_resolver_matches_a_unique_brand_to_one_product`、`test_resolver_matches_an_exact_casefolded_product_name_only`、`test_resolver_never_resolves_a_product_only_seen_in_an_older_batch` |
| LLM 候选匹配矩阵完整性、顺序与纠错 | `tests/unit/test_model_gateways.py::test_turn_query_parser_corrects_incomplete_candidate_matches`、`test_turn_query_parser_rejects_twice_reordered_candidate_matches` |
| 矩阵基数、目标类型覆盖、pending 候选子集与旧状态兼容 | `tests/unit/test_reference_resolver.py::test_matrix_resolves_one_product_without_using_clue_kind`、`test_matrix_product_ambiguity_lists_only_matched_candidates`、`test_expected_product_target_overrides_model_brand_target`、`test_pending_allowed_ids_prevent_escape_to_unmatched_product`、`test_invalid_matrix_coverage_fails_closed_to_clarification`；`tests/unit/test_conversation_models.py::test_legacy_pending_reference_without_candidate_matches_still_loads` |
| 品牌式商品追问、歧义候选缩小与澄清越界保护 | `tests/unit/test_multi_turn_workflow.py::test_product_question_brand_wording_resolves_unique_matched_product`、`test_candidate_matrix_ambiguity_persists_only_matched_products`、`test_pending_candidate_subset_blocks_clarification_answer_escape`；`tests/integration/test_chat_api.py::test_compiled_http_brand_wording_resolves_matched_product_id` |
| 焦点和后续代词 | `tests/unit/test_multi_turn_workflow.py::test_acceptance_ordinal_question_sets_focus_and_pronoun_reuses_it`；`tests/integration/test_chat_api.py::test_compiled_http_dialogue_persists_focus_for_follow_up_pronoun` |
| 歧义保存/恢复、取消、新搜索覆盖、两次退出 | `tests/unit/test_multi_turn_workflow.py::test_acceptance_ambiguous_question_persists_and_answer_resumes_p2`、`test_cancel_clears_pending_persists_and_emits_exact_text`、`test_clear_new_search_discards_pending_without_reviving_suspended_action`、`test_second_unresolved_attempt_clears_pending_and_requests_complete_restatement`、`test_ambiguous_pending_answer_without_reference_exits_attempt_limit` |
| 品类切换重置 | `tests/unit/test_multi_turn_workflow.py::test_acceptance_category_switch_resets_old_query_and_display_state`；`tests/unit/test_multi_turn_query_compiler.py::test_category_switch_resets_old_state_and_keeps_only_restated_slots` |
| 标量、列表、SKU、数值和语义操作 | `tests/unit/test_multi_turn_query_compiler.py::test_refinement_replaces_budget_and_preserves_unrelated_slot`、`test_brand_and_feature_slots_support_remove_and_clear`、`test_sku_operations_are_stable_and_copy_on_write`、`test_numeric_operations_are_stable_and_copy_on_write`、`test_semantic_terms_add_and_remove_in_stable_order`；`tests/unit/test_turn_query_models.py::test_semantic_term_add_and_remove_reject_blank_values` |
| 相对价格基准与明确金额覆盖 | `tests/unit/test_multi_turn_workflow.py::test_acceptance_relative_cheaper_uses_latest_minimum_minus_one_cent`、`test_missing_price_baseline_answer_preserves_existing_snapshot_and_retrieves`；`tests/unit/test_multi_turn_query_compiler.py::test_focus_price_is_the_cheaper_baseline`、`test_latest_batch_extreme_is_relative_price_baseline`、`test_explicit_applicable_boundary_overrides_relative_price` |
| seen 累积、mutation 全库重检索且 ordinal 只看最新批 | `tests/unit/test_multi_turn_workflow.py::test_acceptance_more_batches_accumulate_seen_and_final_ordinal_targets_h`、`test_more_results_with_query_mutation_refines_from_full_catalog`；`tests/unit/test_multi_turn_query_compiler.py::test_more_results_with_price_operation_becomes_refinement`、`test_more_results_with_semantic_operation_becomes_refinement`、`test_more_results_with_relative_price_becomes_refinement` |
| 无结果原因分类与固定文案 | `tests/unit/test_workflow_routes.py::test_no_hits_skips_rerank_validation_and_decision`、`test_evidence_empty_skips_candidate_decision`；`tests/unit/test_multi_turn_workflow.py::test_failed_more_results_preserves_latest_reference_context`、`test_empty_final_selection_uses_insufficient_evidence_response`、`test_more_results_with_only_ineligible_remaining_products_is_exhausted` |
| 自然语言品类唯一绑定、歧义恢复、越界保护与目录不支持短路 | `tests/unit/test_model_gateways.py::test_turn_query_parser_accepts_grounded_exact_category_candidates`、`test_turn_query_parser_rejects_invalid_category_references_after_retry`；`tests/unit/test_reference_resolver.py::test_category_resolver_resolves_one_exact_catalog_scope`、`test_category_resolver_clarifies_all_multiple_catalog_scopes`；`tests/unit/test_multi_turn_workflow.py::test_unique_category_reference_retrieves_only_the_resolved_scope`、`test_category_clarification_resumes_suspended_budget`、`test_category_clarification_answer_cannot_escape_pending_scopes`、`test_explicit_unsupported_category_skips_retrieval` |
| 结果耗尽的 HTTP/SSE 与持久化边界 | `tests/integration/test_chat_api.py::test_compiled_http_more_results_exhaustion_preserves_follow_up_reference`；`tests/unit/test_multi_turn_workflow.py::test_failed_more_results_preserves_latest_reference_context`、`test_no_result_paths_persist_before_text_and_clear_latest_focus` |
| 无显式商品 reference 的焦点/单候选回退与多候选澄清 | `tests/unit/test_multi_turn_workflow.py::test_reference_less_product_question_uses_focused_product`、`test_reference_less_structured_question_uses_focused_product_skus`、`test_reference_less_product_question_uses_only_recent_candidate`、`test_reference_less_product_question_with_multiple_candidates_clarifies` |
| 引用必须来自当前消息原文，不能由候选或焦点补写 | `tests/unit/test_model_gateways.py::test_turn_query_parser_corrects_ungrounded_reference_to_none`、`test_turn_query_parser_rejects_twice_ungrounded_reference` |
| TurnQuery 引用品牌 taxonomy 纠正与安全失败 | `tests/unit/test_model_gateways.py::test_turn_query_parser_corrects_invalid_reference_brand`、`test_turn_query_parser_normalizes_twice_invalid_reference_brand` |
| 商品问答生成器信任已解析目标，不重新质疑序数绑定 | `tests/unit/test_multi_turn_workflow.py::test_semantic_question_fetches_only_target_chunks_and_persists_focus` |
| Catalog 结构化事实与 Qdrant 精确商品 scroll | `tests/unit/test_multi_turn_workflow.py::test_structured_fields_are_catalog_and_current_snapshot_only`；`tests/unit/test_qdrant_filters.py::test_fetch_product_chunks_scrolls_all_pages_in_order_without_scores`；`tests/unit/test_retrieval_service.py::test_fetch_product_chunks_delegates_without_embedding_or_reranking` |
| SQLite 重建、隔离、版本冲突和错误归一化 | `tests/unit/test_conversation_repository.py::test_save_new_state_creates_parent_and_survives_repository_recreation`、`test_conversations_remain_isolated`、`test_stale_version_returns_retryable_conversation_conflict`、`test_invalid_persisted_state_is_normalized_without_content_leakage` |
| 固定商品数据、无失效迁移、v1 无 Redis/MySQL | `tests/unit/test_conversation_repository.py::test_state_json_is_compact_domain_state_without_product_body` 固化只存 ID/领域状态的边界；固定数据和无 Redis/MySQL 是本文“范围”和“关键决策”的显式 v1 限制 |
| HTTP/SSE 兼容及生成/保存失败顺序 | `tests/integration/test_chat_api.py::test_chat_stream_emits_start_products_text_and_end`、`test_generation_failure_after_products_is_partial`、`test_compiled_http_generation_failure_persists_candidates_for_follow_up_ordinal`、`test_compiled_graph_pre_product_errors_are_safe_over_http`；`tests/unit/test_multi_turn_workflow.py::test_persist_completes_before_first_product_and_exact_display_price_is_saved` |

### Fresh 验证

2026-07-28 自然语言品类解析接入完成后执行：

```bash
env -u ALL_PROXY -u all_proxy .venv/bin/pytest -q -p no:cacheprovider
# 458 passed, 21 skipped in 8.13s

env -u ALL_PROXY -u all_proxy .venv/bin/ruff check .
# All checks passed!

env -u ALL_PROXY -u all_proxy .venv/bin/mypy src scripts
# Success: no issues found in 39 source files
```

21 个跳过项均来自显式 opt-in 的 live 测试：20 个用例由“耳机”“手机”“鞋”“T恤”
四组输入各重复五次，另一个是完整真实服务流程。随后使用
`RUN_LIVE_TESTS=1` 单独运行 20 个品类稳定性用例，但第一个 DashScope 调用等待约
120 秒仍未返回，测试由开发者中断，因此不将真实模型候选完整性记为通过。清除
`ALL_PROXY/all_proxy` 是为了避免当前开发机的 `socks://` 代理配置影响测试 HTTP
客户端，不改变被测代码。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-07-28 | 纯换一批耗尽时保留上一批候选与焦点 | 无商品返回的轮次不应覆盖最近展示批次，使用户仍可继续询问上一批商品 |
| 2026-07-28 | 增加自然语言品类候选、确定性唯一绑定、可续接歧义追问和目录不支持短路 | 避免“耳机”等用户说法只进入语义词而失去 Qdrant 品类硬过滤，导致换一批时混入手机或食品 |
| 2026-07-28 | 区分结果耗尽、零匹配与候选信息不足，并改为后端固定文案 | 避免“没有更多商品”被误报为筛选失败，同时移除确定性无结果场景的回答模型调用 |
| 2026-07-28 | 为显式商品指代增加完整候选匹配矩阵、确定性基数判定、商品问题目标覆盖和 pending 候选子集保护 | 让 LLM 理解“三星这个”“中间那个”等自然语言，同时由后端安全地产生唯一 `product_id` 或限定范围追问 |
| 2026-07-26 | 修复澄清恢复、无显式商品指代、含条件换一批和引用品牌校验边界 | 保留已有查询快照与条件，避免 mutation 静默丢失，并将越界品牌纳入一次纠正与安全失败链路 |
| 2026-07-26 | 接入生产多轮图、SQLite、定向商品问答并补充六组端到端验收与覆盖矩阵 | 同步 Tasks 1 至 11 的实际类名、节点、路径、状态与 fresh 验证证据 |
| 2026-07-26 | 同步最终增量操作、商品问题、会话版本术语与精确相对价格规则 | 使批准文档与 Task 1 模型一致，并将 `[399, 459, 529]` 的“更贵”结果按 `max + Decimal("0.01")` 更正为 `529.01` |
| 2026-07-26 | 创建多轮 Query 编译、最近候选指代、澄清恢复和 SQLite 会话设计 | 将多轮碎片表达编译为确定性查询，并与现有单轮检索和商品事实边界衔接 |
