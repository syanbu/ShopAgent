import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from shop_agent.api.dependencies import ApiDependencies, Dependencies
from shop_agent.errors import ServiceError
from shop_agent.models.events import ErrorData, MessageEndData, MessageStartData
from shop_agent.models.state import ShoppingState


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


@router.post("/stream", response_class=EventSourceResponse)
async def chat_stream(
    body: ChatRequest, dependencies: Dependencies
) -> EventSourceResponse:
    request_id = dependencies.id_factory()
    conversation_id = body.conversation_id or dependencies.id_factory()
    return EventSourceResponse(
        _stream_events(
            dependencies,
            request_id=request_id,
            conversation_id=conversation_id,
            message=body.message,
        ),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        ping=15,
    )


async def _stream_events(
    dependencies: ApiDependencies,
    *,
    request_id: str,
    conversation_id: str,
    message: str,
) -> AsyncIterator[ServerSentEvent]:
    start = MessageStartData(
        request_id=request_id,
        conversation_id=conversation_id,
    )
    yield _sse("message_start", start.model_dump(mode="json"))

    status: Literal["completed", "partial", "failed"] = "completed"
    product_sent = False
    state: ShoppingState = {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "user_message": message,
    }
    try:
        async for part in dependencies.graph.astream(
            state, stream_mode="custom", version="v2"
        ):
            payload = part["data"]
            event_name = payload["event"]
            if event_name == "product":
                product_sent = True
            yield _sse(event_name, payload["data"])
    except ServiceError as exc:
        status = "partial" if product_sent else "failed"
        yield _sse("error", exc.to_payload())
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "unhandled chat stream error",
            extra={"request_id": request_id},
        )
        status = "partial" if product_sent else "failed"
        error = ErrorData(
            code="INTERNAL_ERROR",
            message="internal service error",
            retryable=False,
        )
        yield _sse("error", error.model_dump(mode="json"))

    end = MessageEndData(request_id=request_id, status=status)
    yield _sse("message_end", end.model_dump(mode="json"))


def _sse(event: str, data: Any) -> ServerSentEvent:
    return ServerSentEvent(
        event=event,
        data=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )
