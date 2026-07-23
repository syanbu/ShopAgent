import logging
from collections.abc import Sequence

from shop_agent.catalog import ProductCatalog
from shop_agent.errors import ServiceError
from shop_agent.models.query import SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceAssessment,
    ProductCandidate,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.services.ports import EvidenceMapper


logger = logging.getLogger(__name__)


def semantic_checks_pass(
    assessment: EvidenceAssessment, constraints: SearchConstraints
) -> bool:
    checks = {check.condition: check for check in assessment.checks}
    for feature in constraints.required_features:
        check = checks.get(feature)
        if check is None or check.status != "supported":
            return False
    for feature in constraints.excluded_features:
        check = checks.get(feature)
        if check is None or check.status != "supported":
            return False
    return True


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
        validated: list[ValidatedCandidate] = []
        has_semantic_constraints = bool(
            constraints.required_features or constraints.excluded_features
        )
        for candidate in candidates:
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
            assessment = EvidenceAssessment(product_id=product.product_id)
            if has_semantic_constraints and not rejection_reasons:
                assessment = await self._mapper.map_conditions(
                    product.product_id,
                    constraints,
                    candidate.evidence,
                )
                self._validate_assessment(candidate, assessment)
                self._log_conflicts(candidate, assessment)
                if not semantic_checks_pass(assessment, constraints):
                    rejection_reasons.append("semantic_conditions_not_satisfied")

            validated.append(
                ValidatedCandidate(
                    candidate=candidate,
                    assessment=assessment,
                    eligible=not rejection_reasons,
                    rejection_reasons=rejection_reasons,
                )
            )
        return validated

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
            evidence_ids = self._decisive_evidence_ids(item.assessment, constraints)
            decision_reasons = ["structured_constraints_passed"]
            if constraints.required_features or constraints.excluded_features:
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
            reasons.append("price_out_of_range")
        return reasons

    @staticmethod
    def _validate_assessment(
        candidate: ProductCandidate, assessment: EvidenceAssessment
    ) -> None:
        if assessment.product_id != candidate.product.product_id:
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "evidence product ID mismatch",
                retryable=False,
            )
        conditions = [check.condition for check in assessment.checks]
        if len(conditions) != len(set(conditions)):
            raise ServiceError(
                "EVIDENCE_PARSE_FAILED",
                "duplicate evidence condition",
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
        assessment: EvidenceAssessment, constraints: SearchConstraints
    ) -> list[str]:
        conditions = {
            *constraints.required_features,
            *constraints.excluded_features,
        }
        selected: list[str] = []
        for check in assessment.checks:
            if check.condition not in conditions or check.status != "supported":
                continue
            for evidence_id in check.evidence_ids:
                if evidence_id not in selected:
                    selected.append(evidence_id)
        return selected
