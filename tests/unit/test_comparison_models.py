import pytest
from pydantic import ValidationError

from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonEvidence,
    ComparisonProductFinding,
    ComparisonProductMaterial,
)
from shop_agent.models.turn_query import (
    ComparisonCandidateMatch,
    ProductComparison,
    TurnQuery,
)


def _finding(product_id: str) -> ComparisonProductFinding:
    return ComparisonProductFinding(
        product_id=product_id,
        evidence_ids=[f"{product_id}:structured"],
        supported_summary=f"{product_id} 的保湿资料",
        limitations=[],
    )


def test_product_comparison_accepts_stable_candidate_selection() -> None:
    comparison = ProductComparison(
        question="第一款和第二款哪个更保湿",
        dimension="保湿",
        surface_text="第一款和第二款",
        candidate_matches=[
            ComparisonCandidateMatch(product_id="p1", selected=True),
            ComparisonCandidateMatch(product_id="p2", selected=True),
            ComparisonCandidateMatch(product_id="p3", selected=False),
        ],
    )

    turn = TurnQuery(
        schema_version=1,
        intent="product_comparison",
        product_comparison=comparison,
    )

    assert turn.product_comparison == comparison


def test_product_comparison_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        ProductComparison(
            question="对比一下",
            dimension="保湿",
            surface_text="对比",
            candidate_matches=[
                ComparisonCandidateMatch(product_id="p1", selected=True),
                ComparisonCandidateMatch(product_id="p1", selected=True),
            ],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "intent": "product_comparison"},
        {
            "schema_version": 1,
            "intent": "product_comparison",
            "product_comparison": {
                "question": "哪个好",
                "dimension": None,
                "surface_text": None,
            },
        },
        {
            "schema_version": 1,
            "intent": "new_search",
            "product_comparison": {
                "question": "哪个好",
                "surface_text": "哪个",
            },
        },
    ],
)
def test_turn_query_rejects_invalid_comparison_contract(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TurnQuery.model_validate(payload)


def test_clarification_answer_can_carry_only_a_comparison_dimension() -> None:
    turn = TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "clarification_answer",
            "product_comparison": {
                "question": "保湿",
                "dimension": "保湿",
                "surface_text": None,
                "candidate_matches": [],
            },
        }
    )

    assert turn.product_comparison is not None
    assert turn.product_comparison.dimension == "保湿"


def test_comparison_material_requires_unique_evidence_ids() -> None:
    evidence = ComparisonEvidence(
        evidence_id="p1:structured",
        source_type="structured_facts",
        content="真实商品资料",
    )

    with pytest.raises(ValidationError, match="must be unique"):
        ComparisonProductMaterial(
            product_id="p1",
            title="商品一",
            evidence=[evidence, evidence],
        )


def test_comparison_assessment_enforces_winner_contract() -> None:
    assessment = ComparisonAssessment(
        dimension="保湿",
        products=[_finding("p1"), _finding("p2")],
        outcome="winner",
        winner_product_id="p1",
        reason="p1 的保湿证据更明确。",
        response_text="p1 的保湿证据更明确。",
    )

    assert assessment.winner_product_id == "p1"

    with pytest.raises(ValidationError, match="only winner"):
        ComparisonAssessment(
            dimension="保湿",
            products=[_finding("p1"), _finding("p2")],
            outcome="tie",
            winner_product_id="p1",
            reason="资料没有明确高下。",
            response_text="两款资料没有明确高下。",
        )

    with pytest.raises(ValidationError, match="winner must"):
        ComparisonAssessment(
            dimension="保湿",
            products=[_finding("p1"), _finding("p2")],
            outcome="winner",
            winner_product_id="p3",
            reason="p3 胜出。",
            response_text="p3 胜出。",
        )

    finding_without_evidence = _finding("p2").model_copy(
        update={"evidence_ids": []}
    )
    with pytest.raises(ValidationError, match="require evidence for every product"):
        ComparisonAssessment(
            dimension="保湿",
            products=[_finding("p1"), finding_without_evidence],
            outcome="winner",
            winner_product_id="p1",
            reason="缺少完成横向比较的证据。",
            response_text="缺少完成横向比较的证据。",
        )
