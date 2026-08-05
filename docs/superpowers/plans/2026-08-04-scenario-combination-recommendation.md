# 场景化组合推荐实施计划

> **执行要求：** 本计划是单个可交付功能，不拆成依赖后续阶段才可使用的半成品。实施时
> 按 Task 顺序使用 TDD：先增加能够证明行为缺口的失败测试，再完成最小实现并运行对应
> 验证。未经用户针对某一次 Git 操作的明确授权，不执行任何 Git 命令或 Git 操作。
>
> **执行状态（2026-08-05）：** 计划已实施；完整非 live、真实 DashScope + Qdrant 场景
> 用例及 `scripts/chat_client.py` 多轮验收已通过。实际业务前端联调仍是发布前边界。

**目标：** 在现有 ShopAgent 多轮 LangGraph 中增加独立的场景化组合推荐分支。用户用
“三亚度假”“徒步”“跑步”“开学”等场景表达跨品类需求后，系统通过审核模板将场景
绑定到精确 Catalog 类型，每槽检索并选择一款商品，返回一套最多六件、硬上限八件的
完整组合；后续“换一套”“还有更多推荐吗”按整套组合续接和排重。

**架构：** 扩展现有 `TurnQuery` 作为唯一会话理解输出；对明显组合形态在同一解析器内部
先执行短提示 recipe gate，未选中时回落完整多轮解析，不增加图外竞争路由器。`route_turn`
根据 `intent=scenario_recommendation` 或“`more_results` + 活动场景任务”
进入独立场景分支。`SolutionRecipe` 把业务槽位映射到 Catalog 的精确类目范围；
`ScenarioRecommendationService` 复用现有 RetrievalService、EvidenceService 和 Catalog
事实边界；`ScenarioSnapshot` 单独保存当前组合和 seen，不经过单品类 `QuerySnapshot`。

**参考实现：** qiuqiu-agent 的固定场景模板、槽位 Top 1 和分组机制作为设计输入；不复制
其关键词路由、Python 硬编码模板、LLM `<!--PRODUCT: ID-->` 卡片协议和松散 SSE。

**技术栈：** Python 3.11、Pydantic v2、LangGraph、FastAPI、SQLite、DashScope、Qdrant、
pytest、pytest-asyncio、Ruff、mypy。不得增加新的语言、运行时、第三方服务或依赖包。

## 已批准的产品规则

- 第一版只返回一套组合；同一回复中的全部商品卡片共同组成该套方案。
- 每个槽位最多选择一个商品；必选槽位全部成功后才允许发送组合。
- 普通推荐继续最多三款；场景默认最多六件，配置硬上限八件。
- 必选槽位先选，可选槽位按模板顺序补充；可选缺失不破坏组合。
- LLM 只选择受限 `recipe_id` 并报告无法映射的显式需求；后台模板决定商品类型。
- 模板必须在服务启动时通过 Catalog 精确 taxonomy 校验。
- 未知场景不进行动态 LLM 规划；进入可恢复场景澄清。
- 当前 Catalog 没有太阳镜；不得用帽子、其他配饰或模型常识冒充。
- `POST /api/v1/chat/stream` 路径、请求体、事件类型和单个商品事件字段不变。
- 场景回复允许连续 1–8 个 `product` 事件；客户端按消息边界把它们视为一套。
- 不增加 `scenario_slot`、`scenario_group`、`bundle_id` 等公开字段。
- “换一套”“再来一套”和场景上下文中的“还有更多推荐吗”重新生成完整组合。
- 换套时每个必选槽位必须使用本场景任务未展示商品；任一必选槽位耗尽则保留旧组合。
- 第一版不支持局部换槽、场景总预算分配、多套并列和服装审美搭配评分。
- 商品 JSON 继续是唯一事实源；Qdrant 只做候选发现。
- 场景组合必须先保存再发送商品；文本生成失败只影响说明，不使完整商品组合失效。

## 全局约束

- 不执行任何 Git 命令或 Git 操作。
- 不把场景槽位塞入现有单品类 `QuerySnapshot` 或 `SearchConstraints`。
- 不修改 `ProductEventData` 字段，不新增 SSE 事件类型或 API 路径。
- 不修改商品 JSON、Qdrant collection schema 或 SQLite 表结构。
- `ConversationState` JSON schema 从 v1 升级到 v2，必须提供确定性 v1 读取迁移。
- 新场景开始时清除普通活动查询；普通新搜索开始时清除活动场景，不能同时存在两个
  可被 `more_results` 续接的任务。
- 普通 `final_product_limit` 不得放宽；使用独立 `scenario_product_limit`。
- 所有模板范围必须来自当前 Catalog；配置非法时启动失败。
- 配置中只能保存业务模板，不保存商品 ID、价格、SKU、模型输出或检索结果。
- 实施预计涉及 18 个以上代码、配置、测试和文档文件。范围较大是因为模型协议、配置
  校验、跨类目检索、持久化迁移、LangGraph、HTTP/SSE 和多轮验收必须同批闭环。
- 完整非 live 验证与真实 DashScope/Qdrant 验收未完成前，状态不能标记“已完成”。

## 数据流

```text
load_conversation(v1 -> v2 migration)
  -> parse_turn_query
       -> scenario_recommendation
       -> more_results + active_task=scenario_recommendation
  -> route_turn
  -> compile_scenario_snapshot
       -> recipe registry validation / clarification
  -> build_scenario_bundle
       -> required slots
       -> optional slots until scenario limit
  -> persist_scenario_result
  -> emit existing product events
  -> stream scenario explanation
```

该数据流无环。场景换套读取上一次 `ScenarioSnapshot`，成功时生成新快照；耗尽或失败时
不覆盖旧快照。普通搜索和场景搜索在活动任务切换处互相清理，不互相继承错误条件。

---

## Task 1：建立模板、槽位和快照模型

**文件：**

- 新增：`config/scenario_recipes.json`
- 新增：`src/shop_agent/models/scenario.py`
- 新增：`src/shop_agent/services/scenario_recipes.py`
- 修改：`src/shop_agent/config.py`
- 新增：`tests/unit/test_scenario_models.py`
- 新增：`tests/unit/test_scenario_recipes.py`
- 修改：`tests/unit/test_settings.py`

- [x] 先为 `SolutionRecipe`、`ScenarioSlotSpec`、`ScenarioRequest`、
  `ScenarioBundleItem` 和 `ScenarioSnapshot` 增加 Pydantic 合同测试，全部使用
  `extra="forbid"`，快照和模板 ID 去空白且禁止空字符串。
- [x] 测试 recipe ID 全局唯一、模板内 slot ID 唯一、别名跨模板唯一、至少一个必选槽位、
  `max_products` 不小于必选槽位数且不超过 8。
- [x] 测试每个槽位至少有一个检索词和一个精确 `category + sub_category`，范围重复被拒绝。
- [x] 为 `Settings.scenario_recipe_path` 增加默认值 `config/scenario_recipes.json`，为
  `scenario_product_limit` 增加默认值 6 和范围 `1..8`；确认现有
  `final_product_limit` 仍严格限制为 `1..3`。
- [x] 在 JSON 中写入六个已批准模板：`beach_vacation`、`hiking`、`running`、
  `back_to_school`、`home_office`、`summer_commute`，槽位、必选性、范围和上限逐字使用
  功能文档中的表格。
- [x] 实现 `ScenarioRecipeRegistry.load(path, catalog)`，在启动时校验全部精确范围真实存在于
  Catalog；任一模板非法时抛出明确配置错误，不删除坏模板后继续。
- [x] 增加当前真实 Catalog 验收测试，断言六个模板全部加载，三亚模板没有太阳镜且只绑定
  `防晒 / 短袖T恤或速干T恤 / 运动短裤 / 帽子 / 背包`。
- [x] 增加构造型坏配置测试：未知子品类、重复别名、必选槽位超过上限、空检索词和
  `max_products=9` 均失败。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_scenario_models.py tests/unit/test_scenario_recipes.py tests/unit/test_settings.py
```

## Task 2：扩展唯一 TurnQuery 解析路径

**文件：**

- 修改：`src/shop_agent/models/turn_query.py`
- 修改：`src/shop_agent/services/ports.py`
- 修改：`src/shop_agent/services/dashscope_chat.py`
- 修改：`src/shop_agent/api/dependencies.py`
- 修改：`tests/unit/test_turn_query_models.py`
- 修改：`tests/unit/test_model_gateways.py`

- [x] 先增加 `TurnIntent="scenario_recommendation"` 测试，并要求该意图必须且只能携带
  `ScenarioRequest`；普通搜索、对比、商品问题和非购物意图不得携带场景请求。
- [x] `ScenarioRequest` 固定包含 `surface_text`、`recipe_id: str | null` 和去重后的
  `unmapped_requirements[]`；非法或空 recipe ID、空 unmapped 条目被拒绝。
- [x] 扩展 `TurnContext`，向解析器提供 `active_task`、当前场景的 recipe ID、名称和槽位
  标签；不得把完整商品、seen 历史或场景检索结果注入意图提示词。
- [x] `DashScopeTurnQueryParser` 启动时接收 Registry 的紧凑模板摘要：稳定 ID、名称、
  aliases、description、slot labels。提示词按 Schema 和语义规则选择模板，不枚举用户句式。
- [x] 提示词明确：普通单品请求如“推荐防晒霜”仍是 `new_search`；完整场景清单才是
  `scenario_recommendation`；当前活动场景下“换一套”和“还有更多推荐吗”输出已有
  `more_results`。
- [x] 模型只能输出 Registry 允许的 recipe ID 或 null；后端在解析后验证，非法 ID 使用
  现有一次结构化纠正，第二次仍非法时返回 `TURN_QUERY_PARSE_FAILED`。
- [x] 增加 fake 模型解析测试：
  - “下周去三亚度假，帮我搭配一套从防晒到穿搭的方案”选择 `beach_vacation`；
  - “推荐防晒霜”是普通搜索；
  - “给宝宝准备周岁宴用品”因没有模板返回 null；
  - 显式“还需要太阳镜”进入 `unmapped_requirements`；
  - 活动场景下“换一套”与“还有更多推荐吗”均为纯 `more_results`；
  - 非法 recipe ID 一次纠正后成功及两次失败。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_turn_query_models.py tests/unit/test_model_gateways.py
```

## Task 3：实现确定性场景编译和活动任务状态

**文件：**

- 新增：`src/shop_agent/services/scenario_compiler.py`
- 修改：`src/shop_agent/models/conversation.py`
- 修改：`src/shop_agent/services/conversation_repository.py`
- 修改：`tests/unit/test_conversation_models.py`
- 新增：`tests/unit/test_scenario_compiler.py`
- 修改：`tests/unit/test_conversation_repository.py`

- [x] 先增加 `ConversationState` v2 测试：`active_task` 只允许 null、`product_search`、
  `scenario_recommendation`；普通和场景快照互斥并与活动任务严格一致。
- [x] 场景快照的 `current_bundle.rank` 必须从 1 连续编号，slot ID 与 product ID 唯一，
  当前商品必须属于 `seen_product_ids`，`generation_index` 从 1 开始。
- [x] 为 `PendingClarification.kind` 增加 `scenario_recipe` 和受限候选 recipe IDs；场景回答
  只能从原候选范围恢复，不能由模型扩大模板域。
- [x] 实现 v1 -> v2 持久化迁移：v1 有 `query_snapshot` 时设置
  `active_task=product_search`，否则为 null；补充空场景快照并按 v2 模型校验。迁移只发生
  在内存读取边界，下一次成功保存写回 v2。
- [x] 增加旧 SQLite row 的真实 round-trip 测试，并断言 repository 重建后读取一致。
- [x] 实现 `compile_scenario_turn`：
  - 首轮受支持 recipe 创建 generation 1 的空场景快照并清除普通活动状态；
  - recipe null 产生 `scenario_recipe` pending 和 Registry 支持列表；
  - `unmapped_requirements` 产生确定性不支持结果且不创建活动场景；
  - 纯 `more_results` 只有在活动场景与版本一致时编译为 `replace_bundle`；
  - recipe 版本漂移返回重新发起场景的固定说明；
  - 场景中的 mutation 或局部替换请求返回第一版能力边界说明，不误入普通编译器。
- [x] 普通 `new_search` / `switch_category` 成功编译时清除场景快照并设置
  `active_task=product_search`；现有细化与普通 `more_results` 保持该活动任务。
- [x] 增加从场景切普通、普通切场景、商品问题后继续换套、未知场景澄清恢复和取消 pending
  的测试。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_conversation_models.py tests/unit/test_conversation_repository.py tests/unit/test_scenario_compiler.py
```

## Task 4：逐槽检索并原子编排一套组合

**文件：**

- 新增：`src/shop_agent/services/scenario_recommendation.py`
- 修改：`src/shop_agent/workflow/dependencies.py`
- 新增：`tests/unit/test_scenario_recommendation.py`
- 修改：`tests/unit/workflow_fakes.py`

- [x] 建立 recording fakes，分别记录每槽的 ParsedIntent、excluded IDs、聚合、统一重排、
  证据校验和候选选择调用。
- [x] 为每个槽位的每个精确 scope 构造 `intent=product_search`，检索 query 由 recipe 名称、
  用户原始场景和 slot query terms 稳定拼接；category 和 sub_category 必须来自模板，不
  接受 LLM 输出覆盖。
- [x] 同槽多个 scope 的候选按 product ID 去重后只调用一次统一 Rerank，使分数可比较；
  Catalog 事实不属于允许 scope 的候选在证据模型前确定性淘汰。
- [x] 复用 `EvidenceService.validate_candidates` 和 `select_candidates(limit=1)`；价格和 SKU
  继续从匹配 SKU 计算，语义 `unknown` 继续保留但不能生成支持性理由。
- [x] 组合器先按顺序完成全部必选槽位，再处理可选槽位，使用
  `min(recipe.max_products, settings.scenario_product_limit)` 限制总数。
- [x] 全局排除当前场景 `seen_product_ids`，并在同一新组合内立即排除已经选中的商品，
  防止跨槽重复。
- [x] 任一必选槽位没有可选商品时返回 `incomplete_required_slots`，不携带任何准备发送的
  `selected_products`；可选槽位为空时继续。
- [x] 增加六模板参数化测试和三亚精确测试，断言模板顺序、一个槽位一个商品、普通上限
  不参与场景、场景上限 6、硬上限 8、太阳镜不进入任何调用。
- [x] 增加失败清理测试：Embedding、Qdrant、Rerank、Evidence 任一抛错时取消并等待同轮
  未完成任务，原样传播 `ServiceError`，不返回部分组合。
- [x] 第一版槽位按模板顺序串行编排，避免并发结果导致排重与可选优先级不确定；槽位内部
  多 scope 检索可以并发，但必须统一等待、去重和 Rerank 后再选择。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_scenario_recommendation.py
```

## Task 5：接入 LangGraph、保存和多轮换套

**文件：**

- 修改：`src/shop_agent/models/state.py`
- 修改：`src/shop_agent/workflow/nodes.py`
- 修改：`src/shop_agent/workflow/graph.py`
- 修改：`src/shop_agent/workflow/dependencies.py`
- 修改：`src/shop_agent/api/dependencies.py`
- 新增：`tests/unit/test_scenario_workflow.py`
- 修改：`tests/unit/test_workflow_routes.py`
- 修改：`tests/unit/test_multi_turn_workflow.py`

- [x] 先增加路由失败测试：`scenario_recommendation` 不进入 `merge_query_snapshot`、主动偏好
  澄清或普通 `compile_effective_query`；普通搜索路径节点序列保持不变。
- [x] 增加场景 transient state：编译结果、recipe、场景操作、组合项、缺失必选槽位和
  场景无结果原因；不污染普通 `parsed_intent` 与单品类候选状态。
- [x] 在 `route_turn` 增加 `scenario` 分支。通用 `more_results` 根据持久化
  `active_task` 路由：普通任务进入现有路径，场景任务进入场景编译，无活动任务沿用现有
  缺少上下文行为。
- [x] 新增节点 `compile_scenario_snapshot`、`build_scenario_bundle`、
  `persist_scenario_result`、`persist_scenario_no_results`；商品发送继续复用现有
  `emit_product_events`。
- [x] `persist_scenario_result` 按模板顺序构造全部 `CandidateReference`，更新活动任务、
  current bundle、seen、generation index 和 recent candidates，清除旧焦点，并使用现有
  expected version 乐观保存。
- [x] 保存完成前 recording writer 必须没有 `product`；保存失败断言零商品事件。
- [x] 成功换套排除全部历史商品并替换 current bundle；每轮新商品累积到 seen。
- [x] 换套耗尽时复用普通换批的“保留最近引用上下文”原则：不覆盖 current bundle、
  recent candidates、seen 或 focus，只保存必要的版本状态并发送固定文本
  “当前条件下没有更多完整组合了。”
- [x] 初始必选槽位不足时没有旧组合可保留，返回“当前商品库暂时无法组成完整方案。”，
  不建立活动场景。
- [x] 场景成功后显式商品序数和名称指代可以在全部最近组合商品中解析；多商品对比仍最多
  选择两至三款，六款裸“这些哪个好”需要现有比较澄清，不能扩大比较上限。
- [x] 场景回答提示只接收已选商品 Catalog 事实、slot label、group 和证据白名单；要求按
  槽位解释用途，不声称天气、审美搭配分或未知语义条件已经验证。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_scenario_workflow.py tests/unit/test_workflow_routes.py tests/unit/test_multi_turn_workflow.py
```

## Task 6：锁定 HTTP/SSE 兼容与客户端数量契约

**文件：**

- 修改：`tests/integration/api_fakes.py`
- 修改：`tests/integration/test_chat_api.py`
- 修改：`tests/unit/test_workflow_stream.py`
- 修改：`scripts/chat_client.py`
- 修改：`tests/live/test_live_shopping_flow.py`

- [x] 增加 HTTP 集成测试：三亚场景仍使用原请求体，得到
  `message_start -> product*5 -> text_delta+ -> message_end(completed)`，每个 product
  payload 与当前 `ProductEventData` 字段集合完全一致。
- [x] 增加场景六件和构造型八件边界测试；普通搜索继续断言 `product*0..3`。
- [x] 增加跨 repository 两轮 HTTP 测试：首轮三亚组合成功，重建依赖后“换一套”返回
  全部未展示商品，recipe 和 generation index 正确恢复。
- [x] 增加第三轮“还有更多推荐吗”耗尽测试，断言零商品事件、旧组合仍可被随后商品问题
  引用。
- [x] 增加 unknown recipe、太阳镜 unmapped、缺必选槽位、可选槽位缺失、保存冲突、
  retrieval 失败和 generation 失败的事件顺序与状态测试。
- [x] generation 失败时必须已经发送完整组合且 `message_end.status=partial`；必选槽位、
  检索或保存失败前未发送商品时为 `failed` 或确定性无结果 `completed`。
- [x] `scripts/chat_client.py` 已按事件流逐条渲染，无三张硬限制；增加/更新测试或手工检查，
  保证 rank 4..8 不被截断。不得增加场景专用客户端协议。
- [x] live 测试增加以下真实解析与工作流用例：
  - 三亚完整方案；
  - 三亚后“换一套”；
  - 场景后“还有更多推荐吗”；
  - “推荐防晒霜”不误触场景；
  - 无模板场景不自由生成清单；
  - 显式太阳镜不返回伪商品。
- [x] 运行：

```powershell
uv run pytest -q tests/unit/test_workflow_stream.py tests/integration/test_chat_api.py
```

## Task 7：同步功能文档与当前行为

**文件：**

- 修改：`docs/features/scenario-combination-recommendation.md`
- 修改：`docs/features/text-shopping-workflow.md`
- 修改：`docs/features/multi-turn-query-engine.md`
- 修改：`docs/README.md`
- 新增：`docs/superpowers/status/2026-08-04-scenario-combination-recommendation-status.md`

- [x] 实施开始时把本功能和索引状态从“提议”改为“开发中”。
- [x] 在单轮工作流文档中把全局 `product*0..3` 改为“普通推荐 0..3”，并链接场景分支的
  `0..8` 基数；不得把场景上限误写成普通 `final_product_limit`。
- [x] 在多轮 Query 文档中增加 `scenario_recommendation`、活动任务互斥、通用
  `more_results` 的上下文路由、ConversationState v2 迁移和场景耗尽保留规则。
- [x] 更新本功能文档中的实际类名、配置入口、节点、测试和已实现首批模板，不记录未落地
  行为。
- [x] 状态文档分别记录模型/Registry、场景服务、工作流、HTTP、完整非 live、Ruff、mypy、
  真实模型解析、真实 Qdrant 和手工多轮验收；未运行项明确标记“未运行”。
- [x] 只有完整非 live 与真实 DashScope/Qdrant、手工两轮换套全部通过后，才标记“已完成”；
  否则保持“开发中”并写明阻塞层。

## Task 8：完整验证与范围审查

- [x] 串行运行聚焦测试：

```powershell
uv run pytest -q tests/unit/test_scenario_models.py tests/unit/test_scenario_recipes.py tests/unit/test_scenario_compiler.py tests/unit/test_scenario_recommendation.py tests/unit/test_scenario_workflow.py tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_conversation_repository.py tests/unit/test_model_gateways.py tests/unit/test_workflow_routes.py tests/unit/test_workflow_stream.py tests/unit/test_multi_turn_workflow.py tests/integration/test_chat_api.py
```

- [x] 运行完整非 live 测试和静态检查：

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src scripts
```

- [x] Docker Linux daemon、Qdrant 和 DashScope 配置健康时运行 live：

```powershell
$env:RUN_LIVE_TESTS = "1"
uv run pytest -q tests/live/test_live_shopping_flow.py -k "scenario"
Remove-Item Env:RUN_LIVE_TESTS
```

- [x] 使用 `scripts/chat_client.py` 完成手工对话：

```text
下周去三亚度假，帮我搭配一套从防晒到穿搭的方案
换一套
还有更多推荐吗
```

执行结果：三亚第一轮返回五件完整组合，第二、三轮因当前 Catalog 必选槽候选耗尽而零卡，
没有泄露半套。为覆盖成功换套路径，使用 `back_to_school` 完成首轮六卡、第二轮六张全部
未展示商品、第三轮零卡耗尽。任何一轮均未出现太阳镜、天气预报、库存、优惠或数据集外
商品事实。

- [x] 再完成普通搜索隔离对话：

```text
推荐防晒霜
还有更多推荐吗
```

通过条件：走普通 `QuerySnapshot` 与三款上限，第二轮是普通换批，不创建场景快照。

- [x] 审查全部变更，确认没有新增 API 路径、SSE 类型、product 字段、数据库表、Qdrant
  collection、依赖、外部服务、动态模板生成或第二个图外意图判断器。
- [x] 审查 `config/scenario_recipes.json` 不包含商品 ID、价格、SKU 或私有数据。
- [x] 审查文档状态与验证层一致；skipped live、无效 DashScope 或不健康 Qdrant 均不能
  计作真实通过。

## 验收场景

1. 三亚请求唯一绑定 `beach_vacation`，返回防晒、上装、下装并按优先级补充帽子、背包。
2. 普通“推荐防晒霜”仍走单品类搜索，最多三款。
3. 三亚模板只引用当前 Catalog 的精确范围，不包含太阳镜。
4. 用户显式要求太阳镜时零检索、零商品事件，返回目录不支持说明。
5. 必选下装无候选时整套失败；可选背包无候选时仍返回其他完整槽位。
6. 场景配置上限 6 时最多六件；构造型配置 8 时最多八件；9 被配置校验拒绝。
7. 每个商品事件字段与现有 API 一致，rank 从 1 连续编号。
8. 首轮保存发生在第一个 product 事件前，保存失败没有卡片。
9. “换一套”继承 recipe，不重新让 LLM 规划槽位，所有新必选商品均未展示过。
10. 场景中的“还有更多推荐吗”按整套换新；普通场景中仍按普通商品换批。
11. 任一必选槽位耗尽时保留上一套和最近候选，用户仍可追问上一套商品。
12. 场景后明确普通新搜索清除场景状态；普通搜索后明确场景请求清除普通快照。
13. v1 SQLite 会话可以读取并升级，v2 场景会话在 repository 重建后继续换套。
14. 生成失败保留已发送完整组合并标记 partial；外部检索失败不发送半套。
15. 模板版本变化时拒绝静默续接，要求用户重新发起场景需求。
16. 两至三款明确商品仍可对比；六款裸比较不会扩大现有对比上限。

## 回滚与兼容

- API、商品事件和数据库表没有迁移，关闭场景路由即可停止产生新场景组合。
- 已写入的 ConversationState v2 包含旧二进制不认识的字段；回滚版本必须保留 v2 的宽容
  读取或部署一个清理迁移，把活动场景会话转换为空上下文后再降级。不得直接部署只接受
  schema v1 的旧二进制读取 v2 数据。
- 代码回滚时先禁止新的 `scenario_recommendation` 路由，保留 v2 reader、场景 pending
  清理和普通新搜索覆盖逻辑，等待活动场景自然清除后再移除模型。
- `config/scenario_recipes.json` 不包含运行时状态，可以独立回滚；快照 recipe version
  不匹配时已有安全重新发起路径。
- 普通 `final_product_limit`、QuerySnapshot、SKU 约束和普通 more-results 语义不变，
  因此关闭场景后普通购物能力无需数据修复。

## 外部依赖

- 不新增第三方依赖、API key、MCP、CLI 或服务。
- 新增一个本地模板路径配置 `SCENARIO_RECIPE_PATH`，默认
  `config/scenario_recipes.json`；模板只读并在启动时加载。
- 确定性和 HTTP 测试使用现有 fakes，不需要真实 DashScope 或 Qdrant。
- live 验收继续依赖现有有效 DashScope 配置和健康 Qdrant。`RUN_LIVE_TESTS=1` 只打开测试
  gate，不提供密钥也不启动 Qdrant。

## 最脆弱假设

本计划假设前端能够消费同一消息内超过三个、最多八个连续 `product` 事件，并以消息边界
把它们作为一套组合。如果实际客户端仍截断到三张卡片，后端协议虽然可发送，用户仍只能
看到残缺方案；这种情况下必须先修复客户端数量假设，本功能不能标记完成。
