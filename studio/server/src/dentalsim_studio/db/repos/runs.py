"""The `runs`, `run_library_versions` and `previews` tables (task T084; data-model.md "Run", "Preview").

A run's library versions are also stored row by row, so rejecting a library version can flag every run that used it
(FR-055).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from dentalsim.clock import new_ulid, utc_now
from dentalsim.errors import NotFound
from dentalsim.model.run import Actual, Convergence, Estimate, Run, RunFlag, RunStatus
from dentalsim.model.scenario import Scenario
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time

_COLUMNS = (
    "id", "scenario_id", "scenario_revision", "snapshot", "detail_level", "input_hash", "library_versions",
    "engine_version", "engine_source_hash", "conventions_version", "solver_build_id", "gmsh_version", "solver_host",
    "estimate", "actual", "status", "failure_reason", "convergence", "result_ref", "locked_results", "flags",
    "study_id", "created_at", "started_at", "finished_at",
)  # fmt: skip


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return to_json(value.model_dump(mode="json"))
    return to_json(value)


def _row(run: Run) -> tuple[Any, ...]:
    return (
        run.id,
        run.scenario_id,
        run.scenario_revision,
        to_json(run.snapshot.model_dump(mode="json")),
        run.detail_level,
        run.input_hash,
        to_json([ref.model_dump(mode="json") for ref in run.library_versions]),
        run.engine_version,
        run.engine_source_hash,
        run.conventions_version,
        run.solver_build_id,
        run.gmsh_version,
        run.solver_host,
        _json(run.estimate),
        _json(run.actual),
        run.status,
        run.failure_reason,
        _json(run.convergence),
        run.result_ref,
        to_json([r.model_dump(mode="json") for r in run.locked_results]),
        to_json(sorted(run.flags)),
        run.study_id,
        to_time(run.created_at),
        to_time(run.started_at),
        to_time(run.finished_at),
    )


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        scenario_id=row["scenario_id"],
        scenario_revision=row["scenario_revision"],
        snapshot=Scenario.model_validate(from_json(row["snapshot"])),
        detail_level=row["detail_level"],
        input_hash=row["input_hash"],
        library_versions=from_json(row["library_versions"]),
        engine_version=row["engine_version"],
        engine_source_hash=row["engine_source_hash"],
        conventions_version=row["conventions_version"],
        solver_build_id=row["solver_build_id"],
        gmsh_version=row["gmsh_version"],
        solver_host=row["solver_host"],
        estimate=None if row["estimate"] is None else Estimate.model_validate(from_json(row["estimate"])),
        actual=None if row["actual"] is None else Actual.model_validate(from_json(row["actual"])),
        status=row["status"],
        failure_reason=row["failure_reason"],
        convergence=None if row["convergence"] is None else Convergence.model_validate(from_json(row["convergence"])),
        result_ref=row["result_ref"],
        locked_results=from_json(row["locked_results"]),
        flags=set(from_json(row["flags"])),
        study_id=row["study_id"],
        created_at=from_time(row["created_at"]),
        started_at=from_time(row["started_at"]),
        finished_at=from_time(row["finished_at"]),
    )


class SqliteRunRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, run: Run) -> None:
        marks = ", ".join("?" for _ in _COLUMNS)
        with self.db.transaction():
            self.db.execute(f"INSERT INTO runs ({', '.join(_COLUMNS)}) VALUES ({marks})", _row(run))
            self.db.executemany(
                "INSERT OR IGNORE INTO run_library_versions (run_id, entry_id, version) VALUES (?, ?, ?)",
                [(run.id, ref.entry_id, ref.version) for ref in run.library_versions],
            )

    def get(self, run_id: str) -> Run | None:
        row = self.db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return None if row is None else _run(row)

    def require(self, run_id: str) -> Run:
        run = self.get(run_id)
        if run is None:
            raise NotFound(f"there is no run {run_id}")
        return run

    def update(self, run: Run) -> None:
        assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
        values = _row(run)
        cursor = self.db.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*values[1:], values[0]))
        if cursor.rowcount == 0:
            raise NotFound(f"there is no run {run.id}")

    def set_status(
        self, run_id: str, status: RunStatus, *, failure_reason: str | None = None, started: bool = False
    ) -> Run:
        run = self.require(run_id)
        update: dict[str, Any] = {"status": status, "failure_reason": failure_reason}
        if started:
            update["started_at"] = utc_now()
        if status in ("succeeded", "failed", "cancelled"):
            update["finished_at"] = utc_now()
        changed = run.model_copy(update=update)
        self.update(Run.model_validate(changed.model_dump()))
        return changed

    def list_for_scenario(self, scenario_id: str) -> list[Run]:
        rows = self.db.all("SELECT * FROM runs WHERE scenario_id = ? ORDER BY created_at DESC, id DESC", (scenario_id,))
        return [_run(row) for row in rows]

    def list_recent(self, *, limit: int = 50) -> list[Run]:
        rows = self.db.all("SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [_run(row) for row in rows]

    def runs_using(self, entry_id: str, version: int) -> list[str]:
        rows = self.db.all(
            "SELECT run_id FROM run_library_versions WHERE entry_id = ? AND version = ? ORDER BY run_id",
            (entry_id, version),
        )
        return [row["run_id"] for row in rows]

    def add_flag(self, run_ids: Sequence[str], flag: RunFlag) -> None:
        with self.db.transaction():
            for run_id in run_ids:
                run = self.get(run_id)
                if run is not None and flag not in run.flags:
                    self.db.execute(
                        "UPDATE runs SET flags = ? WHERE id = ?", (to_json(sorted({*run.flags, flag})), run_id)
                    )

    def find_succeeded(self, input_hash: str) -> Run | None:
        row = self.db.one(
            "SELECT * FROM runs WHERE input_hash = ? AND status = 'succeeded' ORDER BY finished_at DESC LIMIT 1",
            (input_hash,),
        )
        return None if row is None else _run(row)


class PreviewRecord(BaseModel):
    """A preview (data-model.md "Preview"): never publishable (FR-017)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    input_hash: str
    scenario_id: str | None
    scenario_revision: int | None
    status: RunStatus = "queued"
    failure_reason: str | None = None
    result_ref: str | None = None
    accuracy_note: str | None = None
    created_at: AwareDatetime


def _preview(row: sqlite3.Row) -> PreviewRecord:
    return PreviewRecord(
        id=row["id"],
        input_hash=row["input_hash"],
        scenario_id=row["scenario_id"],
        scenario_revision=row["scenario_revision"],
        status=row["status"],
        failure_reason=row["failure_reason"],
        result_ref=row["result_ref"],
        accuracy_note=row["accuracy_note"],
        created_at=from_time(row["created_at"]),
    )


class SqlitePreviewRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self, *, input_hash: str, scenario_id: str | None, scenario_revision: int | None, accuracy_note: str | None
    ) -> PreviewRecord:
        record = PreviewRecord(
            id=new_ulid(),
            input_hash=input_hash,
            scenario_id=scenario_id,
            scenario_revision=scenario_revision,
            accuracy_note=accuracy_note,
            created_at=utc_now(),
        )
        self.db.execute(
            """INSERT INTO previews (id, input_hash, scenario_id, scenario_revision, status, accuracy_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.input_hash,
                record.scenario_id,
                record.scenario_revision,
                record.status,
                record.accuracy_note,
                to_time(record.created_at),
            ),
        )
        return record

    def get(self, preview_id: str) -> PreviewRecord | None:
        row = self.db.one("SELECT * FROM previews WHERE id = ?", (preview_id,))
        return None if row is None else _preview(row)

    def finish(
        self, preview_id: str, status: RunStatus, *, result_ref: str | None = None, failure_reason: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE previews SET status = ?, result_ref = ?, failure_reason = ? WHERE id = ?",
            (status, result_ref, failure_reason, preview_id),
        )

    def set_status(self, preview_id: str, status: RunStatus) -> None:
        self.db.execute("UPDATE previews SET status = ? WHERE id = ?", (status, preview_id))

    def latest_for(self, scenario_id: str) -> PreviewRecord | None:
        row = self.db.one(
            "SELECT * FROM previews WHERE scenario_id = ? ORDER BY created_at DESC, id DESC LIMIT 1", (scenario_id,)
        )
        return None if row is None else _preview(row)
