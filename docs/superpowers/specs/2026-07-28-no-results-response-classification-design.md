# 无结果响应分类与固定文案设计

> 后续变更：纯 `more_results` 耗尽时保留上一批候选、焦点和已展示 ID，详见
> `docs/superpowers/plans/2026-07-28-preserve-candidates-after-exhausted-more-results.md`。

## 背景

当前工作流把所有空结果交给回答模型处理。提示词只区分“没有召回到商品”和“没有
通过证据校验的商品”，对外都要求模型表达“当前没有可靠的匹配结果，建议放宽或修改
条件”。因此，用户在纯 `more_results` 场景已经看完当前条件下的全部商品时，也会收到
像筛选失败一样的提示。

“没有更多商品”和“当前条件没有匹配商品”代表不同状态。前者说明已有结果已经穷尽，
后者说明本次搜索条件没有得到可展示结果。工作流已经知道本轮的确定性
`search_intent`，不需要让回答模型猜测原因。

## 目标

- 纯 `more_results` 没有新商品时，明确告知当前条件下没有更多商品。
- 新搜索、条件细化或品类切换没有结果时，提示当前条件没有匹配商品。
- 区分零召回与候选可靠性不足。
- 无结果响应使用固定文案，不调用回答模型。
- 保持现有 SSE 事件格式和“先保存会话，再发送文本”的顺序。

## 非目标

- 不修改检索、重排、证据校验和 SKU 匹配算法。
- 不判断用户条件在业务上是否“过严”。系统只能确认当前条件没有产生结果。
- 不修改失败的 `more_results` 对 `recent_candidates`、焦点和 `seen_product_ids` 的
  现有持久化规则。
- 不新增公开 API 字段，不修改 SQLite 表结构或 `ConversationState` 序列化格式。
- 不让客户端根据自然语言反推无结果原因。

## 方案比较

### 继续由模型生成不同文案

后端把原因写进提示词，由模型生成对应回复。改动少，但文案仍可能漂移，也会为一个
确定性状态增加模型调用和失败点，不采用。

### 只根据 `search_intent` 选择两条文案

`more_results` 返回“没有更多”，其他搜索返回“没有匹配”。该方案能解决主要问题，
但会丢失零召回和证据不足的区别，也没有覆盖最终 SKU 选择为空的路径。

### 在失败来源记录原因并发送固定文案

各节点在确定空结果时写入内部 `no_result_reason`，专用响应节点根据枚举发送固定文本。
原因产生在拥有事实的节点，后续节点不再猜测。本设计采用该方案。

## 内部状态

`ShoppingState` 增加仅在单次图执行中使用的可选字段：

```text
no_result_reason:
  "exhausted"
  | "no_matches"
  | "insufficient_evidence"
```

该字段不进入 `ConversationState`，不写入 SQLite，也不出现在 SSE 数据结构中。

## 原因判定

| 失败位置 | 普通搜索 | 纯 `more_results` |
|---|---|---|
| `retrieve_chunks` 返回空列表 | `no_matches` | `exhausted` |
| `validate_evidence` 没有合格候选 | `insufficient_evidence` | `exhausted` |
| `decide_candidates` 最终选择为空 | `insufficient_evidence` | `exhausted` |

这里的普通搜索包括 `new_search`、`refine_search` 和 `switch_category`。现有编译器会把
携带条件修改的 `more_results` 转为 `refine_search`，所以只有保持原条件的换一批才能
得到 `exhausted`。

`decide_candidates` 为空需要单独处理。证据校验可能产生 eligible 候选，但 Catalog
中的 SKU 匹配仍可能让最终选择为空。该路径也必须进入无结果持久化和固定响应，不能
保存成空的成功搜索后再调用回答模型。

## 固定文案

```text
exhausted:
当前条件下没有更多符合要求的商品了。

no_matches:
当前筛选条件下没有找到匹配商品，建议您放宽或修改筛选条件。

insufficient_evidence:
找到了一些候选商品，但现有信息不足以确认它们符合要求，建议您调整筛选条件。
```

后端按枚举直接发送一条 `text_delta`。固定文案不经过回答模型，不允许模型润色。

## 工作流

搜索成功路径保持不变。三个空结果入口统一进入 `persist_no_results`，保存完成后进入
专用的固定响应节点：

```text
retrieve_chunks
  -> empty ------------------------------------+
  -> aggregate -> rerank -> validate_evidence  |
                              -> no eligible ---+-> persist_no_results
                              -> decide_candidates
                                   -> empty ----+        |
                                   -> selected           v
                                             emit_no_results_response
                                                       |
                                                      END
```

`persist_no_results` 继续负责保存查询快照和清理当前展示状态。固定响应节点只读取
`no_result_reason`、发送一个 `text_delta`，并写入 `response_text`。原因缺失或枚举值
未知属于内部编程错误，不能静默回退到通用模型文案。

## 外部行为

SSE 顺序保持：

```text
message_start -> text_delta -> message_end
```

无结果时不发送 `product`，`message_end.status` 仍为 `completed`。固定响应在
`persist_no_results` 成功后才发送；保存失败仍走现有错误链路，不提前发送文本。

## 测试

确定性测试至少覆盖：

- 普通搜索零召回返回 `no_matches` 文案，回答模型调用次数为零。
- 条件细化零召回返回 `no_matches`，不误报“没有更多”。
- 纯 `more_results` 零召回返回 `exhausted` 文案。
- 纯 `more_results` 的剩余候选全部未通过证据校验时仍返回 `exhausted`。
- 普通搜索有召回但证据全部不合格时返回 `insufficient_evidence`。
- eligible 候选在最终 SKU 选择后为空时进入 `persist_no_results`，不走空成功结果。
- 会话保存发生在第一个 `text_delta` 之前。
- HTTP 层仍输出 `message_start -> text_delta -> message_end`，且没有模型生成调用。
- 搜索成功、商品问答、澄清和非购物响应不受影响。

## 影响范围

预计涉及内部状态、工作流节点、图路由、工作流测试、HTTP 集成测试和既有功能文档，
会超过五个文件。该范围来自三个空结果入口和“保存先于发送”的共同约束，不引入新服务
或外部依赖。

该能力属于现有“多轮 Query 编译与指代消解”。实施时更新
`docs/features/multi-turn-query-engine.md` 的外部行为、工作流、覆盖矩阵和变更记录；
不新建功能文档，也不修改 `docs/README.md` 中的功能身份或索引行。

## 风险与回滚

设计依赖一个现有保证：只有不含条件修改的纯 `more_results` 才保留该意图。如果编译器
未来改变这条规则，`exhausted` 可能被错误用于带新条件的搜索。对应测试必须固定该意图
归一化边界。

该改动没有数据库迁移和外部状态变化。回滚代码即可恢复原有模型生成文案，已有会话
数据不需要处理。
