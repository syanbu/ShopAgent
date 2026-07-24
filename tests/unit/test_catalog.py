from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.models.product import Product
from shop_agent.models.query import SearchConstraints


def test_catalog_loads_and_resolves_image(
    sample_dataset_root: Path, sample_product: Product
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    product = catalog.get("p_digital_001")
    assert product == sample_product
    assert product.brand == "测试品牌"
    assert catalog.image_file(product.product_id).is_file()


def test_catalog_selects_only_skus_inside_budget(
    sample_dataset_root: Path,
) -> None:
    catalog = ProductCatalog.load(sample_dataset_root)
    skus = catalog.matched_skus("p_digital_001", SearchConstraints(max_price=500))
    assert [sku.sku_id for sku in skus] == ["sku-low"]


def test_catalog_exposes_sorted_unique_brands(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    products = [
        sample_product.model_copy(update={"product_id": "p3", "brand": "小米"}),
        sample_product.model_copy(update={"product_id": "p1", "brand": "Apple 苹果"}),
        sample_product.model_copy(update={"product_id": "p2", "brand": "小米"}),
    ]
    catalog = ProductCatalog(
        sample_dataset_root,
        {product.product_id: product for product in products},
        {},
    )

    assert catalog.brands() == ["Apple 苹果", "小米"]


def test_repository_dataset_contains_100_products() -> None:
    root = Path("ecommerce_agent_dataset")
    if not root.exists():
        pytest.skip("repository dataset is unavailable")
    catalog = ProductCatalog.load(root)
    assert len(catalog.all()) == 100
    brands = set(catalog.brands())
    assert {"Apple 苹果", "Nike 耐克", "北面"}.issubset(brands)
    assert brands.isdisjoint({"苹果", "Nike", "耐克", "The North Face"})
