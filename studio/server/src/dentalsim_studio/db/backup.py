"""Copying the studio database out of and back into a running studio (task T121; FR-034; research R12).

Both directions use SQLite's online backup, which copies a consistent state page by page while other connections
stay open, so neither needs the studio to stop.

- `snapshot`: the database as it is now, into a new file. The job making the backup (if any) is marked succeeded
  in the copy, so restoring it does not show that job as still running.
- `restore`: the copy is checked first (readable, integrity check, a schema this studio knows), then copied over the
  live database and migrated to the current schema. Work that was queued or running when the backup was made
  cannot resume after a restore, so its jobs, runs, previews and studies are cancelled with a reason. The job doing
  the restore keeps its own row, so the worker can still finish it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from dentalsim.clock import utc_now
from dentalsim.errors import Refused
from dentalsim_studio.db.connection import Database, to_time
from dentalsim_studio.db.migrations import SCHEMA_VERSION, migrate, user_version

IN_FLIGHT_REASON = "It was in progress when the backup was made, so the restore stopped it; start it again."
_IN_FLIGHT = ("queued", "running", "interrupted")


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def snapshot(db: Database, target: Path, *, finished_job_id: str | None = None) -> None:
    """Write a consistent copy of the live database to `target` (which must not exist)."""
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = sqlite3.connect(target, isolation_level=None)
    try:
        db.conn.backup(copy)
        copy.execute("PRAGMA journal_mode = DELETE")
        if finished_job_id is not None:
            copy.execute(
                "UPDATE jobs SET status = 'succeeded', finished_at = ? WHERE id = ?",
                (to_time(utc_now()), finished_job_id),
            )
    finally:
        copy.close()


def check_copy(source: Path) -> int:
    """The schema version of a database copy; refused (`restore_refused`) when it is damaged or too new."""
    try:
        conn = _open_read_only(source)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = user_version(conn)
            conn.execute("SELECT count(*) FROM projects").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise Refused(f"The database in the backup cannot be read ({exc}).", code="restore_refused") from exc
    if integrity != "ok":
        raise Refused(f"The database in the backup is damaged: {integrity}.", code="restore_refused")
    if version > SCHEMA_VERSION:
        raise Refused(
            f"The backup comes from a newer studio (database schema {version}, this studio knows {SCHEMA_VERSION}); "
            "update the studio before restoring it.",
            code="restore_refused",
        )
    return version


def restore(db: Database, source: Path, *, keep_job_id: str | None = None) -> None:
    """Replace the live database's content with the copy at `source` (checked first; nothing changes if it fails)."""
    check_copy(source)
    kept: dict[str, Any] | None = None
    if keep_job_id is not None:
        row = db.one("SELECT * FROM jobs WHERE id = ?", (keep_job_id,))
        kept = None if row is None else dict(row)
    copy = _open_read_only(source)
    try:
        copy.backup(db.conn)
    finally:
        copy.close()
    migrate(db.conn)
    now = to_time(utc_now())
    marks = ", ".join("?" for _ in _IN_FLIGHT)
    with db.transaction():
        db.execute(
            f"UPDATE jobs SET status = 'cancelled', failure_reason = ?, finished_at = ? WHERE status IN ({marks})",
            (IN_FLIGHT_REASON, now, *_IN_FLIGHT),
        )
        for table in ("runs", "previews"):
            finished = ", finished_at = ?" if table == "runs" else ""
            db.execute(
                f"UPDATE {table} SET status = 'cancelled', failure_reason = ?{finished} WHERE status IN ({marks})",
                (IN_FLIGHT_REASON, *((now,) if finished else ()), *_IN_FLIGHT),
            )
        db.execute(
            "UPDATE studies SET status = 'cancelled', updated_at = ? WHERE status IN ('queued', 'running')", (now,)
        )
        if kept is not None:
            columns = ", ".join(kept)
            db.execute(
                f"INSERT OR REPLACE INTO jobs ({columns}) VALUES ({', '.join('?' for _ in kept)})", tuple(kept.values())
            )
