# 单轮文本导购工作流开发状态

> 保存日期：2026-07-23
>
> 实施计划：`docs/superpowers/plans/2026-07-22-text-shopping-workflow.md`
>
> 当前结论：暂停开发；下次从 Task 5 开始，但应先补做 Task 4 的真实 Qdrant 集成验收。

## 总体进度

| Task | 状态 | 说明 |
|---|---|---|
| Task 1：Python 项目基础与核心 Schema | 已完成 | TDD、静态检查和独立评审通过 |
| Task 2：商品目录与图片访问 | 已完成 | 真实数据集 100 个商品加载通过，路径安全和 SKU 预算筛选评审通过 |
| Task 3：确定性证据切分 | 已完成 | Chunk 内容、顺序、来源路径和 UUID5 公式测试及评审通过 |
| Task 4：DashScope/Qdrant 网关与离线索引器 | 代码完成，外部验收未完成 | 单元测试和代码评审通过；Docker Desktop Linux daemon 未运行，真实 Qdrant 集成测试被跳过 |
| Task 5：召回聚合、证据校验与候选决策 | 未开始 | 下次主要开发起点 |
| Task 6：LangGraph 工作流与自定义流 | 未开始 | 等待 Task 5 |
| Task 7：FastAPI SSE、图片与健康接口 | 未开始 | 等待 Task 6 |
| Task 8：完整验证、真实冒烟与文档同步 | 未开始 | 等待 Task 5–7 |

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
- 真实 Qdrant integration：`1 skipped`，不能视为通过。

## 当前阻断

Docker Desktop Linux daemon 未运行。执行 `docker compose up -d qdrant` 时失败：

```text
failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine
The system cannot find the file specified.
```

因此以下真实行为尚未验收：

- 创建唯一测试 collection。
- 创建 payload indexes。
- upsert 确定性测试向量。
- 按品牌和价格过滤。
- 查询结果解析。
- 精准删除唯一测试 collection。

## 下次恢复步骤

1. 启动 Docker Desktop，并确认 Linux engine 正常。
2. 在仓库根目录执行：

```powershell
docker compose up -d qdrant
uv run pytest tests/integration/test_qdrant_store.py -q
```

3. 只有集成测试真实通过后，才把 Task 4 标记为完全完成。
4. 按计划继续 Task 5，并保持 TDD 顺序：先写测试、确认 RED、最小实现、聚焦验证、独立复审。
5. 依次完成 Task 6、Task 7 和 Task 8。
6. Task 8 的真实 DashScope 冒烟需要有效的北京地域 `DASHSCOPE_API_KEY`；没有真实 Key 时不得把 live test 标记为通过。

## 已知非阻断项

- `compose.yaml` 当前使用 `qdrant/qdrant:latest`；后续确定支持版本后应固定 tag，最好固定 digest。
- `src/shop_agent/cli/index_products.py` 的索引存储类型边界含 `Any`，后续可改为专用 Protocol。
- Task 2 可进一步补充重复 ID、空目录、图片越界和价格等值边界测试。

## 约束提醒

- 商品 JSON 始终是唯一事实源。
- 不得在第一阶段加入多轮合并、澄清、图片理解、SQLite checkpoint 或七维加权评分。
- 所有模型生成 JSON 必须通过 Pydantic 后才能进入图状态。
- 商品卡片必须由 catalog 组装，不能从生成文本解析。
- 功能文档状态继续保持“开发中”；只有 Task 8 全量验证完成后才能改为“已完成”。
- 截至本状态文件写入前，实施过程中未执行任何 Git 命令或 Git 操作。
