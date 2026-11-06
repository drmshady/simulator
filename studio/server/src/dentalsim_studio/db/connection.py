"""SQLite connections for the studio database (research R12): WAL, foreign keys, a busy timeout, migrations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dentalsim_studio.db.migrations import migrate

DB_FILENAME = "studio.db"
BUSY_TIMEOUT_MS = 10_000

Params = Sequence[Any] | dict[str, Any]


class Database:
    """One SQLite connection in autocommit mode, with explicit transactions.

    A statement outside `transaction()` commits on its own. Use one `Database` per thread at a time.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._depth = 0

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """`BEGIN IMMEDIATE` ... `COMMIT`, or `ROLLBACK` on an exception. Nested use joins the outer transaction."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._depth = 0

    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Params]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, rows)

    def one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(sql, params).fetchone()
        return row

    def all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def close(self) -> None:
        self.conn.close()


def connect(path: Path) -> Database:
    """Open (creating if needed) and migrate the database at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return Database(conn)


def db_path(data_dir: Path) -> Path:
    return data_dir / DB_FILENAME


# Column codecs shared by the repositories.


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def from_json(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def to_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("times must be timezone-aware (UTC)")
    return value.isoformat()


def from_time(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)
