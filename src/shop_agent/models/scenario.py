"""Structured contracts for template-grounded scenario recommendations."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MACHINE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _normalized_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


def _normalized_machine_id(value: Any, *, label: str) -> str:
    normalized = _normalized_text(value, label=label)
    if _MACHINE_ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a stable lowercase machine ID")
    return normalized


def _normalized_unique_texts(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list or tuple")
    normalized = tuple(_normalized_text(item, label=label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    return normalized


class CatalogScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    sub_category: str

    @field_validator("category", "sub_category", mode="before")
    @classmethod
    def normalize_scope_text(cls, value: Any, info: Any) -> str:
        return _normalized_text(value, label=info.field_name)


class ScenarioSlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str
    label: str
    group: str
    required: bool
    query_terms: tuple[str, ...]
    catalog_scopes: tuple[CatalogScope, ...]

    @field_validator("slot_id", mode="before")
    @classmethod
    def normalize_slot_id(cls, value: Any) -> str:
        return _normalized_machine_id(value, label="slot ID")

    @field_validator("label", "group", mode="before")
    @classmethod
    def normalize_display_text(cls, value: Any, info: Any) -> str:
        return _normalized_text(value, label=info.field_name)

    @field_validator("query_terms", mode="before")
    @classmethod
    def normalize_query_terms(cls, value: Any) -> tuple[str, ...]:
        terms = _normalized_unique_texts(value, label="query terms")
        if not terms:
            raise ValueError("query terms cannot be empty")
        return terms

    @field_validator("catalog_scopes", mode="before")
    @classmethod
    def validate_scope_collection(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("catalog scopes must be a list or tuple")
        if not value:
            raise ValueError("catalog scopes cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_unique_scopes(self) -> "ScenarioSlotSpec":
        keys = [
            (scope.category, scope.sub_category) for scope in self.catalog_scopes
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("catalog scopes must be unique")
        return self


class SolutionRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    recipe_id: str
    recipe_version: int = Field(ge=1)
    display_name: str
    aliases: tuple[str, ...]
    description: str
    max_products: int = Field(ge=1, le=8)
    slots: tuple[ScenarioSlotSpec, ...]

    @field_validator("recipe_id", mode="before")
    @classmethod
    def normalize_recipe_id(cls, value: Any) -> str:
        return _normalized_machine_id(value, label="recipe ID")

    @field_validator("display_name", "description", mode="before")
    @classmethod
    def normalize_recipe_text(cls, value: Any, info: Any) -> str:
        return _normalized_text(value, label=info.field_name)

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any) -> tuple[str, ...]:
        aliases = _normalized_unique_texts(value, label="recipe aliases")
        if not aliases:
            raise ValueError("recipe aliases cannot be empty")
        return aliases

    @field_validator("slots", mode="before")
    @classmethod
    def validate_slot_collection(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("slots must be a list or tuple")
        if not value:
            raise ValueError("slots cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_recipe_contract(self) -> "SolutionRecipe":
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot IDs must be unique within a recipe")
        required_count = sum(slot.required for slot in self.slots)
        if required_count == 0:
            raise ValueError("recipe must contain at least one required slot")
        if self.max_products < required_count:
            raise ValueError("max_products cannot be smaller than required slot count")
        return self


class ScenarioRecipeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    recipes: tuple[SolutionRecipe, ...]

    @field_validator("recipes", mode="before")
    @classmethod
    def validate_recipe_collection(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("recipes must be a list or tuple")
        if not value:
            raise ValueError("recipes cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_unique_recipe_ids(self) -> "ScenarioRecipeDocument":
        recipe_ids = [recipe.recipe_id for recipe in self.recipes]
        if len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("recipe IDs must be unique")
        return self


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface_text: str
    recipe_id: str | None = None
    unmapped_requirements: tuple[str, ...] = ()

    @field_validator("surface_text", mode="before")
    @classmethod
    def normalize_surface_text(cls, value: Any) -> str:
        return _normalized_text(value, label="surface text")

    @field_validator("recipe_id", mode="before")
    @classmethod
    def normalize_optional_recipe_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _normalized_machine_id(value, label="recipe ID")

    @field_validator("unmapped_requirements", mode="before")
    @classmethod
    def normalize_unmapped_requirements(cls, value: Any) -> tuple[str, ...]:
        return _normalized_unique_texts(value, label="unmapped requirements")


class ScenarioBundleItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    slot_id: str
    product_id: str
    display_price: float = Field(ge=0)

    @field_validator("slot_id", mode="before")
    @classmethod
    def normalize_bundle_slot_id(cls, value: Any) -> str:
        return _normalized_machine_id(value, label="slot ID")

    @field_validator("product_id", mode="before")
    @classmethod
    def normalize_bundle_product_id(cls, value: Any) -> str:
        return _normalized_text(value, label="product ID")


class ScenarioSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    recipe_id: str
    recipe_version: int = Field(ge=1)
    original_request: str
    current_bundle: tuple[ScenarioBundleItem, ...] = ()
    seen_product_ids: tuple[str, ...] = ()
    generation_index: int = Field(ge=1)

    @field_validator("recipe_id", mode="before")
    @classmethod
    def normalize_snapshot_recipe_id(cls, value: Any) -> str:
        return _normalized_machine_id(value, label="recipe ID")

    @field_validator("original_request", mode="before")
    @classmethod
    def normalize_original_request(cls, value: Any) -> str:
        return _normalized_text(value, label="original request")

    @field_validator("seen_product_ids", mode="before")
    @classmethod
    def normalize_seen_product_ids(cls, value: Any) -> tuple[str, ...]:
        return _normalized_unique_texts(value, label="seen product IDs")

    @model_validator(mode="after")
    def validate_bundle_contract(self) -> "ScenarioSnapshot":
        ranks = [item.rank for item in self.current_bundle]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("bundle ranks must be contiguous from one")
        slot_ids = [item.slot_id for item in self.current_bundle]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("bundle slot IDs must be unique")
        product_ids = [item.product_id for item in self.current_bundle]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("bundle product IDs must be unique")
        if not set(product_ids).issubset(set(self.seen_product_ids)):
            raise ValueError("current bundle products must be included in seen IDs")
        return self
