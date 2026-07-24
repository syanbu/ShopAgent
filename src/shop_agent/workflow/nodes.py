import json
import logging
from dataclasses import dataclass
from typing import Literal

from langgraph.types import StreamWriter

from shop_agent.errors import ServiceError
from shop_agent.models.events import ProductEventData, TextDeltaData
from shop_agent.models.retrieval import RetrievedChunk, SelectedProduct
from shop_agent.models.state import ShoppingState
from shop_agent.workflow.dependencies import WorkflowDependencies


logger = logging.getLogger("uvicorn.error")
IntentRoute = Literal["product_search", "non_shopping"]
RetrievalRoute = Literal["has_results", "no_results"]
ValidationRoute = Literal["has_candidates", "no_candidates"]
SAFETY_RULES = (
    "不得声称库存、优惠、优惠券或购买链接；不得补充已校验事实之外的功能、"
    "属性、价格、SKU 或其他事实。"
)


@dataclass(frozen=True, slots=True)
class WorkflowNodes:
    dependencies: WorkflowDependencies

    async def structure_intent(self, state: ShoppingState) -> dict[str, object]:
        parsed = await self.dependencies.intent_parser.parse(state["user_message"])
        updates: dict[str, object] = {
            "parsed_intent": parsed,
            "response_mode": "shopping"
            if parsed.intent == "product_search"
            else "non_shopping",
        }
        request_id = state.get("request_id")
        if request_id is None:
            request_id = self.dependencies.id_factory()
            updates["request_id"] = request_id
        conversation_id = state.get("conversation_id")
        if conversation_id is None:
            conversation_id = self.dependencies.id_factory()
            updates["conversation_id"] = conversation_id
        log_payload = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "intent": parsed.model_dump(mode="json"),
        }
        logger.info(
            "parsed_intent %s",
            json.dumps(
                log_payload,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        )
        return updates

    async def retrieve_chunks(self, state: ShoppingState) -> dict[str, object]:
        chunks = await self.dependencies.retrieval_service.retrieve_chunks(
            state["parsed_intent"]
        )
        updates: dict[str, object] = {"retrieved_chunks": chunks}
        if not chunks:
            updates["response_mode"] = "no_results"
        return updates

    async def aggregate_products(self, state: ShoppingState) -> dict[str, object]:
        candidates = self.dependencies.retrieval_service.aggregate_products(
            state["retrieved_chunks"]
        )
        return {"candidates": candidates}

    async def semantic_rerank(self, state: ShoppingState) -> dict[str, object]:
        query = state["parsed_intent"].retrieval_query
        if query is None:
            raise ValueError("product search is missing a retrieval query")
        candidates = await self.dependencies.retrieval_service.rerank_candidates(
            query, state["candidates"]
        )
        return {"candidates": candidates}

    async def validate_evidence(self, state: ShoppingState) -> dict[str, object]:
        intent = state["parsed_intent"]
        validated = await self.dependencies.evidence_service.validate_candidates(
            state["candidates"],
            intent.constraints,
            category=intent.category,
            sub_category=intent.sub_category,
        )
        updates: dict[str, object] = {"validated_candidates": validated}
        if not any(candidate.eligible for candidate in validated):
            updates["response_mode"] = "no_results"
        return updates

    async def decide_candidates(
        self, state: ShoppingState, writer: StreamWriter
    ) -> dict[str, object]:
        intent = state["parsed_intent"]
        selected = self.dependencies.evidence_service.select_candidates(
            state["validated_candidates"],
            self.dependencies.settings.final_product_limit,
            constraints=intent.constraints,
        )
        for rank, item in enumerate(selected, start=1):
            event = self._product_event(rank, item)
            writer({"event": "product", "data": event.model_dump(mode="json")})
        return {"selected_products": selected, "response_mode": "shopping"}

    async def generate_response(
        self, state: ShoppingState, writer: StreamWriter
    ) -> dict[str, str]:
        prompt = build_verified_response_prompt(state, self.dependencies)
        parts: list[str] = []
        async for delta in self.dependencies.response_generator.stream(prompt):
            if not delta:
                continue
            parts.append(delta)
            data = TextDeltaData(delta=delta)
            writer({"event": "text_delta", "data": data.model_dump(mode="json")})
        if not parts:
            raise ServiceError(
                "GENERATION_FAILED",
                "model returned no response text",
                retryable=True,
            )
        return {"response_text": "".join(parts)}

    def _product_event(self, rank: int, selected: SelectedProduct) -> ProductEventData:
        product = self.dependencies.catalog.get(selected.product_id)
        selected_sku_ids = set(selected.matched_sku_ids)
        matched_skus = [sku for sku in product.skus if sku.sku_id in selected_sku_ids]
        image_url = None
        if self.dependencies.catalog.image_file(product.product_id).is_file():
            base_url = self.dependencies.settings.public_base_url.rstrip("/")
            image_url = f"{base_url}/api/v1/products/{product.product_id}/image"
        return ProductEventData(
            rank=rank,
            product_id=product.product_id,
            title=product.title,
            brand=product.brand,
            base_price=product.base_price,
            display_price=min(
                (sku.price for sku in matched_skus), default=product.base_price
            ),
            matched_skus=matched_skus,
            image_url=image_url,
        )


def build_nodes(dependencies: WorkflowDependencies) -> WorkflowNodes:
    return WorkflowNodes(dependencies)


def route_intent(state: ShoppingState) -> IntentRoute:
    return state["parsed_intent"].intent


def route_retrieval(state: ShoppingState) -> RetrievalRoute:
    return "has_results" if state["retrieved_chunks"] else "no_results"


def route_validation(state: ShoppingState) -> ValidationRoute:
    return (
        "has_candidates"
        if any(candidate.eligible for candidate in state["validated_candidates"])
        else "no_candidates"
    )


def build_verified_response_prompt(
    state: ShoppingState, dependencies: WorkflowDependencies
) -> str:
    user_message = state["user_message"]
    mode = state.get("response_mode")
    if mode == "non_shopping":
        return (
            "你是文本导购助手。用户本次输入不是商品搜索，请简短、自然地回应，"
            "不要虚构商品推荐。\n"
            f"{SAFETY_RULES}\n"
            f"用户原话：{user_message}"
        )

    selected = state.get("selected_products", [])
    if not selected:
        reason = (
            "没有召回到商品"
            if not state.get("retrieved_chunks")
            else "没有通过证据校验的商品"
        )
        return (
            "你是文本导购助手。请告知用户当前没有可靠的匹配结果，建议放宽或修改条件。"
            f"原因：{reason}。不要推荐未提供的商品。\n"
            f"{SAFETY_RULES}\n"
            f"用户原话：{user_message}"
        )

    facts = [
        _selected_product_facts(state, selected_product, dependencies)
        for selected_product in selected
    ]
    facts_json = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return (
        "你是文本导购助手。请基于下方已校验事实简洁说明推荐理由。\n"
        f"{SAFETY_RULES}\n"
        f"用户原话：{user_message}\n"
        f"已校验事实：{facts_json}"
    )


def _selected_product_facts(
    state: ShoppingState,
    selected: SelectedProduct,
    dependencies: WorkflowDependencies,
) -> dict[str, object]:
    product = dependencies.catalog.get(selected.product_id)
    selected_sku_ids = set(selected.matched_sku_ids)
    matched_skus = [
        sku.model_dump(mode="json")
        for sku in product.skus
        if sku.sku_id in selected_sku_ids
    ]
    evidence_by_id = _evidence_by_id(state, selected.product_id)
    evidence = [
        {
            "chunk_id": evidence_id,
            "text": evidence_by_id[evidence_id].text,
        }
        for evidence_id in selected.evidence_ids
        if evidence_id in evidence_by_id
    ]
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
        "matched_skus": matched_skus,
        "evidence": evidence,
    }


def _evidence_by_id(state: ShoppingState, product_id: str) -> dict[str, RetrievedChunk]:
    for item in state.get("validated_candidates", []):
        if item.candidate.product.product_id == product_id:
            return {chunk.chunk_id: chunk for chunk in item.candidate.evidence}
    return {}
