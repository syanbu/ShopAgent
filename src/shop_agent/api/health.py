from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from shop_agent.api.dependencies import Dependencies


router = APIRouter(tags=["health"])


@router.get("/health")
async def health(dependencies: Dependencies) -> JSONResponse:
    catalog_ready = bool(dependencies.catalog.all())
    settings = dependencies.settings
    models_ready = all(
        (
            settings.dashscope_api_key.strip(),
            settings.chat_model.strip(),
            settings.comparison_model.strip(),
            settings.evidence_model.strip(),
            settings.embedding_model.strip(),
            settings.rerank_model.strip(),
        )
    )
    try:
        qdrant_ready = await dependencies.readiness_probe.collection_ready()
    except Exception:
        qdrant_ready = False

    dependency_status: dict[str, Literal["ready", "not_ready"]] = {
        "catalog": "ready" if catalog_ready else "not_ready",
        "models": "ready" if models_ready else "not_ready",
        "qdrant": "ready" if qdrant_ready else "not_ready",
    }
    ready = all(value == "ready" for value in dependency_status.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "dependencies": dependency_status,
        },
    )
