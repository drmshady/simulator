"""Engine test fixtures. A test's category marker comes from its folder: unit/, integration/, benchmark/,
regression/."""

from pathlib import Path

import pytest

from dentalsim.testing import data_dir, fixed_time, fixed_ulids, mark_categories  # noqa: F401

HERE = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mark_categories(items, HERE, default="unit")
