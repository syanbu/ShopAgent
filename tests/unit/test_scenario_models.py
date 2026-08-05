import pytest
from pydantic import ValidationError

from shop_agent.models.scenario import (
    CatalogScope,
    ScenarioBundleItem,
    ScenarioRequest,
    ScenarioSlotSpec,
    ScenarioSnapshot,
    SolutionRecipe,
)


def _slot(
    slot_id: str = "sun_protection",
    *,
    required: bool = True,
) -> ScenarioSlotSpec:
    return ScenarioSlotSpec(
        slot_id=slot_id,
        label="防晒护理",
        group="防护",
        required=required,
        query_terms=["防晒霜", "海边防晒"],
        catalog_scopes=[
            CatalogScope(category="美妆护肤", sub_category="防晒")
        ],
    )


def _recipe(*slots: ScenarioSlotSpec, max_products: int = 5) -> SolutionRecipe:
    return SolutionRecipe(
        schema_version=1,
        recipe_id=" beach_vacation ",
        recipe_version=1,
        display_name=" 海边度假 ",
        aliases=["三亚度假", "海边度假"],
        description=" 海边或海岛旅行的一套防晒与穿搭方案。 ",
        max_products=max_products,
        slots=list(slots or (_slot(),)),
    )


def test_recipe_normalizes_ids_text_and_immutable_collections() -> None:
    recipe = _recipe()

    assert recipe.recipe_id == "beach_vacation"
    assert recipe.display_name == "海边度假"
    assert recipe.slots[0].query_terms == ("防晒霜", "海边防晒")
    assert recipe.slots[0].catalog_scopes == (
        CatalogScope(category="美妆护肤", sub_category="防晒"),
    )


@pytest.mark.parametrize(
    ("slots", "max_products", "message"),
    [
        ((_slot("same"), _slot("same", required=False)), 5, "slot IDs"),
        ((_slot(),), 0, "greater than or equal to 1"),
        ((_slot(),), 9, "less than or equal to 8"),
        (
            (_slot("one"), _slot("two")),
            1,
            "required slot count",
        ),
    ],
)
def test_recipe_rejects_invalid_slot_or_limit_contracts(
    slots: tuple[ScenarioSlotSpec, ...],
    max_products: int,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _recipe(*slots, max_products=max_products)


def test_recipe_requires_at_least_one_required_slot() -> None:
    with pytest.raises(ValidationError, match="required slot"):
        _recipe(_slot(required=False))


def test_slot_rejects_duplicate_query_terms_and_scopes() -> None:
    with pytest.raises(ValidationError, match="query terms"):
        ScenarioSlotSpec(
            slot_id="top",
            label="上装",
            group="穿搭",
            required=True,
            query_terms=["速干", "速干"],
            catalog_scopes=[
                {"category": "服饰运动", "sub_category": "速干T恤"}
            ],
        )
    with pytest.raises(ValidationError, match="catalog scopes"):
        ScenarioSlotSpec(
            slot_id="top",
            label="上装",
            group="穿搭",
            required=True,
            query_terms=["速干"],
            catalog_scopes=[
                {"category": "服饰运动", "sub_category": "速干T恤"},
                {"category": "服饰运动", "sub_category": "速干T恤"},
            ],
        )


def test_scenario_request_normalizes_unmapped_requirements() -> None:
    request = ScenarioRequest(
        surface_text=" 三亚度假并且要太阳镜 ",
        recipe_id=" beach_vacation ",
        unmapped_requirements=[" 太阳镜 "],
    )

    assert request.surface_text == "三亚度假并且要太阳镜"
    assert request.recipe_id == "beach_vacation"
    assert request.unmapped_requirements == ("太阳镜",)

    with pytest.raises(ValidationError, match="unmapped requirements"):
        ScenarioRequest(
            surface_text="三亚",
            recipe_id="beach_vacation",
            unmapped_requirements=["太阳镜", "太阳镜"],
        )


def test_scenario_snapshot_requires_contiguous_unique_seen_bundle() -> None:
    valid = ScenarioSnapshot(
        schema_version=1,
        recipe_id="beach_vacation",
        recipe_version=1,
        original_request="三亚度假",
        current_bundle=[
            ScenarioBundleItem(
                rank=1,
                slot_id="sun_protection",
                product_id="p1",
                display_price=199,
            ),
            ScenarioBundleItem(
                rank=2,
                slot_id="top",
                product_id="p2",
                display_price=299,
            ),
        ],
        seen_product_ids=["p1", "p2", "old"],
        generation_index=1,
    )
    assert [item.rank for item in valid.current_bundle] == [1, 2]

    with pytest.raises(ValidationError, match="contiguous"):
        ScenarioSnapshot(
            schema_version=1,
            recipe_id="beach_vacation",
            recipe_version=1,
            original_request="三亚度假",
            current_bundle=[
                valid.current_bundle[0],
                valid.current_bundle[1].model_copy(update={"rank": 3}),
            ],
            seen_product_ids=["p1", "p2"],
            generation_index=1,
        )

    with pytest.raises(ValidationError, match="included in seen"):
        ScenarioSnapshot(
            schema_version=1,
            recipe_id="beach_vacation",
            recipe_version=1,
            original_request="三亚度假",
            current_bundle=[valid.current_bundle[0]],
            seen_product_ids=[],
            generation_index=1,
        )


def test_scenario_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SolutionRecipe.model_validate({**_recipe().model_dump(), "unknown": True})
