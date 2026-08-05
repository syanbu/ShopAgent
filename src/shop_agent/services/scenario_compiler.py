"""Compile constrained scenario turns into persisted scenario task state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from shop_agent.models.conversation import ConversationState, PendingClarification
from shop_agent.models.scenario import ScenarioSnapshot, SolutionRecipe
from shop_agent.models.turn_query import TurnQuery
from shop_agent.services.scenario_recipes import ScenarioRecipeRegistry


ScenarioOperation = Literal[
    "new_bundle",
    "replace_bundle",
    "clarification",
    "unsupported",
    "version_mismatch",
]


class ScenarioCompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ScenarioOperation
    state: ConversationState
    recipe: SolutionRecipe | None = None
    snapshot: ScenarioSnapshot | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None


def compile_scenario_turn(
    turn: TurnQuery,
    state: ConversationState,
    registry: ScenarioRecipeRegistry,
) -> ScenarioCompileResult:
    """Compile a new scenario request or a context-sensitive bundle replacement."""

    if turn.intent == "more_results":
        if _has_bundle_mutation(turn):
            return ScenarioCompileResult(
                operation="unsupported",
                state=state.model_copy(deep=True),
                snapshot=state.scenario_snapshot,
                clarification_message=(
                    "第一版只支持整套换新，暂不支持带价格、偏好或局部槽位"
                    "修改的换套请求。"
                ),
            )
        return _compile_replacement(state, registry)
    if turn.intent != "scenario_recommendation" or turn.scenario_request is None:
        raise ValueError("compile_scenario_turn requires a scenario or more-results turn")

    request = turn.scenario_request
    if request.unmapped_requirements:
        requirements = "、".join(request.unmapped_requirements)
        return ScenarioCompileResult(
            operation="unsupported",
            state=state.model_copy(deep=True),
            clarification_message=(
                f"当前商品目录还不能覆盖：{requirements}。"
                "我不会用其他商品类型替代。"
            ),
        )

    if request.recipe_id is None or not registry.contains(request.recipe_id):
        pending = PendingClarification(
            kind="scenario_recipe",
            candidate_recipe_ids=registry.recipe_ids(),
            suspended_turn_query=turn.model_copy(deep=True),
        )
        choices = "、".join(recipe.display_name for recipe in registry.recipes())
        return ScenarioCompileResult(
            operation="clarification",
            state=state.model_copy(
                update={"pending_clarification": pending},
                deep=True,
            ),
            needs_clarification=True,
            clarification_message=f"你想准备哪种场景方案？目前支持：{choices}。",
        )

    recipe = registry.get(request.recipe_id)
    snapshot = ScenarioSnapshot(
        schema_version=1,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.recipe_version,
        original_request=request.surface_text,
        generation_index=1,
    )
    compiled_state = state.model_copy(
        update={
            "active_task": "scenario_recommendation",
            "query_snapshot": None,
            "scenario_snapshot": snapshot,
            "recent_candidates": [],
            "focused_product_id": None,
            "seen_product_ids": [],
            "pending_clarification": None,
        },
        deep=True,
    )
    return ScenarioCompileResult(
        operation="new_bundle",
        state=ConversationState.model_validate(compiled_state.model_dump()),
        recipe=recipe,
        snapshot=snapshot,
    )


def _compile_replacement(
    state: ConversationState,
    registry: ScenarioRecipeRegistry,
) -> ScenarioCompileResult:
    snapshot = state.scenario_snapshot
    if state.active_task != "scenario_recommendation" or snapshot is None:
        raise ValueError("bundle replacement requires an active scenario task")
    if not registry.contains(snapshot.recipe_id):
        return _version_mismatch(state)
    recipe = registry.get(snapshot.recipe_id)
    if recipe.recipe_version != snapshot.recipe_version:
        return _version_mismatch(state)
    return ScenarioCompileResult(
        operation="replace_bundle",
        state=state.model_copy(deep=True),
        recipe=recipe,
        snapshot=snapshot,
    )


def _version_mismatch(state: ConversationState) -> ScenarioCompileResult:
    return ScenarioCompileResult(
        operation="version_mismatch",
        state=state.model_copy(deep=True),
        snapshot=state.scenario_snapshot,
        clarification_message="场景方案已更新，请重新描述一次完整的场景需求。",
    )


def _has_bundle_mutation(turn: TurnQuery) -> bool:
    return bool(
        turn.reference is not None
        or turn.category_reference is not None
        or turn.semantic_term_operations
        or turn.slot_operations
        or turn.approximate_price is not None
        or turn.relative_price is not None
        or turn.product_question is not None
        or turn.product_comparison is not None
        or turn.skip_preference_question
        or turn.cancel_pending
        or turn.scenario_request is not None
    )
