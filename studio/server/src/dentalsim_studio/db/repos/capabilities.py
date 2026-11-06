"""The `capability_status` table (FR-011a, FR-062). A capability with no row is awaiting its benchmark."""

from __future__ import annotations

from dentalsim.clock import utc_now
from dentalsim.persistence import CapabilityStatus
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time


class SqliteCapabilityRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, capability_id: str) -> CapabilityStatus | None:
        row = self.db.one("SELECT * FROM capability_status WHERE capability_id = ?", (capability_id,))
        if row is None:
            return None
        return CapabilityStatus(
            capability_id=row["capability_id"],
            status=row["status"],
            report_id=row["report_id"],
            cases=from_json(row["cases"]),
            updated_at=from_time(row["updated_at"]),
        )

    def set(self, status: CapabilityStatus) -> None:
        self.db.execute(
            """INSERT INTO capability_status (capability_id, status, report_id, cases, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (capability_id) DO UPDATE
               SET status = excluded.status, report_id = excluded.report_id, cases = excluded.cases,
                   updated_at = excluded.updated_at""",
            (
                status.capability_id,
                status.status,
                status.report_id,
                to_json(status.cases),
                to_time(status.updated_at or utc_now()),
            ),
        )

    def list_all(self) -> list[CapabilityStatus]:
        rows = self.db.all("SELECT capability_id FROM capability_status ORDER BY capability_id")
        return [status for row in rows if (status := self.get(row["capability_id"])) is not None]
