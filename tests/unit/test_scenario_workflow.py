import json
import logging
from pathlib import Path

import pytest

from shop_agent.catalog import ProductCatalog
from shop_agent.config import Settings
from shop_agent.models.conversation import ConversationRecord
from shop_agent.models.turn_query import TurnQuery
from shop_agent.services.scenario_recommendation import ScenarioRecommendationService
from shop_agent.services.scenario_recipes import ScenarioRecipeRegistry
from shop_agent.workflow.dependencies import WorkflowDependencies
from shop_agent.workflow.graph import build_graph
from tests.unit.workflow_fakes import (
    FakeConversationRepository,
    FakeEvidenceService,
    FakeResponseGenerator,
    FakeRetrievalService,
    FakeTurnQueryParser,
    initial_state,
)


PROJECT_ROOT = Path(__file__).parents[2]


def _scenario_turn(
    recipe_id: str = "beach_vacation",
    surface_text: str = "下周去三亚度假，从防晒到穿搭",
) -> TurnQuery:
    return TurnQuery(
        schema_version=1,
        intent="scenario_recommendation",
        scenario_request={
            "surface_text": surface_text,
            "recipe_id": recipe_id,
        },
    )


def _dependencies(
    *,
    turns: list[TurnQuery],
    record: ConversationRecord | None = None,
) -> tuple[WorkflowDependencies, FakeConversationRepository]:
    catalog = ProductCatalog.load(PROJECT_ROOT / "ecommerce_agent_dataset")
    registry = ScenarioRecipeRegistry.load(
        PROJECT_ROOT / "config" / "scenario_recipes.json",
        catalog,
    )
    retrieval = FakeRetrievalService(products=catalog.all(), return_hits=True)
    evidence = FakeEvidenceService(catalog=catalog, eligible=True)
    repository = FakeConversationRepository(record)
    settings = Settings(
        dashscope_api_key="test-key",
        dataset_root=PROJECT_ROOT / "ecommerce_agent_dataset",
        scenario_recipe_path=PROJECT_ROOT / "config" / "scenario_recipes.json",
        public_base_url="http://testserver",
    )
    return (
        WorkflowDependencies(
            turn_query_parser=FakeTurnQueryParser(turns),
            conversation_repository=repository,
            retrieval_service=retrieval,
            evidence_service=evidence,
            response_generator=FakeResponseGenerator(),
            catalog=catalog,
            settings=settings,
            scenario_registry=registry,
            scenario_recommendation_service=ScenarioRecommendationService(
                retrieval=retrieval,
                evidence=evidence,
                product_limit=settings.scenario_product_limit,
            ),
        ),
        repository,
    )


async def _events(dependencies: WorkflowDependencies, message: str):
    return [
        part["data"]
        async for part in build_graph(dependencies).astream(
            initial_state(message),
            stream_mode="custom",
            version="v2",
        )
    ]


def _log_payloads(
    caplog: pytest.LogCaptureFixture,
    event_name: str,
) -> list[dict[str, object]]:
    prefix = f"{event_name} "
    return [
        json.loads(record.getMessage()[len(prefix) :])
        for record in caplog.records
        if record.name == "uvicorn.error" and record.getMessage().startswith(prefix)
    ]


@pytest.mark.asyncio
async def test_scenario_branch_persists_complete_bundle_before_emitting_cards() -> None:
    dependencies, repository = _dependencies(turns=[_scenario_turn()])

    events = await _events(dependencies, "下周去三亚度假，从防晒到穿搭")

    assert [event["event"] for event in events] == [
        "product",
        "product",
        "product",
        "product",
        "product",
        "text_delta",
        "text_delta",
    ]
    assert repository.record is not None
    state = repository.record.state
    assert state.active_task == "scenario_recommendation"
    assert state.query_snapshot is None
    assert state.scenario_snapshot is not None
    assert state.scenario_snapshot.generation_index == 1
    assert len(state.scenario_snapshot.current_bundle) == 5
    assert [event["data"]["rank"] for event in events[:5]] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_scenario_logs_report_actual_route_and_safe_stage_outcomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_request = "SECRET SCENARIO REQUEST"
    dependencies, repository = _dependencies(
        turns=[_scenario_turn(surface_text=secret_request)]
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await _events(dependencies, secret_request)

    route_payloads = _log_payloads(caplog, "turn_route")
    compile_payloads = _log_payloads(caplog, "scenario_snapshot_compiled")
    bundle_payloads = _log_payloads(caplog, "scenario_bundle_built")
    assert [payload["route"] for payload in route_payloads] == ["scenario"]
    assert [payload["operation"] for payload in compile_payloads] == ["new_bundle"]
    assert [payload["outcome"] for payload in compile_payloads] == ["build"]
    assert [payload["status"] for payload in bundle_payloads] == ["complete"]
    assert [payload["selected_slot_count"] for payload in bundle_payloads] == [5]
    assert secret_request not in "\n".join(
        record.getMessage() for record in caplog.records
    )
    assert repository.record is not None

    caplog.clear()
    next_dependencies, _ = _dependencies(
        turns=[TurnQuery(schema_version=1, intent="more_results")],
        record=repository.record,
    )
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        await _events(next_dependencies, "replace bundle")

    route_payloads = _log_payloads(caplog, "turn_route")
    compile_payloads = _log_payloads(caplog, "scenario_snapshot_compiled")
    bundle_payloads = _log_payloads(caplog, "scenario_bundle_built")
    assert [payload["route"] for payload in route_payloads] == ["scenario"]
    assert [payload["operation"] for payload in compile_payloads] == [
        "replace_bundle"
    ]
    assert [payload["outcome"] for payload in compile_payloads] == ["build"]
    assert [payload["status"] for payload in bundle_payloads] == ["complete"]


@pytest.mark.asyncio
async def test_more_results_replaces_the_whole_scenario_bundle_without_overlap() -> None:
    first_dependencies, repository = _dependencies(
        turns=[_scenario_turn("back_to_school", "开学帮我准备一套")]
    )
    first_events = await _events(
        first_dependencies,
        "开学帮我准备一套",
    )
    first_products = [event for event in first_events if event["event"] == "product"]
    first_ids = {event["data"]["product_id"] for event in first_products}
    assert repository.record is not None

    next_dependencies, next_repository = _dependencies(
        turns=[TurnQuery(schema_version=1, intent="more_results")],
        record=repository.record,
    )
    second_events = await _events(next_dependencies, "换一套")
    second_products = [event for event in second_events if event["event"] == "product"]
    second_ids = {event["data"]["product_id"] for event in second_products}

    assert len(first_products) == 6
    assert len(second_products) == 6
    assert first_ids.isdisjoint(second_ids)
    assert next_repository.record is not None
    snapshot = next_repository.record.state.scenario_snapshot
    assert snapshot is not None
    assert snapshot.generation_index == 2
    assert set(snapshot.seen_product_ids) == first_ids | second_ids


@pytest.mark.asyncio
async def test_required_slot_exhaustion_emits_no_cards_and_preserves_bundle() -> None:
    first_dependencies, repository = _dependencies(turns=[_scenario_turn()])
    await _events(first_dependencies, "下周去三亚度假，从防晒到穿搭")
    assert repository.record is not None
    old_record = repository.record
    old_snapshot = old_record.state.scenario_snapshot
    assert old_snapshot is not None

    catalog = first_dependencies.catalog
    all_sun_ids = [
        product.product_id
        for product in catalog.all()
        if product.category == "美妆护肤" and product.sub_category == "防晒"
    ]
    exhausted_snapshot = old_snapshot.model_copy(
        update={
            "seen_product_ids": tuple(
                dict.fromkeys([*old_snapshot.seen_product_ids, *all_sun_ids])
            )
        }
    )
    exhausted_state = old_record.state.model_copy(
        update={
            "scenario_snapshot": exhausted_snapshot,
            "seen_product_ids": list(exhausted_snapshot.seen_product_ids),
        }
    )
    exhausted_record = ConversationRecord(
        state=exhausted_state,
        version=old_record.version,
    )
    next_dependencies, next_repository = _dependencies(
        turns=[TurnQuery(schema_version=1, intent="more_results")],
        record=exhausted_record,
    )

    events = await _events(next_dependencies, "还有更多推荐吗")

    assert [event["event"] for event in events] == ["text_delta"]
    assert "没有更多完整组合" in events[0]["data"]["delta"]
    assert next_repository.saves == []
    assert next_repository.record is not None
    assert next_repository.record.state.scenario_snapshot == exhausted_snapshot
