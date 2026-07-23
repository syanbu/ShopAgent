import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from shop_agent.config import Settings


def test_settings_resolve_dataset_from_project_root(tmp_path: Path) -> None:
    settings = Settings(
        dashscope_api_key="test-key",
        dataset_root=tmp_path / "dataset",
        qdrant_url="http://127.0.0.1:6333",
    )
    assert settings.embedding_dimension == 1024
    assert settings.qdrant_collection == "product_text_chunks_v1"


def test_project_does_not_declare_an_unimplemented_console_script() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    assert "scripts" not in project


@pytest.mark.parametrize("limit", [0, 4])
def test_final_product_limit_stays_within_card_contract(limit: int) -> None:
    with pytest.raises(ValidationError):
        Settings(dashscope_api_key="test-key", final_product_limit=limit)
