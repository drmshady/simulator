"""The `settings` table: JSON values by key."""

from __future__ import annotations

from typing import Any

from dentalsim.clock import utc_now
from dentalsim_studio.db.connection import Database, from_json, to_json, to_time


class SqliteSettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> Any | None:
        row = self.db.one("SELECT value FROM settings WHERE key = ?", (key,))
        return None if row is None else from_json(row["value"])

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, to_json(value), to_time(utc_now())),
        )

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
