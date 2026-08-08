# 场景化组合推荐

> 状态：已完成（后端自动化与真实服务验收通过；前端联调边界见“验证”）
>
> 代码入口：`config/scenario_recipes.json`、`src/shop_agent/models/scenario.py`、
> `src/shop_agent/services/scenario_recipes.py`、`src/shop_agent/services/scenario_compiler.py`、
> `src/shop_agent/services/scenario_recommendation.py`、`src/shop_agent/models/turn_query.py`、
> `src/shop_agent/models/conversation.py`、`src/shop_agent/workflow/`、
> `src/shop_agent/api/dependencies.py`
>
> 实施计划：[2026-08-04 场景化组合推荐实施计划](../superpowers/plans/2026-08-04-scenario-combination-recommendation.md)

## 功能目标

用户可以用一个生活场景表达跨品类需求，例如：

```text
下周去三亚度假，帮我搭配一套从防晒到穿搭的方案
```

系统把该场景绑定到经过审核的结构化方案模板，再把模板槽位确定性映射到当前
`ProductCatalog` 中真实存在的 `category + sub_category`，分别完成检索、重排、事实校验
和候选选择，最终返回一套由多个商品组成的完整方案。

本功能解决的核心问题不是让 LLM 临时规划“旅行通常需要什么”，而是建立稳定的映射链：

```text
用户场景语义
  -> 受限的 recipe_id
  -> 审核过的方案槽位
  -> 精确 Catalog 类目范围
  -> 每槽商品候选
  -> 一套组合
```

LLM 负责理解用户在说哪种场景以及是否存在模板无法覆盖的显式需求；后端负责模板绑定、
Catalog 校验、商品检索、组合完整性、排重、持久化和事件发送。LLM 不生成可信类目、
商品 ID、价格、SKU、槽位归属或组合数量。

## 设计来源

### 历史设计约束

本方案延续项目已经形成的以下原则：

- 会话理解仍由 LangGraph 使用的唯一 `DashScopeTurnQueryParser` 产生结构化意图，再由
  条件路由选择业务分支。解析器内部对明显的组合形态先执行一次短提示 recipe gate，
  未选中时回落完整多轮解析；它不是图外的第二套路由器，也不直接访问商品类目。
- LLM 输出本轮原始意图，确定性后端把它编译为可执行状态；商品事实继续来自 Catalog，
  Qdrant 只是可重建的候选发现索引。
- 新场景分支复用现有会话、检索、重排、同 SKU 条件校验、语义三态证据、商品事件和
  SQLite 乐观并发控制，但不把多槽位方案强行压入单品类 `QuerySnapshot`。
- 公开 API 保持稳定；内部状态可以增加场景快照，但必须有版本迁移、失败保护和回滚边界。

### qiuqiu-agent 参考

参考仓库：
[pingfangww/qiuqiu-rag-shopping-agent@a0ac29e](https://github.com/pingfangww/qiuqiu-rag-shopping-agent/tree/a0ac29eeb596ae06d4c71e29b4600ce6973e21de)。

其中
[`server/rag/retriever.py`](https://github.com/pingfangww/qiuqiu-rag-shopping-agent/blob/a0ac29eeb596ae06d4c71e29b4600ce6973e21de/server/rag/retriever.py)
采用五个基础场景模板和别名，把场景拆成槽位，每槽按候选 `sub_category` 独立检索并取
Top 1，再按“鞋服装备 / 防护 / 补给”等分组组织商品。这证明了“预设场景模板 + 每槽检索”
适合作为小型电商数据集的第一版方案。

ShopAgent 只吸收以下机制：

- 模板优先，不让模型每轮自由发明商品清单。
- 场景拆成稳定槽位，每槽独立检索。
- 一个槽位选择一个商品，组合结果按模板顺序组织。
- 内部保留槽位和分组关系，回答文本据此解释方案。

以下实现不直接照搬：

- 不使用 Python 字典硬编码模板，改用经过 Pydantic 与 Catalog 启动校验的 JSON 配置。
- 不用场景关键词直接决定业务路由。少量“一套 / 搭配 / 组合”等 cue 只决定是否调用
  短提示 recipe gate，最终是否进入场景分支仍由受 Schema 约束的语义结果和确定性
  recipe 校验决定。
- 不让 LLM 通过 `<!--PRODUCT: ID-->` 标记决定发送哪些卡片；商品事件仍由后端从已选
  商品确定性产生。
- 不复制 qiuqiu-agent 的松散 `type/content` SSE；继续遵守 ShopAgent 已有强类型事件和
  `message_end` 状态语义。

## 范围

第一版包含：

- 模板优先的场景识别和受限 `recipe_id` 选择。
- 结构化 `SolutionRecipe`、必选/可选 `ScenarioSlotSpec` 和精确 Catalog 范围。
- 每槽跨一个或多个审核范围检索、合并、重排、事实校验并选择一个商品。
- 必选槽位优先、可选槽位按优先级补充、单套最多六件且系统硬上限八件。
- 同一回复内返回一套组合，不返回方案 A / B 两套并列结果。
- “换一套”“再来一套”“还有更多推荐吗”的场景上下文续接和整套排重。
- 场景快照、当前组合、历史商品和活动任务的 SQLite 持久化。
- 保持现有 `/api/v1/chat/stream` 请求体、SSE 事件类型和单个 `product` 事件字段不变。
- 显式识别模板无法覆盖或 Catalog 不存在的需求，不用相近商品冒充。

第一版不包含：

- LLM 动态生成新模板、动态创建未知商品类型或无模板场景的自由规划回退。
- 一次回复多套组合，或 `bundle_id`、`scenario_slot` 等面向客户端的新字段。
- “只换帽子”“保留防晒，其他都换掉”等局部槽位替换。
- 场景总预算在多个槽位之间的自动分摊和组合优化。
- 服装颜色、版型、风格之间的审美兼容评分；现有数据不足以支持可靠搭配分。
- 天气预报、目的地实时信息、库存、优惠、购物车和下单。
- 把“下周”解释成实时天气事实；它只保留在用户原始场景文本中。

## 外部行为

### 首轮场景推荐

示例输入：

```text
下周去三亚度假，帮我搭配一套从防晒到穿搭的方案
```

第一版 `beach_vacation` 模板会把它映射为：

| 顺序 | 槽位 | 必选 | Catalog 范围 |
|---:|---|---|---|
| 1 | 防晒护理 | 是 | `美妆护肤 / 防晒` |
| 2 | 轻薄上装 | 是 | `服饰运动 / 短袖T恤`、`服饰运动 / 速干T恤` |
| 3 | 清凉下装 | 是 | `服饰运动 / 运动短裤` |
| 4 | 遮阳帽 | 否 | `服饰运动 / 帽子` |
| 5 | 随身背包 | 否 | `服饰运动 / 背包` |

当前 Catalog 没有太阳镜子品类，因此模板不包含太阳镜。用户显式要求太阳镜时，系统必须
在检索前返回目录不支持说明，不能把帽子、其他配饰或模型常识当作太阳镜结果。

### 返回数量

普通商品推荐继续使用 `Settings.final_product_limit=3`。场景组合使用独立配置：

```text
Settings.scenario_product_limit = 6
系统允许范围 = 1..8
实际数量 = min(模板 max_products, scenario_product_limit, 可用槽位数量)
```

选择顺序固定为：

1. 每个必选槽位选择一个商品。
2. 任一必选槽位没有合格商品时，不发送残缺组合。
3. 必选槽位齐全后，按模板顺序填充可选槽位。
4. 达到本模板上限或全局场景上限后停止。
5. 同一套内 `product_id` 不得重复。
6. 语义证据为 `unknown` 的商品仍可进入候选，但回答不得宣称未知条件已经得到证明。

### SSE 行为

仍然使用：

```text
POST /api/v1/chat/stream
```

请求体不增加字段。场景成功响应的顺序为：

```text
message_start
product * 1..8
text_delta * 1..N
message_end
```

每个 `product` 事件继续使用现有 `ProductEventData`，不增加 `scenario_slot`、
`scenario_group` 或 `bundle_id`。`rank` 按模板槽位顺序从 1 连续编号。

场景回复文本（`text_delta`）为 Markdown：先以一两句话总起整套方案，再按槽位顺序
每件商品一个无序列表项（`- ` 开头），商品名称与价格用 `**` 加粗。格式由生成提示词约束，
客户端负责渲染。

现有接口文档中的 `product * 0..3` 继续适用于普通推荐；场景分支把同一事件类型的允许
基数扩展到 `0..8`。这是事件结构兼容但事件数量契约发生变化，接入前提是客户端不能写死
三张卡片，并且必须以同一轮 `message_start` 到 `message_end` 之间的全部 `product` 事件
作为一套组合。

组合必须在第一个商品事件发送前完成必选槽位检查并成功保存。回答生成失败时，已经发送的
完整商品组合仍然有效，结束状态为 `partial`；检索、编排或保存失败发生在商品发送前时，
不得泄露半套结果。

### 多轮语义

| 用户表达 | 活动任务 | 后端操作 |
|---|---|---|
| “换一套”“再来一套” | 场景组合 | 继承 recipe，排除已展示商品，生成新的完整组合 |
| “还有更多推荐吗” | 场景组合 | 与“换一套”相同，分页单位是整套组合 |
| “还有更多推荐吗” | 普通商品搜索 | 继续使用现有 `more_results` 换一批 |
| “还有更多推荐吗” | 无购物上下文 | 继续使用现有缺少上下文澄清 |
| “换一套便宜点的” | 场景组合 | 第一版返回固定能力边界说明，不误入普通单品类细化 |
| “只换帽子” | 场景组合 | 第一版返回固定能力边界说明，不静默整套替换 |
| 明确发起普通商品搜索 | 任意 | 切换到普通搜索并清除活动场景快照 |

LLM 对“换一套”和“还有更多推荐吗”仍输出通用 `intent="more_results"`。后端根据持久化的
`active_task` 确定进入普通换批还是场景换套分支，避免为相同自然语言建立两套提示词枚举。

场景换套的排重规则为：

- 每个新必选槽位都必须使用本场景任务中从未展示过的商品。
- 可选槽位也只使用未展示商品；缺失可选槽位不影响完整性。
- 任一必选槽位耗尽时，不发送商品事件，保留上一套 `current_bundle`、最近候选和焦点，
  返回“当前条件下没有更多完整组合了。”
- 成功换套后才替换 `current_bundle` 并累积 `seen_product_ids`。

## 接口与数据

### 场景意图

`TurnIntent` 增加 `scenario_recommendation`。该意图必须携带：

```json
{
  "schema_version": 1,
  "intent": "scenario_recommendation",
  "scenario_request": {
    "surface_text": "下周去三亚度假，从防晒到穿搭",
    "recipe_id": "beach_vacation",
    "unmapped_requirements": []
  }
}
```

解析器接收 Registry 提供的紧凑 `recipe_id + 名称 + 语义描述 + 槽位标签` 列表。模型只能
选择其中一个 `recipe_id` 或返回 `null`，不能直接输出 Catalog 类目。后端验证 ID 属于
当前 Registry；非法 ID 进入现有一次结构化纠正，仍非法时安全失败。

为避免完整多轮提示把“开学帮我准备一套学习和生活用品”误判成非购物输入，解析器对
包含“一套 / 整套 / 搭配 / 组合 / 配齐”等明显组合形态、且当前不处于场景续接或待澄清
状态的消息，先调用短提示 `_ScenarioGateResult`：

```text
schema_version: 1
is_scenario_recommendation: bool
recipe_id: approved ID | null
unmapped_requirements[]
```

gate 为真时直接构造同一份 `TurnQuery(scenario_recommendation)`；为假、调用失败或输出非法
时回落原完整解析器。cue 只控制是否支付这次额外模型调用，不决定 recipe 或工作流路由，
因此“推荐一套防晒霜”等普通单品表达仍可回落普通分支。场景活动中的“换一套”跳过 gate，
继续由完整解析器和持久化 `active_task` 解释为 `more_results`。

`unmapped_requirements` 只记录用户明确提出、但所选模板没有槽位且当前 Catalog 没有可
绑定类型的原始需求，例如“太阳镜”。该列表非空时不开始检索，返回确定性的目录不支持
说明。普通宽泛词“从防晒到穿搭”由完整模板覆盖，不需要把每个隐含物品枚举到该列表。

### 方案模板

模板保存在 `config/scenario_recipes.json`，由以下结构表示：

```text
SolutionRecipe
  schema_version: 1
  recipe_id: 稳定英文 ID
  recipe_version: 正整数
  display_name: 用户可读名称
  aliases[]: 语义提示，不作为后端 substring 路由器
  description: 场景适用范围
  max_products: 1..8
  slots[]: ScenarioSlotSpec

ScenarioSlotSpec
  slot_id: 模板内稳定英文 ID
  label: 用户可读角色
  group: 回答组织分组
  required: bool
  query_terms[]: 仅用于本槽位检索和重排
  catalog_scopes[]:
    category
    sub_category
```

Registry 在 API 依赖构建时一次性加载并校验：

- `recipe_id` 全局唯一，别名不能跨模板冲突。
- 同一模板内 `slot_id` 唯一且至少存在一个必选槽位。
- `max_products` 不得超过 8，也不得小于必选槽位数量。
- 每个 `catalog_scope` 必须精确存在于启动时 Catalog。
- 槽位必须有非空检索词和至少一个 Catalog 范围。
- 配置非法时服务启动失败，不能跳过坏模板继续运行。

### 首批模板

第一版配置以下六个模板；所有范围都来自当前 4 个一级类目、37 个二级类目的 Catalog：

| recipe_id | 场景与别名 | 必选槽位 | 按优先级填充的可选槽位 | max |
|---|---|---|---|---:|
| `beach_vacation` | 三亚度假、海边度假、海岛旅行 | 防晒、短袖/速干上装、运动短裤 | 帽子、背包 | 5 |
| `hiking` | 爬山、徒步、登山、户外 | 徒步鞋、速干T恤、户外裤/运动长裤 | 帽子、背包、防晒、功能饮料、坚果/零食 | 6 |
| `running` | 跑步、晨跑、夜跑 | 跑步鞋、速干/短袖T恤、运动短裤 | 真无线耳机、防晒、功能饮料 | 6 |
| `back_to_school` | 开学、入学、新学期 | 笔记本电脑、背包 | 智能手机、真无线耳机、咖啡、方便食品 | 6 |
| `home_office` | 居家办公、宅家办公 | 笔记本电脑 | 真无线耳机、卫衣、运动长裤、咖啡、坚果/零食 | 6 |
| `summer_commute` | 夏日通勤、高温通勤 | 防晒、短袖T恤 | 帽子、粉底液、蜜粉、茶饮/碳酸饮料 | 6 |

候选范围列表表示同一个槽位允许合并检索的精确范围，不表示由模型自由选类目。不同范围
召回的候选先按 `product_id` 去重，再用同一槽位查询统一重排，避免比较不同调用中不可比的
Rerank 分数。

### 场景快照

`ConversationState` 升级到 schema version 2，并增加：

```text
active_task: null | product_search | scenario_recommendation
scenario_snapshot: ScenarioSnapshot | null

ScenarioSnapshot
  schema_version: 1
  recipe_id
  recipe_version
  original_request
  current_bundle[]:
    rank
    slot_id
    product_id
    display_price
  seen_product_ids[]
  generation_index
```

状态保持“单一活动任务”不变量：

- `active_task=product_search` 时只允许普通 `query_snapshot` 存在。
- `active_task=scenario_recommendation` 时只允许 `scenario_snapshot` 存在。
- 场景开始时清除旧普通查询快照、候选、焦点和 seen；普通新搜索开始时反向清除场景状态。
- 商品问答和二至三款明确商品对比不改变活动任务；完成后仍可继续“换一套”。
- `recent_candidates` 保存当前组合的全部商品，允许超过三款；`focused_product_id` 仍必须
  属于最近候选。

SQLite 表结构不变，仍写入 `conversation_state.state_json`。Repository 读取 schema v1
状态时执行确定性迁移：存在普通 `query_snapshot` 则设置 `active_task=product_search`，
否则为 `null`，并补充空 `scenario_snapshot` 后按 v2 校验；下一次保存写回 v2。

模板 `recipe_version` 与快照不一致时，不使用新模板静默续接旧方案。系统保留上一套，
返回“场景方案已更新，请重新描述本次需求”，等待用户重新发起场景推荐。

## 工作流

场景推荐是 `route_turn` 之后的独立分支，不经过单品类 `merge_query_snapshot`：

```text
START
  -> load_conversation
  -> parse_turn_query
       -> explicit bundle shape -> constrained recipe gate
            -> selected -> scenario TurnQuery
            -> rejected / failed -> full multi-turn parser
       -> otherwise -> full multi-turn parser
  -> resolve_reference / pending recovery
  -> route_turn
       -> ordinary search -> existing QuerySnapshot pipeline
       -> scenario
            -> compile_scenario_snapshot
                 -> unsupported / version mismatch -> emit_scenario_message -> END
                 -> ambiguous recipe -> persist_clarification -> END
                 -> compiled
            -> build_scenario_bundle
                 -> required slot unavailable -> emit_scenario_message -> END
                 -> complete
            -> persist_scenario_result
            -> emit_product_events
            -> generate_response
            -> END
```

`build_scenario_bundle` 对每个激活槽位执行：

```text
recipe slot
  -> 为每个精确 catalog_scope 构造槽位 ParsedIntent
  -> RetrievalService.retrieve_chunks(excluded seen IDs)
  -> aggregate_products
  -> 合并范围候选并按 product_id 去重
  -> 使用同一个 slot query 统一 rerank
  -> 过滤不属于 slot catalog_scopes 的陈旧或越界候选
  -> EvidenceService.validate_candidates
  -> select top 1
```

槽位按照模板顺序处理，组合器先完成所有必选槽位，再处理可选槽位。第一版不在槽位之间
做价格或风格的组合搜索；它保证功能角色完整、商品事实真实和顺序稳定，不声称存在未经
数据证明的“最佳搭配”。

### 可观测性日志

场景分支继续使用 `uvicorn.error` 的单行结构化 INFO 日志，并遵守以下语义：

- `turn_route.route` 记录 LangGraph 实际采用的业务分支。首轮
  `scenario_recommendation`，以及活动任务为场景推荐时的 `more_results`，都必须记录为
  `scenario`，不能按脱离会话上下文的 intent 默认值记录成 `non_shopping` 或 `search`。
- `scenario_snapshot_compiled` 记录场景编译的 `operation`、`outcome`、`recipe_id`、模板版本
  和已展示商品数量，用于区分首套、换套、澄清、不支持及模板版本不一致。
- `scenario_bundle_built` 记录组合状态、候选/合格/选中数量、选中槽位与商品 ID，以及缺失的
  必选槽位，用于确认逐槽检索最终形成完整组合还是原子失败。
- 日志不记录用户原始场景文本、检索 query、商品描述、证据正文、SKU 明细或模型完整响应；
  用户输入只参与实际编译与检索，不进入上述诊断日志。
- `conversation_persisted(state_kind="scenario_results")` 仍只在完整组合成功保存后出现；组合
  构建日志不能替代持久化成功日志。

## 失败行为

- 无法唯一绑定 recipe：保存 `PendingClarification(kind="scenario_recipe")`，列出 Registry
  当前支持的场景，跳过 Embedding、Qdrant、Rerank 和证据模型。
- 存在 `unmapped_requirements`：不检索，返回目录不支持说明。
- 必选槽位无召回或无合格商品：不发送任何商品事件，返回无法组成完整方案。
- 换套时必选槽位耗尽：保留上一套、最近候选和焦点，返回没有更多完整组合。
- 可选槽位无商品：跳过该槽位，不影响完整组合。
- Qdrant、Embedding、Rerank 或证据依赖失败：沿用现有 `ServiceError` 和 `failed` 状态。
- SQLite 保存失败：商品事件尚未发送，返回失败；不向客户端暴露未持久化组合。
- 文本生成失败：完整组合已发送，返回现有 `error` 并以 `partial` 结束。
- Catalog 返回的类目与模板范围不一致：在证据校验前确定性丢弃，不让越界商品进入组合。

## 关键决策

### 模板是业务知识，LLM 只选择模板

“防晒到穿搭”对应哪些后台商品类型属于商配规则，不属于语言模型常识。模板把业务角色
固定映射到 Catalog taxonomy；新增场景主要通过新增审核 JSON 数据完成，不修改通用意图
Schema 和工作流代码。

### 独立分支，不复用单品类 QuerySnapshot

普通 `QuerySnapshot` 只有一个 `category + sub_category`，无法同时表达防晒、上装、下装
和帽子。把多个槽位压进现有快照会破坏价格、SKU、`more_results` 和指代语义，因此场景
使用独立 `ScenarioSnapshot`，但继续复用底层检索和事实校验服务。

### 保持事件结构，明确修改数量契约

同一 `product` SSE 可以重复任意合理次数，技术上无需增加事件类型。第一版前端只展示
一套组合，也不需要机器可读分组，因此不新增卡片字段。代价是客户端必须按消息边界收集
全部卡片；如果未来需要同时展示多套或按槽位交互，再单独设计兼容字段或 `/api/v2`。

### 整套换新是原子操作

“换一套”的用户预期是获得另一套完整方案。新组合在必选槽位齐全并保存前不发送商品，
任何必选槽位耗尽都保留旧组合，避免前端出现半套新、半套旧的状态。

### 第一版不做动态场景规划

当前数据只有 37 个子品类，模板能够直接暴露目录真实边界。动态规划会产生 Catalog 不存在
的太阳镜、防晒衣等类型，并把缺少事实的数据问题伪装成检索问题。未知场景先澄清或明确
不支持，待积累模板覆盖率和评测集后再独立设计动态 fallback。

## 代码与验证

当前已实现的主要入口：

- `config/scenario_recipes.json`：审核后的首批六个方案模板。
- `src/shop_agent/models/scenario.py`：模板、槽位、请求、组合项和快照模型。
- `src/shop_agent/services/scenario_recipes.py`：配置加载、Catalog 校验和 Registry 查询。
- `src/shop_agent/services/scenario_compiler.py`：首轮、换套、unsupported 与版本检查。
- `src/shop_agent/services/scenario_recommendation.py`：逐槽检索、统一重排、校验与组合。
- `src/shop_agent/models/turn_query.py`：新增场景意图与请求 Schema。
- `src/shop_agent/models/conversation.py`：活动任务、场景快照和 v2 持久化合同。
- `src/shop_agent/workflow/nodes.py`、`graph.py`：独立场景分支、保存和流式输出。
- `src/shop_agent/api/dependencies.py`：启动加载 Registry 并注入场景服务。

当前确定性验证覆盖：

- 六个模板的精确 Catalog 范围、别名唯一性、必选槽位和上限。
- 三亚请求得到防晒、上装、下装并按顺序补充帽子和背包，不出现太阳镜。
- 普通“推荐防晒霜”不误入场景分支。
- 普通推荐仍最多三款，场景可以发送四至八个相同结构的 `product` 事件。
- 必选槽位失败时零商品事件；可选槽位失败时仍返回完整必选组合。
- “换一套”和场景中的“还有更多推荐吗”按整套排重。
- 耗尽时保留上一套与最近候选；成功时先保存再发送商品。
- schema v1 SQLite 状态迁移到 v2，重建 Repository 后场景仍可续接。
- 商品事件只使用 Catalog 事实、同 SKU `matched_skus` 和正确 `display_price`。
- 生成失败为 `partial`，检索或保存失败发生在商品发送前时为 `failed`。
- Parser 网关拒绝非 Registry recipe；HTTP 场景响应保持原请求体和商品字段集合。

真实 DashScope + Qdrant 自动化场景验收已完成；手工客户端与实际前端消费仍单独列为联调
边界，不把自动化通过表述为生产发布验收。

### Fresh 验证

2026-08-05 实施后执行：

```powershell
uv run pytest -q tests/unit/test_scenario_models.py tests/unit/test_scenario_recipes.py `
  tests/unit/test_scenario_compiler.py tests/unit/test_scenario_recommendation.py `
  tests/unit/test_scenario_workflow.py tests/unit/test_turn_query_models.py `
  tests/unit/test_conversation_models.py tests/unit/test_conversation_repository.py `
  tests/unit/test_model_gateways.py tests/unit/test_workflow_routes.py `
  tests/unit/test_workflow_stream.py tests/unit/test_multi_turn_workflow.py `
  tests/integration/test_chat_api.py
# 368 passed

uv run pytest -q
# 614 passed, 22 skipped

uv run ruff check .
# All checks passed!

.\.venv\Scripts\python.exe -m mypy src scripts
# Success: no issues found in 45 source files
```

用户明确授权后，已运行 `test_live_scenario_combination_flow`，真实使用 DashScope、当前
Catalog 和健康 Qdrant 验证三亚五槽、开学六槽、整套换新、必选槽耗尽保留、太阳镜未映射
短路及普通防晒单品隔离：`1 passed, 21 deselected in 94.20s`。其中首次运行暴露完整多轮
提示把开学集合需求误判为 `non_shopping`；增加受限短提示 recipe gate，并明确宽泛集合词
不属于 `unmapped_requirements` 后，完整用例通过。其余 live 测试仍为显式 opt-in；
随后使用真实 `scripts/chat_client.py` 完成手工多轮：三亚首轮连续渲染五卡，当前 Catalog
在第二轮即按必选槽耗尽返回零卡；开学首轮和换套轮分别连续渲染六张互不重复的卡，第三轮
耗尽返回零卡；普通防晒首轮保持三卡，更多推荐仍走普通耗尽文案。手工验收同时暴露 Windows
GBK 终端不能编码 U+00A5 `¥`，客户端价格前缀已改为 ASCII `CNY`，并增加真实 GBK writer
回归测试。实际业务前端连续四至八卡消费尚未联调，当前结论不是生产发布验收。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-08-05 | 修正场景首轮及换套的 `turn_route` 日志，并增加安全的场景编译与组合结果日志 | 原日志使用不感知活动任务的默认 intent 路由，把实际场景分支错误记录为 `non_shopping` 或 `search`，且缺少场景内部阶段信息 |
| 2026-08-05 | 新增 mock 商品 `p_clothes_026`（运动短裤）及三亚必选槽位双份库存回归，并刷新 Qdrant 索引 | 原 Catalog 的清凉下装必选槽只有一件商品，首套后无法验证成功的三亚整套换新；新增第二件精确类目商品后可手工验证换套 |
| 2026-08-05 | 完成 `scripts/chat_client.py` 场景/普通多轮验收，并将终端价格前缀从 `¥` 改为 `CNY` | Windows GBK stdout 在第一张商品卡处抛出 `UnicodeEncodeError`；ASCII 价格前缀保证客户端能继续消费整套连续卡片 |
| 2026-08-05 | 增加组合形态触发的短提示 recipe gate，并完成真实 DashScope + Qdrant 自动化场景验收 | 解决完整多轮提示将“开学准备一套学习和生活用品”误判为非购物输入的问题，同时保持 recipe 白名单、普通单品回落和工作流单一结构化意图边界 |
| 2026-08-05 | 增加真实场景组合 live 验收用例；本地 Qdrant 已健康，DashScope 外发执行等待明确授权 | 把三亚组合、整套换新、耗尽保留、太阳镜短路和普通搜索隔离纳入真实服务门槛，同时不把未授权外发误记为通过 |
| 2026-08-05 | 实现场景 Schema、六模板 Registry、ConversationState v2、逐槽组合服务、独立 LangGraph 分支、整套换新与兼容 SSE 输出 | 将审核 recipe 确定性映射到 Catalog 范围，并保证必选槽完整、保存先于卡片、普通推荐三款上限不变 |
| 2026-08-04 | 创建场景化组合推荐提议，确定模板优先、独立分支、单套多商品、整套换新和 v1 SSE 兼容边界 | 将历史 Query 编译原则与 qiuqiu-agent 的模板化槽位检索经验结合，并解决“场景角色如何映射后台商品类型”的核心问题 |
| 2026-08-09 | 场景回复提示词新增 Markdown 格式契约：总起句 + 按槽位顺序的无序列表，商品名称与价格加粗 | 纯散文段落可读性差；格式由提示词约束、客户端渲染，SSE 事件结构不变 |
