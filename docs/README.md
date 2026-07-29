# 项目文档索引

这里是项目文档的统一入口。先阅读项目背景，再按功能索引打开与当前任务相关的文档。没有进入当前任务的功能文档不需要预先读取。

## 项目文档

| 内容 | 文档 |
|---|---|
| 项目背景、范围与技术选型 | [background.md](background.md) |
| 课题原始说明 | [shopAgentDescription.md](shopAgentDescription.md) |
| 功能文档约定 | [features/README.md](features/README.md) |
| 功能文档模板 | [features/_template.md](features/_template.md) |

## 功能索引

功能开发开始时，在下表登记对应文档。状态使用“提议”“开发中”“已完成”或“已废弃”。

| 功能 | 状态 | 功能文档 | 代码入口 |
|---|---|---|---|
| 单轮文本商品推荐工作流（含性价比价格编译） | 已完成 | [features/text-shopping-workflow.md](features/text-shopping-workflow.md) | `src/shop_agent/models/`, `src/shop_agent/catalog.py`, `src/shop_agent/chunking.py`, `src/shop_agent/services/query_compiler.py`, `src/shop_agent/workflow/`, `src/shop_agent/api/`, `src/shop_agent/cli/index_products.py`, `scripts/chat_client.py`, `tests/live/test_live_shopping_flow.py`, `compose.yaml` |
| 跨品类商品约束与 SKU 匹配 | 已完成 | [features/cross-category-shopping-constraints.md](features/cross-category-shopping-constraints.md) | `src/shop_agent/models/query.py`、`src/shop_agent/sku_attributes.py`、`src/shop_agent/catalog.py`、`src/shop_agent/services/dashscope_chat.py`、`src/shop_agent/services/evidence.py`、`src/shop_agent/workflow/` |
| 多轮 Query 编译与指代消解 | 开发中 | [features/multi-turn-query-engine.md](features/multi-turn-query-engine.md) | `src/shop_agent/models/turn_query.py`、`src/shop_agent/models/conversation.py`、`src/shop_agent/services/ports.py`、`src/shop_agent/services/conversation_repository.py`、`src/shop_agent/services/reference_resolver.py`、`src/shop_agent/services/multi_turn_query_compiler.py`、`src/shop_agent/services/dashscope_chat.py`、`src/shop_agent/services/retrieval.py`、`src/shop_agent/services/qdrant_store.py`、`src/shop_agent/workflow/nodes.py`、`src/shop_agent/workflow/graph.py`、`src/shop_agent/api/dependencies.py`、`src/shop_agent/api/chat.py`、`tests/unit/test_model_gateways.py`、`tests/unit/test_reference_resolver.py`、`tests/unit/test_multi_turn_workflow.py`、`tests/integration/test_chat_api.py`、`tests/live/test_live_shopping_flow.py` |

## 更新规则

- 新增用户能力、Agent 分支、API、数据结构或存储方案时，新建功能文档并登记索引。
- 修改已有功能时，更新原有功能文档，不重复新建。
- 小型修复、内部重构和补充测试不单独建文档。外部行为或关键决策发生变化时，更新所属功能文档。
- 功能文档与代码在同一次修改中更新。
- 变更记录只保留影响行为、接口或设计决策的内容，不复制 Git 提交日志。
