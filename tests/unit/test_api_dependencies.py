from pathlib import Path
from typing import Any

from shop_agent.api import dependencies as api_dependencies
from shop_agent.config import Settings
from shop_agent.services import conversation_repository as repository_module
from shop_agent.services.conversation_repository import SqliteConversationRepository
from shop_agent.services.dashscope_chat import (
    DashScopeComparisonAssessor,
    DashScopeTurnQueryParser,
)
from shop_agent.workflow.dependencies import WorkflowDependencies
from tests.unit.workflow_fakes import build_harness


def test_build_api_dependencies_injects_lazy_repository_and_catalog_turn_parser(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    harness = build_harness(tmp_path / "catalog", product_count=3)
    settings = Settings(
        dashscope_api_key="test-key",
        dataset_root=tmp_path / "catalog",
        conversation_db_path=tmp_path / "state" / "conversations.sqlite3",
    )
    captured: list[WorkflowDependencies] = []
    graph = object()
    store = object()
    connect_calls: list[object] = []

    def fail_if_database_opens(*args: object, **kwargs: object) -> None:
        connect_calls.append((args, kwargs))
        raise AssertionError("SQLite must remain lazy during dependency construction")

    def capture_graph(dependencies: WorkflowDependencies) -> object:
        captured.append(dependencies)
        return graph

    monkeypatch.setattr(
        api_dependencies.ProductCatalog,
        "load",
        lambda _: harness.catalog,
    )
    monkeypatch.setattr(api_dependencies, "QdrantStore", lambda _: store)
    monkeypatch.setattr(api_dependencies, "build_graph", capture_graph)
    monkeypatch.setattr(repository_module.aiosqlite, "connect", fail_if_database_opens)

    built = api_dependencies.build_api_dependencies(settings)

    assert built.graph is graph
    assert built.readiness_probe is store
    assert len(captured) == 1
    workflow = captured[0]
    assert isinstance(workflow.conversation_repository, SqliteConversationRepository)
    assert workflow.conversation_repository._database_path == settings.conversation_db_path
    assert isinstance(workflow.turn_query_parser, DashScopeTurnQueryParser)
    assert isinstance(workflow.comparison_assessor, DashScopeComparisonAssessor)
    assert workflow.turn_query_parser._model == settings.chat_model
    assert workflow.comparison_assessor._model == settings.comparison_model
    assert workflow.turn_query_parser._categories == ("数码电子",)
    assert workflow.turn_query_parser._sub_categories == ("蓝牙耳机",)
    assert workflow.turn_query_parser._category_pairs == (("数码电子", "蓝牙耳机"),)
    assert workflow.turn_query_parser._brands == ("品牌 1", "品牌 2", "品牌 3")
    assert workflow.turn_query_parser._sku_taxonomy == {
        pair: {key: tuple(values) for key, values in attributes.items()}
        for pair, attributes in harness.catalog.sku_taxonomy().items()
    }
    assert connect_calls == []
