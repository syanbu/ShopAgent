# 单轮文本商品推荐工作流

> 状态：已完成
>
> 代码入口：`src/shop_agent/models/`, `src/shop_agent/catalog.py`, `src/shop_agent/chunking.py`, `src/shop_agent/services/`, `src/shop_agent/workflow/`, `src/shop_agent/api/`, `src/shop_agent/cli/index_products.py`, `tests/live/test_live_shopping_flow.py`, `compose.yaml`

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

`message` 必须是非空字符串。`conversation_id` 最长 128 个字符；未提供时由服务端生成，并在首个事件中返回。正常事件顺序为：

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

比如，商品 `p_digital_001` 的描述里有一条官方问答和一条用户评价，索引时会生成三个 Point：

| `chunk_id` | `chunk_type` | Chunk 原文 |
|---|---|---|
| `p_digital_001:summary` | `product_summary` | `商品：测试蓝牙耳机\n品牌：测试品牌\n类目：数码电子/蓝牙耳机\n适合通勤的测试蓝牙耳机。` |
| `p_digital_001:faq:0` | `official_faq` | `问题：是否支持蓝牙？\n回答：支持蓝牙连接。` |
| `p_digital_001:review:0` | `user_review` | `评分：5/5\n评价：佩戴舒适。` |

其中，官方问答 Point 写入 Qdrant 后的结构如下。`vector` 实际包含 1024 个浮点数，这里省略中间值：

```json
{
  "id": "229e1bfe-2861-5a81-a738-a4592e8cf5cf",
  "vector": [0.012, -0.034, "... 共 1024 维"],
  "payload": {
    "chunk_id": "p_digital_001:faq:0",
    "product_id": "p_digital_001",
    "chunk_type": "official_faq",
    "text": "问题：是否支持蓝牙？\n回答：支持蓝牙连接。",
    "category": "数码电子",
    "sub_category": "蓝牙耳机",
    "brand": "测试品牌",
    "min_sku_price": 399.0,
    "max_sku_price": 599.0,
    "source_path": "1_数码电子/data/p_digital_001.json"
  }
}
```

三个 Point 共用商品级过滤字段，但保留各自的 `chunk_id`、`chunk_type` 和原文。查询“500 元以内、佩戴舒适的蓝牙耳机”时，Qdrant 先用 `min_sku_price <= 500` 等条件过滤，再用查询向量与三个 Point 的向量计算 Cosine 相似度。

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

第一阶段假设完成索引后本地商品 JSON 不会删除或重排 FAQ、评价等证据内容，因此不实现陈旧 Qdrant Point 的差异清理。商品数据更新与索引同步需要作为后续独立功能设计。

### 商品事件与生成文本分离

商品事件由代码生成，Qwen3.7-Max 只负责自然语言说明。客户端不需要从生成文本中解析商品字段，文本生成失败也不会破坏已经返回的商品卡片。

第一阶段保留模型原生的文本流式输出，因此无法在发送后撤回已经产生的文本。回复节点只接收校验通过的商品事实和证据白名单，提示词禁止补充白名单之外的属性、价格和 SKU。结构化商品事件仍是客户端展示事实的依据，端到端测试需要检查推荐文本没有引入未验证事实。

## 外部依赖与配置

- 阿里云百炼 API Key，通过 `DASHSCOPE_API_KEY` 提供。
- 北京地域的 Qwen3.7-Max、`qwen3.7-text-embedding` 和 `qwen3-rerank`。
- 本地 Qdrant 服务，通过 Docker Compose 管理。
- 模型 ID、Qdrant 地址、集合名称、召回数量和超时写入环境配置，不在代码中保存密钥；最终商品数量配置只接受 1–3，防止突破最多三个商品卡片的接口契约。

第一阶段不依赖 SQLite。DashScope 或 Qdrant 不可用时，健康检查应明确显示服务未就绪。

## 本地运行

```bash
uv sync
cp .env.example .env
docker compose up -d qdrant
uv run python -m shop_agent.cli.index_products
uv run uvicorn shop_agent.api.app:app --reload
```

在 `.env` 中至少配置 `DASHSCOPE_API_KEY`，并按需覆盖聊天、Embedding、重排模型、Qdrant 地址、集合名、召回数量、超时、数据集目录和公开图片基础 URL。Compose 只把无认证的本地 Qdrant 绑定到 `127.0.0.1:6333`，不会暴露给局域网。首个版本只处理单轮请求；`conversation_id` 仅关联事件，不恢复历史状态。

服务启动后，可在另一个终端运行交互式测试客户端：

```bash
.venv/bin/python scripts/chat_client.py
```

客户端会在本次进程中复用同一个 `conversation_id`，输入 `/quit` 或 `/exit` 退出。当前服务仍按单轮请求处理，复用标识只用于关联事件。也可发送单条消息并在响应结束后退出：

```bash
.venv/bin/python scripts/chat_client.py --message "推荐一款降噪耳机"
```

本地接口：

| 接口 | 用途 |
|---|---|
| `POST /api/v1/chat/stream` | 返回 `message_start`、`product`、`text_delta`、`error` 和 `message_end` SSE 事件 |
| `GET /api/v1/products/{product_id}/image` | 按 catalog 中的安全路径返回商品图片 |
| `GET /health` | 返回 catalog、模型配置和 Qdrant collection readiness |

真实服务验收为 opt-in：

```bash
RUN_LIVE_TESTS=1 uv run pytest tests/live/test_live_shopping_flow.py -m live -q
```

## 代码与验证

基础配置、Schema、商品目录、DashScope 模型网关、Qdrant 集合管理、离线索引入口、在线召回聚合、重排结果绑定、证据校验、候选决策、LangGraph 工作流和 FastAPI 接口均已创建，并通过真实 DashScope/Qdrant 冒烟与手工 SSE 验收。离线索引使用 `python -m shop_agent.cli.index_products`，以不超过 20 条的批次生成 1024 维文档向量，并按稳定 UUID point ID 幂等 upsert 到 `product_text_chunks_v1`。本地 Qdrant 由 `compose.yaml` 启动；索引器不会删除或重建已有生产集合。

`RetrievalService` 使用意图中的查询文本生成 query embedding，并把类目、子类目、品牌和价格约束交给 Qdrant。召回结果必须能在 catalog 中解析为真实商品；每个商品只保留分数最高的五条证据，并将最多十个商品交给重排序。`EvidenceService` 重新使用 catalog 校验类目、子类目、品牌和实际 SKU 价格，只有语义条件存在且结构化条件已经通过时才调用证据映射模型。模型返回的决定性和冲突证据 ID 都必须属于该商品的召回证据，同一条件不得重复；未知 ID 或重复条件直接映射为不可重试的 `EVIDENCE_PARSE_FAILED`。

候选选择只读取已标记为 eligible 且具有本次重排分数的商品，按分数降序返回最多三个结果。选择接口必须显式接收与校验阶段相同的 `SearchConstraints`，`matched_sku_ids` 使用该约束重新从 catalog 精确计算；语义证据列表只保留当前条件下状态为 `supported` 的决定性证据，不包含冲突证据。

`build_graph()` 使用显式条件边编译无 checkpointer 的单轮图。非购物输入直接进入回复节点；零召回和证据校验后无合格候选都会跳过剩余候选链路。候选决策先从 catalog 重新组装商品卡片，并通过 LangGraph custom stream 发出 `product` 事件；随后回复节点逐段发出非空 `text_delta`。商品提示词只包含用户原话、已选商品的结构化字段、匹配 SKU 和决定性证据文本，并明确禁止库存、优惠、优惠券、购买链接和白名单之外的事实。缺失请求或会话标识时，工作流通过可注入 ID 工厂补齐，便于接口接入和确定性测试。

FastAPI 应用在 lifespan 中延迟装配真实服务，测试可注入 graph、catalog、settings 和 Qdrant readiness probe。聊天接口在图运行前生成关联 ID，直接把 LangGraph custom part 转换为同名 SSE 事件；依赖错误在商品发送前返回 `failed`，商品发送后返回 `partial`，客户端取消则立即向图传播且不补发结束事件。图片接口只按 catalog 中的安全相对路径返回文件，未知商品和文件缺失统一返回不含本地路径的 404。健康接口同时检查 catalog、模型配置以及非空且向量配置匹配的目标 Qdrant collection，任一依赖不可用即返回 503 与逐项状态。最终生成的事实边界使用 system role，用户原话只作为待处理数据。

意图网关接收 catalog 的真实类目与子类目枚举，提示模型只返回这些精确值；模型仍返回同义但越界的枚举时，代码将对应过滤项降为 `null`，保留向量查询文本，避免错误的严格过滤导致零召回。

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
| 2026-07-22 | 建立配置、错误、商品、意图、检索、SSE 与图状态基础 Schema | 为后续索引、检索和工作流提供类型契约 |
| 2026-07-22 | 创建第一阶段单轮文本商品推荐设计 | 先跑通文本 RAG 与工作流编排，再增加多轮和图片能力 |
| 2026-07-22 | 添加商品目录与本地图片文件解析入口 | 从商品 JSON 建立内存目录，按 SKU 预算筛选并安全解析相对图片路径 |
| 2026-07-22 | 添加确定性证据切块入口 | 为商品摘要、官方问答和用户评价生成稳定的 UUID5 点位 ID 与原始 JSON 路径 |
| 2026-07-23 | 添加 DashScope 网关、Qdrant 存储与离线索引入口 | 固化结构化输出、向量、重排和版本化证据集合契约，为后续在线检索工作流提供外部服务边界 |
| 2026-07-23 | 添加召回聚合、证据校验与候选决策 | 将 Qdrant 结果重新绑定 catalog 事实，限制证据白名单，并按结构化条件、语义证据和本次重排结果确定最多三个候选 |
| 2026-07-23 | 添加 LangGraph 条件路由与 custom stream | 固化非购物、零召回、证据为空和正常推荐路径，保证商品事实事件先于白名单约束下的生成文本 |
| 2026-07-23 | 添加 FastAPI SSE、商品图片与健康接口 | 将工作流事件稳定映射为 HTTP 流，隔离本地图片路径，并明确依赖未就绪与部分失败语义 |
| 2026-07-23 | 完成真实服务冒烟、taxonomy 边界与最终验收 | 使用真实 DashScope/Qdrant 验证商品事实、非购物短路、排除条件证据和 SSE 顺序，并消除模型同义类目造成的零召回 |
