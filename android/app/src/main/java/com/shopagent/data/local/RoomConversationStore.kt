package com.shopagent.data.local

import androidx.room.withTransaction
import com.shopagent.data.dto.ErrorDto
import com.shopagent.data.dto.ProductDto
import com.shopagent.data.dto.SkuDto
import com.shopagent.domain.ChatError
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import com.shopagent.domain.ProductCard
import com.shopagent.domain.Sku
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** Room 实现的会话持久化：商品卡片与错误复用 SSE DTO 序列化为 JSON 列 */
class RoomConversationStore(
    private val db: AppDatabase,
) : ConversationStore {

    private val dao = db.conversationDao()
    private val json = Json { ignoreUnknownKeys = true }
    private val productListSerializer = ListSerializer(ProductDto.serializer())

    override fun observeConversations(): Flow<List<ConversationSummary>> =
        dao.observeConversations().map { list ->
            list.map { ConversationSummary(id = it.id, title = it.title, updatedAt = it.updatedAt) }
        }

    override suspend fun saveConversation(conversationId: String, messages: List<ChatMessage>) {
        val title = messages.filterIsInstance<ChatMessage.User>().firstOrNull()?.text
            ?.take(MAX_TITLE_LENGTH) ?: return
        db.withTransaction {
            dao.upsertConversation(
                ConversationEntity(
                    id = conversationId,
                    title = title,
                    updatedAt = System.currentTimeMillis(),
                ),
            )
            dao.deleteMessages(conversationId)
            dao.insertMessages(
                messages.mapIndexed { index, message -> message.toEntity(conversationId, index) },
            )
        }
    }

    override suspend fun loadMessages(conversationId: String): List<ChatMessage> =
        dao.getMessages(conversationId).map { it.toDomain() }

    private fun ChatMessage.toEntity(conversationId: String, sortIndex: Int): MessageEntity =
        when (this) {
            is ChatMessage.User -> MessageEntity(
                id = id,
                conversationId = conversationId,
                role = ROLE_USER,
                text = text,
                productsJson = null,
                status = null,
                errorJson = null,
                sortIndex = sortIndex,
            )
            is ChatMessage.Assistant -> MessageEntity(
                id = id,
                conversationId = conversationId,
                role = ROLE_ASSISTANT,
                text = text,
                productsJson = products
                    .takeIf { it.isNotEmpty() }
                    ?.let { json.encodeToString(productListSerializer, it.map { p -> p.toDto() }) },
                status = status.name,
                errorJson = error?.let { json.encodeToString(ErrorDto.serializer(), it.toDto()) },
                sortIndex = sortIndex,
            )
        }

    private fun MessageEntity.toDomain(): ChatMessage = when (role) {
        ROLE_USER -> ChatMessage.User(id = id, text = text)
        else -> ChatMessage.Assistant(
            id = id,
            products = productsJson
                ?.let { json.decodeFromString(productListSerializer, it).map { p -> p.toDomain() } }
                ?: emptyList(),
            text = text,
            // 流式中途被打断保存下来的 Streaming，恢复时视为不完整回复
            status = status
                ?.let { MessageStatus.valueOf(it) }
                ?.takeIf { it != MessageStatus.Streaming }
                ?: MessageStatus.Partial,
            error = errorJson
                ?.let { json.decodeFromString(ErrorDto.serializer(), it).toDomain() },
        )
    }

    private fun ProductCard.toDto() = ProductDto(
        rank = rank,
        productId = productId,
        title = title,
        brand = brand,
        basePrice = basePrice,
        displayPrice = displayPrice,
        matchedSkus = matchedSkus.map { SkuDto(skuId = it.skuId, properties = it.properties, price = it.price) },
        imageUrl = imageUrl,
        description = description,
    )

    private fun ProductDto.toDomain() = ProductCard(
        rank = rank,
        productId = productId,
        title = title,
        brand = brand,
        basePrice = basePrice,
        displayPrice = displayPrice,
        matchedSkus = matchedSkus.map { Sku(skuId = it.skuId, properties = it.properties, price = it.price) },
        imageUrl = imageUrl,
        description = description,
    )

    private fun ChatError.toDto() = ErrorDto(code = code, message = message, retryable = retryable)

    private fun ErrorDto.toDomain() = ChatError(code = code, message = message, retryable = retryable)

    private companion object {
        const val ROLE_USER = "user"
        const val ROLE_ASSISTANT = "assistant"
        const val MAX_TITLE_LENGTH = 20
    }
}
