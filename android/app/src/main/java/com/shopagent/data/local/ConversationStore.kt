package com.shopagent.data.local

import com.shopagent.domain.ChatMessage
import kotlinx.coroutines.flow.Flow

/** 抽屉历史会话列表条目 */
data class ConversationSummary(
    val id: String,
    val title: String,
    val updatedAt: Long,
)

/**
 * 会话本地持久化接口。ViewModel 只依赖此接口，
 * 生产实现为 [RoomConversationStore]，测试用内存 fake。
 */
interface ConversationStore {

    /** 按更新时间倒序的会话列表，随写入自动刷新 */
    fun observeConversations(): Flow<List<ConversationSummary>>

    /**
     * 整体覆盖保存一个会话：标题取首条用户消息（截断 20 字），
     * 消息全量替换（重试会更换助手消息 id，旧行需清掉）。
     */
    suspend fun saveConversation(conversationId: String, messages: List<ChatMessage>)

    /** 按会话内顺序读出全部消息 */
    suspend fun loadMessages(conversationId: String): List<ChatMessage>
}
