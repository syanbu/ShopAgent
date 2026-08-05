# 场景化组合推荐实施状态

> 更新时间：2026-08-05
>
> 功能状态：已完成（后端自动化与真实服务验收通过；前端联调未执行）
>
> 设计文档：[场景化组合推荐](../../features/scenario-combination-recommendation.md)
>
> 实施计划：[2026-08-04 场景化组合推荐实施计划](../plans/2026-08-04-scenario-combination-recommendation.md)

## 已实现

- `config/scenario_recipes.json` 提供六个审核模板；Pydantic Registry 在依赖构建时校验
  ID、别名、槽位、数量上限和 Catalog 精确范围。
- `TurnQuery` 增加互斥的 `scenario_recommendation + ScenarioRequest`；DashScope 解析器
  接收紧凑 recipe 白名单，并校验 recipe、原始场景文本和未映射需求。
- 对包含“一套 / 整套 / 搭配 / 组合 / 配齐”等明显组合形态的新请求，解析器先调用短提示
  recipe gate；gate 只从审核模板选择或拒绝，未选中/失败时回落完整多轮解析，场景续接和
  待澄清轮次不重复调用。宽泛的“学习和生活用品”由模板整体覆盖，不记为未映射具体类型。
- `ConversationState` 升级为 v2，增加互斥的普通/场景活动任务和 `ScenarioSnapshot`；v1
  JSON 在读取边界自动升级，SQLite 表结构不变。
- `ScenarioRecommendationService` 按必选优先、模板顺序逐槽检索；同槽多范围合并后统一
  重排，越界 Catalog 商品在证据判断前丢弃，每槽只选择一件商品。
- LangGraph 在 `route_turn` 后增加独立场景分支，不经过普通 `QuerySnapshot` 编译链。
  完整组合先保存，再发送连续 `product` 事件；公开请求体、事件名和
  `ProductEventData` 字段没有变化。
- 场景上下文中的纯 `more_results` 继承 recipe 并按全部历史商品整套排重；必选槽耗尽
  时不保存、不发送商品卡并保留上一套。带价格、偏好或局部修改的换套请求返回 v1
  能力边界说明。
- 普通推荐仍使用最多三件；场景默认最多六件，模型和服务硬上限为八件。
- 场景首轮与场景上下文中的换套都把 `turn_route` 准确记录为 `scenario`；新增
  `scenario_snapshot_compiled` 和 `scenario_bundle_built` 单行结构化日志，并明确排除用户原文、
  检索 query、证据正文和 SKU 明细。日志回归在修复前稳定复现错误 route，修复后通过。

## 已验证

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

确定性测试覆盖六模板、三亚五槽组合、构造型八件边界、必选槽原子失败、整套换新、
ConversationState v1 到 v2 迁移、SQLite round-trip、原 SSE 商品字段集合和场景五卡 HTTP
响应，以及短提示 gate 的命中、拒绝回落和审核 recipe 约束。

用户授权向 DashScope 发送当前 Catalog 商品材料和模拟查询后，执行：

```powershell
$env:RUN_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -q tests\live\test_live_shopping_flow.py -k scenario -s
# 1 passed, 21 deselected in 94.20s
```

该用例真实覆盖三亚五槽、开学六槽、开学整套换新、必选槽耗尽后保留上一套、显式太阳镜
需求零检索，以及普通“推荐防晒霜”仍走最多三件的普通分支。首次真实运行发现开学集合需求
被完整多轮提示误判为 `non_shopping`；增加受限 recipe gate 并修正宽泛集合词语义后复跑
通过。本地 Qdrant 在验收时健康。

真实 `scripts/chat_client.py` 也已使用固定 conversation ID 完成多轮检查：三亚首轮渲染
五卡后正确耗尽；开学首轮与换套轮各渲染六张互不重复商品卡，第三轮零卡耗尽；普通防晒
首轮保持三卡并沿用普通换批耗尽语义。首次客户端运行在 Windows GBK stdout 渲染 `¥`
时崩溃，已改为 ASCII `CNY`，新增 GBK writer 回归测试并复跑手工流程通过。

## 剩余联调边界

- 实际前端是否确实消费同一消息内四至八张连续商品卡；服务端 HTTP 测试只能证明协议可发。

这些事项不阻塞后端功能实现完成，但在实际客户端/前端接入前仍需联调；当前结论不是生产
发布验收。
