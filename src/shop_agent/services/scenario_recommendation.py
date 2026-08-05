"""Deterministic slot retrieval and atomic scenario bundle composition."""

import asyncio
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from shop_agent.models.query import ParsedIntent, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceChunk,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.models.scenario import ScenarioSlotSpec, ScenarioSnapshot, SolutionRecipe


class RetrievalOperations(Protocol):
    async def retrieve_chunks(
        self,
        intent: ParsedIntent,
        *,
        excluded_product_ids: Sequence[str] = (),
    ) -> list[RetrievedChunk]: ...

    async def fetch_product_chunks(self, product_id: str) -> list[EvidenceChunk]: ...

    def aggregate_products(
        self,
        chunks: Sequence[RetrievedChunk],
        *,
        max_evidence_chunks: int | None = 5,
    ) -> list[ProductCandidate]: ...

    async def rerank_candidates(
        self,
        query: str,
        candidates: Sequence[ProductCandidate],
    ) -> list[ProductCandidate]: ...


class EvidenceOperations(Protocol):
    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]: ...

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]: ...


class ScenarioSelectedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str
    slot_label: str
    slot_group: str
    selected_product: SelectedProduct


class ScenarioRecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "incomplete_required_slots"]
    selected_items: tuple[ScenarioSelectedItem, ...] = ()
    missing_required_slot_ids: tuple[str, ...] = ()
    candidates: tuple[ProductCandidate, ...] = ()
    validated_candidates: tuple[ValidatedCandidate, ...] = ()


class ScenarioRecommendationService:
    def __init__(
        self,
        *,
        retrieval: RetrievalOperations,
        evidence: EvidenceOperations,
        product_limit: int,
    ) -> None:
        if not 1 <= product_limit <= 8:
            raise ValueError("scenario product limit must be between 1 and 8")
        self._retrieval = retrieval
        self._evidence = evidence
        self._product_limit = product_limit

    async def build_bundle(
        self,
        recipe: SolutionRecipe,
        snapshot: ScenarioSnapshot,
    ) -> ScenarioRecommendationResult:
        limit = min(recipe.max_products, self._product_limit, 8)
        selected: list[ScenarioSelectedItem] = []
        candidates: list[ProductCandidate] = []
        validated: list[ValidatedCandidate] = []
        excluded_ids = set(snapshot.seen_product_ids)

        required_slots = [slot for slot in recipe.slots if slot.required]
        optional_slots = [slot for slot in recipe.slots if not slot.required]
        for slot in [*required_slots, *optional_slots]:
            if len(selected) >= limit:
                break
            slot_result = await self._select_slot(
                recipe,
                slot,
                original_request=snapshot.original_request,
                excluded_product_ids=tuple(sorted(excluded_ids)),
            )
            candidates.extend(slot_result[0])
            validated.extend(slot_result[1])
            selected_product = slot_result[2]
            if selected_product is None:
                if slot.required:
                    return ScenarioRecommendationResult(
                        status="incomplete_required_slots",
                        missing_required_slot_ids=(slot.slot_id,),
                        candidates=tuple(candidates),
                        validated_candidates=tuple(validated),
                    )
                continue
            selected.append(
                ScenarioSelectedItem(
                    slot_id=slot.slot_id,
                    slot_label=slot.label,
                    slot_group=slot.group,
                    selected_product=selected_product,
                )
            )
            excluded_ids.add(selected_product.product_id)

        return ScenarioRecommendationResult(
            status="complete",
            selected_items=tuple(selected),
            candidates=tuple(candidates),
            validated_candidates=tuple(validated),
        )

    async def _select_slot(
        self,
        recipe: SolutionRecipe,
        slot: ScenarioSlotSpec,
        *,
        original_request: str,
        excluded_product_ids: Sequence[str],
    ) -> tuple[
        list[ProductCandidate],
        list[ValidatedCandidate],
        SelectedProduct | None,
    ]:
        retrieval_query = "、".join(
            dict.fromkeys(
                [recipe.display_name, original_request, slot.label, *slot.query_terms]
            )
        )
        constraints = SearchConstraints()
        intents = [
            ParsedIntent(
                schema_version=1,
                intent="product_search",
                retrieval_query=retrieval_query,
                category=scope.category,
                sub_category=scope.sub_category,
                constraints=constraints,
            )
            for scope in slot.catalog_scopes
        ]
        tasks = [
            asyncio.create_task(
                self._retrieval.retrieve_chunks(
                    intent,
                    excluded_product_ids=excluded_product_ids,
                )
            )
            for intent in intents
        ]
        try:
            batches = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        chunks = [chunk for batch in batches for chunk in batch]
        if not chunks:
            return [], [], None
        unique_chunks = list({chunk.chunk_id: chunk for chunk in chunks}.values())
        aggregated = self._retrieval.aggregate_products(unique_chunks)
        allowed_scopes = {
            (scope.category, scope.sub_category) for scope in slot.catalog_scopes
        }
        scoped = [
            candidate
            for candidate in aggregated
            if (
                candidate.product.category,
                candidate.product.sub_category,
            )
            in allowed_scopes
            and candidate.product.product_id not in excluded_product_ids
        ]
        reranked = await self._retrieval.rerank_candidates(
            retrieval_query,
            scoped,
        )
        validated = await self._evidence.validate_candidates(
            reranked,
            constraints,
        )
        selected = self._evidence.select_candidates(
            validated,
            1,
            constraints=constraints,
        )
        return reranked, validated, selected[0] if selected else None
