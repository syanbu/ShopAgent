# Agent 主动需求澄清实施计划

> **执行要求：** 按任务顺序使用 TDD 实施。每个任务先增加能够证明行为缺口的失败测试，
> 再完成最小实现并运行对应验证。未经用户针对某一次 Git 操作的明确授权，不执行任何
> Git 命令或 Git 操作。
>
> **执行状态（2026-08-04）：** Tasks 1–5 与 Task 6 的聚焦、完整非 live、Ruff、mypy
> 已完成；真实 DashScope/Qdrant 与手工两轮验收因网络中断且本地 Qdrant 不可用而未运行。
> 功能保持“开发中”。

**目标：** 当一次新搜索已经唯一确定商品子品类，但完整查询除类目外没有任何决策信息，
且该子品类有超过展示上限的差异化候选时，Agent 在检索前主动提出一次经过 Catalog
审核的子品类相关问题；用户回答后复用现有多轮 Query 编译，把普通倾向作为软偏好、
明确限制作为硬约束，再执行现有推荐链路。

**架构：** 保持 `TurnQuery -> QuerySnapshot -> ParsedIntent` 为唯一查询编译路径。新增一个
纯确定性的主动澄清策略服务，在 `merge_query_snapshot` 成功之后、
`compile_effective_query` 之前检查完整快照、Catalog 商品数、子品类策略和显式跳过标记。
需要反问时使用现有 `pending_clarification` 暂停本次搜索并发送固定文本；下一轮回答仍由
`DashScopeTurnQueryParser` 解析成现有语义操作和槽位操作，然后恢复原搜索。

**技术栈：** Python 3.11、Pydantic v2、LangGraph、SQLite、DashScope、pytest、
pytest-asyncio、Ruff、mypy。

## 已批准的产品规则

- 主动反问与阻塞型澄清是两类行为。现有品类歧义、商品指代歧义、条件冲突、缺少价格
  基准和缺少比较维度继续优先处理。
- 主动反问只检查 `new_search` 和 `switch_category`，不检查 `refine_search`、
  `more_results`、商品问答、商品对比或非购物输入。
- 是否“只有类目”以合并后的 `QuerySnapshot` 为准，不以本轮模型输出字段数量为准。
- 只有 `category + sub_category` 已唯一绑定、所有决策槽位为空、子品类商品数大于
  `Settings.final_product_limit`、且存在审核过的问题策略时才反问。
- 问题必须来自子品类白名单。未知子品类直接推荐，不允许 LLM 生成或借用其他品类问题。
- 用户已经给出任意预算、性价比、品牌、语义偏好、feature、SKU、数值或排除条件时，
  直接进入推荐链路。
- 用户可在首次请求或回答时明确跳过；主动反问最多一次，回答无有效信息时也恢复原搜索。
- “拍照优先”继续进入 `semantic_terms` 并触发软偏好重排；“预算 4000”继续进入
  `constraints.max_price` 并作为硬约束，不建立第二套偏好或预算模型。
- 提问轮不调用 Embedding、Qdrant、Rerank、证据模型或回答生成模型。
- `POST /api/v1/chat/stream` 请求体、商品卡片和 SSE 事件类型保持兼容。

## 首批问题白名单

实现必须逐字使用以下问题，不增加未审核的子品类：

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

## 全局约束

- 不执行任何 Git 命令或 Git 操作。
- 商品 JSON 继续是商品事实唯一来源，Qdrant 继续是派生检索索引。
- 不新增数据库表、数据库列、Redis、外部服务、依赖包、模型调用或环境变量。
- 不修改 `SearchConstraints`、`QuerySnapshot` 的既有软硬约束语义。
- 不把 `skip_preference_question` 写入查询快照、检索文本或日志事实载荷。
- 新增 `missing_preferences` pending 时，发送文本前必须先完成 SQLite 保存。
- 真实 DashScope/Qdrant 验收未通过时，功能文档和索引不得标记“已完成”。
- 实施预计涉及 12 个以上代码与测试文件以及 5 个文档文件；范围较大是因为模型契约、
  持久化状态、LangGraph 分支、HTTP/SSE 和文档必须同批闭环。本功能不拆成表面独立、
  实际不可用的多阶段交付。

## 数据流

```text
TurnQuery
  -> resolve_category_reference
  -> merge_query_snapshot
  -> ProactiveClarificationPolicy
       | ask
       v
     PendingClarification(kind=missing_preferences)
       -> SQLite save
       -> fixed text SSE
       -> next TurnQuery(clarification_answer)
       -> merge suspended search + answer operations
       -> merge_query_snapshot
       -> skip second question
       -> existing compile/retrieve/rerank/evidence path
       |
       | continue
       v
     existing compile/retrieve/rerank/evidence path
```

该数据流无环：恢复后的搜索通过 pending 来源标识跳过第二次主动判断，完成搜索后清除
pending。普通新搜索仍会独立评估一次。

---

## Task 1：建立确定性的子品类问题策略

**文件：**

- 新增：`src/shop_agent/services/proactive_clarification.py`
- 新增：`tests/unit/test_proactive_clarification.py`

- [ ] 在 `tests/unit/test_proactive_clarification.py` 增加参数化失败测试，锁定八个精确
  `category + sub_category -> question` 映射，逐字断言问题文本。
- [ ] 增加未知子品类测试，断言即使商品数超过三也不生成问题。
- [ ] 增加完整快照门槛测试，逐项覆盖：`semantic_terms`、价格上下限、
  `price_preference`、指定/排除品牌、必需/排除 feature、SKU 条件和数值条件；任意一项
  非空都不主动反问。
- [ ] 增加意图门槛测试：只允许 `new_search` 和 `switch_category`；其他意图返回继续。
- [ ] 增加 Catalog 数量测试：数量等于 `final_product_limit` 时继续，数量大于上限时才问。
- [ ] 增加显式跳过测试：相同快照与目录条件下，跳过标记强制继续。
- [ ] 实现不可变的内部 `ProactiveClarificationDecision`，只表达 `ask + message` 或
  `continue`，不携带商品事实、排名或模型输出。
- [ ] 实现白名单和确定性判断函数；只读取 `ProductCatalog.all()`、完整 `QuerySnapshot`、
  已编译意图、`final_product_limit` 与跳过标记。
- [ ] 运行 `uv run pytest -q tests/unit/test_proactive_clarification.py`，要求全部通过。

## Task 2：扩展 TurnQuery 与 pending 合同

**文件：**

- 修改：`src/shop_agent/models/turn_query.py`
- 修改：`src/shop_agent/models/conversation.py`
- 修改：`src/shop_agent/services/dashscope_chat.py`
- 修改：`tests/unit/test_turn_query_models.py`
- 修改：`tests/unit/test_conversation_models.py`
- 修改：`tests/unit/test_model_gateways.py`

- [ ] 先增加 `TurnQuery.skip_preference_question: bool = False` 的模型测试。
- [ ] 增加互斥测试：`skip_preference_question=true` 与 `cancel_pending=true` 不能同时存在。
- [ ] 增加适用范围测试：跳过标记只允许出现在 `new_search`、`switch_category` 或
  `clarification_answer`，不得污染商品问答、比较和非购物意图。
- [ ] 将 `missing_preferences` 加入 `PendingClarification.kind`，增加 JSON round-trip、
  不可变暂停请求和旧会话兼容测试。
- [ ] 更新 Turn Query system prompt：
  - 首次请求包含“直接推荐”“先看看”“随便推荐”“不用问”等明确表达时设置跳过标记；
  - `missing_preferences` pending 下，“先看看”输出 `clarification_answer` 和跳过标记；
  - 偏好回答继续输出现有 `semantic_term_operations`；
  - 明确预算继续输出 `constraints.max_price`；
  - 不允许模型生成问题、问题 ID 或问题文本。
- [ ] 增加解析器测试，覆盖：
  - “推荐一款手机”不自行跳过；
  - “直接推荐几款手机”设置跳过；
  - pending 后“拍照优先，预算 4000”得到软偏好和最高价格硬约束；
  - pending 后“先看看”只设置跳过，不生成虚假偏好；
  - 结构化纠正后仍非法时沿用 `TURN_QUERY_PARSE_FAILED` 安全错误。
- [ ] 运行 `uv run pytest -q tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_model_gateways.py`。

## Task 3：接入 LangGraph、持久化与一次性恢复

**文件：**

- 修改：`src/shop_agent/models/state.py`
- 修改：`src/shop_agent/workflow/nodes.py`
- 修改：`src/shop_agent/workflow/graph.py`
- 修改：`tests/unit/test_multi_turn_workflow.py`
- 修改：`tests/unit/test_workflow_routes.py`
- 修改：`tests/unit/workflow_fakes.py`

- [ ] 在工作流测试中建立“智能手机 14 款、最终上限 3、查询只有类目”的失败用例，断言
  固定反问、没有商品事件，且 Embedding、Qdrant、聚合、Rerank、证据和回答生成均未调用。
- [ ] 增加“面霜 3 款”与“未知策略子品类”用例，断言直接进入现有检索链路。
- [ ] 增加每类非空决策信号的工作流短路测试，至少覆盖一个软偏好、一个价格硬约束、
  一个 SKU 硬约束和一个排除条件。
- [ ] 新增 `decide_proactive_clarification` 节点和两路路由；将
  `merge_query_snapshot -> compiled` 改为先进入该节点，再决定保存反问或继续编译。
- [ ] 需要反问时构造 `PendingClarification(kind="missing_preferences")`，保存暂停的
  `TurnQuery`，清除与新搜索相同的旧候选、焦点和 seen 状态，并复用
  `persist_clarification` 保证先保存后发文本。
- [ ] 扩展 `_merge_pending_turn`：
  - 有回答操作时，将回答的语义/槽位/价格操作与暂停的新搜索合并；
  - 跳过或回答无有效操作时，恢复暂停的新搜索但不增加条件；
  - `cancel_pending` 继续取消；
  - 任何恢复路径都清除 pending，并携带单次工作流标识跳过第二次主动问题。
- [ ] 单次跳过标识只存在于 `ShoppingState`，不进入 SQLite；新的独立新搜索仍可再次触发。
- [ ] 增加跨 repository 重建测试：提问保存后重新创建 SQLite repository，下一轮回答仍
  恢复同一类目和原始搜索。
- [ ] 增加无效回答测试：只问一次，随后按原始类目检索，不进入 attempt_count 第二轮。
- [ ] 增加新搜索覆盖和取消测试，确保旧 pending 不复活。
- [ ] 运行 `uv run pytest -q tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_routes.py`。

## Task 4：锁定 HTTP/SSE 与真实模型行为

**文件：**

- 修改：`tests/integration/test_chat_api.py`
- 修改：`tests/integration/api_fakes.py`
- 修改：`tests/live/test_live_shopping_flow.py`

- [ ] 增加 HTTP 集成测试：发送“推荐一款手机”后得到
  `message_start -> text_delta -> message_end`，没有 `product` 事件；SQLite 已保存
  `missing_preferences` pending。
- [ ] 第二轮发送“拍照优先，预算 4000”，断言 pending 清除、快照包含
  `semantic_terms=["拍照优先"]` 和 `max_price=4000`，并恢复正常商品事件顺序。
- [ ] 增加“先看看”的 HTTP 集成测试，断言第二轮恢复原始手机搜索且不产生伪偏好。
- [ ] 增加显式首次跳过、三款目录直接推荐、重复问题抑制、保存冲突和模型解析失败测试。
- [ ] 保留现有错误语义：保存失败前不发送文本；已经发送商品后的生成错误仍为 partial。
- [ ] 增加 opt-in live 测试，真实解析以下输入：
  - “推荐一款手机”；
  - “拍照优先，预算 4000”；
  - “先看看”；
  - “推荐跑步鞋”；
  - “直接推荐几款真无线耳机”。
- [ ] live 断言只检查结构化意图、软硬约束和跳过标记，不把自然语言措辞漂移误判为失败。
- [ ] 运行 `uv run pytest -q tests/integration/test_chat_api.py`。

## Task 5：同步当前行为、功能索引和状态

**文件：**

- 修改：`docs/features/proactive-requirement-clarification.md`
- 修改：`docs/features/text-shopping-workflow.md`
- 修改：`docs/features/multi-turn-query-engine.md`
- 修改：`docs/README.md`
- 修改：`docs/superpowers/status/2026-08-03-proactive-requirement-clarification-status.md`

- [ ] 实施开始时将功能和索引状态从“提议”改为“开发中”。
- [ ] 在单轮工作流文档中将“品类明确时总是直接推荐”改为当前实际门槛，并链接本功能。
- [ ] 在多轮 Query 文档中增加 `missing_preferences`、跳过语义、一次性恢复、工作流节点和
  SQLite 兼容边界。
- [ ] 更新本功能文档中的实际代码入口、测试入口和最终白名单，不记录未实现的行为。
- [ ] 状态文档分别记录单元/集成、Ruff、mypy、真实模型解析和真实端到端流程；未运行项
  明确写“未运行”，不能推断为通过。
- [ ] 只有完整非 live 验证与真实 DashScope/Qdrant 验收全部通过后，才将功能和索引状态
  改为“已完成”；否则保持“开发中”并记录阻塞条件。
- [ ] 增加 2026-08-03 变更记录，说明主动需求澄清取代“条件少时总是直接推荐”的旧行为。

## Task 6：完整验证与范围审查

- [ ] 运行聚焦测试：

```powershell
uv run pytest -q tests/unit/test_proactive_clarification.py tests/unit/test_turn_query_models.py tests/unit/test_conversation_models.py tests/unit/test_model_gateways.py tests/unit/test_multi_turn_workflow.py tests/unit/test_workflow_routes.py tests/integration/test_chat_api.py
```

- [ ] 运行完整非 live 测试：

```powershell
uv run pytest -q
```

- [ ] 运行静态验证：

```powershell
uv run ruff check .
uv run mypy src scripts
```

- [ ] 在 Docker/Qdrant 健康且 DashScope 配置有效时运行 live 测试：

```powershell
$env:RUN_LIVE_TESTS = "1"
uv run pytest -q tests/live/test_live_shopping_flow.py -k "proactive or full_shopping_flow"
Remove-Item Env:RUN_LIVE_TESTS
```

- [ ] 使用 `scripts/chat_client.py` 完成手工对话：

```text
推荐一款手机
拍照优先，预算 4000
```

通过条件：第一轮只发送子品类正确的问题；第二轮不再次反问，推荐商品全部满足
`max_price=4000`，推荐说明围绕拍照软偏好但不把它升级为硬事实。

- [ ] 再完成跳过对话：

```text
推荐一款手机
先看看
```

通过条件：只反问一次，第二轮直接返回手机结果，快照没有新增虚假偏好或预算。

- [ ] 审查全部受影响路径，确认没有新增 API/SSE、数据库表、依赖、环境变量、商品字段、
  第二套查询模型或开放式问题生成。
- [ ] 审查文档状态与实际验证一致；live 未通过时保留“开发中”。

## 验收场景

1. “推荐一款手机”在 14 款手机和展示上限 3 的目录中触发手机固定问题。
2. “推荐拍照好的手机”直接检索，“拍照”作为软偏好。
3. “推荐 4000 元以内的手机”直接检索，`max_price=4000`。
4. “直接推荐几款手机”不反问。
5. “推荐一款手机”后回答“拍照优先，预算 4000”，恢复搜索且软硬条件来源正确。
6. 同一反问后回答“先看看”，不添加条件并直接搜索。
7. 同一反问后回答无有效信息，只恢复原搜索，不进行第二次主动问题。
8. “推荐面霜”因当前目录只有三款而直接检索。
9. “推荐鞋”继续追问跑步鞋、篮球鞋或徒步鞋，不进入主动偏好问题。
10. 跑步鞋问题不包含数码属性；手机问题不包含尺码或肤质。
11. 白名单之外的新增子品类即使超过三款也直接推荐。
12. 提问轮在 SQLite 保存完成前不发送文本，且不调用任何检索或模型评估依赖。

## 回滚与兼容

- 本功能没有数据库表迁移、商品数据迁移或外部状态写入。
- 行为回滚时先移除/关闭 `decide_proactive_clarification` 的 ask 路径，恢复直接编译；
  暂时保留 `missing_preferences` enum 的读取和“恢复原搜索”逻辑，使已有 pending 会话能够
  自然清除。
- 所有 `missing_preferences` pending 清除后，才可在后续版本删除兼容 enum；立即回退到
  完全不认识该 enum 的旧二进制可能使这些会话读取失败。
- `skip_preference_question` 不持久化到快照，因此关闭功能后不会改变现有检索结果。

## 外部依赖

- 不新增第三方依赖、API key、MCP、CLI 或服务。
- 确定性与 HTTP 测试使用现有 fakes，不需要 DashScope 或 Qdrant。
- live 验收继续依赖现有 DashScope 配置和健康的 Qdrant；两者只用于验证现有生产链路，
  不是新功能引入的依赖。
