from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.models.product import Product
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceAssessment,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.models.state import ShoppingState


class FakeIntentParser:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.intent: ParsedIntent | None = None

    async def parse(self, message: str) -> ParsedIntent:
        self.calls.append(message)
        if self.intent is not None:
            return self.intent
        if message == "你好":
            return ParsedIntent(
                schema_version=1,
                intent="non_shopping",
                retrieval_query=None,
                category=None,
                sub_category=None,
            )
        return ParsedIntent(
            schema_version=1,
            intent="product_search",
            retrieval_query=message,
            category="数码电子",
            sub_category="蓝牙耳机",
            constraints=SearchConstraints(
                max_price=None if "性价比" in message else 500,
                price_preference="value" if "性价比" in message else None,
            ),
        )


class FakeRetrievalService:
    def __init__(
        self,
        *,
        products: Sequence[Product],
        return_hits: bool,
    ) -> None:
        self.products = list(products)
        self.return_hits = return_hits
        self.retrieve_calls: list[ParsedIntent] = []
        self.aggregate_calls: list[list[RetrievedChunk]] = []
        self.rerank_calls: list[tuple[str, list[ProductCandidate]]] = []

    async def retrieve_chunks(self, intent: ParsedIntent) -> list[RetrievedChunk]:
        self.retrieve_calls.append(intent)
        if not self.return_hits:
            return []
        return [
            RetrievedChunk(
                chunk_id=f"{product.product_id}:summary",
                point_id=f"00000000-0000-0000-0000-{index:012d}",
                product_id=product.product_id,
                chunk_type="product_summary",
                text=f"{product.title} 适合通勤",
                source_path=f"data/{product.product_id}.json",
                score=1 - index / 10,
            )
            for index, product in enumerate(self.products, start=1)
        ]

    def aggregate_products(
        self, chunks: Sequence[RetrievedChunk]
    ) -> list[ProductCandidate]:
        self.aggregate_calls.append(list(chunks))
        chunks_by_product = {chunk.product_id: chunk for chunk in chunks}
        return [
            ProductCandidate(
                product=product,
                evidence=[chunks_by_product[product.product_id]],
            )
            for product in self.products
            if product.product_id in chunks_by_product
        ]

    async def rerank_candidates(
        self, query: str, candidates: Sequence[ProductCandidate]
    ) -> list[ProductCandidate]:
        self.rerank_calls.append((query, list(candidates)))
        return [
            candidate.model_copy(update={"rerank_score": 0.9 - index / 10})
            for index, candidate in enumerate(candidates)
        ]


class FakeEvidenceService:
    def __init__(self, *, catalog: ProductCatalog, eligible: bool) -> None:
        self.catalog = catalog
        self.eligible = eligible
        self.validate_calls: list[
            tuple[
                list[ProductCandidate],
                SearchConstraints,
                str | None,
                str | None,
            ]
        ] = []
        self.select_calls: list[
            tuple[list[ValidatedCandidate], int, SearchConstraints]
        ] = []

    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]:
        self.validate_calls.append(
            (list(candidates), constraints, category, sub_category)
        )
        return [
            ValidatedCandidate(
                candidate=candidate,
                assessment=EvidenceAssessment(product_id=candidate.product.product_id),
                eligible=self.eligible,
                rejection_reasons=[]
                if self.eligible
                else ["semantic_conditions_not_satisfied"],
            )
            for candidate in candidates
        ]

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]:
        self.select_calls.append((list(validated), limit, constraints))
        selected: list[SelectedProduct] = []
        for item in validated:
            if not item.eligible:
                continue
            product_id = item.candidate.product.product_id
            matched = self.catalog.matched_skus(product_id, constraints)
            if not matched:
                continue
            selected.append(
                SelectedProduct(
                    product_id=product_id,
                    rerank_score=item.candidate.rerank_score or 0,
                    evidence_ids=[item.candidate.evidence[0].chunk_id],
                    decision_reasons=["rerank_selected"],
                    matched_sku_ids=[sku.sku_id for sku in matched],
                )
            )
        return selected[:limit]


class FakeResponseGenerator:
    def __init__(self, deltas: Sequence[str] = ("推荐", "完成")) -> None:
        self.deltas = list(deltas)
        self.prompts: list[str] = []

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        for delta in self.deltas:
            yield delta


@dataclass
class WorkflowHarness:
    catalog: ProductCatalog
    settings: Settings
    parser: FakeIntentParser
    retrieval: FakeRetrievalService
    evidence: FakeEvidenceService
    response: FakeResponseGenerator


def build_harness(
    root: Path,
    *,
    product_count: int = 3,
    return_hits: bool = True,
    eligible: bool = True,
) -> WorkflowHarness:
    products = [_product(root, index) for index in range(1, product_count + 1)]
    catalog = ProductCatalog(
        root,
        {product.product_id: product for product in products},
        {product.product_id: f"data/{product.product_id}.json" for product in products},
    )
    return WorkflowHarness(
        catalog=catalog,
        settings=Settings(
            dashscope_api_key="test-key",
            dataset_root=root,
            final_product_limit=3,
            public_base_url="http://testserver",
        ),
        parser=FakeIntentParser(),
        retrieval=FakeRetrievalService(products=products, return_hits=return_hits),
        evidence=FakeEvidenceService(catalog=catalog, eligible=eligible),
        response=FakeResponseGenerator(),
    )


def initial_state(message: str) -> ShoppingState:
    return {
        "request_id": "request-fixed",
        "conversation_id": "conversation-fixed",
        "user_message": message,
    }


def _product(root: Path, index: int) -> Product:
    product_id = f"p{index}"
    image_path = f"images/{product_id}.jpg"
    image_file = root / image_path
    image_file.parent.mkdir(exist_ok=True)
    image_file.write_bytes(b"image")
    return Product.model_validate(
        {
            "product_id": product_id,
            "title": f"通勤耳机 {index}",
            "brand": f"品牌 {index}",
            "category": "数码电子",
            "sub_category": "蓝牙耳机",
            "base_price": 399 + index,
            "image_path": image_path,
            "skus": [
                {
                    "sku_id": f"{product_id}-black",
                    "properties": {"颜色": "黑色"},
                    "price": 399 + index,
                },
                {
                    "sku_id": f"{product_id}-white",
                    "properties": {"颜色": "白色"},
                    "price": 599 + index,
                },
            ],
            "rag_knowledge": {
                "marketing_description": "测试商品",
                "official_faq": [],
                "user_reviews": [],
            },
        }
    )
