"""Deterministic policy for optional, category-specific preference questions."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from shop_agent.catalog import ProductCatalog
from shop_agent.models.conversation import QuerySnapshot


ProactiveSearchIntent = Literal[
    "new_search",
    "refine_search",
    "switch_category",
    "more_results",
]


QUESTION_POLICIES: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        (
            "数码电子",
            "智能手机",
        ): "你更看重拍照、续航、性能还是性价比？也可以补充预算。",
        (
            "数码电子",
            "真无线耳机",
        ): "你更看重降噪、音质、佩戴体验还是续航？也可以补充预算。",
        (
            "数码电子",
            "笔记本电脑",
        ): "主要用于办公学习、便携出行还是内容创作？也可以补充预算。",
        (
            "数码电子",
            "平板电脑",
        ): "主要用于学习办公、影音娱乐还是绘画创作？也可以补充预算。",
        (
            "美妆护肤",
            "精华",
        ): "你更关注修护、提亮、淡纹抗老还是控油？也可以补充预算。",
        (
            "服饰运动",
            "跑步鞋",
        ): "你更偏向日常训练、长距离缓震还是轻量竞速？也可以补充预算和尺码。",
        (
            "食品饮料",
            "咖啡",
        ): "你更偏好黑咖啡、奶咖口感还是冷萃便捷？也可以补充预算。",
        (
            "食品饮料",
            "方便食品",
        ): "你更偏好哪种口味，以及杯装还是袋装？也可以补充数量或预算。",
    }
)


@dataclass(frozen=True, slots=True)
class ProactiveClarificationDecision:
    should_ask: bool
    message: str | None = None

    def __post_init__(self) -> None:
        if self.should_ask != (self.message is not None):
            raise ValueError("ask decisions require a message and continue decisions forbid it")


def decide_proactive_clarification(
    *,
    catalog: ProductCatalog,
    snapshot: QuerySnapshot,
    search_intent: ProactiveSearchIntent,
    final_product_limit: int,
    skip_preference_question: bool = False,
) -> ProactiveClarificationDecision:
    """Return one approved question or a deterministic continue decision."""

    if (
        search_intent not in {"new_search", "switch_category"}
        or skip_preference_question
        or not _contains_only_category(snapshot)
        or snapshot.category is None
        or snapshot.sub_category is None
    ):
        return ProactiveClarificationDecision(should_ask=False)

    scope = (snapshot.category, snapshot.sub_category)
    message = QUESTION_POLICIES.get(scope)
    if message is None:
        return ProactiveClarificationDecision(should_ask=False)

    product_count = sum(
        product.category == snapshot.category
        and product.sub_category == snapshot.sub_category
        for product in catalog.all()
    )
    if product_count <= final_product_limit:
        return ProactiveClarificationDecision(should_ask=False)

    return ProactiveClarificationDecision(should_ask=True, message=message)


def _contains_only_category(snapshot: QuerySnapshot) -> bool:
    constraints = snapshot.constraints
    return not (
        snapshot.semantic_terms
        or constraints.min_price is not None
        or constraints.max_price is not None
        or constraints.price_preference is not None
        or constraints.include_brands
        or constraints.exclude_brands
        or constraints.required_features
        or constraints.excluded_features
        or constraints.sku_constraints
        or constraints.numeric_constraints
    )
