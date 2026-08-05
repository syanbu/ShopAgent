"""Load and validate scenario recipe configuration against the live Catalog."""

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shop_agent.catalog import ProductCatalog
from shop_agent.models.scenario import ScenarioRecipeDocument, SolutionRecipe


class ScenarioRecipeRegistry:
    def __init__(self, recipes: tuple[SolutionRecipe, ...]) -> None:
        self._recipes = recipes
        self._by_id = {recipe.recipe_id: recipe for recipe in recipes}

    @classmethod
    def load(
        cls,
        path: str | Path,
        catalog: ProductCatalog,
    ) -> "ScenarioRecipeRegistry":
        resolved = Path(path)
        try:
            document = ScenarioRecipeDocument.model_validate_json(
                resolved.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise ValueError(f"invalid scenario recipe configuration: {resolved}") from error

        alias_owners: dict[str, str] = {}
        catalog_scopes = {
            (product.category, product.sub_category) for product in catalog.all()
        }
        for recipe in document.recipes:
            for alias in recipe.aliases:
                normalized_alias = alias.casefold()
                owner = alias_owners.get(normalized_alias)
                if owner is not None:
                    raise ValueError(
                        f"duplicate recipe alias {alias!r}: {owner}, {recipe.recipe_id}"
                    )
                alias_owners[normalized_alias] = recipe.recipe_id
            for slot in recipe.slots:
                for scope in slot.catalog_scopes:
                    key = (scope.category, scope.sub_category)
                    if key not in catalog_scopes:
                        raise ValueError(
                            "unknown Catalog scope in scenario recipe: "
                            f"{recipe.recipe_id}/{slot.slot_id} -> "
                            f"{scope.category}/{scope.sub_category}"
                        )
        return cls(document.recipes)

    def get(self, recipe_id: str) -> SolutionRecipe:
        return self._by_id[recipe_id]

    def contains(self, recipe_id: str) -> bool:
        return recipe_id in self._by_id

    def recipe_ids(self) -> tuple[str, ...]:
        return tuple(recipe.recipe_id for recipe in self._recipes)

    def recipes(self) -> tuple[SolutionRecipe, ...]:
        return self._recipes

    def prompt_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "recipe_id": recipe.recipe_id,
                "display_name": recipe.display_name,
                "aliases": list(recipe.aliases),
                "description": recipe.description,
                "slot_labels": [slot.label for slot in recipe.slots],
            }
            for recipe in self._recipes
        ]
