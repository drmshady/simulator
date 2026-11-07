"""The `studies` table (task T124; data-model.md "Study"; FR-020, FR-021).

A study's runs are rows of `runs` with its `study_id`; the study keeps them in queue order in `points`, each with the
axis values that made it, so the summary can group the runs by axis value.
"""

from __future__ import annotations

import sqlite3

from dentalsim.clock import utc_now
from dentalsim.errors import NotFound
from dentalsim.persistence import StudyAxis, StudyPoint, StudyRecord
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time

_COLUMNS = (
    "id", "project_id", "base_scenario_id", "name", "kind", "detail_level", "axes", "points", "status", "summary",
    "estimate", "created_at", "updated_at",
)  # fmt: skip


def _study(row: sqlite3.Row) -> StudyRecord:
    created, updated = from_time(row["created_at"]), from_time(row["updated_at"])
    assert created is not None and updated is not None
    return StudyRecord(
        id=row["id"],
        project_id=row["project_id"],
        base_scenario_id=row["base_scenario_id"],
        name=row["name"],
        kind=row["kind"],
        detail_level=row["detail_level"],
        axes=[StudyAxis.model_validate(a) for a in from_json(row["axes"])],
        points=[StudyPoint.model_validate(p) for p in from_json(row["points"])],
        status=row["status"],
        summary=from_json(row["summary"]),
        estimate=from_json(row["estimate"]),
        created_at=created,
        updated_at=updated,
    )


def _row(study: StudyRecord) -> tuple[object, ...]:
    return (
        study.id,
        study.project_id,
        study.base_scenario_id,
        study.name,
        study.kind,
        study.detail_level,
        to_json([axis.model_dump(mode="json") for axis in study.axes]),
        to_json([point.model_dump(mode="json") for point in study.points]),
        study.status,
        None if study.summary is None else to_json(study.summary),
        None if study.estimate is None else to_json(study.estimate),
        to_time(study.created_at),
        to_time(study.updated_at),
    )


class SqliteStudyRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, study: StudyRecord) -> None:
        marks = ", ".join("?" for _ in _COLUMNS)
        self.db.execute(f"INSERT INTO studies ({', '.join(_COLUMNS)}) VALUES ({marks})", _row(study))

    def get(self, study_id: str) -> StudyRecord | None:
        row = self.db.one("SELECT * FROM studies WHERE id = ?", (study_id,))
        return None if row is None else _study(row)

    def require(self, study_id: str) -> StudyRecord:
        study = self.get(study_id)
        if study is None:
            raise NotFound(f"there is no study {study_id}")
        return study

    def update(self, study: StudyRecord) -> None:
        """Stores the status, points, summary and estimate; `updated_at` becomes now."""
        cursor = self.db.execute(
            "UPDATE studies SET points = ?, status = ?, summary = ?, estimate = ?, updated_at = ? WHERE id = ?",
            (
                to_json([point.model_dump(mode="json") for point in study.points]),
                study.status,
                None if study.summary is None else to_json(study.summary),
                None if study.estimate is None else to_json(study.estimate),
                to_time(utc_now()),
                study.id,
            ),
        )
        if cursor.rowcount == 0:
            raise NotFound(f"there is no study {study.id}")

    def list_for_project(self, project_id: str) -> list[StudyRecord]:
        rows = self.db.all(
            "SELECT * FROM studies WHERE project_id = ? ORDER BY created_at DESC, id DESC", (project_id,)
        )
        return [_study(row) for row in rows]
