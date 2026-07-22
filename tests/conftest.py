import json
from pathlib import Path

import pytest

from shop_agent.models.product import Product


@pytest.fixture
def sample_product_data() -> dict[str, object]:
    return {
        "product_id": "p_digital_001",
        "title": "测试蓝牙耳机",
        "brand": "测试品牌",
        "category": "数码电子",
        "sub_category": "蓝牙耳机",
        "base_price": 399.0,
        "image_path": "1_数码电子/images/p_digital_001.jpg",
        "skus": [
            {
                "sku_id": "sku-low",
                "properties": {"颜色": "黑色"},
                "price": 399.0,
            },
            {
                "sku_id": "sku-high",
                "properties": {"颜色": "白色"},
                "price": 599.0,
            },
        ],
        "rag_knowledge": {
            "marketing_description": "适合通勤的测试蓝牙耳机。",
            "official_faq": [
                {
                    "question": "是否支持蓝牙？",
                    "answer": "支持蓝牙连接。",
                }
            ],
            "user_reviews": [
                {
                    "nickname": "测试用户",
                    "rating": 5,
                    "content": "佩戴舒适。",
                }
            ],
        },
    }


@pytest.fixture
def sample_dataset_root(tmp_path: Path, sample_product_data: dict[str, object]) -> Path:
    category_root = tmp_path / "1_数码电子"
    data_dir = category_root / "data"
    image_dir = category_root / "images"
    data_dir.mkdir(parents=True)
    image_dir.mkdir()
    (data_dir / "p_digital_001.json").write_text(
        json.dumps(sample_product_data, ensure_ascii=False), encoding="utf-8"
    )
    (image_dir / "p_digital_001.jpg").write_bytes(b"test image")
    return tmp_path


@pytest.fixture
def sample_product(sample_product_data: dict[str, object]) -> Product:
    return Product.model_validate(sample_product_data)
