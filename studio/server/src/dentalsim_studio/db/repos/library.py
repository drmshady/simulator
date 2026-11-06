"""The literature library tables: library_entries, library_versions, sources, version_sources (T037, research R13).

Implements `dentalsim.persistence.LibraryRepo`. The rules (what may be approved, what supersedes what) live in
`dentalsim.library.service`; this module only stores and reads records.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from dentalsim.clock import utc_now
from dentalsim.errors import Conflict, NotFound
from dentalsim.library.models import (
    EntryStatus,
    LiteratureEntry,
    LiteratureEntryVersion,
    Source,
    SourceCheck,
    ValueBySource,
)
from dentalsim_studio.db.connection import Database, from_json, from_time, to_json, to_time


class SqliteLibraryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # Sources ----------------------------------------------------------------------------------------------------

    def add_source(self, source: Source) -> None:
        try:
            self.db.execute(
                """INSERT INTO sources (id, authors, year, title, journal, doi, pmid, study_type, check_status,
                                        check_result, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.id,
                    to_json(source.authors),
                    source.year,
                    source.title,
                    source.journal,
                    source.doi,
                    source.pmid,
                    source.study_type,
                    source.check.status,
                    to_json(source.check.model_dump(mode="json")),
                    to_time(utc_now()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"cannot store source {source.id}: the id, DOI or PMID is already stored ({exc})") from exc

    def get_source(self, source_id: str) -> Source | None:
        row = self.db.one("SELECT * FROM sources WHERE id = ?", (source_id,))
        return None if row is None else _source(row)

    def find_source(self, *, doi: str | None = None, pmid: str | None = None) -> Source | None:
        if doi:
            row = self.db.one("SELECT * FROM sources WHERE lower(doi) = lower(?)", (doi,))
            if row is not None:
                return _source(row)
        if pmid:
            row = self.db.one("SELECT * FROM sources WHERE pmid = ?", (pmid,))
            if row is not None:
                return _source(row)
        return None

    def update_source_check(self, source_id: str, check: SourceCheck) -> None:
        cursor = self.db.execute(
            "UPDATE sources SET check_status = ?, check_result = ? WHERE id = ?",
            (check.status, to_json(check.model_dump(mode="json")), source_id),
        )
        if cursor.rowcount == 0:
            raise NotFound(f"no source {source_id}")

    # Entries ----------------------------------------------------------------------------------------------------

    def add_entry(self, entry: LiteratureEntry) -> None:
        try:
            self.db.execute(
                """INSERT INTO library_entries (id, parameter_key, category, label, current_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.parameter_key,
                    entry.category,
                    entry.label,
                    entry.current_version,
                    to_time(entry.created_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"an entry with id {entry.id} or key {entry.parameter_key} is already stored") from exc

    def get_entry(self, entry_id: str) -> LiteratureEntry | None:
        row = self.db.one("SELECT * FROM library_entries WHERE id = ?", (entry_id,))
        return None if row is None else _entry(row)

    def get_entry_by_key(self, parameter_key: str) -> LiteratureEntry | None:
        row = self.db.one("SELECT * FROM library_entries WHERE parameter_key = ?", (parameter_key,))
        return None if row is None else _entry(row)

    def set_current_version(self, entry_id: str, version: int | None) -> None:
        cursor = self.db.execute("UPDATE library_entries SET current_version = ? WHERE id = ?", (version, entry_id))
        if cursor.rowcount == 0:
            raise NotFound(f"no library entry {entry_id}")

    def list_entries(
        self, *, status: EntryStatus | None = None, category: str | None = None, text: str | None = None
    ) -> list[LiteratureEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("EXISTS (SELECT 1 FROM library_versions v WHERE v.entry_id = e.id AND v.status = ?)")
            params.append(status)
        if category is not None:
            clauses.append("e.category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.all(f"SELECT e.* FROM library_entries e {where} ORDER BY e.parameter_key", params)
        entries = [_entry(row) for row in rows]
        if text:
            # In Python rather than SQL, because SQLite's lower() folds ASCII letters only.
            needle = text.casefold()
            entries = [e for e in entries if needle in e.parameter_key.casefold() or needle in e.label.casefold()]
        return entries

    # Versions ---------------------------------------------------------------------------------------------------

    def add_version(self, version: LiteratureEntryVersion) -> None:
        try:
            with self.db.transaction():
                self.db.execute(
                    """INSERT INTO library_versions (entry_id, version, units, typical, range_min, range_max, notes,
                                                     status, origin, proposed_at, approved_at, rejected_at,
                                                     rejected_reason, superseded_at, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        version.entry_id,
                        version.version,
                        version.units,
                        version.typical,
                        version.range_min,
                        version.range_max,
                        version.notes,
                        version.status,
                        version.origin,
                        to_time(version.proposed_at),
                        to_time(version.approved_at),
                        to_time(version.rejected_at),
                        version.rejected_reason,
                        to_time(version.superseded_at),
                        version.content_hash,
                    ),
                )
                self.db.executemany(
                    """INSERT INTO version_sources (entry_id, version, source_id, position, value, note)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (version.entry_id, version.version, item.source_id, position, item.value, item.note)
                        for position, item in enumerate(version.values_by_source)
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict(
                f"cannot store version {version.version} of entry {version.entry_id}: it exists already, or the "
                f"entry or a cited source is not stored ({exc})"
            ) from exc

    def get_version(self, entry_id: str, version: int) -> LiteratureEntryVersion | None:
        row = self.db.one("SELECT * FROM library_versions WHERE entry_id = ? AND version = ?", (entry_id, version))
        return None if row is None else self._version(row)

    def list_versions(self, entry_id: str) -> list[LiteratureEntryVersion]:
        rows = self.db.all("SELECT * FROM library_versions WHERE entry_id = ? ORDER BY version", (entry_id,))
        return [self._version(row) for row in rows]

    def update_version_status(self, version: LiteratureEntryVersion) -> None:
        cursor = self.db.execute(
            """UPDATE library_versions
               SET status = ?, approved_at = ?, rejected_at = ?, rejected_reason = ?, superseded_at = ?
               WHERE entry_id = ? AND version = ?""",
            (
                version.status,
                to_time(version.approved_at),
                to_time(version.rejected_at),
                version.rejected_reason,
                to_time(version.superseded_at),
                version.entry_id,
                version.version,
            ),
        )
        if cursor.rowcount == 0:
            raise NotFound(f"no version {version.version} of library entry {version.entry_id}")

    def _version(self, row: sqlite3.Row) -> LiteratureEntryVersion:
        links = self.db.all(
            "SELECT source_id, value, note FROM version_sources WHERE entry_id = ? AND version = ? ORDER BY position",
            (row["entry_id"], row["version"]),
        )
        return LiteratureEntryVersion(
            entry_id=row["entry_id"],
            version=row["version"],
            units=row["units"],
            typical=row["typical"],
            range_min=row["range_min"],
            range_max=row["range_max"],
            values_by_source=[ValueBySource(source_id=r["source_id"], value=r["value"], note=r["note"]) for r in links],
            notes=row["notes"],
            status=row["status"],
            origin=row["origin"],
            proposed_at=from_time(row["proposed_at"]),
            approved_at=from_time(row["approved_at"]),
            rejected_at=from_time(row["rejected_at"]),
            rejected_reason=row["rejected_reason"],
            superseded_at=from_time(row["superseded_at"]),
            content_hash=row["content_hash"],
        )


def _source(row: sqlite3.Row) -> Source:
    check = {**(from_json(row["check_result"]) or {}), "status": row["check_status"]}
    return Source(
        id=row["id"],
        authors=from_json(row["authors"]),
        year=row["year"],
        title=row["title"],
        journal=row["journal"],
        doi=row["doi"],
        pmid=row["pmid"],
        study_type=row["study_type"],
        check=SourceCheck.model_validate(check),
    )


def _entry(row: sqlite3.Row) -> LiteratureEntry:
    return LiteratureEntry(
        id=row["id"],
        parameter_key=row["parameter_key"],
        category=row["category"],
        label=row["label"],
        current_version=row["current_version"],
        created_at=from_time(row["created_at"]),
    )
