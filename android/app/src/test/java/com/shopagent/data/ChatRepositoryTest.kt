package com.shopagent.data

import com.shopagent.data.dto.ChatRequest
import com.shopagent.data.dto.ErrorDto
import com.shopagent.data.dto.MessageEndDto
import com.shopagent.data.dto.MessageStartDto
import com.shopagent.data.dto.ProductDto
import com.shopagent.data.dto.SkuDto
import com.shopagent.data.dto.SseEvent
import com.shopagent.data.dto.TextDeltaDto
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus
import java.io.IOException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeStreamSource(
    private val result: Flow<SseEvent>,
) : ChatStreamSource {
    val requests = mutableListOf<ChatRequest>()

    override fun stream(request: ChatRequest): Flow<SseEvent> {
        requests += request
        return result
    }
}

class ChatRepositoryTest {

    private fun product(rank: Int = 1, imageUrl: String? = null) = SseEvent.Product(
        ProductDto(
            rank = rank,
            productId = "p_$rank",
            title = "商品$rank",
            brand = "品牌",
            basePrice = 100.0,
            displayPrice = 80.0,
            matchedSkus = listOf(
                SkuDto(skuId = "sku_1", properties = mapOf("规格" to "标准"), price = 80.0),
                SkuDto(skuId = "sku_2", properties = mapOf("规格" to "加量"), price = 90.0),
            ),
            imageUrl = imageUrl,
        ),
    )

    private fun messageStart() =
        SseEvent.MessageStart(MessageStartDto(requestId = "req-1", conversationId = "conv-1"))

    private fun messageEnd(status: String) =
        SseEvent.MessageEnd(MessageEndDto(requestId = "req-1", status = status))

    private suspend fun collect(source: FakeStreamSource): Pair<List<ChatStreamUpdate>, List<ChatMessage.Assistant>> {
        val repo = ChatRepository(source)
        val updates = repo.streamReply("msg-1", "你好", conversationId = null).toList()
        val states = updates.filterIsInstance<ChatStreamUpdate.AssistantState>().map { it.message }
        return updates to states
    }

    @Test
    fun `product 先于 text_delta 聚合`() = runTest {
        val source = FakeStreamSource(
            flowOf(
                messageStart(),
                product(rank = 1),
                product(rank = 2),
                SseEvent.TextDelta(TextDeltaDto("推荐")),
                SseEvent.TextDelta(TextDeltaDto("这两款")),
                messageEnd("completed"),
            ),
        )
        val (updates, states) = collect(source)

        // 首个快照为空助手占位
        assertEquals(MessageStatus.Streaming, states.first().status)
        assertTrue(states.first().products.isEmpty())

        // 商品先聚合，此时还没有文本
        assertEquals(1, states[1].products.size)
        assertEquals("", states[1].text)
        assertEquals(2, states[2].products.size)
        assertEquals("商品1", states[2].products[0].title)
        assertEquals(2, states[2].products[0].matchedSkus.size)

        // 文本逐段追加
        assertEquals("推荐", states[3].text)
        assertEquals("推荐这两款", states[4].text)

        // 终态 Done，内容完整保留
        val last = states.last()
        assertEquals(MessageStatus.Done, last.status)
        assertEquals(2, last.products.size)
        assertEquals("推荐这两款", last.text)
        assertNull(last.error)

        // conversation_id 事件
        val conv = updates.filterIsInstance<ChatStreamUpdate.ConversationId>()
        assertEquals("conv-1", conv.single().value)

        // 请求体未携带 conversation_id（首轮）
        assertNull(source.requests.single().conversationId)
        assertEquals("你好", source.requests.single().message)
    }

    @Test
    fun `error 后接 message_end partial 保留已收内容`() = runTest {
        val source = FakeStreamSource(
            flowOf(
                messageStart(),
                product(),
                SseEvent.TextDelta(TextDeltaDto("部分内容")),
                SseEvent.Error(ErrorDto("GENERATION_FAILED", "生成中断", retryable = true)),
                messageEnd("partial"),
            ),
        )
        val (_, states) = collect(source)

        // error 事件记录后状态仍为 Streaming，内容保留
        val withError = states.first { it.error != null }
        assertEquals(MessageStatus.Streaming, withError.status)
        assertEquals("GENERATION_FAILED", withError.error?.code)
        assertEquals(true, withError.error?.retryable)

        // message_end partial：终态 Partial，商品与文本保留
        val last = states.last()
        assertEquals(MessageStatus.Partial, last.status)
        assertEquals(1, last.products.size)
        assertEquals("部分内容", last.text)
        assertNotNull(last.error)
    }

    @Test
    fun `message_end failed 映射为 Failed`() = runTest {
        val source = FakeStreamSource(
            flowOf(
                messageStart(),
                SseEvent.Error(ErrorDto("INTERNAL_ERROR", "内部错误", retryable = false)),
                messageEnd("failed"),
            ),
        )
        val (_, states) = collect(source)
        val last = states.last()
        assertEquals(MessageStatus.Failed, last.status)
        assertEquals(false, last.error?.retryable)
    }

    @Test
    fun `传输层异常转为 retryable Failed`() = runTest {
        val source = FakeStreamSource(
            flow { throw IOException("连接超时") },
        )
        val (_, states) = collect(source)
        val last = states.last()
        assertEquals(MessageStatus.Failed, last.status)
        assertEquals("NETWORK", last.error?.code)
        assertEquals(true, last.error?.retryable)
    }

    @Test
    fun `流提前关闭未收到 message_end 视为失败`() = runTest {
        val source = FakeStreamSource(
            flowOf(
                messageStart(),
                SseEvent.TextDelta(TextDeltaDto("半截文本")),
            ),
        )
        val (_, states) = collect(source)
        val last = states.last()
        assertEquals(MessageStatus.Failed, last.status)
        assertEquals("CONNECTION_CLOSED", last.error?.code)
        // 已收到的文本保留
        assertEquals("半截文本", last.text)
    }

    @Test
    fun `非 2xx 响应转为 retryable Failed`() = runTest {
        val source = FakeStreamSource(
            flow { throw HttpStatusException(500) },
        )
        val (_, states) = collect(source)
        val last = states.last()
        assertEquals(MessageStatus.Failed, last.status)
        assertEquals("HTTP_500", last.error?.code)
        assertEquals(true, last.error?.retryable)
    }
}
