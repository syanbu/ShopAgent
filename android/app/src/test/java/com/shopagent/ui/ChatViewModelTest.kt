package com.shopagent.ui

import com.shopagent.data.ChatRepository
import com.shopagent.data.ChatStreamSource
import com.shopagent.data.dto.ChatRequest
import com.shopagent.data.dto.ErrorDto
import com.shopagent.data.dto.MessageEndDto
import com.shopagent.data.dto.MessageStartDto
import com.shopagent.data.dto.SseEvent
import com.shopagent.data.dto.TextDeltaDto
import com.shopagent.data.local.ConversationStore
import com.shopagent.data.local.ConversationSummary
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** 每次 stream 调用按序弹出预置事件流，并记录请求 */
private class ScriptedStreamSource : ChatStreamSource {
    val requests = mutableListOf<ChatRequest>()
    private val scripted = ArrayDeque<Flow<SseEvent>>()

    fun enqueue(events: List<SseEvent>) {
        scripted.addLast(flowOf(*events.toTypedArray()))
    }

    override fun stream(request: ChatRequest): Flow<SseEvent> {
        requests += request
        return scripted.removeFirst()
    }
}

/** 内存版会话存储，记录保存内容并维护列表 Flow */
private class FakeConversationStore : ConversationStore {
    val saved = LinkedHashMap<String, List<ChatMessage>>()
    private val summaries = MutableStateFlow<List<ConversationSummary>>(emptyList())

    override fun observeConversations(): Flow<List<ConversationSummary>> = summaries

    override suspend fun saveConversation(conversationId: String, messages: List<ChatMessage>) {
        saved[conversationId] = messages
        val title = messages.filterIsInstance<ChatMessage.User>().firstOrNull()?.text?.take(20) ?: ""
        summaries.value = summaries.value.filter { it.id != conversationId } +
            ConversationSummary(id = conversationId, title = title, updatedAt = 0L)
    }

    override suspend fun loadMessages(conversationId: String): List<ChatMessage> =
        saved[conversationId] ?: emptyList()
}

@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private lateinit var source: ScriptedStreamSource
    private lateinit var store: FakeConversationStore
    private lateinit var viewModel: ChatViewModel

    private fun messageStart(convId: String = "conv-1") =
        SseEvent.MessageStart(MessageStartDto(requestId = "req-1", conversationId = convId))

    private fun messageEnd(status: String) =
        SseEvent.MessageEnd(MessageEndDto(requestId = "req-1", status = status))

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        source = ScriptedStreamSource()
        store = FakeConversationStore()
        viewModel = ChatViewModel(ChatRepository(source), store)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `发送后插入 User 与 Assistant 占位并完成`() = runTest(dispatcher) {
        source.enqueue(
            listOf(
                messageStart(),
                SseEvent.TextDelta(TextDeltaDto("你好")),
                SseEvent.TextDelta(TextDeltaDto("，我是导购")),
                messageEnd("completed"),
            ),
        )

        viewModel.send("  推荐一款洗面奶  ")

        // 发送后立即插入两条消息（事件尚未消费）
        val before = viewModel.uiState.value
        assertEquals(2, before.messages.size)
        assertTrue(before.messages[0] is ChatMessage.User)
        assertEquals("推荐一款洗面奶", (before.messages[0] as ChatMessage.User).text)
        val placeholder = before.messages[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Streaming, placeholder.status)
        assertTrue(before.isStreaming)

        advanceUntilIdle()

        val after = viewModel.uiState.value
        assertEquals(2, after.messages.size)
        val assistant = after.messages[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Done, assistant.status)
        assertEquals("你好，我是导购", assistant.text)
        assertTrue(!after.isStreaming)

        // 首轮请求不携带 conversation_id
        assertNull(source.requests.single().conversationId)
    }

    @Test
    fun `多轮请求携带 conversation_id`() = runTest(dispatcher) {
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))

        viewModel.send("第一条")
        advanceUntilIdle()
        viewModel.send("第二条")
        advanceUntilIdle()

        assertEquals(2, source.requests.size)
        assertNull(source.requests[0].conversationId)
        assertEquals("conv-abc", source.requests[1].conversationId)
        assertEquals(4, viewModel.uiState.value.messages.size)
    }

    @Test
    fun `partial 状态保留已收内容`() = runTest(dispatcher) {
        source.enqueue(
            listOf(
                messageStart(),
                SseEvent.TextDelta(TextDeltaDto("半截回复")),
                SseEvent.Error(ErrorDto("GENERATION_FAILED", "生成中断", retryable = true)),
                messageEnd("partial"),
            ),
        )

        viewModel.send("测试")
        advanceUntilIdle()

        val assistant = viewModel.uiState.value.messages[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Partial, assistant.status)
        assertEquals("半截回复", assistant.text)
        assertNotNull(assistant.error)
        assertTrue(!viewModel.uiState.value.isStreaming)
    }

    @Test
    fun `retryable 失败后重试成功`() = runTest(dispatcher) {
        source.enqueue(
            listOf(
                messageStart(),
                SseEvent.Error(ErrorDto("RETRIEVAL_UNAVAILABLE", "检索不可用", retryable = true)),
                messageEnd("failed"),
            ),
        )

        viewModel.send("推荐手机")
        advanceUntilIdle()

        val failed = viewModel.uiState.value.messages[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Failed, failed.status)
        assertEquals("RETRIEVAL_UNAVAILABLE", failed.error?.code)

        // 重试：复用同一用户消息，替换失败的助手占位
        source.enqueue(
            listOf(
                messageStart(),
                SseEvent.TextDelta(TextDeltaDto("这次成功了")),
                messageEnd("completed"),
            ),
        )
        viewModel.retry(failed.id)
        advanceUntilIdle()

        val state = viewModel.uiState.value
        assertEquals(2, state.messages.size) // 没有重复插入用户消息
        val retried = state.messages[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Done, retried.status)
        assertEquals("这次成功了", retried.text)
        assertNull(retried.error)

        // 重试请求携带 conversation_id 且复用原文
        assertEquals(2, source.requests.size)
        assertEquals("推荐手机", source.requests[1].message)
        assertEquals("conv-1", source.requests[1].conversationId)
    }

    @Test
    fun `空白输入不发送`() = runTest(dispatcher) {
        viewModel.send("   ")
        advanceUntilIdle()
        assertTrue(viewModel.uiState.value.messages.isEmpty())
        assertTrue(source.requests.isEmpty())
    }

    @Test
    fun `新会话清空消息并复位 conversation_id`() = runTest(dispatcher) {
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))
        viewModel.send("旧会话消息")
        advanceUntilIdle()
        assertEquals(2, viewModel.uiState.value.messages.size)

        viewModel.newConversation()

        val cleared = viewModel.uiState.value
        assertTrue(cleared.messages.isEmpty())
        assertTrue(!cleared.isStreaming)

        // 新会话的首条请求不再携带旧 conversation_id，与旧会话隔离
        source.enqueue(listOf(messageStart("conv-new"), messageEnd("completed")))
        viewModel.send("新会话消息")
        advanceUntilIdle()

        assertEquals(2, source.requests.size)
        assertNull(source.requests[0].conversationId)
        assertNull(source.requests[1].conversationId)
        assertEquals(2, viewModel.uiState.value.messages.size)
    }

    @Test
    fun `一轮结束后会话自动保存到本地`() = runTest(dispatcher) {
        // conversations 是 WhileSubscribed 共享流，需有收集者才会更新
        backgroundScope.launch { viewModel.conversations.collect { } }
        source.enqueue(
            listOf(
                messageStart("conv-abc"),
                SseEvent.TextDelta(TextDeltaDto("推荐这款")),
                messageEnd("completed"),
            ),
        )

        viewModel.send("推荐洗面奶")
        advanceUntilIdle()

        val saved = store.saved["conv-abc"]
        assertNotNull(saved)
        assertEquals(2, saved!!.size)
        assertTrue(saved[0] is ChatMessage.User)
        val assistant = saved[1] as ChatMessage.Assistant
        assertEquals(MessageStatus.Done, assistant.status)
        assertEquals("推荐洗面奶", viewModel.conversations.value.single().title)
    }

    @Test
    fun `打开历史会话恢复消息并携带原 conversation_id 继续聊`() = runTest(dispatcher) {
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))
        viewModel.send("旧会话消息")
        advanceUntilIdle()

        viewModel.newConversation()
        advanceUntilIdle()
        assertTrue(viewModel.uiState.value.messages.isEmpty())

        viewModel.openConversation("conv-abc")
        advanceUntilIdle()

        val restored = viewModel.uiState.value
        assertEquals(2, restored.messages.size)
        assertEquals("旧会话消息", (restored.messages[0] as ChatMessage.User).text)
        assertEquals("conv-abc", viewModel.conversationId.value)

        // 在历史会话里继续发送，请求携带原 conversation_id
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))
        viewModel.send("接着问")
        advanceUntilIdle()
        assertEquals("conv-abc", source.requests.last().conversationId)
        assertEquals(4, viewModel.uiState.value.messages.size)
    }

    @Test
    fun `新会话不影响已保存的历史记录`() = runTest(dispatcher) {
        backgroundScope.launch { viewModel.conversations.collect { } }
        source.enqueue(listOf(messageStart("conv-abc"), messageEnd("completed")))
        viewModel.send("旧会话消息")
        advanceUntilIdle()

        viewModel.newConversation()
        advanceUntilIdle()

        assertTrue(store.saved.containsKey("conv-abc"))
        assertEquals(1, viewModel.conversations.value.size)
    }
}
