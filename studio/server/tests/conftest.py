"""Studio server test fixtures. Studio tests are `integration` tests unless they carry another category marker."""

from pathlib import Path

import pytest

from dentalsim.testing import data_dir, fixed_time, fixed_ulids, mark_categories  # noqa: F401

HERE = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mark_categories(items, HERE, default="integration")
