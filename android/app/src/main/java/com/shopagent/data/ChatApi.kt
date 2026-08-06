package com.shopagent.data

import com.shopagent.data.dto.ChatRequest
import com.shopagent.data.dto.ErrorDto
import com.shopagent.data.dto.MessageEndDto
import com.shopagent.data.dto.MessageStartDto
import com.shopagent.data.dto.ProductDto
import com.shopagent.data.dto.SseEvent
import com.shopagent.data.dto.TextDeltaDto
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/** SSE 事件源抽象，便于在单元测试中替换为 fake 实现 */
interface ChatStreamSource {
    fun stream(request: ChatRequest): Flow<SseEvent>
}

/** 服务端返回非 2xx 时的异常 */
class HttpStatusException(val statusCode: Int) : Exception("HTTP $statusCode")

class ChatApi(
    private val baseUrl: String,
    private val json: Json = defaultJson(),
    client: OkHttpClient? = null,
) : ChatStreamSource {

    private val client: OkHttpClient = client ?: OkHttpClient.Builder()
        // SSE 长连接：关闭读超时，依赖后端 ping 保活
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    override fun stream(request: ChatRequest): Flow<SseEvent> = callbackFlow {
        val body = json.encodeToString(ChatRequest.serializer(), request)
            .toRequestBody("application/json; charset=utf-8".toMediaType())
        val httpRequest = Request.Builder()
            .url("$baseUrl/api/v1/chat/stream")
            .post(body)
            .build()

        val listener = object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                parseEvent(type, data)?.let { trySend(it) }
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                val error = when {
                    t != null -> t
                    response != null -> HttpStatusException(response.code)
                    else -> IllegalStateException("SSE connection failed")
                }
                close(error)
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }
        }

        val eventSource = EventSources.createFactory(client).newEventSource(httpRequest, listener)
        awaitClose { eventSource.cancel() }
    }

    private fun parseEvent(type: String?, data: String): SseEvent? = when (type) {
        "message_start" -> SseEvent.MessageStart(json.decodeFromString<MessageStartDto>(data))
        "product" -> SseEvent.Product(json.decodeFromString<ProductDto>(data))
        "text_delta" -> SseEvent.TextDelta(json.decodeFromString<TextDeltaDto>(data))
        "error" -> SseEvent.Error(json.decodeFromString<ErrorDto>(data))
        "message_end" -> SseEvent.MessageEnd(json.decodeFromString<MessageEndDto>(data))
        else -> null // ping 等保活帧忽略
    }

    companion object {
        fun defaultJson(): Json = Json { ignoreUnknownKeys = true }
    }
}
