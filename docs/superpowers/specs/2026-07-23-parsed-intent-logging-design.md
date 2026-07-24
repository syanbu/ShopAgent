# 意图识别结果日志设计

## 目标

在每次意图识别成功后，由服务端以 `INFO` 日志记录最终的 `ParsedIntent` JSON
对象，便于排查价格、品牌、类目及语义约束是否被正确识别。

## 范围

- 购物与非购物请求均记录。
- 日志位于 LangGraph 的 `structure_intent` 节点。
- 日志通过 Uvicorn 的服务端 logger 输出，确保默认启动命令可以在终端显示。
- 记录经过 Pydantic 校验和商品类目纠偏后的最终对象，而不是模型的原始输出。
- 整条日志载荷使用标准库 `json.dumps()` 序列化，客户端提供的关联标识不能拆分或伪造日志行。
- 不改变 SSE 协议、图状态、意图数据结构或错误处理。
- 意图识别失败时不打印成功对象，继续使用现有错误链路。

## 日志格式

每次识别成功输出一条单行 `INFO` 日志：

```text
parsed_intent {"request_id":"<request_id>","conversation_id":"<conversation_id>","intent":<JSON>}
```

载荷使用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`
序列化，使中文在终端中保持可读。JSON 编码负责转义换行、引号、反斜杠和控制字符，
随后显式将 `U+0085`、`U+2028`、`U+2029` 替换为对应的 `\uXXXX` 序列，因此
每条记录只占一个物理日志行。解析 JSON 后仍能得到原始字符和值。`intent` 与
`ParsedIntent` 对象一致，包括 `schema_version`、意图类型、
`retrieval_query`、类目字段和完整 `constraints`。

请求未携带关联标识时，节点先通过现有 `id_factory` 生成标识，再写入日志和图状态，
保证日志中的标识能够关联后续请求处理。

## 验证

- 商品搜索请求记录一次最终意图 JSON，并包含价格等结构化约束。
- 非购物请求同样记录一次最终意图 JSON。
- 日志包含本次请求的 `request_id` 和 `conversation_id`。
- 中文意图字段直接显示中文，不转换为 ASCII Unicode 转义。
- 包含换行符或 Unicode 行分隔符的 `conversation_id` 不会产生额外日志行，
  JSON 解析后仍保持原值。
- 现有工作流路由和 SSE 测试继续通过。
