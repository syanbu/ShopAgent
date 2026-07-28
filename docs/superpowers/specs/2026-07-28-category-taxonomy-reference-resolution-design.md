# 品类 Taxonomy 指代解析设计

> 日期：2026-07-28
>
> 状态：已实现；真实模型稳定性验收仍受外部服务响应阻塞
>
> 所属功能：多轮 Query 编译与指代消解

## 背景

用户输入“推荐一款耳机”时，当前 `DashScopeTurnQueryParser` 将“耳机”保存为
`semantic_terms`，没有生成 `category/sub_category` 槽位。Catalog 中对应的规范值是
`数码电子 / 真无线耳机`，因此查询快照的两个品类字段均为 `null`。

后续“还有吗”保持该快照，只排除已经展示的 `product_id`。Qdrant 因品类为空而不添加
品类 payload 过滤；耳机商品逐批耗尽后，手机、食品等全库语义近邻可以进入结果。

本设计建立统一的自然语言品类解析机制，不为“耳机”等单个词编写特例。

## 目标

- 用户可以使用简称、上位词或自然语言商品类型，不必说出 Catalog 的规范分类名称。
- LLM 负责判断用户表达可能对应哪些 Catalog 品类。
- 后端只接受 Catalog 中真实存在的规范品类，并确定性执行唯一绑定或歧义追问。
- 唯一候选自动进入查询快照；多个候选必须追问，不允许模型静默选择一个。
- 明确商品类型但没有目录候选时不得退化为全库语义检索。
- 品类澄清必须保留同轮预算、品牌、功能、SKU 等其他查询操作。
- 不破坏没有明确品类的合法全库语义搜索，例如“推荐适合送人的礼物”。

## 非目标

- 不建立长期用户画像或完整历史消息拼接。
- 不使用向量检索决定规范品类。
- 不增加外部服务、数据库表或缓存。
- 不自动改写已经持久化的错误品类空快照。
- 不删除现有 `category/sub_category` 槽位操作；它们继续服务编译器、旧状态和测试替身。
- 不建立人工维护的“耳机 → 真无线耳机”单点修正规则。

## 方案选择

### 采用：LLM 候选理解 + 后端确定性绑定

模型输出用户品类原文和所有可能的规范候选。后端验证原文、候选域和候选基数：

- 一个候选：生成可信规范品类；
- 多个候选：持久化澄清并列出候选；
- 零个候选：返回目录不支持提示，不执行商品检索。

该方案延续现有商品指代的职责边界：语言关系由模型判断，可信目标和控制流由代码产生。

### 未采用：只修改提示词

只要求模型直接输出规范 `category/sub_category` 改动最小，但不能表达“鞋”同时对应
跑步鞋、篮球鞋和徒步鞋，也无法阻止模型偶尔再次把品类放进语义词。

### 未采用：人工别名表

别名表可以稳定修复已知词，但需要持续维护自然语言变体，且容易形成散落在代码中的
品类特例。它不作为第一版统一机制。

## 数据契约

### `CategoryReference`

`TurnQuery` 新增可空字段 `category_reference`：

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

建议模型如下：

```text
CategoryReference
  surface_text: str
  candidates: list[CategoryCandidate]

CategoryCandidate
  category: str
  sub_category: str | null
```

`sub_category=null` 只表示用户明确指向整个 Catalog 顶级类目；普通上位词不能借此扩大
范围。例如“鞋”不能映射为整个“服饰运动”，因为该顶级类目还包含服装和背包。

规则：

- `surface_text` 必须是当前用户消息中的连续原文片段。
- 每个候选的 `category` 必须是 Catalog 精确值。
- 非空 `sub_category` 必须与 `category` 构成 Catalog 中存在的精确组合。
- 候选不得重复，并按注入 taxonomy 的稳定顺序输出。
- 模型必须列出所有语义上可能的规范范围，不能为了避免追问只返回一个。
- 当前消息没有明确商品类型时，`category_reference` 必须为 `null`。
- 新模型输出 `category_reference` 时，不得同时输出 `category` 或 `sub_category`
  槽位操作；冲突进入现有一次结构化纠错。
- 品类词和描述词分开表达。例如“适合运动的耳机”以“耳机”为
  `category_reference.surface_text`，以“适合运动”为语义或功能条件。

现有 `TurnQuery.reference` 继续只描述最近商品候选或品牌引用；品类引用使用独立字段，
避免把商品对象和查询范围混为一体。

### `PendingClarification`

`PendingClarification.kind` 增加 `ambiguous_category`，并增加带默认空值的规范品类候选
集合，例如：

```text
candidate_category_scopes:
  - category: "服饰运动"
    sub_category: "跑步鞋"
  - category: "服饰运动"
    sub_category: "篮球鞋"
  - category: "服饰运动"
    sub_category: "徒步鞋"
```

`suspended_turn_query` 继续保存完整原操作。旧 SQLite JSON 没有新字段时使用默认空值，
因此不需要修改数据库表，也不提升 `ConversationState.schema_version`。

## 解析与验证

`DashScopeTurnQueryParser` 的提示词改为：

- 用户说法可以是别名、简称或上位词；
- 候选值必须使用注入 taxonomy 的精确名称；
- 所有可能候选都必须返回；
- 唯一性由后端决定，模型不得隐藏歧义；
- 已识别为品类的原文不能只作为普通 `semantic_term` 输出。

解析器在 Pydantic 校验后继续执行上下文校验：

1. 验证 `surface_text` 来自本轮原文。
2. 验证候选无重复且顺序稳定。
3. 验证每个候选属于当前 Catalog。
4. 验证 `category_reference` 与直接品类槽位操作不冲突。
5. 非法输出沿用一次 structured correction；第二次失败返回
   `TURN_QUERY_PARSE_FAILED`。

候选中的语言匹配仍属于模型判断。后端不使用字符串包含规则重新解释“耳机”“鞋”等
自然语言，只验证候选域并按候选数量执行控制流。

## 工作流

在本轮解析后、查询快照合并前增加品类引用解析：

```text
load_conversation
  -> parse_turn_query
  -> resume_pending_action（如存在）
  -> resolve_reference（商品或品牌）
  -> resolve_category_reference
       -> not_required
       -> resolved
       -> ambiguous -> persist_clarification -> END
       -> unsupported -> emit_fixed_response -> END
  -> route_turn
  -> merge_query_snapshot
  -> retrieval
```

### 唯一候选

resolver 产生可信的 `resolved_category` 和 `resolved_sub_category`。合并器把它们视为本轮
规范品类替换，并在校验 SKU 操作前应用。与旧快照品类不同则沿用现有
`switch_category` 行为，清理旧查询条件、候选、焦点和 `seen_product_ids`。

示例：

```text
用户：推荐耳机
模型候选：数码电子 / 真无线耳机
最终快照：数码电子 / 真无线耳机
```

### 多个候选

工作流保存：

- `kind=ambiguous_category`
- 不可变的 `candidate_category_scopes`
- 完整 `suspended_turn_query`

并根据候选生成固定追问：

```text
你说的是跑步鞋、篮球鞋还是徒步鞋？
```

不调用回答模型，不执行 Embedding、Qdrant、重排或证据校验。

用户回答后，解析出的候选必须与 pending 的不可变候选集合求交。唯一命中时恢复被暂停
的操作；多个或零个命中时计为澄清失败。连续第二次仍无法确定，沿用现有规则清空
pending，并要求用户重新完整描述。

示例：

```text
用户：推荐500元以内的鞋
系统：你说的是跑步鞋、篮球鞋还是徒步鞋？
用户：跑步鞋
最终快照：服饰运动 / 跑步鞋，max_price=500
```

### 零候选

当 `category_reference` 非空但候选为空时，说明模型识别出明确商品类型，但当前目录没有
对应规范范围。系统返回固定提示并停止检索：

```text
当前商品目录暂不支持“{surface_text}”，请换一种商品类型。
```

该轮不覆盖现有查询快照，也不把未知品类降级为全库语义词。

### 没有明确品类

`category_reference=null` 时不启动品类 resolver。诸如“推荐适合送人的礼物”可以继续
使用现有 categoryless 全库语义检索。这一行为与“明确品类但目录不支持”严格区分。

## 与语义词的关系

规范品类负责硬过滤，语义词负责相似度召回，两者不能互相替代：

```text
“适合运动的耳机”
  category/sub_category = 数码电子 / 真无线耳机
  semantic_terms = ["适合运动"]
```

`QuerySnapshot.to_parsed_intent()` 继续优先使用规范子品类生成检索文本，Qdrant 使用
相同规范值生成 payload Filter。因此连续“还有吗”只会在真无线耳机范围内排除
`seen_product_ids`；十款耳机耗尽后进入既有 `exhausted` 固定响应，不会用手机或食品
补足三个结果。

## 兼容与迁移

- SQLite 表结构不变。
- 新增 pending 字段有默认值，旧状态继续加载。
- 现有 `category/sub_category` 槽位操作保留。
- 不自动迁移已有的 categoryless 快照。仅凭持久化的 `semantic_terms` 无法可靠区分
  “耳机”这种遗漏品类与“送礼物”这种合法全库请求。
- 部署前已经处于错误快照的会话，需要用户重新发起明确搜索或使用新的
  `conversation_id`。
- 回滚只需恢复解析器、模型和工作流代码；没有数据库 DDL 或不可逆数据写入。

## 错误处理

- 原文不匹配、伪造 taxonomy、重复候选、顺序错误和字段冲突：
  structured correction 一次，随后安全失败。
- 多候选：正常业务澄清，不发送 SSE `error`。
- 零候选：固定目录不支持响应，不发送 SSE `error`。
- pending 回答越界：不能选择原候选集合之外的品类。
- 持久化失败：不发送澄清或固定文本，沿用保存先于事件的一致性边界。

## 验证

### 模型与解析器

- “推荐耳机”可表达单个规范候选。
- “推荐鞋”可表达跑步鞋、篮球鞋和徒步鞋三个候选。
- `surface_text` 不在当前消息中时纠正。
- 非 Catalog 候选、重复候选、顺序错误时纠正。
- `category_reference` 与直接品类槽位并存时纠正。
- 第二次仍非法时归一化为 `TURN_QUERY_PARSE_FAILED`。

### resolver 与编译器

- 单候选生成可信规范品类。
- 多候选生成 `ambiguous_category` pending。
- 空候选不生成查询快照或 ParsedIntent。
- pending 只能在不可变候选集合内恢复。
- 唯一候选与旧快照不同会触发品类切换。
- 澄清恢复保留预算、品牌、功能和 SKU 操作。

### 工作流与 API

- “推荐耳机”持久化为 `数码电子 / 真无线耳机`。
- 连续换一批只返回十款真无线耳机；耗尽后返回
  “当前条件下没有更多符合要求的商品了。”
- 耳机耗尽后不会出现智能手机、食品或其他品类。
- “500元以内的鞋”追问，回答“跑步鞋”后保留预算。
- 澄清回答越界和连续两次失败符合现有 pending 规则。
- 明确但不支持的商品类型不调用检索。
- “推荐适合送人的礼物”仍允许 categoryless 全库搜索。
- 旧 SQLite 状态和旧 pending 可以加载。
- opt-in live 测试验证真实模型对“耳机”和“鞋”的候选输出。

## 预计修改范围

预计涉及约 12—15 个代码、测试和文档文件。这是一次核心模型契约与工作流分支修改，
不能压缩成单纯提示词修补：

- `src/shop_agent/models/turn_query.py`
- `src/shop_agent/models/conversation.py`
- `src/shop_agent/models/state.py`
- `src/shop_agent/services/dashscope_chat.py`
- `src/shop_agent/services/reference_resolver.py`
- `src/shop_agent/services/multi_turn_query_compiler.py`
- `src/shop_agent/workflow/nodes.py`
- `src/shop_agent/workflow/graph.py`
- `tests/unit/test_model_gateways.py`
- `tests/unit/test_reference_resolver.py`
- `tests/unit/test_multi_turn_query_compiler.py`
- `tests/unit/test_multi_turn_workflow.py`
- `tests/integration/test_chat_api.py`
- `tests/live/test_live_shopping_flow.py`
- `docs/features/multi-turn-query-engine.md`

不新增 API 字段、外部依赖、数据库表或 Qdrant payload 字段。

## 关键风险

最脆弱的前提是：LLM 能完整列出所有语义上合理的 Catalog 候选。如果模型把“鞋”只
返回为“跑步鞋”，后端虽然能验证该候选真实存在，却无法仅从候选列表证明其他合理候选
没有被遗漏。

第一版通过明确提示词、代表性歧义示例、稳定 taxonomy 顺序、一次 structured correction
和真实模型验收降低该风险。实现验收分别重复执行“耳机”“手机”“鞋”“T恤”五次：
唯一品类必须每次只返回正确候选，歧义品类必须每次返回全部合理候选。任意一次遗漏即
停止候选列表方案，改为让模型对完整 taxonomy 域逐项输出 `matches` 的匹配矩阵；不能
以不完整候选列表上线。

## 文档归属

该能力属于多轮 Query 对用户品类表达的增量解析与确定性绑定，应在实现时更新
`docs/features/multi-turn-query-engine.md` 的功能目标、外部行为、`TurnQuery`、
澄清流程、错误边界、覆盖矩阵和变更记录。`docs/README.md` 已有对应功能索引，不新建
重复功能条目。
