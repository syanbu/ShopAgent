from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from operator import eq, ge, gt, le, lt
from pathlib import Path
from statistics import median
from typing import cast

from shop_agent.models.product import Product, Sku
from shop_agent.models.query import (
    CanonicalSkuKey,
    CategoryPriceReference,
    NumericConstraint,
    SearchConstraints,
)
from shop_agent.sku_attributes import (
    build_sku_taxonomy,
    normalize_sku_properties,
    parse_quantity,
)


_NUMERIC_OPERATORS = {
    "==": eq,
    ">": gt,
    ">=": ge,
    "<": lt,
    "<=": le,
}


class ProductCatalog:
    def __init__(
        self,
        root: Path,
        products: dict[str, Product],
        sources: dict[str, str],
    ) -> None:
        self._root = root.resolve()
        self._products = products
        self._sources = sources
        self._price_references = self._build_price_references()
        self._sku_taxonomy = build_sku_taxonomy(products.values())

    @classmethod
    def load(cls, root: Path) -> "ProductCatalog":
        resolved = root.resolve()
        products: dict[str, Product] = {}
        sources: dict[str, str] = {}
        for path in sorted(resolved.glob("*/data/*.json")):
            product = Product.model_validate_json(path.read_text(encoding="utf-8"))
            if product.product_id in products:
                raise ValueError(f"duplicate product_id: {product.product_id}")
            products[product.product_id] = product
            sources[product.product_id] = path.relative_to(resolved).as_posix()
        if not products:
            raise ValueError(f"no product JSON found under {resolved}")
        return cls(resolved, products, sources)

    def get(self, product_id: str) -> Product:
        return self._products[product_id]

    def all(self) -> list[Product]:
        return list(self._products.values())

    def brands(self) -> list[str]:
        return sorted({product.brand for product in self._products.values()})

    def source_path(self, product_id: str) -> str:
        return self._sources[product_id]

    def sku_taxonomy(
        self,
    ) -> dict[str, dict[CanonicalSkuKey, list[str]]]:
        return deepcopy(self._sku_taxonomy)

    def price_reference(
        self, category: str, sub_category: str
    ) -> CategoryPriceReference | None:
        return self._price_references.get((category, sub_category))

    def image_file(self, product_id: str) -> Path:
        product = self.get(product_id)
        path = (self._root / product.image_path).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("image path escapes dataset root")
        return path

    def matched_skus(
        self, product_id: str, constraints: SearchConstraints
    ) -> list[Sku]:
        product = self.get(product_id)
        structured_numeric_fields = self._structured_numeric_fields(product_id)
        return [
            sku
            for sku in product.skus
            if self._sku_matches(
                product,
                sku,
                constraints,
                structured_numeric_fields,
            )
        ]

    def unresolved_numeric_constraints(
        self,
        product_id: str,
        constraints: SearchConstraints,
    ) -> list[NumericConstraint]:
        fields = self._structured_numeric_fields(product_id)
        return [
            item
            for item in constraints.numeric_constraints
            if item.field not in fields
        ]

    def _structured_numeric_fields(self, product_id: str) -> set[str]:
        product = self.get(product_id)
        fields: set[str] = set()
        for sku in product.skus:
            for key, value in normalize_sku_properties(product, sku).items():
                if parse_quantity(value) is not None:
                    fields.add(key)
        return fields

    @staticmethod
    def _sku_matches(
        product: Product,
        sku: Sku,
        constraints: SearchConstraints,
        structured_numeric_fields: set[str],
    ) -> bool:
        if constraints.min_price is not None and sku.price < constraints.min_price:
            return False
        if constraints.max_price is not None and sku.price > constraints.max_price:
            return False
        properties = normalize_sku_properties(product, sku)
        for key, allowed_values in constraints.sku_constraints.items():
            if properties.get(key) not in allowed_values:
                return False
        for item in constraints.numeric_constraints:
            if item.field not in structured_numeric_fields:
                continue
            raw_value = properties.get(cast(CanonicalSkuKey, item.field))
            if raw_value is None or not _numeric_matches(raw_value, item):
                return False
        return True

    def _build_price_references(
        self,
    ) -> dict[tuple[str, str], CategoryPriceReference]:
        grouped: dict[tuple[str, str], list[Decimal]] = {}
        for product in self._products.values():
            key = (product.category, product.sub_category)
            grouped.setdefault(key, []).append(
                min(Decimal(str(sku.price)) for sku in product.skus)
            )
        references: dict[tuple[str, str], CategoryPriceReference] = {}
        for (category, sub_category), prices in grouped.items():
            median_price = Decimal(median(prices)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            cap = (median_price * Decimal("1.2")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            references[(category, sub_category)] = CategoryPriceReference(
                category=category,
                sub_category=sub_category,
                sample_count=len(prices),
                median_min_sku_price=float(median_price),
                value_price_cap=float(cap),
            )
        return references


def _numeric_matches(raw_value: str, constraint: NumericConstraint) -> bool:
    actual = parse_quantity(raw_value)
    expected = parse_quantity(f"{constraint.value:g}{constraint.unit}")
    if actual is None or expected is None or actual[1] != expected[1]:
        return False
    return _NUMERIC_OPERATORS[constraint.operator](actual[0], expected[0])
