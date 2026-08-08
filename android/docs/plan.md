# 安卓客户端实施计划

> 背景、接口契约与约束见 [background.md](background.md)。本文档是代码实施计划，按阶段拆分，每个阶段独立可用。

## 技术选型

| 模块 | 选型 | 理由 |
|---|---|---|
| 语言 | Kotlin | Android 官方语言 |
| UI | Jetpack Compose + Material 3 | 聊天列表 + 流式文本重组是最短路径 |
| 异步 | Kotlin Coroutines + Flow | ViewModel 暴露 `StateFlow` 驱动 UI |
| 网络 | OkHttp + okhttp-sse | 官方 EventSource 实现，免手写 SSE 帧解析 |
| JSON | kotlinx.serialization | Kotlin 官方序列化，与协程体系一致 |
| 图片 | Coil | Compose 原生支持 |
| 架构 | MVVM，手动依赖注入 | 单 Repository + ViewModelFactory，不引入 Hilt |
| 最低版本 | minSdk 26 | 覆盖主流设备，协程/Compose 无兼容负担 |

不引入 Retrofit（对 SSE 无帮助）。Room 已在会话持久化阶段引入（KSP 编译，见 `data/local/`）。

## 目录结构

```
android/
├── app/
│   └── src/main/java/com/shopagent/
│       ├── data/
│       │   ├── ChatApi.kt              # okhttp-sse 事件流封装
│       │   ├── dto/                    # 与后端事件对齐的 DTO
│       │   │   ├── ChatRequest.kt
│       │   │   └── SseEvents.kt        # message_start/product/text_delta/error/message_end
│       │   ├── local/                  # Room 会话持久化
│       │   │   ├── AppDatabase.kt
│       │   │   ├── Entities.kt         # conversations / messages 两张表
│       │   │   ├── ConversationDao.kt
│       │   │   ├── ConversationStore.kt    # 持久化接口（测试用内存 fake）
│       │   │   └── RoomConversationStore.kt
│       │   └── ChatRepository.kt       # 事件流 → 消息聚合
│       ├── domain/
│       │   └── ChatMessage.kt          # 消息模型（见下）
│       ├── ui/
│       │   ├── ChatViewModel.kt        # StateFlow<ChatUiState>
│       │   ├── ChatScreen.kt           # LazyColumn 对话列表 + 悬浮输入栏
│       │   ├── AppDrawer.kt            # 侧边抽屉（功能项/历史会话占位/用户行）
│       │   └── components/
│       │       ├── MessageBubble.kt    # 用户/助手气泡 + 流式文本
│       │       ├── ProductCard.kt      # 图片/标题/品牌/双价格 + SKU 数量提示，点击弹详情
│       │       ├── ProductDetailSheet.kt # 商品详情 BottomSheet（见下）
│       │       └── FloatingInputBar.kt # 悬浮无边框输入胶囊
│       └── MainActivity.kt             # ModalNavigationDrawer + TopAppBar
└── app/src/test/                       # 单元测试
```

后端地址通过 `buildConfigField` 注入（debug 默认 `http://10.0.2.2:8000`），debug 的 `networkSecurityConfig` 放行明文 HTTP。

## 消息模型

```kotlin
sealed interface ChatMessage {
    data class User(val id: String, val text: String) : ChatMessage
    data class Assistant(
        val id: String,
        val products: List<ProductCard>,   // product 事件聚合，先到达
        val text: String,                  // text_delta 逐段追加
        val status: Status,                // Streaming / Done / Partial / Failed
        val error: ChatError? = null,      // error 事件，含 retryable
    ) : ChatMessage
}
```

- 发送消息时立即插入 User 消息和空 Assistant 占位；SSE 事件按序更新占位消息。
- `product` 事件追加到 `products`；`text_delta` 追加到 `text`，Compose 自动重组形成打字机效果。
- `message_end` 按 `status` 置为 Done/Partial/Failed；`error` 事件记录到 `error`，`retryable=true` 时 UI 显示重试按钮。
- `conversation_id` 存 ViewModel，后续请求携带。

## 数据流

```
ChatScreen (Compose)
    ↑ StateFlow<ChatUiState>
ChatViewModel ── send(text) ──→ ChatRepository
                                    ↓ okhttp-sse EventSource
                              FastAPI /api/v1/chat/stream
Coil AsyncImage ←── image_url (GET /api/v1/products/{id}/image)
```

单向数据流，无环。Repository 把 SSE 事件回调转为 Flow，ViewModel 收集后reduce 进 `ChatUiState`。

## 商品详情弹窗（ProductDetailSheet）

- 点击商品卡片弹出 `ModalBottomSheet`，下滑或点击空白处关闭；弹出状态由 `ProductCardRow` 持有（`selectedProduct`）。
- 内容纵向可滚动：商品大图（4:3 裁切，加载失败用占位图）、标题、品牌、描述（`description` 占位字段，未下发时显示"暂无商品描述"）、`display_price` 主价格 + `base_price` 删除线、完整 SKU 列表（每条展示 `properties` 全部键值对摘要 + `price`）。
- SKU 不在卡片内联展开（曾用 AnimatedVisibility 内联展开，会撑高卡片、顶开聊天列表，已废弃）。

## 商品卡片（ProductCard）

- 横向一行多张卡片（`LazyRow`），按 `rank` 排序。
- 卡片内容：Coil 加载 `image_url`（`null` 时用占位图）、标题、品牌、`display_price` 为主价格，`base_price` 不同则以删除线小字展示；有 SKU 时底部显示"共 N 个 SKU，点击查看"提示。
- 点击卡片弹出 ProductDetailSheet。

## 阶段拆分

### Phase 1：对话壳 + 流式文本（独立可用）

- 工程脚手架：Gradle 模块、依赖、debug 网络配置。
- DTO + ChatApi（okhttp-sse）+ ChatRepository。
- ChatViewModel + ChatScreen：消息列表、输入栏、流式文本、错误条 + 重试。
- 验收：模拟器发送"推荐一款适合油皮的洗面奶"，文本逐段流出；断网/500 时错误提示与重试正常。

### Phase 2：商品卡片 + SKU 堆叠

- ProductCard + ProductCardRow + ProductDetailSheet。
- Coil 图片加载与占位图。
- 验收：同一请求下卡片先于文本出现、点击卡片弹出详情 BottomSheet 展示完整 SKU 列表、`image_url=null` 时占位图正常。

### Phase 3：打磨

- 会话历史进程内保持。
- 加载态（发送后等待 `message_start`）、流式光标动画。

### Phase 4：会话持久化（已完成）

- 引入 Room（KSP），本地保存会话与消息；抽屉历史会话列表点击载入，恢复 `conversation_id` 可继续聊。
- 冷启动自动恢复最近会话（历史列表按 `updatedAt` 倒序，首条即最近会话）；无历史时为空白新会话。
- 验收：杀进程重启后历史会话仍在且自动进入最近会话；打开历史会话消息完整（含商品卡片）；在历史会话中发送消息携带原 `conversation_id`。

## 测试计划

- 单元测试：
  - SSE 事件 JSON → DTO 反序列化（含 `image_url=null`、多 SKU、各错误码）。
  - Repository 事件聚合：product 先于 text_delta、error 后接 message_end、partial 保留已收内容。
  - ViewModel 状态迁移：发送 → Streaming → Done/Partial/Failed、retryable 重试。
- 手动验收：后端 `docker compose up -d` + uvicorn 起服务，模拟器跑通 Phase 1/2 验收项。

## 风险与注意

- `PUBLIC_BASE_URL` 配错会导致图片全挂而文本正常——排查时先看图片域名。
- SSE 长连接需关 OkHttp 读超时（`readTimeout(0)`），并依赖后端 15s ping 保活。
- 流式期间按消息 id 更新列表项，避免 LazyColumn 全量重组。
