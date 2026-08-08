package com.shopagent.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.shopagent.data.ChatRepository
import com.shopagent.data.ChatStreamUpdate
import com.shopagent.data.local.ConversationStore
import com.shopagent.data.local.ConversationSummary
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val isStreaming: Boolean = false,
)

class ChatViewModel(
    private val repository: ChatRepository,
    private val store: ConversationStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    /** 抽屉历史会话列表，随本地写入自动刷新 */
    val conversations: StateFlow<List<ConversationSummary>> =
        store.observeConversations()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** 当前会话 id；null 表示尚未与后端建立会话 */
    private val _conversationId = MutableStateFlow<String?>(null)
    val conversationId: StateFlow<String?> = _conversationId.asStateFlow()

    private var streamJob: Job? = null
    private var persistJob: Job? = null

    init {
        // 冷启动恢复最近会话：历史列表按更新时间倒序，首条即最近会话
        viewModelScope.launch {
            val latest = store.observeConversations().first().firstOrNull() ?: return@launch
            openConversation(latest.id)
        }
    }

    /** 有新消息落库前为 true；纯查看历史会话不刷新其排序位置 */
    private var hasUnsavedChanges = false

    /** 最近一次发送的用户文本，供 retryable 错误重试 */
    private var lastUserText: String? = null

    /**
     * 开启新会话：先保存当前会话，再取消进行中的流、清空消息并复位
     * conversation_id。下次发送时不携带旧 id，由后端分配新会话，与旧会话隔离。
     */
    fun newConversation() {
        persist()
        streamJob?.cancel()
        streamJob = null
        _conversationId.value = null
        lastUserText = null
        hasUnsavedChanges = false
        _uiState.value = ChatUiState()
    }

    /** 打开历史会话：保存当前会话后，从本地载入旧消息并恢复 conversation_id，可继续聊 */
    fun openConversation(id: String) {
        if (id == _conversationId.value) return
        persist()
        streamJob?.cancel()
        streamJob = null
        viewModelScope.launch {
            val messages = store.loadMessages(id)
            _conversationId.value = id
            // 恢复最后一条用户文本，让历史会话里的 retryable 失败仍可重试
            lastUserText = messages.filterIsInstance<ChatMessage.User>().lastOrNull()?.text
            // 载入的内容与本地库一致，标记为无需保存
            hasUnsavedChanges = false
            _uiState.value = ChatUiState(messages = messages)
        }
    }

    fun send(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty() || _uiState.value.isStreaming) return

        lastUserText = trimmed
        val userMessage = ChatMessage.User(id = newId(), text = trimmed)
        val assistantId = newId()

        _uiState.update { state ->
            state.copy(
                messages = state.messages + userMessage + ChatMessage.Assistant(id = assistantId),
                isStreaming = true,
            )
        }
        startStream(assistantId, trimmed)
    }

    /** 重试最近一次失败的用户消息：替换失败的助手占位，不重复插入用户气泡 */
    fun retry(failedAssistantId: String) {
        val text = lastUserText ?: return
        if (_uiState.value.isStreaming) return

        val assistantId = newId()
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map {
                    if (it.id == failedAssistantId) ChatMessage.Assistant(id = assistantId) else it
                },
                isStreaming = true,
            )
        }
        startStream(assistantId, text)
    }

    private fun startStream(assistantId: String, text: String) {
        streamJob?.cancel()
        streamJob = viewModelScope.launch {
            repository.streamReply(assistantId, text, _conversationId.value).collect { update ->
                when (update) {
                    is ChatStreamUpdate.ConversationId -> _conversationId.value = update.value
                    is ChatStreamUpdate.AssistantState -> {
                        _uiState.update { state ->
                            state.copy(
                                messages = state.messages.map {
                                    if (it.id == assistantId) update.message else it
                                },
                                isStreaming = update.message.status == MessageStatus.Streaming,
                            )
                        }
                        // 一轮结束（含失败/partial）即落库；流式中途不写
                        if (update.message.status != MessageStatus.Streaming) {
                            hasUnsavedChanges = true
                            persist()
                        }
                    }
                }
            }
        }
    }

    /**
     * 整体覆盖保存当前会话。只在有新消息（一轮对话结束）时真正写库：
     * 打开/离开会话时的兜底调用若原样写入，会把 updatedAt 刷成当前时间，
     * 导致仅被查看的会话跳到历史列表顶部。
     */
    private fun persist() {
        if (!hasUnsavedChanges) return
        val id = _conversationId.value ?: return
        val messages = _uiState.value.messages
        if (messages.isEmpty()) return
        hasUnsavedChanges = false
        persistJob?.cancel()
        persistJob = viewModelScope.launch {
            store.saveConversation(id, messages)
        }
    }

    private fun newId(): String = UUID.randomUUID().toString()

    class Factory(
        private val repository: ChatRepository,
        private val store: ConversationStore,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ChatViewModel(repository, store) as T
    }
}
