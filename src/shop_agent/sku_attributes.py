import re
from collections.abc import Iterable

from shop_agent.models.product import Product, Sku
from shop_agent.models.query import CanonicalSkuKey


RAW_KEY_ALIASES: dict[str, CanonicalSkuKey] = {
    "logo配色": "accent_color",
    "版本": "version",
    "包装": "package_type",
    "包装规格": "package_type",
    "包装类型": "package_type",
    "包装数量": "package_count",
    "产品版本": "version",
    "产品规格": "specification",
    "产品类型": "product_type",
    "尺寸": "screen_size",
    "尺码": "size",
    "充电盒类型": "charging_case",
    "刺绣logo配色": "accent_color",
    "存储": "storage",
    "存储规格": "storage",
    "存储配置": "storage",
    "存储容量": "storage",
    "单盒容量": "capacity",
    "单条净含量": "capacity",
    "定制服务": "custom_service",
    "附加服务": "add_on_service",
    "固态存储": "storage",
    "固态硬盘容量": "storage",
    "规格": "specification",
    "机身存储": "storage",
    "机身颜色": "color",
    "口味": "flavor",
    "裤长": "pants_length",
    "款式": "fit",
    "款型": "fit",
    "帽身颜色": "color",
    "帽围调节方式": "hat_adjustment",
    "帽围类型": "hat_fit",
    "每箱数量": "package_count",
    "内存": "memory",
    "内存容量": "memory",
    "内存组合": "memory_configuration",
    "内含条数": "package_count",
    "配色": "color",
    "屏幕尺寸": "screen_size",
    "容量": "capacity",
    "色号": "shade",
    "色号规格": "shade",
    "适用人群": "target_audience",
    "适用性别": "gender",
    "数量": "package_count",
    "网络版本": "network_version",
    "箱规": "package_count",
    "鞋码": "size",
    "鞋楦": "shoe_last",
    "鞋楦类型": "shoe_last",
    "芯片": "chip",
    "芯片型号": "chip",
    "颜色": "color",
    "运行内存": "memory",
    "整箱规格": "package_count",
    "整箱盒数": "package_count",
    "整箱数量": "package_count",
    "总袋数": "package_count",
}

CONTEXT_OVERRIDES: dict[tuple[str, str, str], CanonicalSkuKey] = {
    ("服饰运动", "背包", "容量"): "capacity",
    ("数码电子", "笔记本电脑", "尺寸"): "screen_size",
}

_QUANTITY = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+|毫升|升|克|千克|小时|英寸|寸)\s*$"
)
_UNIT_CONVERSIONS = {
    "tb": (1024.0, "GB"),
    "gb": (1.0, "GB"),
    "mb": (1 / 1024.0, "GB"),
    "l": (1000.0, "ml"),
    "升": (1000.0, "ml"),
    "ml": (1.0, "ml"),
    "毫升": (1.0, "ml"),
    "kg": (1000.0, "g"),
    "千克": (1000.0, "g"),
    "g": (1.0, "g"),
    "克": (1.0, "g"),
    "h": (1.0, "h"),
    "小时": (1.0, "h"),
    "英寸": (1.0, "in"),
    "寸": (1.0, "in"),
}


def canonical_sku_key(
    category: str,
    sub_category: str,
    raw_key: str,
) -> CanonicalSkuKey | None:
    return CONTEXT_OVERRIDES.get(
        (category, sub_category, raw_key),
        RAW_KEY_ALIASES.get(raw_key),
    )


def normalize_sku_properties(
    product: Product,
    sku: Sku,
) -> dict[CanonicalSkuKey, str]:
    normalized: dict[CanonicalSkuKey, str] = {}
    for raw_key, value in sku.properties.items():
        key = canonical_sku_key(product.category, product.sub_category, raw_key)
        if key is None:
            continue
        if key in normalized and normalized[key] != value:
            raise ValueError(f"SKU {sku.sku_id} maps conflicting values to {key}")
        normalized[key] = value.strip()
    return normalized


def build_sku_taxonomy(
    products: Iterable[Product],
) -> dict[str, dict[CanonicalSkuKey, list[str]]]:
    collected: dict[str, dict[CanonicalSkuKey, set[str]]] = {}
    for product in products:
        pair = f"{product.category}/{product.sub_category}"
        pair_values = collected.setdefault(pair, {})
        for sku in product.skus:
            for key, value in normalize_sku_properties(product, sku).items():
                pair_values.setdefault(key, set()).add(value)
    return {
        pair: {
            key: sorted(values)
            for key, values in sorted(attributes.items())
        }
        for pair, attributes in sorted(collected.items())
    }


def parse_quantity(text: str) -> tuple[float, str] | None:
    match = _QUANTITY.fullmatch(text)
    if match is None:
        return None
    number = float(match.group(1))
    conversion = _UNIT_CONVERSIONS.get(match.group(2).lower())
    if conversion is None:
        return None
    factor, base_unit = conversion
    return number * factor, base_unit
