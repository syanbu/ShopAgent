import json
from pathlib import Path

import httpx
import pytest

from shop_agent.api.app import create_app
from shop_agent.api.dependencies import ApiDependencies
from shop_agent.api.health import health as health_endpoint
from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from tests.integration.api_fakes import FakeGraph, FakeReadinessProbe


def _dependencies(
    root: Path,
    *,
    qdrant_ready: bool = True,
    api_key: str = "test-key",
    comparison_model: str = "qwen3.6-flash",
    evidence_model: str = "qwen3.6-flash",
) -> tuple[ApiDependencies, FakeReadinessProbe]:
    probe = FakeReadinessProbe(qdrant_ready)
    return (
        ApiDependencies(
            graph=FakeGraph([]),
            catalog=ProductCatalog.load(root),
            settings=Settings(
                dashscope_api_key=api_key,
                comparison_model=comparison_model,
                evidence_model=evidence_model,
                dataset_root=root,
            ),
            readiness_probe=probe,
        ),
        probe,
    )


async def _health(dependencies: ApiDependencies) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(dependencies))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


@pytest.mark.asyncio
async def test_health_returns_ready_when_dependencies_are_ready(
    sample_dataset_root: Path,
) -> None:
    dependencies, probe = _dependencies(sample_dataset_root)

    response = await _health(dependencies)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {
            "catalog": "ready",
            "models": "ready",
            "qdrant": "ready",
        },
    }
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_health_returns_503_when_qdrant_is_unavailable(
    sample_dataset_root: Path,
) -> None:
    dependencies, _ = _dependencies(sample_dataset_root, qdrant_ready=False)

    response = await _health(dependencies)

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["dependencies"]["qdrant"] == "not_ready"


@pytest.mark.asyncio
async def test_health_returns_503_when_model_config_is_missing(
    sample_dataset_root: Path,
) -> None:
    dependencies, _ = _dependencies(sample_dataset_root, api_key="")

    response = await _health(dependencies)

    assert response.status_code == 503
    assert response.json()["dependencies"]["models"] == "not_ready"


@pytest.mark.asyncio
async def test_health_requires_comparison_model(
    sample_dataset_root: Path,
) -> None:
    dependencies, _ = _dependencies(sample_dataset_root, comparison_model="")

    response = await _health(dependencies)

    assert response.status_code == 503
    assert response.json()["dependencies"]["models"] == "not_ready"


@pytest.mark.asyncio
async def test_health_requires_evidence_model(
    sample_dataset_root: Path,
) -> None:
    dependencies, _ = _dependencies(sample_dataset_root, evidence_model="")

    response = await health_endpoint(dependencies)

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["dependencies"]["models"] == "not_ready"


@pytest.mark.asyncio
async def test_health_maps_qdrant_probe_error_to_not_ready(
    sample_dataset_root: Path,
) -> None:
    dependencies, _ = _dependencies(sample_dataset_root)
    dependencies = ApiDependencies(
        graph=dependencies.graph,
        catalog=dependencies.catalog,
        settings=dependencies.settings,
        readiness_probe=FakeReadinessProbe(error=RuntimeError("connection failed")),
    )

    response = await _health(dependencies)

    assert response.status_code == 503
    assert response.json()["dependencies"]["qdrant"] == "not_ready"
    assert "connection failed" not in response.text
