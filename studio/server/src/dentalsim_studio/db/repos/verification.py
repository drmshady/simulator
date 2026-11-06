"""The `verification_reports` table (task T071): each report as a JSON document, with its identity columns."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dentalsim_studio.db.connection import Database, from_json, to_json


class SqliteVerificationRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_report(self, report: Mapping[str, Any]) -> str:
        """Store a report (`VerificationReport.model_dump(mode="json")`) and return its id."""
        self.db.execute(
            """INSERT INTO verification_reports
               (id, engine_version, solver_build_id, conventions_version, overall, report, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                report["id"],
                report["engine_version"],
                report["solver_build_id"],
                report["conventions_version"],
                report["overall"],
                to_json(dict(report)),
                report["created_at"],
            ),
        )
        return str(report["id"])

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT report FROM verification_reports WHERE id = ?", (report_id,))
        return None if row is None else dict(from_json(row["report"]))

    def latest_report(self) -> dict[str, Any] | None:
        row = self.db.one("SELECT report FROM verification_reports ORDER BY created_at DESC, id DESC LIMIT 1")
        return None if row is None else dict(from_json(row["report"]))

    def list_reports(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.all(
            """SELECT id, engine_version, solver_build_id, conventions_version, overall, created_at
               FROM verification_reports ORDER BY created_at DESC, id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in rows]
