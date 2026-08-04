import asyncio
import json
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


class ConcurrencyTrackingMapper:
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0

    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0)
        finally:
            self.active_calls -= 1
        return EvidenceAssessment(
            product_id=product_id,
            checks=[
                EvidenceCheck(
                    condition=condition.condition_id,
                    status="unknown",
                )
                for condition in conditions
            ],
        )


class ReverseCompletionMapper:
    def __init__(self) -> None:
        self.completion_order: list[str] = []

    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        for _ in range(10 - int(product_id.removeprefix("p"))):
            await asyncio.sleep(0)
        self.completion_order.append(product_id)
        return EvidenceAssessment(
            product_id=product_id,
            checks=[
                EvidenceCheck(
                    condition=condition.condition_id,
                    status="unknown",
                )
                for condition in conditions
            ],
        )


class FailingConcurrentMapper:
    def __init__(self, error: ServiceError) -> None:
        self.error = error
        self.release = asyncio.Event()
        self.started: set[str] = set()
        self.cancelled: set[str] = set()
        self.siblings_finished = asyncio.Event()
        self._finished_siblings = 0

    async def map_conditions(
        self,
        product_id: str,
        conditions: Sequence[EvidenceCondition],
        evidence: Sequence[EvidenceChunk],
    ) -> EvidenceAssessment:
        self.started.add(product_id)
        if product_id == "p0":
            await asyncio.sleep(0)
            raise self.error
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.add(product_id)
            raise
        finally:
            self._finished_siblings += 1
            if self._finished_siblings == 4:
                self.siblings_finished.set()
        return EvidenceAssessment(
            product_id=product_id,
            checks=[
                EvidenceCheck(
                    condition=condition.condition_id,
                    status="unknown",
                )
                for condition in conditions
            ],
        )


def _product(
    product_id: str,
    *,
    brand: str = "测试品牌",
    category: str = "数码电子",
    sub_category: str = "蓝牙耳机",
    low_price: float = 399.0,
    high_price: float = 599.0,
    marketing_description: str = "测试商品",
    official_faq: list[dict[str, str]] | None = None,
    user_reviews: list[dict[str, object]] | None = None,
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
                "marketing_description": marketing_description,
                "official_faq": official_faq or [],
                "user_reviews": user_reviews or [],
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
async def test_semantic_validation_uses_complete_catalog_evidence() -> None:
    product = _product(
        "p1",
        marketing_description="清爽防晒产品。",
        official_faq=[
            {
                "question": "是否含酒精？",
                "answer": "配方中含有酒精。",
            }
        ],
        user_reviews=[
            {
                "nickname": "测试用户",
                "rating": 4,
                "content": "成膜速度很快。",
            }
        ],
    )
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[
            EvidenceCheck(
                condition="excluded:酒精",
                status="contradicted",
                evidence_ids=["p1:faq:0"],
            )
        ],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)
    retrieved_summary_only = _candidate(
        product,
        0.8,
        evidence=[_chunk("p1", "p1:summary", chunk_type="product_summary")],
    )

    validated = await service.validate_candidates(
        [retrieved_summary_only],
        SearchConstraints(excluded_features=["酒精"]),
    )

    assert [chunk.chunk_id for chunk in mapper.calls[0][2]] == [
        "p1:summary",
        "p1:faq:0",
        "p1:review:0",
    ]
    assert [chunk.chunk_id for chunk in validated[0].candidate.evidence] == [
        "p1:summary",
        "p1:faq:0",
        "p1:review:0",
    ]
    assert validated[0].eligible is False
    assert validated[0].rejection_reasons == ["semantic_condition_contradicted"]


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
    mapper = FakeEvidenceMapper({"p1": EvidenceAssessment(product_id="p1", checks=[])})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with pytest.raises(
        ServiceError,
        match="evidence conditions do not match request",
    ):
        await service.validate_candidates(
            [_candidate(product, 0.8)],
            SearchConstraints(required_features=["防水"]),
        )


def test_evidence_check_rejects_fields_outside_tool_schema() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceCheck.model_validate(
            {
                "condition": "excluded:曲面屏",
                "status": "unknown",
                "supported": False,
            }
        )


def test_evidence_assessment_requires_checks() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        EvidenceAssessment.model_validate({"product_id": "p1"})


@pytest.mark.asyncio
async def test_validate_candidates_limits_evidence_concurrency_to_five() -> None:
    products = [_product(f"p{index}") for index in range(10)]
    mapper = ConcurrencyTrackingMapper()
    service = EvidenceService(catalog=_catalog(products), mapper=mapper)

    await service.validate_candidates(
        [
            _candidate(product, 1.0 - index / 100)
            for index, product in enumerate(products)
        ],
        SearchConstraints(required_features=["防水"]),
    )

    assert mapper.max_active_calls == 5


@pytest.mark.asyncio
async def test_validate_candidates_preserves_input_order_when_calls_finish_out_of_order() -> (
    None
):
    products = [_product(f"p{index}") for index in range(10)]
    mapper = ReverseCompletionMapper()
    service = EvidenceService(catalog=_catalog(products), mapper=mapper)

    validated = await service.validate_candidates(
        [_candidate(product, 0.5) for product in products],
        SearchConstraints(required_features=["防水"]),
    )

    input_order = [product.product_id for product in products]
    assert mapper.completion_order != input_order
    assert [item.candidate.product.product_id for item in validated] == input_order


@pytest.mark.asyncio
async def test_validate_candidates_cancels_siblings_and_preserves_error() -> None:
    products = [_product(f"p{index}") for index in range(5)]
    error = ServiceError(
        "EVIDENCE_PARSE_FAILED",
        "invalid evidence response",
        retryable=True,
    )
    mapper = FailingConcurrentMapper(error)
    service = EvidenceService(catalog=_catalog(products), mapper=mapper)

    try:
        with pytest.raises(ServiceError) as caught:
            await service.validate_candidates(
                [_candidate(product, 0.5) for product in products],
                SearchConstraints(required_features=["防水"]),
            )

        assert caught.value is error
        assert mapper.started == {"p0", "p1", "p2", "p3", "p4"}
        assert mapper.cancelled == {"p1", "p2", "p3", "p4"}
    finally:
        mapper.release.set()
        await asyncio.wait_for(mapper.siblings_finished.wait(), timeout=1)


@pytest.mark.asyncio
async def test_evidence_condition_mismatch_logs_exact_set_difference(
    caplog: pytest.LogCaptureFixture,
) -> None:
    product = _product("p1")
    mapper = FakeEvidenceMapper(
        {
            "p1": EvidenceAssessment(
                product_id="p1",
                checks=[EvidenceCheck(condition="防水", status="unknown")],
            )
        }
    )
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with caplog.at_level(logging.ERROR, logger="shop_agent.services.evidence"):
        with pytest.raises(
            ServiceError,
            match="evidence conditions do not match request",
        ):
            await service.validate_candidates(
                [_candidate(product, 0.8)],
                SearchConstraints(required_features=["防水"]),
            )

    message = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("evidence_condition_mismatch ")
    )
    assert json.loads(message.removeprefix("evidence_condition_mismatch ")) == {
        "product_id": "p1",
        "expected": ["required:防水"],
        "returned": ["防水"],
        "missing": ["required:防水"],
        "unexpected": ["防水"],
        "checks": [
            {
                "condition": "防水",
                "status": "unknown",
                "evidence_ids": [],
                "conflicting_evidence_ids": [],
            }
        ],
    }


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
    product = _product(
        "p1",
        official_faq=[
            {
                "question": "是否防水？",
                "answer": "支持防水。",
            }
        ],
        user_reviews=[
            {
                "nickname": "测试用户",
                "rating": 2,
                "content": "实际使用时进水了。",
            }
        ],
    )
    assessment = EvidenceAssessment(
        product_id="p1",
        checks=[
            EvidenceCheck(
                condition="required:防水",
                status="supported",
                evidence_ids=["p1:faq:0"],
                conflicting_evidence_ids=["p1:review:0"],
            )
        ],
    )
    mapper = FakeEvidenceMapper({"p1": assessment})
    service = EvidenceService(catalog=_catalog([product]), mapper=mapper)

    with caplog.at_level(logging.WARNING):
        validated = await service.validate_candidates(
            candidates=[_candidate(product, 0.8)],
            constraints=SearchConstraints(required_features=["防水"]),
        )

    assert "catalog_evidence_conflict" in caplog.text
    assert validated[0].assessment.checks[0].evidence_ids == ["p1:faq:0"]
    selected = service.select_candidates(
        validated,
        limit=3,
        constraints=SearchConstraints(required_features=["防水"]),
    )
    assert selected[0].evidence_ids == ["p1:faq:0"]


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
