from pathlib import Path

import httpx
import pytest

from shop_agent.api.app import create_app
from shop_agent.api.dependencies import ApiDependencies
from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from tests.integration.api_fakes import FakeGraph, FakeReadinessProbe


def _dependencies(root: Path) -> ApiDependencies:
    return ApiDependencies(
        graph=FakeGraph([]),
        catalog=ProductCatalog.load(root),
        settings=Settings(dashscope_api_key="test-key", dataset_root=root),
        readiness_probe=FakeReadinessProbe(),
    )


async def _get(dependencies: ApiDependencies, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(dependencies))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_product_image_returns_fixture_bytes(sample_dataset_root: Path) -> None:
    response = await _get(
        _dependencies(sample_dataset_root),
        "/api/v1/products/p_digital_001/image",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"test image"


@pytest.mark.asyncio
async def test_unknown_product_image_returns_404(sample_dataset_root: Path) -> None:
    response = await _get(
        _dependencies(sample_dataset_root), "/api/v1/products/not-found/image"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "product image not found"}


@pytest.mark.asyncio
async def test_missing_image_returns_404_without_absolute_path(
    sample_dataset_root: Path,
) -> None:
    image = sample_dataset_root / "1_数码电子/images/p_digital_001.jpg"
    image.unlink()

    response = await _get(
        _dependencies(sample_dataset_root),
        "/api/v1/products/p_digital_001/image",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "product image not found"}
    assert str(sample_dataset_root) not in response.text
