# ShopAgent

ShopAgent 是一个单轮文本购物 Agent。服务从本地商品 JSON 构建证据 Chunk，使用 DashScope 生成向量并写入 Qdrant，再通过 LangGraph、FastAPI 和 SSE 返回商品卡片与推荐说明。

详细设计见 [项目文档索引](docs/README.md) 和 [文本购物工作流](docs/features/text-shopping-workflow.md)。

## 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Docker 和 Docker Compose
- 可用的 DashScope API Key

## 安装依赖

```bash
uv sync
```

首次运行时，从示例文件创建 `.env`：

```bash
cp .env.example .env
```

如果 `.env` 已存在，请不要覆盖。至少需要配置：

```dotenv
DASHSCOPE_API_KEY=你的_API_Key
```

其余模型、Qdrant、数据集和超时配置可参考 [.env.example](.env.example)。

## 启动 Qdrant

```bash
docker compose up -d qdrant
docker compose ps
```

Qdrant 默认只监听本机 `127.0.0.1:6333`。`docker compose ps` 显示容器为 `healthy` 后再执行索引。

## 构建 Chunk 并写入 Qdrant

```bash
uv run python -m shop_agent.cli.index_products
```

该命令会：

1. 读取 `ecommerce_agent_dataset` 下的商品 JSON。
2. 为每个商品构建商品摘要、官方问答和用户评价 Chunk。
3. 调用 DashScope Embedding 模型生成 1024 维向量。
4. 创建 `product_text_chunks_v1` collection 和 payload indexes。
5. 分批将向量和 Chunk payload 写入 Qdrant。

Point ID 根据 `chunk_id` 稳定生成，重复执行会覆盖同一 Chunk。第一阶段假设完成索引后本地商品 JSON 不会删除或重排证据内容，索引器不负责清理陈旧 Point。

商品 JSON 中的品牌、类目、价格或参与 Chunk 文本的字段发生变化后，必须重新运行
全量索引，使 Qdrant 向量和 payload 与 catalog 保持一致。若商品或 Chunk 已被删除，
应先删除 `product_text_chunks_v1` 集合再完整重建；健康接口只检查集合和向量配置，
不能识别旧 payload 与当前 JSON 的语义漂移。

## 启动 Agent API

```bash
env -u ALL_PROXY -u all_proxy \
  uv run uvicorn shop_agent.api.app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```
```powershell
uv run uvicorn shop_agent.api.app:app --host 127.0.0.1 --port 8000 --reload
```

`env -u ALL_PROXY -u all_proxy` 只对本次命令临时忽略代理，避免本机 Qdrant 和 API 请求被转发到代理服务器。

服务启动后可检查健康状态：

```bash
curl http://127.0.0.1:8000/health
```

健康接口返回 `ready` 后即可发起对话请求。

## 运行测试客户端

另开一个终端运行交互式客户端：

```bash
uv run python scripts/chat_client.py
```

输入 `/quit` 或 `/exit` 退出。也可以只发送一条消息：

```bash
uv run python scripts/chat_client.py --message "推荐一款降噪耳机"
```

## 启动顺序

```text
配置 .env
    ↓
启动 Qdrant
    ↓
构建 Chunk 并写入 Qdrant
    ↓
启动 Agent API
    ↓
运行测试客户端
```

## API

| 接口 | 用途 |
|---|---|
| `POST /api/v1/chat/stream` | 通过 SSE 返回商品和推荐文本 |
| `GET /api/v1/products/{product_id}/image` | 返回商品图片 |
| `GET /health` | 检查 catalog、模型配置和 Qdrant 状态 |

## 停止服务

停止 Agent API 时按 `Ctrl+C`。停止 Qdrant：

```bash
docker compose down
```
