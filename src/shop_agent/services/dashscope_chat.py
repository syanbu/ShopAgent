import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TypeVar

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject
from pydantic import BaseModel, ValidationError

from shop_agent.config import Settings
from shop_agent.errors import ErrorCode, ServiceError
from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import EvidenceAssessment, EvidenceChunk


logger = logging.getLogger(__name__)
StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class _DashScopeChatGateway:
    def __init__(self, settings: Settings) -> None:
        self._model = settings.chat_model
        self._client = AsyncOpenAI(
            base_url=settings.dashscope_base_url,
            api_key=settings.dashscope_api_key,
            timeout=settings.model_timeout_seconds,
        )

    async def _structured_call(
        self,
        messages: list[ChatCompletionMessageParam],
        validator: Callable[[str], StructuredResult],
        error_code: ErrorCode,
    ) -> StructuredResult:
        current_messages = list(messages)
        response_format: ResponseFormatJSONObject = {"type": "json_object"}
        for attempt in range(2):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=current_messages,
                    response_format=response_format,
                    extra_body={"enable_thinking": False},
                )
                content = response.choices[0].message.content or ""
            except Exception as exc:
                logger.exception("DashScope structured call failed", exc_info=exc)
                raise ServiceError(
                    error_code,
                    "upstream structured output error",
                    retryable=True,
                ) from exc

            try:
                return validator(content)
            except ValidationError as exc:
                if attempt == 1:
                    raise ServiceError(
                        error_code,
                        "invalid structured output",
                        retryable=True,
                    ) from exc
                current_messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "The original output failed Pydantic validation. "
                            f"Original output: {content}\n"
                            f"Validation error: {exc}\n"
                            "Return one corrected JSON object only."
                        ),
                    },
                ]
        raise AssertionError("unreachable")


class DashScopeIntentParser(_DashScopeChatGateway):
    async def parse(self, message: str) -> ParsedIntent:
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "Classify one user turn as product_search or non_shopping and "
                    "return JSON matching ParsedIntent schema_version 1. Keep price, "
                    "brand, required features, and excluded features in constraints."
                ),
            },
            {"role": "user", "content": message},
        ]
        return await self._structured_call(
            messages,
            ParsedIntent.model_validate_json,
            "INTENT_PARSE_FAILED",
        )


class DashScopeEvidenceMapper(_DashScopeChatGateway):
    async def map_conditions(
        self,
        product_id: str,
        constraints: SearchConstraints,
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "chunk_type": chunk.chunk_type,
                "text": chunk.text,
            }
            for chunk in evidence
        ]
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "Map semantic shopping conditions to the supplied evidence only. "
                    "Use source priority official_faq > product_summary > user_review. "
                    "Put losing-side chunk IDs in conflicting_evidence_ids, and never "
                    "use a user review to prove an official specification. Return JSON "
                    "matching EvidenceAssessment."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Apply source priority official_faq > product_summary > "
                    "user_review; never use a user review to prove an official "
                    "specification.\n"
                    + json.dumps(
                        {
                            "product_id": product_id,
                            "constraints": constraints.model_dump(),
                            "evidence": chunks,
                        },
                        ensure_ascii=False,
                    )
                ),
            },
        ]
        return await self._structured_call(
            messages,
            EvidenceAssessment.model_validate_json,
            "EVIDENCE_PARSE_FAILED",
        )


class DashScopeResponseGenerator(_DashScopeChatGateway):
    async def stream(self, prompt: str) -> AsyncIterator[str]:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                extra_body={"enable_thinking": False},
            )
            async for chunk in response:
                if chunk.choices:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
        except Exception as exc:
            logger.exception("DashScope response streaming failed", exc_info=exc)
            raise ServiceError(
                "GENERATION_FAILED",
                "upstream generation error",
                retryable=True,
            ) from exc
