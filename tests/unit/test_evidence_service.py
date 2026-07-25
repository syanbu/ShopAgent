import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from shop_agent.catalog import ProductCatalog
from shop_agent.errors import ServiceError
from shop_agent.models.product import Product
from shop_agent.models.query import (
    EvidenceCondition,
    NumericConstraint,
    SearchConstraints,
)
from shop_agent.models.retrieval import (
    ChunkType,
    EvidenceAssessment,
    EvidenceChunk,
    EvidenceCheck,
    ProductCandidate,
    RetrievedChunk,
    ValidatedCandidate,
)
from shop_agent.services.evidence import EvidenceService


def test_supported_evidence_check_requires_decisive_evidence() -> None:
    with pytest.raises(ValidationError, match="supported check requires evidence"):
        EvidenceCheck(
            condition="required:防水",
            status="supported",
            evidence_ids=[],
        )


def test_contradicted_evidence_check_requires_decisive_evidence() -> None:
    with pytest.raises(ValidationError, match="contradicted check requires evidence"):
        EvidenceCheck(
            condition="required:防水",
            status="contradicted",
            evidence_ids=[],
        )


class FakeEvidenceMapper:
    def __init__(self, assessments: dict[str, EvidenceAssessment]) -> None:
        self.assessments = assessments
        self.calls: list[tuple[str, list[EvidenceCondition], list[EvidenceChunk]]] = []

    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        self.calls.append((product_id, list(conditions), list(evidence)))
        return self.assessments[product_id]


def _product(
    product_id: str,
    *,
    brand: str = "测试品牌",
    category: str = "数码电子",
    sub_category: str = "蓝牙耳机",
    low_price: float = 399.0,
    high_price: float = 599.0,
) -> Product:
    return Product.model_validate(
        {
            "product_id": product_id,
            "title": f"商品 {product_id}",
            "brand": brand,
            "category": category,
            "sub_category": sub_category,
            "base_price": low_price,
            "image_path": f"2_数码电子/images/{product_id}.jpg",
            "skus": [
                {
                    "sku_id": f"{product_id}-low",
                    "properties": {"款式": "基础"},
                    "price": low_price,
                },
                {
                    "sku_id": f"{product_id}-high",
                    "properties": {"款式": "升级"},
                    "price": high_price,
                },
            ],
            "rag_knowledge": {
                "marketing_description": "测试商品",
                "official_faq": [],
                "user_reviews": [],
            },
        }
    )


def _chunk(
    product_id: str,
    chunk_id: str,
    *,
    chunk_type: ChunkType = "official_faq",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        point_id="00000000-0000-0000-0000-000000000001",
        product_id=product_id,
        chunk_type=chunk_type,
        text=f"证据 {chunk_id}",
        source_path=f"2_数码电子/data/{product_id}.json",
        score=0.9,
    )


def _candidate(
    product: Product,
    score: float,
    *,
    evidence: list[RetrievedChunk] | None = None,
) -> ProductCandidate:
    return ProductCandidate(
        product=product,
        evidence=evidence
        or [_chunk(product.product_id, f"{product.product_id}:summary")],
        rerank_score=score,
    )


def _catalog(products: list[Product]) -> ProductCatalog:
    return ProductCatalog(
        Path("."),
        {product.product_id: product for product in products},
        {
            product.product_id: f"2_数码电子/data/{product.product_id}.json"
            for product in products
        },
    )


def _validated(
    candidate: ProductCandidate,
    *,
    eligible: bool,
    checks: list[EvidenceCheck] | None = None,
    rejection_reasons: list[str] | None = None,
) -> ValidatedCandidate:
    return ValidatedCandidate(
        candidate=candidate,
        assessment=EvidenceAssessment(
            product_id=candidate.product.product_id,
            checks=checks or [],
        ),
        eligible=eligible,
        rejection_reasons=rejection_reasons or [],
    )


@pytest.mark.asyncio
async def test_required_unknown_feature_keeps_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition="required:防水", status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(required_features=["防水"]),
    )

    assert validated[0].eligible is True
    assert validated[0].rejection_reasons == []


@pytest.mark.asyncio
async def test_excluded_unknown_feature_keeps_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition="excluded:入耳式", status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(excluded_features=["入耳式"]),
    )

    assert validated[0].eligible is True


@pytest.mark.asyncio
async def test_contradicted_feature_rejects_candidate() -> None:
    product = _product("p1")
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[
            EvidenceCheck(
                condition="required:防水",
                status="contradicted",
                evidence_ids=["p1:summary"],
            )
        ],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(required_features=["防水"]),
    )

    assert validated[0].eligible is False
    assert validated[0].rejection_reasons == ["semantic_condition_contradicted"]


@pytest.mark.asyncio
async def test_missing_evidence_condition_is_parse_failure() -> None:
    product = _product("p1")
    mapper = FakeEvidenceMapper(
        {"p1": EvidenceAssessment(product_id="p1", checks=[])}
    )
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with pytest.raises(
        ServiceError,
        match="evidence conditions do not match request",
    ):
        await service.validate_candidates(
            [_candidate(product, 0.8)],
            SearchConstraints(required_features=["防水"]),
        )


@pytest.mark.asyncio
async def test_text_only_numeric_constraint_is_sent_to_evidence_mapper() -> None:
    product = _product("p1")
    numeric = NumericConstraint(
        field="battery_capacity",
        operator=">=",
        value=5000,
        unit="mAh",
    )
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[EvidenceCheck(condition=numeric.condition_id(), status="unknown")],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(numeric_constraints=[numeric]),
    )

    assert validated[0].eligible is True
    assert [condition.condition_id for condition in mapper.calls[0][1]] == [
        numeric.condition_id()
    ]


def test_no_semantic_constraints_selects_rerank_top_three() -> None:
    products = [_product(f"p{index}") for index in range(1, 5)]
    candidates = [
        _candidate(products[0], 0.51),
        _candidate(products[1], 0.82),
        _candidate(products[2], 0.93),
        _candidate(products[3], 0.20),
    ]
    service = EvidenceService(catalog=_catalog(products), mapper=FakeEvidenceMapper({}))
    eligible_ranked_candidates = [
        _validated(candidate, eligible=True) for candidate in candidates
    ]

    selected = service.select_candidates(
        validated=eligible_ranked_candidates,
        limit=3,
        constraints=SearchConstraints(),
    )

    assert [item.product_id for item in selected] == ["p3", "p2", "p1"]


def test_select_candidates_requires_constraints_context() -> None:
    product = _product("p1")
    service = EvidenceService(
        catalog=_catalog([product]), mapper=FakeEvidenceMapper({})
    )
    validated = _validated(_candidate(product, 0.8), eligible=True)

    with pytest.raises(TypeError, match="constraints"):
        cast(Any, service.select_candidates)([validated], limit=3)


@pytest.mark.asyncio
async def test_validate_candidates_skips_mapper_without_semantic_constraints() -> None:
    product = _product("p1")
    mapper = FakeEvidenceMapper({})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)], SearchConstraints(max_price=500)
    )

    assert validated[0].eligible is True
    assert validated[0].assessment.checks == []
    assert mapper.calls == []


@pytest.mark.asyncio
async def test_validate_candidates_checks_catalog_category_brand_and_sku_price() -> (
    None
):
    product = _product("p1", brand="品牌A", low_price=399, high_price=599)
    service = EvidenceService(
        catalog=_catalog([product]), mapper=FakeEvidenceMapper({})
    )

    validated = await service.validate_candidates(
        [_candidate(product, 0.8)],
        SearchConstraints(include_brands=["品牌B"], max_price=300),
        category="服饰运动",
        sub_category="运动鞋",
    )

    assert validated[0].eligible is False
    assert validated[0].rejection_reasons == [
        "category_mismatch",
        "sub_category_mismatch",
        "brand_not_included",
        "no_matching_sku",
    ]


@pytest.mark.asyncio
async def test_conflicting_text_evidence_is_logged_and_not_selected_as_proof(
    caplog: pytest.LogCaptureFixture,
) -> None:
    product = _product("p1")
    evidence = [
        _chunk("p1", "faq-decisive"),
        _chunk("p1", "review-conflict", chunk_type="user_review"),
    ]
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[
            EvidenceCheck(
                condition="required:防水",
                status="supported",
                evidence_ids=["faq-decisive"],
                conflicting_evidence_ids=["review-conflict"],
            )
        ],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with caplog.at_level(logging.WARNING):
        validated = await service.validate_candidates(
            candidates=[_candidate(product, 0.8, evidence=evidence)],
            constraints=SearchConstraints(required_features=["防水"]),
        )

    assert "catalog_evidence_conflict" in caplog.text
    assert validated[0].assessment.checks[0].evidence_ids == ["faq-decisive"]
    selected = service.select_candidates(
        validated,
        limit=3,
        constraints=SearchConstraints(required_features=["防水"]),
    )
    assert selected[0].evidence_ids == ["faq-decisive"]


@pytest.mark.asyncio
async def test_validate_candidates_rejects_unknown_evidence_id() -> None:
    product = _product("p1")
    mapper = FakeEvidenceMapper(
        {
            "p1": EvidenceAssessment(
                product_id="p1",
                checks=[
                    EvidenceCheck(
                        condition="required:防水",
                        status="supported",
                        evidence_ids=["not-in-candidate"],
                    )
                ],
            )
        }
    )
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with pytest.raises(ServiceError) as error:
        await service.validate_candidates(
            [_candidate(product, 0.8)],
            SearchConstraints(required_features=["防水"]),
        )

    assert error.value.code == "EVIDENCE_PARSE_FAILED"
    assert error.value.message == "unknown evidence ID"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_validate_candidates_rejects_duplicate_evidence_condition() -> None:
    product = _product("p1")
    evidence = [
        _chunk("p1", "faq-supported"),
        _chunk("p1", "review-unknown", chunk_type="user_review"),
    ]
    mapper = FakeEvidenceMapper(
        {
            "p1": EvidenceAssessment(
                product_id="p1",
                checks=[
                    EvidenceCheck(
                        condition="required:防水",
                        status="supported",
                        evidence_ids=["faq-supported"],
                    ),
                    EvidenceCheck(
                        condition="required:防水",
                        status="unknown",
                        evidence_ids=["review-unknown"],
                    ),
                ],
            )
        }
    )
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with pytest.raises(ServiceError) as error:
        await service.validate_candidates(
            [_candidate(product, 0.8, evidence=evidence)],
            SearchConstraints(required_features=["防水"]),
        )

    assert error.value.code == "EVIDENCE_PARSE_FAILED"
    assert error.value.message == "duplicate evidence condition"
    assert error.value.retryable is False


def test_select_candidates_uses_exact_catalog_price_filter() -> None:
    product = _product("p1", low_price=399, high_price=599)
    service = EvidenceService(
        catalog=_catalog([product]), mapper=FakeEvidenceMapper({})
    )
    validated = _validated(_candidate(product, 0.8), eligible=True)

    selected = service.select_candidates(
        [validated],
        limit=3,
        constraints=SearchConstraints(max_price=500),
    )

    assert selected[0].matched_sku_ids == ["p1-low"]
