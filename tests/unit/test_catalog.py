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


def test_catalog_builds_price_references_from_one_sample_per_product(
    sample_dataset_root: Path,
    sample_product: Product,
) -> None:
    products = {
        "p1": sample_product.model_copy(
            update={
                "product_id": "p1",
                "skus": [
                    sample_product.skus[0].model_copy(update={"price": 100}),
                    sample_product.skus[1].model_copy(update={"price": 999}),
                ],
            }
        ),
        "p2": sample_product.model_copy(
            update={
                "product_id": "p2",
                "skus": [sample_product.skus[0].model_copy(update={"price": 200})],
            }
        ),
    }
    catalog = ProductCatalog(sample_dataset_root, products, {})

    reference = catalog.price_reference("数码电子", "蓝牙耳机")

    assert reference is not None
    assert reference.sample_count == 2
    assert reference.median_min_sku_price == 150.0
    assert reference.value_price_cap == 180.0


def test_repository_price_reference_matches_design_baseline() -> None:
    root = Path("ecommerce_agent_dataset")
    if not root.exists():
        pytest.skip("repository dataset is unavailable")
    catalog = ProductCatalog.load(root)

    smartphone = catalog.price_reference("数码电子", "智能手机")
    tshirt = catalog.price_reference("服饰运动", "短袖T恤")

    assert smartphone is not None
    assert (smartphone.sample_count, smartphone.median_min_sku_price) == (10, 7249.0)
    assert smartphone.value_price_cap == 8698.8
    assert tshirt is not None
    assert (tshirt.sample_count, tshirt.median_min_sku_price) == (3, 129.0)
    assert tshirt.value_price_cap == 154.8
