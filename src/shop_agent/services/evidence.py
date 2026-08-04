import asyncio
import json
import logging
from collections.abc import Sequence

from shop_agent.catalog import ProductCatalog
from shop_agent.chunking import build_product_chunks
from shop_agent.errors import ServiceError
from shop_agent.models.query import (
    EvidenceCondition,
    SearchConstraints,
    build_evidence_conditions,
)
from shop_agent.models.retrieval import (
    EvidenceAssessment,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.services.ports import EvidenceMapper


logger = logging.getLogger(__name__)
_EVIDENCE_CONCURRENCY_LIMIT = 5
_UNICODE_LINE_SEPARATOR_ESCAPES = str.maketrans(
    {
        "\u0085": "\\u0085",
        "\u2028": "\\u2028",
        "\u2029": "\\u2029",
    }
)


def _single_line_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return encoded.translate(_UNICODE_LINE_SEPARATOR_ESCAPES)


def semantic_conditions_allow_candidate(assessment: EvidenceAssessment) -> bool:
    return all(check.status != "contradicted" for check in assessment.checks)


class EvidenceService:
    def __init__(self, *, catalog: ProductCatalog, mapper: EvidenceMapper) -> None:
        self._catalog = catalog
        self._mapper = mapper

    async def validate_candidates(
        self,
        candidates: Sequence[ProductCandidate],
        constraints: SearchConstraints,
        *,
        category: str | None = None,
        sub_category: str | None = None,
    ) -> list[ValidatedCandidate]:
        semaphore = asyncio.Semaphore(_EVIDENCE_CONCURRENCY_LIMIT)
        tasks = [
            asyncio.create_task(
                self._validate_candidate(
                    candidate,
                    constraints,
                    category=category,
                    sub_category=sub_category,
                    semaphore=semaphore,
                )
            )
            for candidate in candidates
        ]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _validate_candidate(
        self,
        candidate: ProductCandidate,
        constraints: SearchConstraints,
        *,
        category: str | None,
        sub_category: str | None,
        semaphore: asyncio.Semaphore,
    ) -> ValidatedCandidate:
        product = self._catalog.get(candidate.product.product_id)
        rejection_reasons = self._structured_rejections(
            product.product_id,
            product.category,
            product.sub_category,
            product.brand,
            constraints,
            category=category,
            sub_category=sub_category,
        )
        assessment = EvidenceAssessment(product_id=product.product_id, checks=[])
        unresolved_numeric = self._catalog.unresolved_numeric_constraints(
            product.product_id,
            constraints,
        )
        evidence_constraints = constraints.model_copy(
            update={"numeric_constraints": unresolved_numeric}
        )
        conditions = build_evidence_conditions(evidence_constraints)
        evidence_candidate = candidate
        if conditions and not rejection_reasons:
            evidence_candidate = self._with_complete_catalog_evidence(candidate)
            async with semaphore:
                assessment = await self._mapper.map_conditions(
                    product.product_id,
                    conditions,
                    evidence_candidate.evidence,
                )
            self._validate_assessment(evidence_candidate, assessment, conditions)
            self._log_conflicts(evidence_candidate, assessment)
            if not semantic_conditions_allow_candidate(assessment):
                rejection_reasons.append("semantic_condition_contradicted")

        return ValidatedCandidate(
            candidate=evidence_candidate,
            assessment=assessment,
            eligible=not rejection_reasons,
            rejection_reasons=rejection_reasons,
        )

    def _with_complete_catalog_evidence(
        self,
        candidate: ProductCandidate,
    ) -> ProductCandidate:
        product_id = candidate.product.product_id
        retrieved_scores = {
            chunk.chunk_id: chunk.score for chunk in candidate.evidence
        }
        evidence = [
            RetrievedChunk.model_validate(
                {
                    **chunk.model_dump(mode="python"),
                    "score": retrieved_scores.get(chunk.chunk_id, 0.0),
                }
            )
            for chunk in build_product_chunks(
                self._catalog.get(product_id),
                self._catalog.source_path(product_id),
            )
        ]
        return candidate.model_copy(update={"evidence": evidence}, deep=True)

    def select_candidates(
        self,
        validated: Sequence[ValidatedCandidate],
        limit: int,
        *,
        constraints: SearchConstraints,
    ) -> list[SelectedProduct]:
        scored: list[tuple[ValidatedCandidate, float]] = []
        for item in validated:
            if not item.eligible:
                continue
            score = item.candidate.rerank_score
            if score is None:
                raise ServiceError(
                    "RERANK_UNAVAILABLE",
                    "eligible candidate is missing a rerank score",
                    retryable=False,
                )
            scored.append((item, score))
        scored.sort(
            key=lambda pair: (
                -pair[1],
                pair[0].candidate.product.product_id,
            )
        )

        selected: list[SelectedProduct] = []
        for item, score in scored[: max(limit, 0)]:
            product_id = item.candidate.product.product_id
            evidence_ids = self._decisive_evidence_ids(item.assessment)
            decision_reasons = ["structured_constraints_passed"]
            statuses = {check.status for check in item.assessment.checks}
            if "unknown" in statuses:
                decision_reasons.append("semantic_conditions_unknown")
            elif statuses:
                decision_reasons.append("semantic_conditions_supported")
            decision_reasons.append("rerank_selected")
            selected.append(
                SelectedProduct(
                    product_id=product_id,
                    rerank_score=score,
                    evidence_ids=evidence_ids,
                    decision_reasons=decision_reasons,
                    matched_sku_ids=[
                        sku.sku_id
                        for sku in self._catalog.matched_skus(product_id, constraints)
                    ],
                )
            )
        return selected

    def _structured_rejections(
        self,
        product_id: str,
        product_category: str,
        product_sub_category: str,
        product_brand: str,
        constraints: SearchConstraints,
        *,
        category: str | None,
        sub_category: str | None,
    ) -> list[str]:
        reasons: list[str] = []
        if category is not None and product_category != category:
            reasons.append("category_mismatch")
        if sub_category is not None and product_sub_category != sub_category:
            reasons.append("sub_category_mismatch")
        if (
            constraints.include_brands
            and product_brand not in constraints.include_brands
        ):
            reasons.append("brand_not_included")
        if product_brand in constraints.exclude_brands:
            reasons.append("brand_excluded")
        if not self._catalog.matched_skus(product_id, constraints):
            reasons.append("no_matching_sku")
        return reasons

    @staticmethod
    def _validate_assessment(
        candidate: ProductCandidate,
        assessment: EvidenceAssessment,
        conditions: Sequence[EvidenceCondition],
    ) -> None:
        if assessment.product_id != candidate.product.product_id:
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "evidence product ID mismatch",
                retryable=False,
            )
        returned_condition_ids = [check.condition for check in assessment.checks]
        if len(returned_condition_ids) != len(set(returned_condition_ids)):
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "duplicate evidence condition",
                retryable=False,
            )
        expected_conditions = {condition.condition_id for condition in conditions}
        returned_conditions = {check.condition for check in assessment.checks}
        if returned_conditions != expected_conditions:
            logger.error(
                "evidence_condition_mismatch %s",
                _single_line_json(
                    {
                        "product_id": candidate.product.product_id,
                        "expected": sorted(expected_conditions),
                        "returned": sorted(returned_conditions),
                        "missing": sorted(expected_conditions - returned_conditions),
                        "unexpected": sorted(returned_conditions - expected_conditions),
                        "checks": [
                            check.model_dump(mode="json") for check in assessment.checks
                        ],
                    }
                ),
            )
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "evidence conditions do not match request",
                retryable=False,
            )
        known_ids = {chunk.chunk_id for chunk in candidate.evidence}
        returned_ids = {
            evidence_id
            for check in assessment.checks
            for evidence_id in [
                *check.evidence_ids,
                *check.conflicting_evidence_ids,
            ]
        }
        if not returned_ids.issubset(known_ids):
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "unknown evidence ID",
                retryable=False,
            )

    def _log_conflicts(
        self, candidate: ProductCandidate, assessment: EvidenceAssessment
    ) -> None:
        product_id = candidate.product.product_id
        source_path = self._catalog.source_path(product_id)
        for check in assessment.checks:
            if check.conflicting_evidence_ids:
                logger.warning(
                    "catalog_evidence_conflict",
                    extra={
                        "product_id": product_id,
                        "condition": check.condition,
                        "decisive_ids": check.evidence_ids,
                        "conflicting_ids": check.conflicting_evidence_ids,
                        "source_path": source_path,
                    },
                )

    @staticmethod
    def _decisive_evidence_ids(
        assessment: EvidenceAssessment,
    ) -> list[str]:
        selected: list[str] = []
        for check in assessment.checks:
            if check.status != "supported":
                continue
            for evidence_id in check.evidence_ids:
                if evidence_id not in selected:
                    selected.append(evidence_id)
        return selected
