package com.shopagent.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.shopagent.data.ChatRepository
import com.shopagent.data.ChatStreamUpdate
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val isStreaming: Boolean = false,
)

class ChatViewModel(
    private val repository: ChatRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private var conversationId: String? = null
    private var streamJob: Job? = null

    /** 最近一次发送的用户文本，供 retryable 错误重试 */
    private var lastUserText: String? = null

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
            repository.streamReply(assistantId, text, conversationId).collect { update ->
                when (update) {
                    is ChatStreamUpdate.ConversationId -> conversationId = update.value
                    is ChatStreamUpdate.AssistantState -> {
                        _uiState.update { state ->
                            state.copy(
                                messages = state.messages.map {
                                    if (it.id == assistantId) update.message else it
                                },
                                isStreaming = update.message.status == MessageStatus.Streaming,
                            )
                        }
                    }
                }
            }
        }
    }

    private fun newId(): String = UUID.randomUUID().toString()

    class Factory(private val repository: ChatRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ChatViewModel(repository) as T
    }
}
