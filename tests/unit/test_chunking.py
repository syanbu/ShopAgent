from uuid import NAMESPACE_URL, uuid5

from shop_agent.chunking import build_product_chunks


def test_chunking_emits_expected_chunks_in_order(sample_product) -> None:
    source_path = "1_数码电子/data/p_digital_001.json"
    chunks = build_product_chunks(sample_product, source_path)
    expected = (
        1
        + len(sample_product.rag_knowledge.official_faq)
        + len(sample_product.rag_knowledge.user_reviews)
    )
    assert len(chunks) == expected
    assert all(chunk.product_id == sample_product.product_id for chunk in chunks)
    assert [
        (chunk.chunk_id, chunk.chunk_type, chunk.text, chunk.source_path)
        for chunk in chunks
    ] == [
        (
            "p_digital_001:summary",
            "product_summary",
            "商品：测试蓝牙耳机\n品牌：测试品牌\n类目：数码电子/蓝牙耳机\n适合通勤的测试蓝牙耳机。",
            source_path,
        ),
        (
            "p_digital_001:faq:0",
            "official_faq",
            "问题：是否支持蓝牙？\n回答：支持蓝牙连接。",
            source_path,
        ),
        (
            "p_digital_001:review:0",
            "user_review",
            "评分：5/5\n评价：佩戴舒适。",
            source_path,
        ),
    ]


def test_chunking_point_ids_use_chunk_id_uuid5_namespace(sample_product) -> None:
    chunks = build_product_chunks(sample_product, "source.json")

    assert all(
        chunk.point_id == str(uuid5(NAMESPACE_URL, chunk.chunk_id)) for chunk in chunks
    )


def test_chunking_is_deterministic(sample_product) -> None:
    first = build_product_chunks(sample_product, "source.json")
    second = build_product_chunks(sample_product, "source.json")
    assert [chunk.point_id for chunk in first] == [chunk.point_id for chunk in second]


def test_chunking_strips_outer_whitespace_from_text(sample_product) -> None:
    product = sample_product.model_copy(deep=True)
    product.rag_knowledge.marketing_description = "  适合通勤  "
    product.rag_knowledge.official_faq[0].question = "  是否支持蓝牙？"
    product.rag_knowledge.official_faq[0].answer = "支持蓝牙连接。  "
    product.rag_knowledge.user_reviews[0].content = "  佩戴舒适。  "

    chunks = build_product_chunks(product, "source.json")

    assert all(chunk.text == chunk.text.strip() for chunk in chunks)
