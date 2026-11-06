"""The `events` table: the worker and the server append, the event stream reads (research R10)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dentalsim.clock import utc_now
from dentalsim.persistence import EventRecord
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time


class SqliteEventRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def emit(self, type: str, payload: Mapping[str, Any]) -> int:
        cursor = self.db.execute(
            "INSERT INTO events (type, payload, created_at) VALUES (?, ?, ?)",
            (type, to_json(dict(payload)), to_time(utc_now())),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def since(self, after_id: int, *, limit: int = 100) -> list[EventRecord]:
        rows = self.db.all("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
        return [
            EventRecord(
                id=row["id"],
                type=row["type"],
                payload=from_json(row["payload"]),
                created_at=from_time(row["created_at"]),
            )
            for row in rows
        ]

    def latest_id(self) -> int:
        row = self.db.one("SELECT COALESCE(MAX(id), 0) AS id FROM events")
        return int(row["id"]) if row else 0
