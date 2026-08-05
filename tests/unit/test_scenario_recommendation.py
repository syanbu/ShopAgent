from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.models.scenario import ScenarioSnapshot, SolutionRecipe
from shop_agent.services.scenario_recommendation import ScenarioRecommendationService
from shop_agent.services.scenario_recipes import ScenarioRecipeRegistry
from tests.unit.workflow_fakes import FakeEvidenceService, FakeRetrievalService


PROJECT_ROOT = Path(__file__).parents[2]


def _catalog() -> ProductCatalog:
    return ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")


def _registry(catalog: ProductCatalog) -> ScenarioRecipeRegistry:
    return ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )


def _service(
    catalog: ProductCatalog,
    *,
    product_limit: int = 6,
) -> tuple[ScenarioRecommendationService, FakeRetrievalService]:
    retrieval = FakeRetrievalService(products=catalog.all(), return_hits=True)
    evidence = FakeEvidenceService(catalog=catalog, eligible=True)
    return (
        ScenarioRecommendationService(
            retrieval=retrieval,
            evidence=evidence,
            product_limit=product_limit,
        ),
        retrieval,
    )


def _snapshot(recipe_id: str, recipe_version: int = 1) -> ScenarioSnapshot:
    return ScenarioSnapshot(
        schema_version=1,
        recipe_id=recipe_id,
        recipe_version=recipe_version,
        original_request="下周去三亚度假，从防晒到穿搭",
        generation_index=1,
    )


@pytest.mark.asyncio
async def test_beach_bundle_uses_template_order_and_one_product_per_slot() -> None:
    catalog = _catalog()
    registry = _registry(catalog)
    service, retrieval = _service(catalog)
    recipe = registry.get("beach_vacation")

    result = await service.build_bundle(recipe, _snapshot(recipe.recipe_id))

    assert result.status == "complete"
    assert [item.slot_id for item in result.selected_items] == [
        "sun_protection",
        "top",
        "bottom",
        "hat",
        "bag",
    ]
    product_ids = [item.selected_product.product_id for item in result.selected_items]
    assert len(product_ids) == len(set(product_ids))
    assert len(result.selected_items) == 5
    assert "下周去三亚度假，从防晒到穿搭" in (
        retrieval.retrieve_calls[0].intent.retrieval_query or ""
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recipe_id",
    [
        "beach_vacation",
        "hiking",
        "running",
        "back_to_school",
        "home_office",
        "summer_commute",
    ],
)
async def test_all_registered_recipes_can_build_a_complete_bounded_bundle(
    recipe_id: str,
) -> None:
    catalog = _catalog()
    registry = _registry(catalog)
    service, _ = _service(catalog)
    recipe = registry.get(recipe_id)

    result = await service.build_bundle(recipe, _snapshot(recipe_id))

    assert result.status == "complete"
    assert len(result.selected_items) <= min(recipe.max_products, 6)
    assert all(
        any(
            item.slot_id == required.slot_id
            for item in result.selected_items
        )
        for required in recipe.slots
        if required.required
    )


@pytest.mark.asyncio
async def test_required_slot_exhaustion_returns_no_partial_bundle() -> None:
    catalog = _catalog()
    registry = _registry(catalog)
    service, _ = _service(catalog)
    recipe = registry.get("beach_vacation")
    sun_ids = [
        product.product_id
        for product in catalog.all()
        if product.category == "美妆护肤" and product.sub_category == "防晒"
    ]
    snapshot = _snapshot(recipe.recipe_id).model_copy(
        update={"seen_product_ids": tuple(sun_ids)}
    )

    result = await service.build_bundle(recipe, snapshot)

    assert result.status == "incomplete_required_slots"
    assert result.missing_required_slot_ids == ("sun_protection",)
    assert result.selected_items == ()


@pytest.mark.asyncio
async def test_global_scenario_limit_is_independent_from_ordinary_limit() -> None:
    catalog = _catalog()
    registry = _registry(catalog)
    service, _ = _service(catalog, product_limit=4)
    recipe = registry.get("hiking")

    result = await service.build_bundle(recipe, _snapshot(recipe.recipe_id))

    assert result.status == "complete"
    assert len(result.selected_items) == 4
    assert [item.slot_id for item in result.selected_items[:3]] == [
        "shoes",
        "top",
        "bottom",
    ]


@pytest.mark.asyncio
async def test_scenario_service_can_return_the_hard_ceiling_of_eight_items() -> None:
    catalog = _catalog()
    service, _ = _service(catalog, product_limit=8)
    recipe = SolutionRecipe(
        schema_version=1,
        recipe_id="eight_phone_slots",
        recipe_version=1,
        display_name="八槽边界",
        aliases=["八槽边界"],
        description="用于验证场景商品数量硬上限。",
        max_products=8,
        slots=[
            {
                "slot_id": f"phone_{index}",
                "label": f"手机槽位 {index}",
                "group": "数码",
                "required": index == 1,
                "query_terms": [f"智能手机 {index}"],
                "catalog_scopes": [
                    {"category": "数码电子", "sub_category": "智能手机"}
                ],
            }
            for index in range(1, 9)
        ],
    )

    result = await service.build_bundle(recipe, _snapshot(recipe.recipe_id))

    assert result.status == "complete"
    assert len(result.selected_items) == 8
    assert len(
        {item.selected_product.product_id for item in result.selected_items}
    ) == 8
