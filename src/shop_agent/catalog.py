from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from shop_agent.models.product import Product, Sku
from shop_agent.models.query import CategoryPriceReference, SearchConstraints


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
        skus = self.get(product_id).skus
        return [
            sku
            for sku in skus
            if (constraints.min_price is None or sku.price >= constraints.min_price)
            and (constraints.max_price is None or sku.price <= constraints.max_price)
        ]

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
