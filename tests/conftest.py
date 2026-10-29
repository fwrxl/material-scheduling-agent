from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools import BusinessTools


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data_dir() -> Path:
    return ROOT / "data"


@pytest.fixture
def load_scenario():
    def load(name: str) -> dict:
        with (ROOT / "examples" / "inputs" / f"{name}.json").open(encoding="utf-8") as handle:
            return json.load(handle)

    return load


@pytest.fixture
def tools(data_dir: Path, tmp_path: Path) -> BusinessTools:
    return BusinessTools(data_dir, tmp_path / "drafts.json")
