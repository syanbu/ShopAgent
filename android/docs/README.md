# 安卓客户端文档索引

这里是 ShopAgent 安卓客户端文档的统一入口。先阅读项目背景，再按功能索引打开与当前任务相关的文档。后端 Python Agent 的文档见仓库根目录 [docs/README.md](../../docs/README.md)。

## 项目文档

| 内容 | 文档 |
|---|---|
| 客户端背景、后端接口契约与开发约束 | [background.md](background.md) |
| 代码实施计划（技术选型、目录结构、阶段拆分） | [plan.md](plan.md) |
| 后端功能文档约定（客户端功能文档同样遵守） | [../../docs/features/README.md](../../docs/features/README.md) |

## 功能索引

功能开发开始时，在下表登记对应文档。状态使用"提议""开发中""已完成"或"已废弃"。

| 功能 | 状态 | 功能文档 | 代码入口 |
|---|---|---|---|
| 安卓客户端（流式对话导购、商品卡片、SKU 堆叠） | 开发中 | [features/android-client.md](features/android-client.md) | `app/src/main/java/com/shopagent/`（`MainActivity.kt`、`ui/ChatScreen.kt`、`ui/ChatViewModel.kt`、`data/ChatRepository.kt`、`data/ChatApi.kt`） |

## 更新规则

- 遵守仓库根目录 `docs/features/README.md` 的功能文档约定；客户端功能文档放在 `android/docs/features/` 下。
- 新增客户端能力时，新建功能文档并登记本索引。
- 修改已有功能时，更新原有功能文档，不重复新建。
- 功能文档与代码在同一次修改中更新。
- 变更记录只保留影响行为、接口或设计决策的内容，不复制 Git 提交日志。
