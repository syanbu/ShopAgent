package com.shopagent.domain

/** SKU 条目，对应后端 product 事件的 matched_skus 元素 */
data class Sku(
    val skuId: String,
    val properties: Map<String, String>,
    val price: Double,
)

/** 商品卡片，对应后端 product 事件 */
data class ProductCard(
    val rank: Int,
    val productId: String,
    val title: String,
    val brand: String,
    val basePrice: Double,
    val displayPrice: Double,
    val matchedSkus: List<Sku>,
    val imageUrl: String?,
    /** 商品描述，后端暂未下发，占位字段 */
    val description: String? = null,
)

/** 助手消息状态 */
enum class MessageStatus {
    Streaming,
    Done,
    Partial,
    Failed,
}

/** error 事件或传输层错误，retryable 决定 UI 是否显示重试 */
data class ChatError(
    val code: String,
    val message: String,
    val retryable: Boolean,
)

sealed interface ChatMessage {
    val id: String

    data class User(
        override val id: String,
        val text: String,
    ) : ChatMessage

    data class Assistant(
        override val id: String,
        val products: List<ProductCard> = emptyList(),
        val text: String = "",
        val status: MessageStatus = MessageStatus.Streaming,
        val error: ChatError? = null,
    ) : ChatMessage
}
