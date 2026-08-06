package com.shopagent.data.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SseEventsTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `message_start 反序列化`() {
        val dto = json.decodeFromString<MessageStartDto>(
            """{"request_id":"req-1","conversation_id":"conv-1"}""",
        )
        assertEquals("req-1", dto.requestId)
        assertEquals("conv-1", dto.conversationId)
    }

    @Test
    fun `product 反序列化 image_url 为 null`() {
        val dto = json.decodeFromString<ProductDto>(
            """
            {
              "rank": 1,
              "product_id": "p_beauty_001",
              "title": "氨基酸温和洁面乳",
              "brand": "某品牌",
              "base_price": 99.0,
              "display_price": 79.0,
              "matched_skus": [
                {"sku_id": "sku_a", "properties": {"规格": "100ml"}, "price": 79.0}
              ],
              "image_url": null
            }
            """.trimIndent(),
        )
        assertNull(dto.imageUrl)
        assertEquals(1, dto.matchedSkus.size)
        assertEquals(79.0, dto.displayPrice, 0.001)
    }

    @Test
    fun `product 反序列化 多 SKU`() {
        val dto = json.decodeFromString<ProductDto>(
            """
            {
              "rank": 2,
              "product_id": "p_digital_008",
              "title": "旗舰手机",
              "brand": "某手机",
              "base_price": 3999.0,
              "display_price": 3599.0,
              "matched_skus": [
                {"sku_id": "sku_1", "properties": {"颜色": "曜石黑", "容量": "256GB"}, "price": 3599.0},
                {"sku_id": "sku_2", "properties": {"颜色": "皓月白", "容量": "256GB"}, "price": 3599.0},
                {"sku_id": "sku_3", "properties": {"颜色": "曜石黑", "容量": "512GB"}, "price": 3999.0}
              ],
              "image_url": "http://10.0.2.2:8000/api/v1/products/p_digital_008/image"
            }
            """.trimIndent(),
        )
        assertEquals(3, dto.matchedSkus.size)
        assertEquals("曜石黑", dto.matchedSkus[0].properties["颜色"])
        assertEquals(
            "http://10.0.2.2:8000/api/v1/products/p_digital_008/image",
            dto.imageUrl,
        )
    }

    @Test
    fun `text_delta 反序列化`() {
        val dto = json.decodeFromString<TextDeltaDto>("""{"delta":"推荐这款"}""")
        assertEquals("推荐这款", dto.delta)
    }

    @Test
    fun `error 反序列化 各错误码`() {
        val codes = listOf(
            "INTENT_PARSE_FAILED",
            "EVIDENCE_PARSE_FAILED",
            "EMBEDDING_UNAVAILABLE",
            "RETRIEVAL_UNAVAILABLE",
            "RERANK_UNAVAILABLE",
            "GENERATION_FAILED",
            "INTERNAL_ERROR",
        )
        for (code in codes) {
            val dto = json.decodeFromString<ErrorDto>(
                """{"code":"$code","message":"出错了","retryable":true}""",
            )
            assertEquals(code, dto.code)
            assertTrue(dto.retryable)
        }
    }

    @Test
    fun `error 缺省 retryable 为 false`() {
        val dto = json.decodeFromString<ErrorDto>(
            """{"code":"INTENT_PARSE_FAILED","message":"无法理解"}""",
        )
        assertTrue(!dto.retryable)
    }

    @Test
    fun `message_end 各 status 反序列化`() {
        for (status in listOf("completed", "partial", "failed")) {
            val dto = json.decodeFromString<MessageEndDto>(
                """{"request_id":"req-1","status":"$status"}""",
            )
            assertEquals(status, dto.status)
        }
    }

    @Test
    fun `未知字段被忽略`() {
        val dto = json.decodeFromString<MessageStartDto>(
            """{"request_id":"req-1","conversation_id":"conv-1","extra":"x"}""",
        )
        assertEquals("conv-1", dto.conversationId)
    }
}
