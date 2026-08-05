from typing import Literal, TypedDict

from shop_agent.errors import ErrorCode
from shop_agent.models.conversation import (
    ConversationRecord,
    ConversationState,
    QuerySnapshot,
)
from shop_agent.models.comparison import (
    ComparisonAssessment,
    ComparisonProductMaterial,
)
from shop_agent.models.query import ParsedIntent, PriceCompilationReference, SearchConstraints
from shop_agent.models.retrieval import (
    EvidenceChunk,
    ProductCandidate,
    RetrievedChunk,
    SelectedProduct,
    ValidatedCandidate,
)
from shop_agent.models.scenario import ScenarioSnapshot, SolutionRecipe
from shop_agent.models.turn_query import CategoryCandidate, TurnQuery
from shop_agent.services.scenario_recommendation import ScenarioSelectedItem


NoResultReason = Literal[
    "exhausted",
    "no_matches",
    "insufficient_evidence",
]


class ShoppingState(TypedDict, total=False):
    request_id: str
    conversation_id: str
    user_message: str
    conversation_record: ConversationRecord
    conversation_state: ConversationState
    turn_query: TurnQuery
    scenario_operation: Literal["new_bundle", "replace_bundle"]
    scenario_compile_outcome: Literal["build", "persist_message", "emit_message"]
    scenario_recipe: SolutionRecipe
    scenario_snapshot: ScenarioSnapshot
    scenario_previous_state: ConversationState
    scenario_selected_items: list[ScenarioSelectedItem]
    missing_required_slot_ids: list[str]
    resolved_product_id: str
    comparison_product_ids: list[str]
    comparison_materials: list[ComparisonProductMaterial]
    comparison_assessment: ComparisonAssessment
    resolved_brand: str
    resolved_category_scope: CategoryCandidate
    allowed_category_scopes: tuple[CategoryCandidate, ...]
    query_snapshot: QuerySnapshot
    search_intent: Literal[
        "new_search",
        "refine_search",
        "switch_category",
        "more_results",
    ]
    result_strategy: Literal["stable_refine", "full_rerank", "more_results"]
    skip_proactive_clarification: bool
    pending_expected_version: int | None
    product_knowledge: list[EvidenceChunk]
    parsed_intent: ParsedIntent
    effective_constraints: SearchConstraints
    price_reference: PriceCompilationReference
    clarification_message: str
    retrieved_chunks: list[RetrievedChunk]
    candidates: list[ProductCandidate]
    validated_candidates: list[ValidatedCandidate]
    selected_products: list[SelectedProduct]
    response_mode: Literal["shopping", "non_shopping", "no_results", "clarification"]
    no_result_reason: NoResultReason
    response_text: str
    error_code: ErrorCode
