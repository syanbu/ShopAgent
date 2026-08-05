import json
from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.services.scenario_recipes import ScenarioRecipeRegistry


PROJECT_ROOT = Path(__file__).parents[2]


def test_project_recipe_registry_loads_six_catalog_grounded_recipes() -> None:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    registry = ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )

    assert registry.recipe_ids() == (
        "beach_vacation",
        "hiking",
        "running",
        "back_to_school",
        "home_office",
        "summer_commute",
    )
    beach = registry.get("beach_vacation")
    assert [slot.slot_id for slot in beach.slots] == [
        "sun_protection",
        "top",
        "bottom",
        "hat",
        "bag",
    ]
    assert {
        (scope.category, scope.sub_category)
        for slot in beach.slots
        for scope in slot.catalog_scopes
    } == {
        ("美妆护肤", "防晒"),
        ("服饰运动", "短袖T恤"),
        ("服饰运动", "速干T恤"),
        ("服饰运动", "运动短裤"),
        ("服饰运动", "帽子"),
        ("服饰运动", "背包"),
    }
    assert "太阳镜" not in json.dumps(
        beach.model_dump(mode="json"), ensure_ascii=False
    )


def test_beach_required_slots_have_inventory_for_a_second_bundle() -> None:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    registry = ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )
    beach = registry.get("beach_vacation")

    for slot in (item for item in beach.slots if item.required):
        scopes = {
            (scope.category, scope.sub_category)
            for scope in slot.catalog_scopes
        }
        matching_product_ids = {
            product.product_id
            for product in catalog.all()
            if (product.category, product.sub_category) in scopes
        }
        assert len(matching_product_ids) >= 2, slot.slot_id


def test_registry_summaries_expose_business_roles_not_product_facts() -> None:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    registry = ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )

    summaries = registry.prompt_summaries()

    assert summaries[0]["recipe_id"] == "beach_vacation"
    assert summaries[0]["slot_labels"] == [
        "防晒护理",
        "轻薄上装",
        "清凉下装",
        "遮阳帽",
        "随身背包",
    ]
    assert "catalog_scopes" not in summaries[0]
    assert "product_id" not in json.dumps(summaries, ensure_ascii=False)


def test_registry_rejects_catalog_scope_missing_from_catalog(tmp_path: Path) -> None:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    source = json.loads(
        (PROJECT_ROOT / "config" / "scenario_recipes.json").read_text(
            encoding="utf-8"
        )
    )
    source["recipes"][0]["slots"][0]["catalog_scopes"] = [
        {"category": "服饰运动", "sub_category": "太阳镜"}
    ]
    invalid_path = tmp_path / "invalid-recipes.json"
    invalid_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown Catalog scope"):
        ScenarioRecipeRegistry.load(invalid_path, catalog)


def test_registry_rejects_alias_collisions(tmp_path: Path) -> None:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    source = json.loads(
        (PROJECT_ROOT / "config" / "scenario_recipes.json").read_text(
            encoding="utf-8"
        )
    )
    source["recipes"][1]["aliases"].append(source["recipes"][0]["aliases"][0])
    invalid_path = tmp_path / "duplicate-alias.json"
    invalid_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate recipe alias"):
        ScenarioRecipeRegistry.load(invalid_path, catalog)
