from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.models.conversation import QuerySnapshot
from shop_agent.models.query import NumericConstraint, SearchConstraints
from shop_agent.services.proactive_clarification import (
    decide_proactive_clarification,
)


@pytest.fixture(scope="module")
def catalog() -> ProductCatalog:
    return ProductCatalog.load(Path("ecommerce_agent_dataset"))


@pytest.mark.parametrize(
    ("category", "sub_category", "question"),
    [
        (
            "数码电子",
            "智能手机",
            "你更看重拍照、续航、性能还是性价比？也可以补充预算。",
        ),
        (
            "数码电子",
            "真无线耳机",
            "你更看重降噪、音质、佩戴体验还是续航？也可以补充预算。",
        ),
        (
            "数码电子",
            "笔记本电脑",
            "主要用于办公学习、便携出行还是内容创作？也可以补充预算。",
        ),
        (
            "数码电子",
            "平板电脑",
            "主要用于学习办公、影音娱乐还是绘画创作？也可以补充预算。",
        ),
        (
            "美妆护肤",
            "精华",
            "你更关注修护、提亮、淡纹抗老还是控油？也可以补充预算。",
        ),
        (
            "服饰运动",
            "跑步鞋",
            "你更偏向日常训练、长距离缓震还是轻量竞速？也可以补充预算和尺码。",
        ),
        (
            "食品饮料",
            "咖啡",
            "你更偏好黑咖啡、奶咖口感还是冷萃便捷？也可以补充预算。",
        ),
        (
            "食品饮料",
            "方便食品",
            "你更偏好哪种口味，以及杯装还是袋装？也可以补充数量或预算。",
        ),
    ],
)
def test_approved_subcategories_use_exact_question_policy(
    catalog: ProductCatalog,
    category: str,
    sub_category: str,
    question: str,
) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category=category, sub_category=sub_category),
        search_intent="new_search",
        final_product_limit=3,
    )

    assert decision.should_ask is True
    assert decision.message == question


def test_unknown_policy_continues_even_with_more_than_limit(
    catalog: ProductCatalog,
) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category="服饰运动", sub_category="短袖T恤"),
        search_intent="new_search",
        final_product_limit=2,
    )

    assert decision.should_ask is False
    assert decision.message is None


@pytest.mark.parametrize(
    ("semantic_terms", "constraints"),
    [
        (["拍照优先"], SearchConstraints()),
        ([], SearchConstraints(min_price=1000)),
        ([], SearchConstraints(max_price=4000)),
        ([], SearchConstraints(price_preference="value")),
        ([], SearchConstraints(include_brands=["Apple 苹果"])),
        ([], SearchConstraints(exclude_brands=["Apple 苹果"])),
        ([], SearchConstraints(required_features=["防水"])),
        ([], SearchConstraints(excluded_features=["曲面屏"])),
        ([], SearchConstraints(sku_constraints={"color": ["黑色"]})),
        (
            [],
            SearchConstraints(
                numeric_constraints=[
                    NumericConstraint(
                        field="storage",
                        operator=">=",
                        value=256,
                        unit="GB",
                    )
                ]
            ),
        ),
    ],
)
def test_any_decision_signal_skips_proactive_question(
    catalog: ProductCatalog,
    semantic_terms: list[str],
    constraints: SearchConstraints,
) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
            semantic_terms=semantic_terms,
            constraints=constraints,
        ),
        search_intent="new_search",
        final_product_limit=3,
    )

    assert decision.should_ask is False


@pytest.mark.parametrize(
    "search_intent",
    ["refine_search", "more_results"],
)
def test_only_new_search_and_switch_category_can_ask(
    catalog: ProductCatalog,
    search_intent: str,
) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category="数码电子", sub_category="智能手机"),
        search_intent=search_intent,
        final_product_limit=3,
    )

    assert decision.should_ask is False


def test_switch_category_can_ask(catalog: ProductCatalog) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category="数码电子", sub_category="智能手机"),
        search_intent="switch_category",
        final_product_limit=3,
    )

    assert decision.should_ask is True


def test_product_count_must_exceed_display_limit(
    catalog: ProductCatalog,
) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category="美妆护肤", sub_category="面霜"),
        search_intent="new_search",
        final_product_limit=3,
    )

    assert decision.should_ask is False


def test_explicit_skip_forces_continue(catalog: ProductCatalog) -> None:
    decision = decide_proactive_clarification(
        catalog=catalog,
        snapshot=QuerySnapshot(category="数码电子", sub_category="智能手机"),
        search_intent="new_search",
        final_product_limit=3,
        skip_preference_question=True,
    )

    assert decision.should_ask is False
    assert decision.message is None
