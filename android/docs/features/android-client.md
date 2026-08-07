# 安卓客户端（流式对话导购）

> 状态：开发中
>
> 代码入口：`app/src/main/java/com/shopagent/`（`MainActivity.kt`、`ui/ChatScreen.kt`、`ui/ChatViewModel.kt`、`ui/components/`、`data/ChatApi.kt`、`data/ChatRepository.kt`、`domain/ChatMessage.kt`），单元测试在 `app/src/test/java/com/shopagent/`

## 功能目标

为 ShopAgent 提供移动端导购入口，交互形态类似豆包 APP：用户以自然语言发起多轮对话，客户端通过 SSE 流式接口消费后端响应，将大模型推荐文本逐段渲染（打字机效果），并把先到的结构化商品事件渲染为商品卡片。商品卡片展示标题、品牌、价格和图片；一个商品包含多个 SKU 时，以堆叠卡片形式展示，可展开查看全部 SKU。

## 范围

包含：

- 纯文本多轮对话：发送用户消息，流式渲染助手回复，保存 `conversation_id` 维持多轮条件继承。
- 商品卡片展示：标题、品牌、双价格（`display_price` / `base_price`）、图片（含占位图）。
- SKU 堆叠组件：多 SKU 折叠堆叠、点击展开、单 SKU 退化为普通卡片。
- 错误处理：错误提示、`retryable` 错误重试、`status=partial` 时保留已收到的卡片与文本。
- 会话本地持久化：每轮结束自动落库（Room），抽屉历史会话列表可点开查看历史消息，并恢复原 `conversation_id` 继续多轮对话。

不包含：

- 图片上传（后端 `ChatRequest` 只接受文本）。
- 库存、优惠券、购买链接、活动信息展示（数据集与后端均不提供）。
- SKU 拖拽翻牌手势（首版范围外）。
- 历史会话的删除、重命名、搜索（后续按需补）。
- 后端任何改动（只消费现有 SSE 协议；历史记录完全存在客户端本地，后端不提供历史查询）。

## 外部行为

- 发送消息后立即插入用户气泡和空助手占位；SSE 事件按序更新占位消息。
- `product` 事件先于文本到达，商品卡片先行渲染；`text_delta` 逐段追加，Compose 重组形成打字机效果。
- `message_end` 按 `status` 将消息置为 Done / Partial / Failed；`status=partial` 时已收到的商品卡片和文本仍然有效，保留展示。
- 收到 `error` 事件后流仍会发出 `message_end`；`retryable=true` 时 UI 显示重试按钮。
- `image_url` 为 `null`（图片文件不存在）时展示占位图。
- 断网 / 服务端 5xx 时提示错误，支持重试。
- 导航为左侧抽屉（ModalNavigationDrawer）：顶栏菜单键或侧滑手势开启，含功能项（聊天/购物车/我的）、历史会话占位、底部用户行；点选后切换界面并自动收起。
- 顶栏固定为居中小字"导购助手"（CenterAlignedTopAppBar），不随 Tab 切换变化。
- 抽屉头部右侧为"新会话"按钮（加号图标）：点击后取消进行中的流、清空消息列表并复位 `conversation_id`，下次发送由后端分配新会话 id，与旧会话完全隔离；同时切回聊天页并收起抽屉。
- 历史会话列表展示本地保存的会话（标题取首条用户消息前 20 字 + 更新时间，按更新时间倒序，当前会话高亮）；点击后从本地载入完整消息（含商品卡片），恢复 `conversation_id` 与最后一条用户文本，可直接继续对话或重试失败消息。
- 会话在每一轮结束（completed/partial/failed）时整体覆盖落库；切换会话、新会话前也会兜底保存。流式中途杀进程会丢失当前轮的助手回复，恢复时流式状态按"回复不完整"处理。
- 长按用户消息气泡弹出操作菜单，支持复制消息文本到剪贴板。
- 等待助手回复时不显示"正在输入"文字，而是三点跳动动画（TypingIndicator）：无内容时在助手气泡内占位，流式输出中显示在文本下方。
- 主题色仿豆包：用户气泡高饱和蓝（`#2B6CF2`）、助手气泡浅灰（`#F2F3F5`）、页面白底，定义在 `ui/theme/Theme.kt`。
- 输入栏为悬浮无边框胶囊（阴影浮起、发送按钮内置右侧），键盘弹出时紧贴键盘上沿；消息列表为底部锚定（reverseLayout），视口收缩时最新消息尾部保持可见；无默认提示文字。

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
- 会话历史存客户端本地（Room，两张表：conversations + messages，商品卡片复用 SSE DTO 打成 JSON 列），不走后端——后端只有工作流状态存储且不提供历史查询接口，还原展示消息不可靠。`ConversationStore` 定义为接口（Room 实现 + 测试 fake），ViewModel 单测不依赖 Robolectric。
- 点开历史会话不引入 navigation-compose：本质是替换同一 ChatScreen 的状态（消息列表 + conversation_id），没有路由与回退栈需求。

## 代码与验证

代码位于 `app/src/main/java/com/shopagent/`，结构与 [../plan.md](../plan.md) 一致：`data/`（ChatApi、dto、ChatRepository）、`data/local/`（AppDatabase、Entities、ConversationDao、ConversationStore 接口 + RoomConversationStore）、`domain/`（ChatMessage）、`ui/`（ChatViewModel、ChatScreen、`components/` 下的 MessageBubble/ProductCard/SkuStack/FloatingInputBar/TypingIndicator）；另有 `ui/cart/CartScreen.kt`、`ui/profile/ProfileScreen.kt` 两个占位页。导航由 `MainActivity` 的左侧抽屉（ModalNavigationDrawer + CenterAlignedTopAppBar，抽屉内容在 `ui/AppDrawer.kt`）经状态切换实现，未引入 navigation-compose。

验证方式（按 [../plan.md](../plan.md) 测试计划）：

- 单元测试：SSE 事件 JSON → DTO 反序列化（含 `image_url=null`、多 SKU、各错误码）；Repository 事件聚合（product 先于 text_delta、error 后接 message_end、partial 保留已收内容）；ViewModel 状态迁移（发送 → Streaming → Done/Partial/Failed、retryable 重试）。
- 手动验收：后端 `docker compose up -d` + uvicorn 起服务，模拟器跑通各 Phase 验收项（流式文本、卡片先于文本、SKU 堆叠展开、占位图、错误重试）。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-08-05 | 创建功能文档；背景与计划文档迁入 `android/docs/` | 客户端文档独立成册，仿照后端 docs 结构组织 |
| 2026-08-05 | 实现 Phase 1 + Phase 2：流式对话、商品卡片、SKU 堆叠、底部导航三界面；状态由「提议」转「开发中」 | 首个可运行版本，单元测试覆盖 DTO 反序列化 / 事件聚合 / ViewModel 状态迁移 |
| 2026-08-05 | UI 改版：输入栏改悬浮无边框胶囊（FloatingInputBar），底部导航改左侧抽屉（ModalNavigationDrawer + AppDrawer，含历史会话与用户行占位） | 参照豆包交互形态；抽屉为后续历史会话功能预留入口 |
| 2026-08-06 | 顶栏改居中小字"导购助手"；用户气泡长按弹出复制菜单；"正在输入…"改三点跳动动画（TypingIndicator）；主题色改豆包蓝（`ui/theme/Theme.kt`） | 进一步对齐豆包交互与视觉；修复用户消息无法复制的可用性问题 |
| 2026-08-06 | 新增"新会话"功能：抽屉头部右侧加号按钮，`ChatViewModel.newConversation()` 清空消息并复位 `conversation_id`，新会话与旧会话按 id 隔离 | 会话隔离是多轮导购的基本需求；会话持久化与历史列表仍为占位，后续接入 |
| 2026-08-06 | 会话本地持久化落地：引入 Room（KSP），`data/local/` 两张表存会话与消息；抽屉"历史会话"占位换成真实列表，点击载入历史并恢复 `conversation_id` 可继续聊 | 历史记录需要重启后仍在；后端不提供历史查询，本地存储是唯一可靠来源 |
