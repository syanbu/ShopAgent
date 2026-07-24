from pathlib import Path

from shop_agent.models.product import Product, Sku
from shop_agent.models.query import SearchConstraints


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
