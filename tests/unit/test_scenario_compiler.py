from pathlib import Path

from shop_agent.catalog import ProductCatalog
from shop_agent.models.conversation import ConversationState, QuerySnapshot
from shop_agent.models.turn_query import TurnQuery
from shop_agent.services.scenario_compiler import compile_scenario_turn
from shop_agent.services.scenario_recipes import ScenarioRecipeRegistry


PROJECT_ROOT = Path(__file__).parents[2]


def _registry() -> ScenarioRecipeRegistry:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    return ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )


def _scenario_turn(
    recipe_id: str | None = "beach_vacation",
    *,
    unmapped: list[str] | None = None,
) -> TurnQuery:
    return TurnQuery(
        schema_version=1,
        intent="scenario_recommendation",
        scenario_request={
            "surface_text": "下周去三亚度假，从防晒到穿搭",
            "recipe_id": recipe_id,
            "unmapped_requirements": unmapped or [],
        },
    )


def test_initial_scenario_compilation_clears_ordinary_task_state() -> None:
    state = ConversationState(
        schema_version=2,
        conversation_id="c1",
        active_task="product_search",
        query_snapshot=QuerySnapshot(
            category="数码电子",
            sub_category="智能手机",
        ),
        seen_product_ids=[],
    )

    result = compile_scenario_turn(_scenario_turn(), state, _registry())

    assert result.operation == "new_bundle"
    assert result.recipe is not None
    assert result.recipe.recipe_id == "beach_vacation"
    assert result.snapshot is not None
    assert result.snapshot.generation_index == 1
    assert result.state.active_task == "scenario_recommendation"
    assert result.state.query_snapshot is None
    assert result.state.scenario_snapshot == result.snapshot
    assert result.state.recent_candidates == []


def test_missing_recipe_creates_recoverable_registry_bounded_pending() -> None:
    state = ConversationState(schema_version=2, conversation_id="c1")

    result = compile_scenario_turn(_scenario_turn(None), state, _registry())

    assert result.needs_clarification is True
    assert result.operation == "clarification"
    assert result.state.pending_clarification is not None
    assert result.state.pending_clarification.kind == "scenario_recipe"
    assert result.state.pending_clarification.candidate_recipe_ids == _registry().recipe_ids()
    assert "海边度假" in (result.clarification_message or "")


def test_unmapped_requirement_short_circuits_without_creating_scenario() -> None:
    state = ConversationState(schema_version=2, conversation_id="c1")

    result = compile_scenario_turn(
        _scenario_turn(unmapped=["太阳镜"]),
        state,
        _registry(),
    )

    assert result.operation == "unsupported"
    assert result.snapshot is None
    assert result.state == state
    assert "太阳镜" in (result.clarification_message or "")


def test_more_results_reuses_active_recipe_and_rejects_version_drift() -> None:
    initial = compile_scenario_turn(
        _scenario_turn(),
        ConversationState(schema_version=2, conversation_id="c1"),
        _registry(),
    )
    more = TurnQuery(schema_version=1, intent="more_results")

    result = compile_scenario_turn(more, initial.state, _registry())

    assert result.operation == "replace_bundle"
    assert result.snapshot == initial.snapshot

    assert initial.snapshot is not None
    drifted = initial.state.model_copy(
        update={
            "scenario_snapshot": initial.snapshot.model_copy(
                update={"recipe_version": 999}
            )
        }
    )
    drifted_result = compile_scenario_turn(more, drifted, _registry())
    assert drifted_result.operation == "version_mismatch"
    assert "方案已更新" in (drifted_result.clarification_message or "")


def test_scenario_more_results_rejects_bundle_mutations() -> None:
    initial = compile_scenario_turn(
        _scenario_turn(),
        ConversationState(schema_version=2, conversation_id="c1"),
        _registry(),
    )
    mutation = TurnQuery.model_validate(
        {
            "schema_version": 1,
            "intent": "more_results",
            "slot_operations": [
                {
                    "slot": "constraints.max_price",
                    "operation": "replace",
                    "value": 500,
                }
            ],
        }
    )

    result = compile_scenario_turn(mutation, initial.state, _registry())

    assert result.operation == "unsupported"
    assert result.state == initial.state
    assert "只支持整套换新" in (result.clarification_message or "")
