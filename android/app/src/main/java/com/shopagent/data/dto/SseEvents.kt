package com.shopagent.data.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class MessageStartDto(
    @SerialName("request_id") val requestId: String,
    @SerialName("conversation_id") val conversationId: String,
)

@Serializable
data class SkuDto(
    @SerialName("sku_id") val skuId: String,
    val properties: Map<String, String> = emptyMap(),
    val price: Double,
)

@Serializable
data class ProductDto(
    val rank: Int,
    @SerialName("product_id") val productId: String,
    val title: String,
    val brand: String,
    @SerialName("base_price") val basePrice: Double,
    @SerialName("display_price") val displayPrice: Double,
    @SerialName("matched_skus") val matchedSkus: List<SkuDto> = emptyList(),
    @SerialName("image_url") val imageUrl: String? = null,
    /** 商品描述，后端暂未下发，占位字段 */
    val description: String? = null,
)

@Serializable
data class TextDeltaDto(
    val delta: String,
)

@Serializable
data class ErrorDto(
    val code: String,
    val message: String,
    val retryable: Boolean = false,
)

@Serializable
data class MessageEndDto(
    @SerialName("request_id") val requestId: String,
    val status: String,
)

/** 解析后的 SSE 事件，未知事件类型（如 ping）不产生实例 */
sealed interface SseEvent {
    data class MessageStart(val data: MessageStartDto) : SseEvent
    data class Product(val data: ProductDto) : SseEvent
    data class TextDelta(val data: TextDeltaDto) : SseEvent
    data class Error(val data: ErrorDto) : SseEvent
    data class MessageEnd(val data: MessageEndDto) : SseEvent
}
