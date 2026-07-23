import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from shop_agent.api.dependencies import Dependencies


router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.get("/{product_id}/image", response_class=FileResponse)
async def product_image(product_id: str, dependencies: Dependencies) -> FileResponse:
    try:
        path = dependencies.catalog.image_file(product_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="product image not found") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="product image not found")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "application/octet-stream")
