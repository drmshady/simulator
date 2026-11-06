"""Schema migrations by `PRAGMA user_version` (research R12).

Migration N brings the database from version N-1 to N. Migration 1 is `schema.sql`. Each migration runs in one
`BEGIN IMMEDIATE` transaction and re-reads the version inside it, so the server and the worker can open the
database at the same moment without both migrating it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from importlib import resources

Migration = Callable[[sqlite3.Connection], None]


def split_statements(script: str) -> Iterator[str]:
    """Complete SQL statements of `script`, one at a time (statements may span lines, as triggers do)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        if not buffer and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        raise ValueError(f"incomplete SQL statement at the end of the script: {buffer.strip()[:80]!r}")


def _run_script(name: str) -> Migration:
    def migrate(conn: sqlite3.Connection) -> None:
        script = resources.files(__package__ or "dentalsim_studio.db").joinpath(name).read_text(encoding="utf-8")
        for statement in split_statements(script):
            conn.execute(statement)

    return migrate


MIGRATIONS: tuple[Migration, ...] = (_run_script("schema.sql"),)
SCHEMA_VERSION = len(MIGRATIONS)


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the missing migrations and return the schema version. `conn` must be in autocommit mode."""
    if user_version(conn) > SCHEMA_VERSION:
        raise RuntimeError(
            f"the studio database has schema version {user_version(conn)}, newer than this studio's {SCHEMA_VERSION}"
        )
    while user_version(conn) < SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = user_version(conn)
            if current < SCHEMA_VERSION:
                MIGRATIONS[current](conn)
                conn.execute(f"PRAGMA user_version = {current + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return user_version(conn)
