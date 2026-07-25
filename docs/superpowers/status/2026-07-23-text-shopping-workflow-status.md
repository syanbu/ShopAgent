# 单轮文本导购工作流开发状态

> 保存日期：2026-07-23
>
> 实施计划：`docs/superpowers/plans/2026-07-22-text-shopping-workflow.md`
>
> 当前结论：Task 1–8 已完成；单轮文本导购工作流已通过确定性验证、真实服务冒烟和手工 SSE 验收。

## 总体进度

| Task | 状态 | 说明 |
|---|---|---|
| Task 1：Python 项目基础与核心 Schema | 已完成 | TDD、静态检查和独立评审通过 |
| Task 2：商品目录与图片访问 | 已完成 | 真实数据集 100 个商品加载通过，路径安全和 SKU 预算筛选评审通过 |
| Task 3：确定性证据切分 | 已完成 | Chunk 内容、顺序、来源路径和 UUID5 公式测试及评审通过 |
| Task 4：DashScope/Qdrant 网关与离线索引器 | 已完成 | 单元测试、静态检查和真实本地 Qdrant 集成验收通过 |
| Task 5：召回聚合、证据校验与候选决策 | 已完成 | 召回、聚合、重排绑定、结构化校验、证据白名单和候选选择测试通过 |
| Task 6：LangGraph 工作流与自定义流 | 已完成 | 条件路由、商品优先事件、文本增量流和事实白名单提示词测试通过 |
| Task 7：FastAPI SSE、图片与健康接口 | 已完成 | SSE 顺序、部分失败、取消传播、图片安全和 readiness 测试通过 |
| Task 8：完整验证、真实冒烟与文档同步 | 已完成 | 124 个非 live 测试、真实 DashScope/Qdrant 流程和手工 SSE 验收通过 |

## 已实现内容

### Task 1

- 建立 Python 3.11、uv、`pyproject.toml`、`uv.lock` 和 `src/shop_agent` 包结构。
- 建立配置、错误、商品、意图、检索、SSE 事件和 LangGraph 状态 Schema。
- 删除 uv 自动生成但无实现目标的 `[project.scripts]`，避免暴露无效 CLI。
- `.env.example`、`.gitignore` 和功能文档保持同步。

验证结果：

- Task 1 聚焦测试：4 passed。
- Ruff：通过。
- mypy：通过。
- 独立规格与质量复审：Approved。

### Task 2

- 实现 `ProductCatalog`：加载并校验 JSON、拒绝重复商品 ID 和空数据集。
- 实现商品源文件相对路径、图片安全路径解析和 SKU 价格闭区间筛选。
- 仓库真实数据只读核验：100 个 JSON、100 个不同商品 ID、无越界图片路径、无缺失图片。

验证结果：

- Task 2 聚焦测试：3 passed。
- Ruff：通过。
- mypy：通过。
- 独立规格与质量复审：Approved。

### Task 3

- 实现商品概览、官方 FAQ 和用户评价 Chunk 构建。
- `chunk_id`、生成顺序、文本格式和 `source_path` 均为确定性输出。
- `point_id` 严格使用 `str(uuid5(NAMESPACE_URL, chunk_id))`。

验证结果：

- Task 3 聚焦测试：4 passed。
- Ruff：通过。
- mypy：通过。
- 独立规格与质量复审：Approved。

### Task 4

- 建立模型服务 Protocol。
- 实现 Qwen3.7-Max 意图解析、证据映射与流式回答网关。
- 实现 `qwen3.7-text-embedding` 的 document/query 区分和 1024 维稠密向量校验。
- 实现 `qwen3-rerank` 调用和成功响应的完整边界校验。
- 实现 Qdrant collection、payload index、结构化过滤、搜索 payload 校验和批量 upsert。
- 实现离线索引 CLI 和 Qdrant Compose 配置。
- 修复成功状态畸形 SDK 响应的错误映射、embedding `text_index` 重排和测试集合精准清理。

验证结果：

- Task 4 聚焦单元测试：44 passed。
- 当前完整单元测试：60 passed。
- Ruff：通过。
- mypy：通过。
- `docker compose config -q`：通过。
- 独立规格与质量复审：Approved。
- 首次真实 Qdrant integration：`1 skipped`，当时不能视为通过。

后续已补做真实本地 Qdrant 验收：

- Qdrant Compose 容器健康检查：通过。
- 真实 Qdrant integration：1 passed。

### Task 5

- 实现查询向量召回和结构化 Qdrant 过滤透传。
- 实现按商品聚合、每商品最多五条高分证据和最多十个重排候选。
- 实现重排文档构建、返回索引绑定和本次查询内的分数排序。
- 使用 catalog 重新校验类目、子类目、品牌和 SKU 价格。
- 实现模型证据 ID 白名单、冲突证据日志和三态语义准入；`supported`、`unknown` 保留，`contradicted` 淘汰。
- 实现最多三个候选的确定性选择、决定性证据筛选和精确 SKU ID 输出。

验证结果：

- Task 5 聚焦测试：15 passed。
- 当前全部非 live 测试：76 passed。
- Ruff：通过。
- Ruff 格式检查：通过。
- mypy：通过。

### Task 6

- 建立无 checkpointer 的 LangGraph 单轮工作流和显式条件边。
- 非购物、零召回和证据为空路径均跳过不需要的下游服务。
- 候选决策从 catalog 重新组装商品事实，并在生成文本前发出 `product` custom event。
- 回复节点只向模型提供匹配 SKU、决定性证据和选中商品字段，所有模式统一禁止库存、优惠、优惠券、购买链接和白名单外事实。
- 支持注入 ID 工厂，在初始状态缺少请求或会话标识时确定性补齐。
- 将最终商品数量配置限制为 1–3，避免配置突破商品卡片上限。

验证结果：

- Task 6 聚焦测试：9 passed。
- 当前全部非 live 测试：87 passed。
- Ruff：通过。
- Ruff 格式检查：通过。
- mypy：通过。

### Task 7

- 建立可注入依赖的 FastAPI 应用，并在生产 lifespan 中延迟装配真实服务。
- 实现 `POST /api/v1/chat/stream`，固定 start/custom/error/end 的 SSE 映射与防缓冲响应头。
- 服务错误按商品事件是否已发送区分 `failed` 和 `partial`；取消会直接传播，不补发事件。
- 实现 catalog 约束下的商品图片接口，未知商品或图片缺失统一安全返回 404。
- 实现 catalog、模型配置和 Qdrant collection 的 readiness 检查，未就绪返回 503。

验证结果：

- Task 7 聚焦测试：17 passed。
- 当前全部非 live 测试：104 passed。
- Ruff：通过。
- Ruff 格式检查：通过。
- mypy：通过。

### Task 8

- 添加默认跳过、仅在 `RUN_LIVE_TESTS=1` 时启用的真实购物流程测试。
- 真实索引 100 个商品的数据集，并验证商品事件与 catalog 的标题、品牌、价格、SKU 和图片来源一致。
- 验证“你好”不调用召回服务；验证“500 元以内，不要入耳式”的返回结果具备本商品有效证据。
- 修正 `.env` 中不完整的聊天模型名，并为意图解析注入 catalog taxonomy；越界类目不再形成错误的严格过滤。
- 本地 Qdrant loopback 客户端不继承环境代理，避免本地连接被错误转发。
- 推送前审查补强 supported 证据非空约束、taxonomy 父子组合规范化、空生成失败语义和 system role 事实边界。
- Compose 将无认证 Qdrant 限制在 `127.0.0.1:6333`，健康检查要求集合非空且向量配置匹配。
- 聊天入口限制会话标识长度，上游错误不向 SSE 暴露原始诊断，测试客户端过滤终端控制字符。
- 手工购物 SSE 返回 2 个商品事件后再输出文本；非购物 SSE 无商品事件；两者均以唯一 completed `message_end` 结束。
- 按用户明确决定不恢复根目录 `README.md`，运行说明统一维护在功能文档。

验证结果：

- 当前全部非 live 测试：124 passed，1 live test deselected。
- 真实 DashScope/Qdrant live test：1 passed in 65.42s。
- Ruff：通过。
- Ruff 格式检查：通过。
- mypy：通过。
- `docker compose config -q`：通过。
- 手工 SSE 验收：通过。

## 当前阻断

无。

## 后续建议

1. 在开始多轮、澄清、图片理解或评分能力前创建新的功能设计，不扩展当前单轮状态语义。
2. 固定 Qdrant 镜像 tag 或 digest，并在确定部署环境后补充持续集成配置。

## 已知非阻断项

- `compose.yaml` 当前使用 `qdrant/qdrant:latest`；后续确定支持版本后应固定 tag，最好固定 digest。
- `src/shop_agent/cli/index_products.py` 的索引存储类型边界含 `Any`，后续可改为专用 Protocol。
- Task 2 可进一步补充重复 ID、空目录、图片越界和价格等值边界测试。

## 约束提醒

- 商品 JSON 始终是唯一事实源。
- 不得在第一阶段加入多轮合并、澄清、图片理解、SQLite checkpoint 或七维加权评分。
- 所有模型生成 JSON 必须通过 Pydantic 后才能进入图状态。
- 商品卡片必须由 catalog 组装，不能从生成文本解析。
- 功能文档状态已在 Task 8 全量验证完成后改为“已完成”。
- 所有 Git 操作均在用户明确授权推送后执行。
