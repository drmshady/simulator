"""Pytest helpers shared by the engine and studio test suites; their conftest.py files import them.

Needs the workspace's dev dependencies (pytest), so nothing outside the tests imports this module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
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


class SnapshotLibrary:
    """The approved library snapshot (`content/library/snapshots/library-snapshot.yaml`) as a value resolver, for
    tests that need the approved typical values without a database. Entry ids are made from the parameter keys."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            root = get_settings().repo_root
            assert root is not None, "the tests run inside the repository"
            path = root / "content" / "library" / "snapshots" / "library-snapshot.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.entries: dict[str, dict[str, Any]] = {e["parameter_key"]: e for e in data["entries"]}

    @staticmethod
    def entry_id(parameter_key: str) -> str:
        return "SNAPSHOT-" + parameter_key.replace(".", "-")

    def parameter_key_of(self, entry_id: str) -> str:
        for key in self.entries:
            if self.entry_id(key) == entry_id:
                return key
        raise KeyError(entry_id)

    def resolve(self, parameter_key: str, choice: str = "typical", *, custom_value: float | None = None) -> Any:
        from dentalsim.library.service import ResolvedValue

        entry = self.entries[parameter_key]
        if choice == "typical":
            value = float(entry["typical"])
        elif choice == "custom":
            assert custom_value is not None
            value = float(custom_value)
        else:
            source = choice.removeprefix("source:")
            value = float(next(v["value"] for v in entry["values_by_source"] if v["source"] == source))
        low, high = float(entry["range"]["min"]), float(entry["range"]["max"])
        return ResolvedValue(
            entry_id=self.entry_id(parameter_key),
            parameter_key=parameter_key,
            category=entry["category"],
            version=int(entry["version"]),
            content_hash=entry["content_hash"],
            units=entry["units"],
            choice=choice,
            value=value,
            outside_range=not low <= value <= high,
            typical=float(entry["typical"]),
            range_min=low,
            range_max=high,
        )


@pytest.fixture
def snapshot_library() -> SnapshotLibrary:
    return SnapshotLibrary()


def seed_library(repos: Any, path: Path | None = None) -> dict[str, str]:
    """Load the approved snapshot into a studio database as approved versions (sources marked as checked), for
    tests that need a real library. Returns the entry id of each parameter key."""
    from dentalsim.clock import new_ulid, utc_now
    from dentalsim.library.models import LiteratureEntry, LiteratureEntryVersion, Source, SourceCheck, ValueBySource

    snapshot = SnapshotLibrary(path)
    root = get_settings().repo_root
    assert root is not None
    data = yaml.safe_load(
        (path or root / "content" / "library" / "snapshots" / "library-snapshot.yaml").read_text(encoding="utf-8")
    )
    now = utc_now()
    for item in data["sources"]:
        repos.library.add_source(
            Source(
                id=item["id"],
                authors=item["authors"],
                year=item["year"],
                title=item["title"],
                journal=item.get("journal"),
                doi=item.get("doi"),
                pmid=item.get("pmid"),
                study_type=item["study_type"],
                check=SourceCheck(status="resolved_match", checked_at=now, resolver="crossref"),
            )
        )
    ids = {}
    for key, entry in snapshot.entries.items():
        entry_id = new_ulid()
        repos.library.add_entry(
            LiteratureEntry(
                id=entry_id, parameter_key=key, category=entry["category"], label=entry["label"], created_at=now
            )  # fmt: skip
        )
        repos.library.add_version(
            LiteratureEntryVersion(
                entry_id=entry_id,
                version=int(entry["version"]),
                units=entry["units"],
                typical=float(entry["typical"]),
                range_min=float(entry["range"]["min"]),
                range_max=float(entry["range"]["max"]),
                values_by_source=[
                    ValueBySource(source_id=v["source"], value=float(v["value"]), note=v.get("note"))
                    for v in entry["values_by_source"]
                ],
                notes=entry.get("notes"),
                status="approved",
                origin="claude_proposal",
                proposed_at=now,
                approved_at=now,
                content_hash=entry["content_hash"],
            )
        )
        repos.library.set_current_version(entry_id, int(entry["version"]))
        ids[key] = entry_id
    return ids


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
