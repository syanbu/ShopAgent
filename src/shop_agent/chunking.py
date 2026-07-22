from uuid import NAMESPACE_URL, uuid5

from shop_agent.models.product import Product
from shop_agent.models.retrieval import ChunkType, EvidenceChunk


def _chunk(
    product: Product,
    chunk_id: str,
    chunk_type: ChunkType,
    text: str,
    source_path: str,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        point_id=str(uuid5(NAMESPACE_URL, chunk_id)),
        product_id=product.product_id,
        chunk_type=chunk_type,
        text=text.strip(),
        source_path=source_path,
    )


def build_product_chunks(product: Product, source_path: str) -> list[EvidenceChunk]:
    summary = "\n".join(
        [
            f"商品：{product.title}",
            f"品牌：{product.brand}",
            f"类目：{product.category}/{product.sub_category}",
            product.rag_knowledge.marketing_description,
        ]
    )
    chunks = [
        _chunk(
            product,
            f"{product.product_id}:summary",
            "product_summary",
            summary,
            source_path,
        )
    ]
    for index, faq in enumerate(product.rag_knowledge.official_faq):
        chunks.append(
            _chunk(
                product,
                f"{product.product_id}:faq:{index}",
                "official_faq",
                f"问题：{faq.question}\n回答：{faq.answer}",
                source_path,
            )
        )
    for index, review in enumerate(product.rag_knowledge.user_reviews):
        chunks.append(
            _chunk(
                product,
                f"{product.product_id}:review:{index}",
                "user_review",
                f"评分：{review.rating}/5\n评价：{review.content}",
                source_path,
            )
        )
    return chunks
