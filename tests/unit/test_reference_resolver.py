from pathlib import Path

import pytest
from pydantic import ValidationError

from shop_agent.catalog import ProductCatalog
from shop_agent.models import (
    CandidateReference,
    CategoryCandidate,
    CategoryReference,
    ConversationState,
    ProductReference,
    ReferenceCandidateMatch,
)
from shop_agent.models.product import Product
from shop_agent.services.reference_resolver import (
    CategoryResolution,
    ReferenceResolution,
    resolve_category_reference,
    resolve_reference,
)


def _three_product_catalog() -> ProductCatalog:
    sample_product = Product.model_validate(
        {
            "product_id": "base",
            "title": "Base product",
            "brand": "Base brand",
            "category": "数码电子",
            "sub_category": "蓝牙耳机",
            "base_price": 100,
            "image_path": "image.jpg",
            "skus": [{"sku_id": "sku-1", "properties": {}, "price": 100}],
            "rag_knowledge": {
                "marketing_description": "description",
                "official_faq": [],
                "user_reviews": [],
            },
        }
    )
    products = [
        sample_product.model_copy(
            update={"product_id": "p1", "title": " Alpha One ", "brand": "共享品牌"}
        ),
        sample_product.model_copy(
            update={"product_id": "p2", "title": "Beta Two", "brand": "共享品牌"}
        ),
        sample_product.model_copy(
            update={"product_id": "p3", "title": "Gamma Three", "brand": "唯一品牌"}
        ),
        sample_product.model_copy(
            update={
                "product_id": "p_old",
                "title": "Older Batch Product",
                "brand": "旧批次品牌",
            }
        ),
    ]
    return ProductCatalog(
        Path("."),
        {product.product_id: product for product in products},
        {},
    )


def _category_catalog() -> ProductCatalog:
    base = _three_product_catalog().get("p1")
    products = [
        base.model_copy(
            update={
                "product_id": "earphone",
                "title": "测试真无线耳机",
                "category": "数码电子",
                "sub_category": "真无线耳机",
            }
        ),
        base.model_copy(
            update={
                "product_id": "running",
                "title": "测试跑步鞋",
                "category": "服饰运动",
                "sub_category": "跑步鞋",
            }
        ),
        base.model_copy(
            update={
                "product_id": "basketball",
                "title": "测试篮球鞋",
                "category": "服饰运动",
                "sub_category": "篮球鞋",
            }
        ),
    ]
    return ProductCatalog(
        Path("."),
        {product.product_id: product for product in products},
        {},
    )


def test_category_resolver_resolves_one_exact_catalog_scope() -> None:
    result = resolve_category_reference(
        CategoryReference(
            surface_text="耳机",
            candidates=[
                CategoryCandidate(
                    category="数码电子",
                    sub_category="真无线耳机",
                )
            ],
        ),
        _category_catalog(),
    )

    assert result == CategoryResolution(
        outcome="resolved",
        scope=CategoryCandidate(
            category="数码电子",
            sub_category="真无线耳机",
        ),
    )


def test_category_resolver_clarifies_all_multiple_catalog_scopes() -> None:
    candidates = [
        CategoryCandidate(category="服饰运动", sub_category="篮球鞋"),
        CategoryCandidate(category="服饰运动", sub_category="跑步鞋"),
    ]

    result = resolve_category_reference(
        CategoryReference(surface_text="鞋", candidates=candidates),
        _category_catalog(),
    )

    assert result.outcome == "ambiguous"
    assert result.candidate_scopes == candidates
    assert result.message is not None
    assert "篮球鞋" in result.message
    assert "跑步鞋" in result.message


def test_category_resolver_returns_unsupported_for_empty_or_invalid_scope() -> None:
    catalog = _category_catalog()

    empty = resolve_category_reference(
        CategoryReference(surface_text="相机"),
        catalog,
    )
    invalid = resolve_category_reference(
        CategoryReference(
            surface_text="耳机",
            candidates=[
                CategoryCandidate(
                    category="服饰运动",
                    sub_category="真无线耳机",
                )
            ],
        ),
        catalog,
    )

    assert empty.outcome == "unsupported"
    assert empty.message == "当前商品目录暂不支持“相机”，请换一种商品类型。"
    assert invalid.outcome == "unsupported"


def test_category_resolver_intersects_pending_allowed_scopes() -> None:
    running = CategoryCandidate(
        category="服饰运动",
        sub_category="跑步鞋",
    )
    result = resolve_category_reference(
        CategoryReference(
            surface_text="跑步鞋",
            candidates=[
                CategoryCandidate(
                    category="数码电子",
                    sub_category="真无线耳机",
                ),
                running,
            ],
        ),
        _category_catalog(),
        allowed_scopes=[running],
    )

    assert result == CategoryResolution(outcome="resolved", scope=running)


def _state(
    product_ids: list[str], *, focus: str | None = None, seen_ids: list[str] | None = None
) -> ConversationState:
    return ConversationState(
        schema_version=1,
        conversation_id="conversation-1",
        recent_candidates=[
            CandidateReference(rank=index, product_id=product_id, display_price=100 + index)
            for index, product_id in enumerate(product_ids, start=1)
        ],
        focused_product_id=focus,
        seen_product_ids=seen_ids if seen_ids is not None else product_ids,
    )


def _reference(**values: object) -> ProductReference:
    return ProductReference.model_validate(values)


def _matrix_reference(
    matched_ids: set[str],
    *,
    target_type: str = "product",
    surface_text: str = "那个",
) -> ProductReference:
    return ProductReference.model_validate(
        {
            "target_type": target_type,
            "surface_text": surface_text,
            "kind": "demonstrative",
            "candidate_matches": [
                ReferenceCandidateMatch(
                    product_id=product_id,
                    matches=product_id in matched_ids,
                )
                for product_id in ("p1", "p2", "p3")
            ],
        }
    )


@pytest.mark.parametrize(
    ("reference", "focus", "expected_product", "clarifies"),
    [
        (
            {
                "target_type": "product",
                "surface_text": "第二个",
                "kind": "ordinal",
                "ordinal": 2,
            },
            None,
            "p2",
            False,
        ),
        (
            {
                "target_type": "product",
                "surface_text": "第四个",
                "kind": "ordinal",
                "ordinal": 4,
            },
            None,
            None,
            True,
        ),
        (
            {"target_type": "product", "surface_text": "它", "kind": "demonstrative"},
            "p2",
            "p2",
            False,
        ),
        (
            {"target_type": "product", "surface_text": "它", "kind": "demonstrative"},
            None,
            None,
            True,
        ),
    ],
)
def test_resolver_uses_the_expected_product_reference_branch(
    reference: dict[str, object],
    focus: str | None,
    expected_product: str | None,
    clarifies: bool,
) -> None:
    result = resolve_reference(
        _reference(**reference),
        _state(["p1", "p2", "p3"], focus=focus),
        _three_product_catalog(),
    )

    assert result.product_id == expected_product
    assert result.needs_clarification is clarifies


def test_resolver_uses_the_sole_latest_candidate_without_focus() -> None:
    result = resolve_reference(
        _reference(target_type="product", surface_text="它", kind="demonstrative"),
        _state(["p2"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(product_id="p2")


def test_resolver_out_of_range_ordinal_clarification_lists_every_latest_product() -> None:
    catalog = _three_product_catalog()
    state = _state(["p1", "p2", "p3"], seen_ids=["p1", "p2", "p3", "p_old"])

    result = resolve_reference(
        _reference(
            target_type="product",
            surface_text="第四个",
            kind="ordinal",
            ordinal=4,
        ),
        state,
        catalog,
    )

    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2", "p3"]
    assert result.clarification_message is not None
    for candidate in state.recent_candidates:
        product = catalog.get(candidate.product_id)
        assert f"第{candidate.rank}款：{product.title}" in result.clarification_message
    old_product = catalog.get("p_old")
    assert "p_old" not in result.clarification_message
    assert old_product.title not in result.clarification_message
    for rank in range(1, 5):
        assert f"第{rank}款：{old_product.title}" not in result.clarification_message


def test_resolver_matches_a_unique_brand_to_one_product() -> None:
    result = resolve_reference(
        _reference(
            target_type="product",
            surface_text="  唯一品牌的  ",
            kind="brand",
            brand="  唯一品牌  ",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(product_id="p3")


def test_resolver_clarifies_when_a_product_brand_matches_multiple_candidates() -> None:
    result = resolve_reference(
        _reference(
            target_type="product",
            surface_text="共享品牌的",
            kind="brand",
            brand="共享品牌",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True


def test_matrix_resolves_one_product_without_using_clue_kind() -> None:
    result = resolve_reference(
        _matrix_reference({"p3"}, surface_text="中间语义由模型判断"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(product_id="p3")


def test_matrix_product_ambiguity_lists_only_matched_candidates() -> None:
    result = resolve_reference(
        _matrix_reference({"p1", "p2"}, surface_text="共享品牌那个"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2"]
    assert "Alpha One" in (result.clarification_message or "")
    assert "Beta Two" in (result.clarification_message or "")
    assert "Gamma Three" not in (result.clarification_message or "")


def test_matrix_brand_target_allows_multiple_products_of_one_brand() -> None:
    result = resolve_reference(
        _matrix_reference(
            {"p1", "p2"},
            target_type="brand",
            surface_text="共享品牌的",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result == ReferenceResolution(brand="共享品牌")


def test_expected_product_target_overrides_model_brand_target() -> None:
    result = resolve_reference(
        _matrix_reference(
            {"p3"},
            target_type="brand",
            surface_text="唯一品牌那个",
        ),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
        expected_target_type="product",
    )

    assert result == ReferenceResolution(product_id="p3")


def test_pending_allowed_ids_prevent_escape_to_unmatched_product() -> None:
    result = resolve_reference(
        _matrix_reference({"p3"}, surface_text="第三个"),
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
        expected_target_type="product",
        allowed_product_ids=("p1", "p2"),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2"]
    assert "Gamma Three" not in (result.clarification_message or "")


def test_invalid_matrix_coverage_fails_closed_to_clarification() -> None:
    incomplete = ProductReference(
        target_type="product",
        surface_text="第二个",
        kind="ordinal",
        ordinal=2,
        candidate_matches=[
            ReferenceCandidateMatch(product_id="p2", matches=True)
        ],
    )

    result = resolve_reference(
        incomplete,
        _state(["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2", "p3"]


def test_resolver_matches_an_exact_casefolded_product_name_only() -> None:
    catalog = _three_product_catalog()
    state = _state(["p1", "p2", "p3"])

    exact = resolve_reference(
        _reference(
            target_type="product",
            surface_text="alpha one",
            kind="product_name",
            product_name="  alpha one  ",
        ),
        state,
        catalog,
    )
    fuzzy = resolve_reference(
        _reference(
            target_type="product",
            surface_text="Alpha",
            kind="product_name",
            product_name="Alpha",
        ),
        state,
        catalog,
    )

    assert exact == ReferenceResolution(product_id="p1")
    assert fuzzy.needs_clarification is True


def test_resolver_resolves_unique_brand_and_focus_supplied_brand() -> None:
    catalog = _three_product_catalog()
    explicit = resolve_reference(
        _reference(
            target_type="brand",
            surface_text="唯一品牌",
            kind="brand",
            brand=" 唯一品牌 ",
        ),
        _state(["p1", "p2", "p3"]),
        catalog,
    )
    focus = resolve_reference(
        _reference(target_type="brand", surface_text="这个牌子", kind="demonstrative"),
        _state(["p1", "p2", "p3"], focus="p2"),
        catalog,
    )

    assert explicit == ReferenceResolution(brand="唯一品牌")
    assert focus == ReferenceResolution(brand="共享品牌")


def test_resolver_brand_demonstrative_without_focus_uses_unique_latest_brand_only() -> None:
    catalog = _three_product_catalog()
    reference = _reference(
        target_type="brand",
        surface_text="这个牌子",
        kind="demonstrative",
    )

    unique_brand = resolve_reference(reference, _state(["p1", "p2"]), catalog)
    ambiguous_brand = resolve_reference(reference, _state(["p1", "p3"]), catalog)

    assert unique_brand == ReferenceResolution(brand="共享品牌")
    assert ambiguous_brand.needs_clarification is True
    assert ambiguous_brand.candidate_product_ids == ["p1", "p3"]


def test_resolver_never_resolves_a_product_only_seen_in_an_older_batch() -> None:
    result = resolve_reference(
        _reference(
            target_type="product",
            surface_text="Gamma Three",
            kind="product_name",
            product_name="Gamma Three",
        ),
        _state(["p1", "p2"], seen_ids=["p1", "p2", "p3"]),
        _three_product_catalog(),
    )

    assert result.product_id is None
    assert result.needs_clarification is True
    assert result.candidate_product_ids == ["p1", "p2"]
    assert "p3" not in result.clarification_message
    assert "Gamma Three" not in result.clarification_message


def test_resolver_clarifies_safely_with_no_latest_candidates() -> None:
    result = resolve_reference(
        _reference(target_type="product", surface_text="它", kind="demonstrative"),
        _state([], seen_ids=["p3"]),
        _three_product_catalog(),
    )

    assert result.needs_clarification is True
    assert result.candidate_product_ids == []
    assert result.clarification_message is not None
    assert "p3" not in result.clarification_message


def test_resolution_model_enforces_coherent_success_and_clarification_states() -> None:
    with pytest.raises(ValidationError):
        ReferenceResolution(product_id="p1", needs_clarification=True)
    with pytest.raises(ValidationError):
        ReferenceResolution(needs_clarification=True)

    first = ReferenceResolution()
    second = ReferenceResolution()
    first.candidate_product_ids.append("p1")

    assert second.candidate_product_ids == []
