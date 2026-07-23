from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shop_agent.api.chat import router as chat_router
from shop_agent.api.dependencies import ApiDependencies, build_api_dependencies
from shop_agent.api.health import router as health_router
from shop_agent.api.products import router as products_router


def create_app(dependencies: ApiDependencies | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if dependencies is None:
            application.state.dependencies = build_api_dependencies()
        yield

    application = FastAPI(title="ShopAgent", lifespan=lifespan)
    if dependencies is not None:
        application.state.dependencies = dependencies
    application.include_router(chat_router)
    application.include_router(products_router)
    application.include_router(health_router)
    return application


app = create_app()
