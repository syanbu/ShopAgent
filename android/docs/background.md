# 安卓客户端背景与约束

> 本文档描述 ShopAgent 安卓客户端的项目背景、后端接口契约和开发约束。代码实施计划见 [plan.md](plan.md)。

## 项目背景

ShopAgent 是基于 RAG 的多模态电商智能导购 Agent（见 `docs/background.md`）。当前阶段只实现了 Python 后端（FastAPI + LangGraph），通过 SSE 流式接口对外提供多轮对话式导购能力。

安卓客户端是导购能力的移动端入口，交互形态类似豆包 APP：用户以自然语言发起对话，后端流式返回大模型推荐文本，并在文本之前返回结构化商品事件，客户端渲染为商品卡片。商品卡片展示标题、品牌、价格和图片；一个商品包含多个 SKU 时，以堆叠卡片形式展示。

## 后端接口契约

### POST /api/v1/chat/stream

SSE 流式对话接口，`Content-Type: text/event-stream`。

请求体（仅支持文本，不支持图片上传）：

```json
{
  "conversation_id": "可选，缺省由后端生成",
  "message": "用户消息，1-4000 字符"
}
```

事件序列：`message_start` → `product` × 0..3 → `text_delta` × 1..N → `message_end`，异常时插入 `error`。

| 事件 | 数据字段 | 说明 |
|---|---|---|
| `message_start` | `request_id`, `conversation_id` | 首个事件；`conversation_id` 需保存用于后续多轮请求 |
| `product` | 见下 | 商品卡片数据，**先于推荐文本到达** |
| `text_delta` | `delta` | 大模型推荐文本增量，逐段追加 |
| `error` | `code`, `message`, `retryable` | 业务错误；`retryable` 决定是否可重试 |
| `message_end` | `request_id`, `status` | `status` 为 `completed` / `partial` / `failed` |

`product` 事件字段（定义见 `src/shop_agent/models/events.py`）：

```json
{
  "rank": 1,
  "product_id": "p_digital_008",
  "title": "商品标题",
  "brand": "品牌",
  "base_price": 3999.0,
  "display_price": 3599.0,
  "matched_skus": [
    {"sku_id": "sku_xxx", "properties": {"颜色": "曜石黑", "容量": "256GB"}, "price": 3599.0}
  ],
  "image_url": "http://<host>/api/v1/products/p_digital_008/image"
}
```

- `display_price` 为符合当前条件的最低 SKU 价，`base_price` 为数据集原始基础价，两者可同时展示。
- `matched_skus` 只包含符合当前对话条件的 SKU，可能多于一个，也可能只有一个。
- `image_url` 可能为 `null`（图片文件不存在），客户端需有占位图。
- **没有商品描述字段**。商品描述由大模型的流式推荐文本（`text_delta`）承载，卡片上不展示结构化描述。

错误码包括：`INTENT_PARSE_FAILED`、`EVIDENCE_PARSE_FAILED`、`EMBEDDING_UNAVAILABLE`、`RETRIEVAL_UNAVAILABLE`、`RERANK_UNAVAILABLE`、`GENERATION_FAILED`、`INTERNAL_ERROR`。

### GET /api/v1/products/{product_id}/image

返回商品图片文件。客户端按 `image_url` 直接加载即可，无需单独调用。

### GET /health

返回依赖就绪状态（catalog / models / qdrant）。可用于启动时连通性自检，200 为就绪，503 为未就绪。

## 约束

1. **后端零改动**：客户端只消费现有 SSE 协议，不要求后端新增字段或接口。
2. **事实边界**：商品标题、品牌、价格、SKU、图片只来自后端 `product` 事件。客户端不得展示库存、优惠券、购买链接或活动信息——数据集没有这些，后端也不会下发。
3. **无图片输入**：`ChatRequest` 只接受文本消息，客户端首版不提供图片上传入口。
4. **无描述字段**：卡片只展示标题、品牌、价格、SKU 和图片；描述性内容依赖流式推荐文本。
5. **多轮会话**：从 `message_start` 获取 `conversation_id`，后续请求必须携带，否则多轮条件继承失效。
6. **错误降级**：收到 `error` 事件后流仍会发出 `message_end`；`status=partial` 时已收到的商品卡片和文本仍然有效，必须保留展示。
7. **图片可达性**：`image_url` 由后端 `PUBLIC_BASE_URL` 配置决定。本地联调时该值必须是客户端可达的地址（模拟器为宿主机局域网 IP 或 `10.0.2.2`），否则图片加载失败。
8. **开发联调**：模拟器通过 `10.0.2.2` 访问宿主机后端；明文 HTTP 只允许在 debug 构建的 `networkSecurityConfig` 中放行，release 构建不携带。
9. **文档义务**：安卓客户端文档独立成册，索引见 `android/docs/README.md`；功能文档为 `android/docs/features/android-client.md`，约定遵守 `docs/features/README.md`。
10. **版本对齐**：SSE 协议或事件字段发生变化时，以 `src/shop_agent/models/events.py` 和 `docs/features/text-shopping-workflow.md` 为准更新本文档。
