"""Pytest helpers shared by the engine and studio test suites; their conftest.py files import them.

Needs the workspace's dev dependencies (pytest), so nothing outside the tests imports this module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ulid import ULID

from dentalsim import clock
from dentalsim.config import ensure_data_folder, get_settings

CATEGORY_MARKERS = ("unit", "integration", "benchmark", "regression")
FIXED_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def mark_categories(items: Sequence[pytest.Item], root: Path, default: str) -> None:
    """Mark the tests under `root`, and skip the solver tests when there is no solver.

    A test with no category marker gets the one named by its folder (`unit/`, `integration/`, ...), or `default`.
    `benchmark` tests are skipped when `DENTALSIM_CCX` is unset.
    """
    skip_benchmark = None
    if not os.environ.get("DENTALSIM_CCX"):
        skip_benchmark = pytest.mark.skip(reason="needs the solver: set DENTALSIM_CCX")
    for item in items:
        if not item.path.is_relative_to(root):
            continue
        if not any(item.get_closest_marker(name) for name in CATEGORY_MARKERS):
            folders = item.path.relative_to(root).parts[:-1]
            item.add_marker(next((f for f in folders if f in CATEGORY_MARKERS), default))
        if skip_benchmark and item.get_closest_marker("benchmark"):
            item.add_marker(skip_benchmark)


class FixedClock:
    """Stands in for the system clock; `advance()` moves it on."""

    def __init__(self, start: datetime = FIXED_TIME) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


class FixedUlids:
    """Deterministic ULIDs in creation order: the fixed time as timestamp, and a counter as the random part."""

    def __init__(self, start: datetime = FIXED_TIME) -> None:
        self.timestamp_ms = int(start.timestamp() * 1000)
        self.count = 0

    def __call__(self) -> ULID:
        self.count += 1
        return ULID.from_bytes(self.timestamp_ms.to_bytes(6, "big") + self.count.to_bytes(10, "big"))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh data folder with its subfolders; `DENTALSIM_DATA` points at it for the test."""
    path = tmp_path / "DentalSimData"
    monkeypatch.setenv("DENTALSIM_DATA", str(path))
    get_settings.cache_clear()
    ensure_data_folder()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> FixedClock:
    """`dentalsim.clock.utc_now()` returns `FIXED_TIME` until the test advances it."""
    fixed = FixedClock()
    monkeypatch.setattr(clock, "_now", fixed)
    return fixed


@pytest.fixture
def fixed_ulids(monkeypatch: pytest.MonkeyPatch) -> FixedUlids:
    """`dentalsim.clock.new_ulid()` returns deterministic ULIDs."""
    ids = FixedUlids()
    monkeypatch.setattr(clock, "_new_ulid", ids)
    return ids
