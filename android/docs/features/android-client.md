# 安卓客户端（流式对话导购）

> 状态：提议
>
> 代码入口：尚未创建，规划见 [../plan.md](../plan.md)

## 功能目标

为 ShopAgent 提供移动端导购入口，交互形态类似豆包 APP：用户以自然语言发起多轮对话，客户端通过 SSE 流式接口消费后端响应，将大模型推荐文本逐段渲染（打字机效果），并把先到的结构化商品事件渲染为商品卡片。商品卡片展示标题、品牌、价格和图片；一个商品包含多个 SKU 时，以堆叠卡片形式展示，可展开查看全部 SKU。

## 范围

包含：

- 纯文本多轮对话：发送用户消息，流式渲染助手回复，保存 `conversation_id` 维持多轮条件继承。
- 商品卡片展示：标题、品牌、双价格（`display_price` / `base_price`）、图片（含占位图）。
- SKU 堆叠组件：多 SKU 折叠堆叠、点击展开、单 SKU 退化为普通卡片。
- 错误处理：错误提示、`retryable` 错误重试、`status=partial` 时保留已收到的卡片与文本。

不包含：

- 图片上传（后端 `ChatRequest` 只接受文本）。
- 库存、优惠券、购买链接、活动信息展示（数据集与后端均不提供）。
- SKU 拖拽翻牌手势（首版范围外）。
- 会话持久化（Phase 3 视情况评估 Room，不在本期承诺）。
- 后端任何改动（只消费现有 SSE 协议）。

## 外部行为

- 发送消息后立即插入用户气泡和空助手占位；SSE 事件按序更新占位消息。
- `product` 事件先于文本到达，商品卡片先行渲染；`text_delta` 逐段追加，Compose 重组形成打字机效果。
- `message_end` 按 `status` 将消息置为 Done / Partial / Failed；`status=partial` 时已收到的商品卡片和文本仍然有效，保留展示。
- 收到 `error` 事件后流仍会发出 `message_end`；`retryable=true` 时 UI 显示重试按钮。
- `image_url` 为 `null`（图片文件不存在）时展示占位图。
- 断网 / 服务端 5xx 时提示错误，支持重试。

## 接口与数据

客户端只消费后端既有接口，详见 [../background.md](../background.md)：

- `POST /api/v1/chat/stream`：SSE 流式对话，事件序列 `message_start` → `product` × 0..3 → `text_delta` × 1..N → `message_end`，异常时插入 `error`。
- `GET /api/v1/products/{product_id}/image`：商品图片，客户端按 `image_url` 直接加载。
- `GET /health`：依赖就绪状态，可用于启动时连通性自检。

核心数据结构（与 `src/shop_agent/models/events.py` 对齐）：

- DTO：`ChatRequest`、`SseEvents`（message_start / product / text_delta / error / message_end）。
- 领域模型：`ChatMessage` sealed interface（User / Assistant），Assistant 聚合 `products`、`text`、`status`、`error`。

后端地址通过 `buildConfigField` 注入（debug 默认 `http://10.0.2.2:8000`）；debug 构建的 `networkSecurityConfig` 放行明文 HTTP，release 不携带。图片可达性依赖后端 `PUBLIC_BASE_URL` 配置为客户端可达地址。

## 关键决策

- 技术选型：Kotlin + Jetpack Compose（Material 3）+ Coroutines/Flow + OkHttp（okhttp-sse）+ kotlinx.serialization + Coil；MVVM，手动依赖注入（单 Repository + ViewModelFactory，不引入 Hilt）；minSdk 26。不引入 Retrofit（对 SSE 无帮助）、Room（Phase 3 再评估）。
- SSE 事件回调由 Repository 转为 Flow，ViewModel 收集后 reduce 进 `ChatUiState`，单向数据流。
- 不手写 SSE 帧解析，使用 okhttp-sse 官方 EventSource；OkHttp 关读超时（`readTimeout(0)`），依赖后端 15s ping 保活。
- 商品卡片不展示结构化描述字段（后端不下发），描述性内容完全依赖流式推荐文本。
- 流式期间按消息 id 更新列表项，避免 LazyColumn 全量重组。

## 代码与验证

代码尚未创建，目录结构规划见 [../plan.md](../plan.md)：`data/`（ChatApi、dto、ChatRepository）、`domain/`（ChatMessage）、`ui/`（ChatViewModel、ChatScreen、ProductCard、SkuStack 等组件）。

验证方式（按 [../plan.md](../plan.md) 测试计划）：

- 单元测试：SSE 事件 JSON → DTO 反序列化（含 `image_url=null`、多 SKU、各错误码）；Repository 事件聚合（product 先于 text_delta、error 后接 message_end、partial 保留已收内容）；ViewModel 状态迁移（发送 → Streaming → Done/Partial/Failed、retryable 重试）。
- 手动验收：后端 `docker compose up -d` + uvicorn 起服务，模拟器跑通各 Phase 验收项（流式文本、卡片先于文本、SKU 堆叠展开、占位图、错误重试）。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-08-05 | 创建功能文档；背景与计划文档迁入 `android/docs/` | 客户端文档独立成册，仿照后端 docs 结构组织 |
