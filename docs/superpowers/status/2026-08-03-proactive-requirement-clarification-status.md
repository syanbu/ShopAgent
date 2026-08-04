# Agent 主动需求澄清状态

> 保存日期：2026-08-04
>
> 功能文档：`docs/features/proactive-requirement-clarification.md`
>
> 实施计划：`docs/superpowers/plans/2026-08-03-proactive-requirement-clarification.md`
>
> 当前结论：本地实现与聚焦验证已完成，真实 DashScope/Qdrant 端到端验收尚未完成；
> 功能状态保持“开发中”。

## 已完成内容

- 新增 `proactive_clarification.py` 确定性策略服务，按八个精确子品类维护审核问题白名单。
- 只依据合并后的完整 `QuerySnapshot`、搜索意图、Catalog 商品数、展示上限和显式跳过
  标记决定是否反问，不依据本轮 LLM 字段数量。
- 新增 `TurnQuery.skip_preference_question`，限制在 `new_search`、`switch_category` 和
  `clarification_answer`，并与 `cancel_pending` 互斥。
- 新增 `PendingClarification.kind="missing_preferences"`，继续使用现有
  `ConversationState.state_json`，没有数据库表或列迁移。
- 在 `merge_query_snapshot` 与 `compile_effective_query` 之间接入
  `decide_proactive_clarification`；阻塞型澄清仍先于主动反问。
- 提问前先保存 pending，再发送固定文本；提问轮不进入检索、聚合、重排、证据或回答生成。
- 回答恢复复用现有 Query 编译：“拍照优先”进入软偏好，“预算 4000”进入最高价格硬约束。
- 跳过或无有效回答时恢复原搜索，并使用单次 `ShoppingState` 标记禁止第二次主动反问。
- 完成真实 SQLite repository 重建恢复测试，以及 HTTP/SSE 两轮、跳过和小目录集成测试。
- 保持 `POST /api/v1/chat/stream` 请求体、SSE 事件类型、商品事实和查询约束语义不变。

## 当前范围

首批覆盖当前 Catalog 中商品数超过三、且能够用真实资料区分的八个子品类：

- 数码电子：智能手机、真无线耳机、笔记本电脑、平板电脑。
- 美妆护肤：精华。
- 服饰运动：跑步鞋。
- 食品饮料：咖啡、方便食品。

其他子品类没有审核策略时继续直接推荐。面霜等商品数不超过三的子品类即使只有类目，
也不主动反问。

## 实施状态

- 策略服务：已实现。
- TurnQuery 跳过合同：已实现。
- `missing_preferences` pending：已实现。
- LangGraph 主动澄清分支：已实现。
- SQLite 跨 repository 恢复：已实现并通过聚焦测试。
- HTTP/SSE 集成：已实现并通过完整 API 集成测试。
- live 解析合同：已加入 opt-in 测试，尚未使用真实模型运行。
- 真实 Qdrant 端到端流程：尚未验证。

## 验证状态

- 确定性策略测试：24 个通过。
- TurnQuery、Conversation 与模型网关聚焦测试：168 个通过。
- 多轮工作流与路由测试：通过；包含 SQLite repository 重建恢复。
- HTTP/SSE 集成测试：30 个通过。
- 计划聚焦测试：327 个通过。
- 完整 pytest：560 个通过，22 个 live 测试因 opt-in 门槛跳过。
- Ruff：通过，`uv run ruff check .` 无问题。
- mypy：通过，`uv run mypy src scripts` 检查 41 个源码文件无问题。
- DashScope live 测试：未运行；密钥配置存在，但当前网络中断，不能验证其有效性或模型行为。
- Qdrant 真实流程：未运行；`http://127.0.0.1:6333` 当前不可用。
- 手工真实对话验收：未运行。

## 已知风险

- 问题策略与 Catalog 数据强绑定。目录更新后如果只增加商品而不审核问题策略，系统会
  继续直接推荐，这是刻意的安全降级。
- `missing_preferences` 是新的持久化 enum。完全回退到不认识该值的旧二进制前，需要先
  关闭 ask 路径并保留兼容读取，等待现有 pending 会话自然清除。
- 真实模型需要稳定地把偏好回答解析为语义操作、把明确预算解析为硬约束，并识别“先看看”
  的跳过语义；未完成 live 验收前不能标记功能完成。
- 当前问题模板基于现有数据集审核，不代表可以自动推广到未来新增子品类。

## 下一步

网络和 Qdrant 恢复后运行 opt-in live 测试和手工两轮对话。只有本地与真实端到端验收
全部通过后，才把功能和索引状态从
“开发中”改为“已完成”。
