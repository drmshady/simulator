"""Round trips through the studio database: migrations, settings, events, capability status (T031)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from dentalsim.persistence import CapabilityStatus, open_repos
from dentalsim.testing import FixedClock
from dentalsim_studio.db import SqliteRepos
from dentalsim_studio.db import open_repos as studio_open_repos
from dentalsim_studio.db.connection import connect, db_path
from dentalsim_studio.db.migrations import SCHEMA_VERSION, split_statements, user_version


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = studio_open_repos(data_dir)
    yield opened
    opened.close()


def test_new_database_is_migrated_with_wal_and_foreign_keys(data_dir: Path) -> None:
    db = connect(db_path(data_dir))
    try:
        assert user_version(db.conn) == SCHEMA_VERSION
        assert db.one("PRAGMA journal_mode")[0] == "wal"  # type: ignore[index]
        assert db.one("PRAGMA foreign_keys")[0] == 1  # type: ignore[index]
        tables = {row["name"] for row in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for name in ("projects", "scenarios", "runs", "jobs", "events", "library_entries", "library_versions",
                     "sources", "version_sources", "capability_status", "verification_reports", "settings",
                     "published_versions", "promo_clips", "search_index"):  # fmt: skip
            assert name in tables
    finally:
        db.close()


def test_opening_twice_does_not_migrate_again(data_dir: Path) -> None:
    connect(db_path(data_dir)).close()
    db = connect(db_path(data_dir))
    assert user_version(db.conn) == SCHEMA_VERSION
    db.close()


def test_engine_open_repos_uses_the_studio_database(data_dir: Path) -> None:
    repos = open_repos(data_dir)
    try:
        repos.settings.set("greeting", {"text": "hello"})
    finally:
        repos.close()
    assert (data_dir / "studio.db").is_file()
    again = open_repos(data_dir)
    try:
        assert again.settings.get("greeting") == {"text": "hello"}
    finally:
        again.close()


def test_settings_round_trip(repos: SqliteRepos) -> None:
    assert repos.settings.get("missing") is None
    repos.settings.set("check.last_pass", {"commit": "abc123", "complete": True})
    repos.settings.set("check.last_pass", {"commit": "def456", "complete": True})
    assert repos.settings.get("check.last_pass") == {"commit": "def456", "complete": True}
    repos.settings.delete("check.last_pass")
    assert repos.settings.get("check.last_pass") is None


def test_events_are_read_in_order_after_an_id(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    assert repos.events.latest_id() == 0
    first = repos.events.emit("run.progress", {"run_id": "r1", "percent": 10})
    second = repos.events.emit("run.finished", {"run_id": "r1"})
    assert second > first
    assert [e.type for e in repos.events.since(0)] == ["run.progress", "run.finished"]
    assert [e.payload for e in repos.events.since(first)] == [{"run_id": "r1"}]
    assert repos.events.since(0)[0].created_at == fixed_time.now
    assert repos.events.latest_id() == second


def test_capability_status_defaults_and_updates(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    assert repos.capabilities.get("bonded_elastic") is None
    repos.capabilities.set(CapabilityStatus(capability_id="bonded_elastic"))
    stored = repos.capabilities.get("bonded_elastic")
    assert stored is not None
    assert stored.status == "awaiting_benchmark"
    assert stored.updated_at == fixed_time.now
    assert [c.capability_id for c in repos.capabilities.list_all()] == ["bonded_elastic"]


def test_ready_needs_a_report(repos: SqliteRepos) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repos.capabilities.set(CapabilityStatus(capability_id="bonded_elastic", status="ready", report_id=None))


def test_transaction_rolls_back_on_error(repos: SqliteRepos) -> None:
    with pytest.raises(RuntimeError), repos.transaction():
        repos.settings.set("a", 1)
        with repos.transaction():  # nested use joins the outer transaction
            repos.settings.set("b", 2)
        raise RuntimeError("stop")
    assert repos.settings.get("a") is None
    assert repos.settings.get("b") is None


def test_split_statements_keeps_multi_line_statements_whole() -> None:
    script = """
-- a comment
CREATE TABLE t (a INTEGER);
CREATE TRIGGER t_ins AFTER INSERT ON t BEGIN
    UPDATE t SET a = a + 1;
END;
"""
    statements = list(split_statements(script))
    assert len(statements) == 2
    assert statements[1].startswith("CREATE TRIGGER") and statements[1].endswith("END;")
