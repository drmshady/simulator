"""Search across projects, scenarios and runs (task T114; FR-031; research R12).

Three kinds of filter, all of which an item must pass:

- **text**: words matched against names, notes and tags in the FTS5 index (`search_index`, kept up to date by the
  triggers of migration 3). Each word matches as a prefix, so "implan" finds "implant".
- **tag**: an exact tag, ignoring case.
- **setting value**: `path:op:value` over the scenario JSON (a run's snapshot for runs), with `op` one of
  `=`, `<`, `>`, `<=`, `>=`. `path` is:
  - `<block>.<setting>`: `<block>` is an instance id (`implant-1`) or a block type (`implant`, any implant), and
    `<setting>` a setting id, which may leave out its unit suffix (`length` finds `length_mm`)
  - `load:<id>.<field>`: a load case field (`load:axial.force`, `load:axial.direction.angle_deg`)
  - a top-level field (`detail_level`)

  A library setting compares its resolved value. Projects have no settings, so a setting filter lists none.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from dentalsim.errors import Refused
from dentalsim_studio.db.connection import Database, from_json, from_time

SearchKind = Literal["project", "scenario", "run"]
Operator = Literal["=", "<", ">", "<=", ">="]
OPERATORS: tuple[Operator, ...] = ("<=", ">=", "=", "<", ">")
UNIT_SUFFIXES = ("mm", "deg", "um", "n", "mpa", "pct", "ue")
"""Unit suffixes a setting path may leave out: `implant-1.length` finds the setting `length_mm`."""
LIMIT = 50
EQUAL_TOLERANCE = 1e-9
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_FIELD = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
_WORD = re.compile(r"\w+", re.UNICODE)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SettingFilter(_Model):
    """One parsed `path:op:value` filter."""

    text: str
    scope: Literal["block", "load", "top"]
    owner: str | None
    """The block instance id or type id, or the load case id; None for a top-level field."""
    field: str
    op: Operator
    value: float | bool | str


class SearchHit(_Model):
    kind: SearchKind
    id: str
    name: str
    project_id: str | None
    """None for a run made from a scenario file on the command line."""
    scenario_id: str | None = None
    notes: str = ""
    tags: list[str] = []
    snippet: str | None = None
    """Where the text matched, with the matching words in [brackets]."""
    status: str | None = None
    """Runs: the run status."""
    detail_level: str | None = None
    updated_at: AwareDatetime


class SearchResults(_Model):
    projects: list[SearchHit] = []
    scenarios: list[SearchHit] = []
    runs: list[SearchHit] = []


def _refuse(message: str) -> Refused:
    return Refused(message, code="validation_failed")


def clean_tags(tags: Iterable[str]) -> list[str]:
    """Tags trimmed, empty ones dropped, and each kept once (the first spelling wins, ignoring case)."""
    seen: set[str] = set()
    cleaned = []
    for tag in tags:
        text = " ".join(tag.split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            cleaned.append(text)
    return cleaned


def _value(text: str) -> float | bool | str:
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return float(text)
    except ValueError:
        return text.strip()


def parse_setting(text: str) -> SettingFilter:
    """`implant-1.length:=:8` → a filter; refused (`validation_failed`) when it cannot be read."""
    parts = text.split(":")
    if parts[0] == "load" and len(parts) >= 2:
        parts = [f"load:{parts[1]}", *parts[2:]]
    if len(parts) != 3:
        raise _refuse(f"a setting search is written path:op:value, as in implant-1.length:=:8, not {text!r}")
    path, op, raw = (p.strip() for p in parts)
    if op not in OPERATORS:
        raise _refuse(f"the operator in {text!r} must be one of {', '.join(OPERATORS)}")
    value = _value(raw)
    if raw == "":
        raise _refuse(f"the setting search {text!r} has no value")
    if isinstance(value, (str, bool)) and op != "=":
        raise _refuse(f"only = compares a value that is not a number, as in {path}:=:{raw}")
    if path.startswith("load:"):
        owner, _, field = path.removeprefix("load:").partition(".")
        scope: Literal["block", "load", "top"] = "load"
    elif "." in path:
        owner, _, field = path.partition(".")
        scope = "block"
    else:
        owner, field, scope = None, path, "top"
    if (owner is not None and not _NAME.match(owner)) or not _FIELD.match(field):
        raise _refuse(f"the setting path {path!r} is not one the studio can search: use block.setting, "
                      "load:<id>.<field> or a top-level field such as detail_level")  # fmt: skip
    return SettingFilter(text=text, scope=scope, owner=owner, field=field, op=op, value=value)


def _compare(expression: str, setting: SettingFilter) -> tuple[str, list[Any]]:
    if setting.op == "=" and isinstance(setting.value, float):
        return f"abs({expression} - ?) <= ?", [setting.value, EQUAL_TOLERANCE * max(1.0, abs(setting.value))]
    if isinstance(setting.value, bool):
        return f"{expression} = ?", [int(setting.value)]
    return f"{expression} {setting.op} ?", [setting.value]


def setting_clause(column: str, setting: SettingFilter) -> tuple[str, list[Any]]:
    """A condition on the scenario JSON in `column` (a scenario's definition, or a run's snapshot)."""
    if setting.scope == "top":
        return _compare(f"json_extract({column}, '$.{setting.field}')", setting)
    if setting.scope == "load":
        value = (f"coalesce(json_extract(l.value, '$.{setting.field}.value'), "
                 f"json_extract(l.value, '$.{setting.field}'))")  # fmt: skip
        condition, params = _compare(value, setting)
        sql = (f"EXISTS (SELECT 1 FROM json_each({column}, '$.loads') l "
               f"WHERE json_extract(l.value, '$.id') = ? AND {condition})")  # fmt: skip
        return sql, [setting.owner, *params]
    names = [setting.field, *(f"{setting.field}_{unit}" for unit in UNIT_SUFFIXES)]
    condition, params = _compare("json_extract(st.value, '$.value')", setting)
    sql = (
        f"EXISTS (SELECT 1 FROM json_each({column}, '$.blocks') b, json_each(b.value, '$.settings') st "
        "WHERE (json_extract(b.value, '$.instance_id') = ? OR json_extract(b.value, '$.type_id') = ?) "
        f"AND st.key IN ({', '.join('?' for _ in names)}) AND {condition})"
    )
    return sql, [setting.owner, setting.owner, *names, *params]


def fts_query(text: str) -> str | None:
    """Each word of `text` as a quoted prefix term, all required; None when there are no words."""
    words = _WORD.findall(text)
    return " ".join(f'"{w}"*' for w in words) or None


def _tag_clause(column: str, tag: str) -> tuple[str, list[Any]]:
    return f"EXISTS (SELECT 1 FROM json_each({column}) t WHERE lower(t.value) = lower(?))", [tag]


_SOURCES: dict[SearchKind, tuple[str, str, str]] = {
    # kind: (table, further joins, the scenario JSON column or '' when it has none)
    "project": ("projects", "", ""),
    "scenario": ("scenarios", "", "x.definition"),
    "run": ("runs", "LEFT JOIN scenarios s ON s.id = x.scenario_id", "x.snapshot"),
}
_COLUMNS: dict[SearchKind, str] = {
    "project": "x.id, x.name, x.id AS project_id, NULL AS scenario_id, x.notes, x.tags, NULL AS status, "
    "NULL AS detail_level, x.updated_at",
    "scenario": "x.id, x.name, x.project_id, x.id AS scenario_id, x.notes, x.tags, NULL AS status, "
    "json_extract(x.definition, '$.detail_level') AS detail_level, x.updated_at",
    "run": "x.id, json_extract(x.snapshot, '$.name') AS name, s.project_id, x.scenario_id, x.notes, x.tags, "
    "x.status, x.detail_level, coalesce(x.finished_at, x.created_at) AS updated_at",
}


def _hit(kind: SearchKind, row: sqlite3.Row) -> SearchHit:
    return SearchHit(
        kind=kind,
        id=row["id"],
        name=row["name"] or "",
        project_id=row["project_id"],
        scenario_id=row["scenario_id"],
        notes=row["notes"],
        tags=from_json(row["tags"]),
        snippet=row["snippet"],
        status=row["status"],
        detail_level=row["detail_level"],
        updated_at=from_time(row["updated_at"]),
    )


def _search_kind(
    db: Database,
    kind: SearchKind,
    *,
    match: str | None,
    tags: Sequence[str],
    settings: Sequence[SettingFilter],
    limit: int,
) -> list[SearchHit]:
    table, joins, json_column = _SOURCES[kind]
    if settings and not json_column:
        return []
    conditions: list[str] = []
    params: list[Any] = []
    for tag in tags:
        sql, extra = _tag_clause("x.tags", tag)
        conditions.append(sql)
        params += extra
    for setting in settings:
        sql, extra = setting_clause(json_column, setting)
        conditions.append(sql)
        params += extra
    columns = _COLUMNS[kind]
    if match is not None:
        where = " AND ".join(["search_index MATCH ?", "search_index.kind = ?", *conditions])
        sql = (
            f"SELECT {columns}, snippet(search_index, -1, '[', ']', '…', 12) AS snippet "
            f"FROM search_index JOIN {table} x ON x.id = search_index.item_id {joins} "
            f"WHERE {where} ORDER BY bm25(search_index) LIMIT ?"
        )
        params = [match, kind, *params, limit]
    else:
        where = " AND ".join(conditions) or "1"
        sql = f"SELECT {columns}, NULL AS snippet FROM {table} x {joins} WHERE {where} ORDER BY updated_at DESC LIMIT ?"
        params = [*params, limit]
    return [_hit(kind, row) for row in db.all(sql, params)]


def search(
    db: Database,
    *,
    text: str | None = None,
    tags: Sequence[str] = (),
    settings: Sequence[str | SettingFilter] = (),
    limit: int = LIMIT,
) -> SearchResults:
    """Projects, scenarios and runs that pass every filter given; nothing when no filter is given."""
    match = fts_query(text) if text else None
    wanted = clean_tags(tags)
    parsed = [s if isinstance(s, SettingFilter) else parse_setting(s) for s in settings]
    if match is None and not wanted and not parsed:
        return SearchResults()
    found = {
        kind: _search_kind(db, kind, match=match, tags=wanted, settings=parsed, limit=limit)
        for kind in ("project", "scenario", "run")
    }
    return SearchResults(projects=found["project"], scenarios=found["scenario"], runs=found["run"])
