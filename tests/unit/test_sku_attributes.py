from pathlib import Path

from shop_agent.catalog import ProductCatalog
from shop_agent.sku_attributes import (
    canonical_sku_key,
    normalize_sku_properties,
    parse_quantity,
)


def test_repository_sku_keys_are_mapped() -> None:
    catalog = ProductCatalog.load(Path("ecommerce_agent_dataset"))
    unresolved: set[tuple[str, str, str]] = set()

    for product in catalog.all():
        for sku in product.skus:
            for raw_key in sku.properties:
                if canonical_sku_key(
                    product.category,
                    product.sub_category,
                    raw_key,
                ) is None:
                    unresolved.add((product.category, product.sub_category, raw_key))

    assert unresolved == set()


def test_normalize_sku_properties_collapses_aliases(sample_product) -> None:
    product = sample_product.model_copy(
        update={"category": "数码电子", "sub_category": "智能手机"}
    )
    sku = product.skus[0].model_copy(
        update={"properties": {"存储配置": "512GB", "机身颜色": "黑色"}}
    )

    assert normalize_sku_properties(product, sku) == {
        "storage": "512GB",
        "color": "黑色",
    }


def test_catalog_taxonomy_is_deduplicated_and_scoped_by_category_pair() -> None:
    catalog = ProductCatalog.load(Path("ecommerce_agent_dataset"))

    taxonomy = catalog.sku_taxonomy()

    assert "storage" in taxonomy["数码电子/智能手机"]
    assert "512GB" in taxonomy["数码电子/智能手机"]["storage"]
    assert "size" in taxonomy["服饰运动/跑步鞋"]
    assert "42码" in taxonomy["服饰运动/跑步鞋"]["size"]
    assert "size" not in taxonomy["食品饮料/碳酸饮料"]


def test_parse_quantity_converts_compatible_units() -> None:
    assert parse_quantity("1TB") == (1024.0, "GB")
    assert parse_quantity("500ml") == (500.0, "ml")
    assert parse_quantity("1.5L") == (1500.0, "ml")
    assert parse_quantity("30小时") == (30.0, "h")
    assert parse_quantity("14英寸") == (14.0, "in")
