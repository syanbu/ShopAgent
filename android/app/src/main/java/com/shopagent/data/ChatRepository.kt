package com.shopagent.data

import com.shopagent.data.dto.ChatRequest
import com.shopagent.data.dto.ProductDto
import com.shopagent.data.dto.SseEvent
import com.shopagent.data.dto.SkuDto
import com.shopagent.domain.ChatError
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import com.shopagent.domain.ProductCard
import com.shopagent.domain.Sku
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/** 事件流聚合结果：conversation_id 与助手消息状态快照 */
sealed interface ChatStreamUpdate {
    data class ConversationId(val value: String) : ChatStreamUpdate
    data class AssistantState(val message: ChatMessage.Assistant) : ChatStreamUpdate
}

class ChatRepository(
    private val source: ChatStreamSource,
) {

    /**
     * 发起一轮对话，返回聚合更新流。
     *
     * 事件按序折叠进 [ChatMessage.Assistant] 快照：
     * - product 追加到 products（先于 text_delta 到达）
     * - text_delta 逐段追加到 text
     * - error 记录到 error，保留已收内容
     * - message_end 按 status 置 Done/Partial/Failed
     * 传输层异常转为 retryable 的 Failed 状态，不向外抛出。
     */
    fun streamReply(
        messageId: String,
        text: String,
        conversationId: String?,
    ): Flow<ChatStreamUpdate> = flow {
        var message = ChatMessage.Assistant(id = messageId)
        emit(ChatStreamUpdate.AssistantState(message))

        try {
            source.stream(ChatRequest(conversationId = conversationId, message = text))
                .collect { event ->
                    when (event) {
                        is SseEvent.MessageStart ->
                            emit(ChatStreamUpdate.ConversationId(event.data.conversationId))

                        is SseEvent.Product -> {
                            message = message.copy(
                                products = message.products + event.data.toDomain(),
                            )
                            emit(ChatStreamUpdate.AssistantState(message))
                        }

                        is SseEvent.TextDelta -> {
                            message = message.copy(text = message.text + event.data.delta)
                            emit(ChatStreamUpdate.AssistantState(message))
                        }

                        is SseEvent.Error -> {
                            message = message.copy(
                                error = ChatError(
                                    code = event.data.code,
                                    message = event.data.message,
                                    retryable = event.data.retryable,
                                ),
                            )
                            emit(ChatStreamUpdate.AssistantState(message))
                        }

                        is SseEvent.MessageEnd -> {
                            message = message.copy(status = event.data.status.toStatus())
                            emit(ChatStreamUpdate.AssistantState(message))
                        }
                    }
                }
        } catch (e: Exception) {
            message = message.copy(
                status = MessageStatus.Failed,
                error = ChatError(
                    code = if (e is HttpStatusException) "HTTP_${e.statusCode}" else "NETWORK",
                    message = e.message ?: "连接失败",
                    retryable = true,
                ),
            )
            emit(ChatStreamUpdate.AssistantState(message))
            return@flow
        }

        // 流正常结束但未收到 message_end：视为连接中断
        if (message.status == MessageStatus.Streaming) {
            message = message.copy(
                status = MessageStatus.Failed,
                error = ChatError(
                    code = "CONNECTION_CLOSED",
                    message = "连接中断",
                    retryable = true,
                ),
            )
            emit(ChatStreamUpdate.AssistantState(message))
        }
    }

    private fun String.toStatus(): MessageStatus = when (this) {
        "completed" -> MessageStatus.Done
        "partial" -> MessageStatus.Partial
        else -> MessageStatus.Failed
    }

    private fun ProductDto.toDomain(): ProductCard = ProductCard(
        rank = rank,
        productId = productId,
        title = title,
        brand = brand,
        basePrice = basePrice,
        displayPrice = displayPrice,
        matchedSkus = matchedSkus.map { it.toDomain() },
        imageUrl = imageUrl,
        description = description,
    )

    private fun SkuDto.toDomain(): Sku = Sku(
        skuId = skuId,
        properties = properties,
        price = price,
    )
}
