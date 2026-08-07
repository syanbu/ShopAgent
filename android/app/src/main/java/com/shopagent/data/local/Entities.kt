package com.shopagent.data.local

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "conversations")
data class ConversationEntity(
    @PrimaryKey val id: String,
    val title: String,
    val updatedAt: Long,
)

@Entity(
    tableName = "messages",
    foreignKeys = [
        ForeignKey(
            entity = ConversationEntity::class,
            parentColumns = ["id"],
            childColumns = ["conversationId"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("conversationId")],
)
data class MessageEntity(
    @PrimaryKey val id: String,
    val conversationId: String,
    /** "user" | "assistant" */
    val role: String,
    val text: String,
    /** 助手消息的商品卡片列表（ProductDto JSON），用户消息为 null */
    val productsJson: String?,
    /** 助手消息状态（MessageStatus.name），用户消息为 null */
    val status: String?,
    /** 助手消息错误（ErrorDto JSON），无错误为 null */
    val errorJson: String?,
    /** 会话内消息顺序 */
    val sortIndex: Int,
)
