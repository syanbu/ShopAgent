package com.shopagent.data.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ChatRequest(
    @SerialName("conversation_id") val conversationId: String? = null,
    val message: String,
)
