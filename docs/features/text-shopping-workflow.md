# 单轮文本商品推荐工作流

> 状态：提议
>
> 代码入口：尚未创建

## 功能目标

使用 LangGraph 编排单轮文本商品推荐流程。用户输入“推荐一款蓝牙耳机”或带有价格、品牌和排除条件的商品需求后，系统从本地商品数据集中召回候选商品，返回最多三个商品卡片，并通过 SSE 流式返回推荐说明。

第一阶段需要跑通真实数据、真实向量检索和真实模型调用。商品 JSON 是唯一事实源，模型不得生成数据集中不存在的商品、价格、SKU、图片或属性。

## 范围

本阶段包含：

- 单轮文本输入与意图结构化。
- 商品搜索与非购物输入两类意图路由。
- 商品描述、官方问答和用户评价的文本向量索引。
- Qdrant 召回、商品聚合和文本重排序。
- 结构化事实校验、语义证据映射和候选决策。
- 最多三个商品卡片与推荐说明的 SSE 流式返回。
- 本地商品图片的 HTTP 访问。

本阶段不包含：

- 多轮 Query 编译、指代消解、条件继承和候选复用。
- 信息不足时的澄清追问。
- 图片理解、图片向量化和以图搜商品。
- 商品对比、购物车、下单和其他交易能力。
- SQLite checkpoint 或其他会话持久化。
- 七维可解释评分、固定权重、分数校准和 Bayesian 平滑。

请求中保留 `conversation_id`，第一阶段只用于关联 SSE 事件，不恢复历史状态。后续多轮功能可以在不修改请求结构的前提下接入 LangGraph checkpointer。

## 外部行为

### 工作流

```text
START
  -> structure_intent
  -> route_intent
       -> non_shopping -> generate_response -> END
       -> product_search
            -> retrieve_chunks
            -> aggregate_products
            -> semantic_rerank
            -> validate_evidence
            -> decide_candidates
            -> generate_response
            -> END
```

`route_intent` 使用 LangGraph 条件边。非购物输入跳过 Embedding、Qdrant、重排序和证据校验。商品条件较少但品类明确时直接推荐，不触发追问。检索无结果或证据校验后没有合格商品时，跳过后续候选决策并生成无结果回复。

每个节点只读取图状态并返回自身负责的局部更新。同一节点不混用静态边和动态路由。

### SSE 事件

对话接口使用 `POST /api/v1/chat/stream`。请求体包含：

```json
{
  "conversation_id": "可选的会话标识",
  "message": "推荐一款蓝牙耳机"
}
```

`message` 必须是非空字符串。未提供 `conversation_id` 时由服务端生成，并在首个事件中返回。正常事件顺序为：

```text
message_start
product * 0..3
text_delta * 1..N
message_end
```

SSE 响应的 `Content-Type` 为 `text/event-stream`。各事件的数据结构如下：

| 事件 | 数据字段 |
|---|---|
| `message_start` | `request_id`、`conversation_id` |
| `product` | 排名、商品事实、符合当前条件的 SKU 和图片 URL |
| `text_delta` | 本次新增的文本 `delta` |
| `error` | `code`、`message`、`retryable` |
| `message_end` | `request_id`、`status`，状态为 `completed`、`partial` 或 `failed` |

商品事件示例：

```json
{
  "rank": 1,
  "product_id": "p_digital_008",
  "title": "商品标题",
  "brand": "品牌",
  "base_price": 499.0,
  "display_price": 499.0,
  "matched_skus": [],
  "image_url": "http://127.0.0.1:8000/api/v1/products/p_digital_008/image"
}
```

商品事件由代码从原始 JSON 组装，在推荐文本之前返回。`display_price` 取符合当前条件的最低 SKU 价格，`base_price` 保留数据集原值。图片接口根据 `product_id` 查询 JSON 中的相对路径并返回本地文件，不在 SSE 中传输 Base64。图片不存在时 `image_url` 为 `null`。自然语言生成失败时，已经发送的商品事件仍然有效，结束事件的 `status` 为 `partial`。

无匹配商品属于正常结果，不发送 `product` 事件。外部模型、Embedding、Qdrant 或重排序服务失败时返回 `error` 事件，并以 `message_end` 结束。错误码包括 `INTENT_PARSE_FAILED`、`EVIDENCE_PARSE_FAILED`、`EMBEDDING_UNAVAILABLE`、`RETRIEVAL_UNAVAILABLE`、`RERANK_UNAVAILABLE`、`GENERATION_FAILED` 和 `INTERNAL_ERROR`。系统不得在依赖失败时改为无检索的模型推荐。

## 接口与数据

### 意图结构

Qwen3.7-Max 只生成 `ParsedIntent`，图状态由代码维护。结构化输出使用 JSON Mode，并经过 Pydantic 校验。

```json
{
  "schema_version": 1,
  "intent": "product_search",
  "retrieval_query": "适合运动使用、佩戴稳定的蓝牙耳机",
  "category": "数码电子",
  "sub_category": "蓝牙耳机",
  "constraints": {
    "min_price": null,
    "max_price": 500.0,
    "include_brands": [],
    "exclude_brands": [],
    "required_features": [],
    "excluded_features": ["入耳式"]
  }
}
```

`retrieval_query` 是面向向量检索的改写文本，保留品类、使用场景和正向需求。价格、品牌和排除项进入 `constraints`，原始用户文本始终保留在图状态中。非购物输入的 `intent` 为 `non_shopping`，检索字段使用空值。

意图 JSON 校验失败后，节点携带校验错误重试一次。第二次仍失败时进入统一错误回复。结构化节点关闭思考模式，避免推理内容干扰 JSON 解析。

### 图状态

图状态至少包含以下信息：

- `request_id`、`conversation_id` 和原始用户文本。
- `parsed_intent`。
- Qdrant 返回的证据 Chunk。
- 按 `product_id` 聚合后的商品候选。
- 重排序分数和证据校验结果。
- 最终商品、回复模式和错误信息。

第一阶段不在图状态中保存历史消息、历史候选、指代信息或多轮查询快照。后续多轮功能复用 `constraints`，并新增本轮增量 `TurnQuery` 与合并结果 `QuerySnapshot`。

### 商品事实源

`ecommerce_agent_dataset` 下的商品 JSON 是唯一事实源。服务启动时加载并校验全部商品，通过 `product_id` 构建内存目录。Qdrant 中的向量和 payload 都是可重建索引，不能作为价格、SKU、图片或商品属性的最终依据。

事实发生冲突时按以下顺序处理：

1. 商品结构化字段和 SKU。
2. 官方问答。
3. 商品详情描述。
4. 用户评价。

用户评价只能说明个人使用体验，不能证明商品成分、规格或官方能力。导入时发现结构化字段与文本证据冲突，需要记录数据问题，回复时以结构化字段为准。

### Qdrant 索引

文本集合名称为 `product_text_chunks_v1`，使用 1024 维稠密向量和 Cosine 距离。每个商品拆成一个商品概览 Chunk、每条官方问答一个 Chunk、每条用户评价一个 Chunk。

每个 Point 的 payload 包含：

- `chunk_id`、`product_id`、`chunk_type` 和 Chunk 原文。
- `category`、`sub_category` 和 `brand`。
- `min_sku_price` 和 `max_sku_price`。
- 原始 JSON 的相对路径。

`product_id`、类目、品牌和 `chunk_type` 建立 keyword payload 索引，价格字段建立数值索引。预算上限使用 `min_sku_price` 过滤，最终卡片只保留实际符合预算的 SKU。

在线检索使用 `qwen3.7-text-embedding` 生成查询向量。类目、品牌和价格进入 Qdrant 过滤条件，语义属性不直接过滤。检索召回最多 30 个 Chunk，结果按 `product_id` 聚合为最多 10 个商品证据包。每个证据包包含商品结构化摘要和召回分数最高的五个 Chunk，再交给 `qwen3-rerank` 重排序。重排序分数只用于同一次查询内的候选排序，不跨查询比较。

### 证据与候选决策

品牌、类目、价格、SKU、图片路径等结构化事实由代码校验。使用场景、功能和排除属性等语义条件需要映射到具体证据 Chunk。存在语义条件时，Qwen3.7-Max 输出 `supported`、`contradicted` 或 `unknown`，并列出证据 `chunk_id`；代码检查这些 ID 是否属于当前商品的真实证据。

候选准入规则：

- 结构化条件不满足时淘汰。
- 必需属性为 `contradicted` 或 `unknown` 时淘汰。
- 排除属性没有明确证据证明不包含时淘汰。
- 没有额外属性条件时，按重排序分数选择最多三个商品。

候选决策保存 `rerank_score`、`evidence_ids`、`decision_reasons` 和淘汰原因。第一阶段不计算人为加权总分。后续七维评分需要单独的功能设计和评测集，确认维度定义、分数校准、子品类基准价、评价先验和权重后再接入。

## 关键决策

### 节点独立，模型调用按职责分配

意图结构化、语义证据映射和最终回复使用 Qwen3.7-Max；文本向量使用 `qwen3.7-text-embedding`；语义重排序使用 `qwen3-rerank`。商品聚合、结构化事实校验和候选准入由代码完成。节点边界与模型调用边界不要求一一对应。

### 第一阶段不实现澄清和多轮编译

品类明确但条件较少的请求直接返回最多三个候选。当前交付可以独立演示真实 RAG 链路，多轮状态合并不会影响单轮工作流的实现和验收。

### JSON 保持完整，Qdrant 只保存证据 Chunk

完整商品数据不迁移到关系型数据库，也不复制成 Qdrant 中的事实副本。工作流先从 Qdrant 获得 `product_id` 和证据，再从内存商品目录读取完整商品。

### 商品事件与生成文本分离

商品事件由代码生成，Qwen3.7-Max 只负责自然语言说明。客户端不需要从生成文本中解析商品字段，文本生成失败也不会破坏已经返回的商品卡片。

第一阶段保留模型原生的文本流式输出，因此无法在发送后撤回已经产生的文本。回复节点只接收校验通过的商品事实和证据白名单，提示词禁止补充白名单之外的属性、价格和 SKU。结构化商品事件仍是客户端展示事实的依据，端到端测试需要检查推荐文本没有引入未验证事实。

## 外部依赖与配置

- 阿里云百炼 API Key，通过 `DASHSCOPE_API_KEY` 提供。
- 北京地域的 Qwen3.7-Max、`qwen3.7-text-embedding` 和 `qwen3-rerank`。
- 本地 Qdrant 服务，通过 Docker Compose 管理。
- 模型 ID、Qdrant 地址、集合名称、召回数量和超时写入环境配置，不在代码中保存密钥。

第一阶段不依赖 SQLite。DashScope 或 Qdrant 不可用时，健康检查应明确显示服务未就绪。

## 代码与验证

代码尚未创建。实现需要分别提供 FastAPI 对话接口、LangGraph 图定义、商品目录、索引构建、Qdrant 检索、证据校验和 SSE 事件模型，完成后将实际入口写回本文档。

实现后至少覆盖以下验证：

- “你好”不调用 Embedding、Qdrant 和重排序。
- “推荐一款蓝牙耳机”返回最多三个真实商品卡片及流式文本。
- “500 元以内、不要入耳式”只返回证据明确满足条件的商品。
- 无匹配商品时不发送商品事件。
- 意图 JSON 无效时只重试一次，随后返回可识别的错误事件。
- 模型、Qdrant 和重排序失败时不生成无依据推荐。
- SSE 事件顺序稳定，图片 URL 可以访问。
- 商品 ID、品牌、价格、SKU 和图片与原始 JSON 一致。
- Chunk 数量、payload 字段和商品聚合结果可重复构建。

代码合入前运行 pytest、Ruff 和 mypy，并使用本地 Qdrant 与真实模型完成一次端到端验收。

## 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-07-22 | 创建第一阶段单轮文本商品推荐设计 | 先跑通文本 RAG 与工作流编排，再增加多轮和图片能力 |
