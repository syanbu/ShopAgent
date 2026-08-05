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

不引入 Retrofit（对 SSE 无帮助）、Room（Phase 3 再评估）。

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
│       │   └── ChatRepository.kt       # 事件流 → 消息聚合
│       ├── domain/
│       │   └── ChatMessage.kt          # 消息模型（见下）
│       ├── ui/
│       │   ├── ChatViewModel.kt        # StateFlow<ChatUiState>
│       │   ├── ChatScreen.kt           # LazyColumn 对话列表 + 输入栏
│       │   └── components/
│       │       ├── MessageBubble.kt    # 用户/助手气泡 + 流式文本
│       │       ├── ProductCard.kt      # 图片/标题/品牌/双价格 + SkuStack
│       │       └── SkuStack.kt         # SKU 堆叠组件（见下）
│       └── MainActivity.kt
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

## SKU 堆叠组件（SkuStack）

- 折叠态：Compose `Box` 中多张 SKU 卡按索引做 `offset(y = index * 6.dp)` + 轻微旋转（`-2°..2°` 交替）+ 缩放（`1 - index * 0.03`），只完整露出顶层卡（显示 `properties` 摘要和 `price`），边缘露出下层形成堆叠感；右上角角标显示 SKU 数量。
- 展开态：点击整叠，`AnimatedVisibility` 展开为竖向列表，每卡展示 `properties` 全部键值对 + `price`；再次点击或点击空白处收起。
- 单 SKU 时退化为普通单卡，不做堆叠。
- 不做拖拽翻牌手势（首版范围外）。

## 商品卡片（ProductCard）

- 横向一行多张卡片（`LazyRow`），按 `rank` 排序。
- 卡片内容：Coil 加载 `image_url`（`null` 时用占位图）、标题、品牌、`display_price` 为主价格，`base_price` 不同则以删除线小字展示。
- 卡片下方嵌 SkuStack。

## 阶段拆分

### Phase 1：对话壳 + 流式文本（独立可用）

- 工程脚手架：Gradle 模块、依赖、debug 网络配置。
- DTO + ChatApi（okhttp-sse）+ ChatRepository。
- ChatViewModel + ChatScreen：消息列表、输入栏、流式文本、错误条 + 重试。
- 验收：模拟器发送"推荐一款适合油皮的洗面奶"，文本逐段流出；断网/500 时错误提示与重试正常。

### Phase 2：商品卡片 + SKU 堆叠

- ProductCard + ProductCardRow + SkuStack。
- Coil 图片加载与占位图。
- 验收：同一请求下卡片先于文本出现、多 SKU 商品堆叠可展开、`image_url=null` 时占位图正常。

### Phase 3：打磨

- 会话历史进程内保持 + 冷启动空态。
- 加载态（发送后等待 `message_start`）、流式光标动画。
- 视情况引入 Room 持久化（单独评估，不在本期承诺）。

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
