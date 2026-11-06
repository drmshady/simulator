"""Read and write `dentalsim.library/1` files (contracts/library-entry-format.md, FR-053, research R13).

- `parse_proposal` reads a bulk proposal (or a snapshot) and applies every import rule. It reports all the problems
  at once, each with its entry key and line, in one `Refused(code="validation_failed")`.
- `proposal_problems` applies the same rules to a `ProposalFile` built in code, such as the owner's one-entry form.
- `write_snapshot` writes approved versions in the same format, sorted by parameter key so snapshots diff cleanly
  in git. A snapshot has `snapshot_of: approved` and `exported_at` instead of `prepared_by` and `prepared_at`, and
  each entry also carries its `version`, `content_hash` and `approved_at`.

Importing creates `proposed` versions whatever the file says; that is the service's job (`library.service`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from dentalsim.clock import utc_now
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions, LibraryCategory, is_parameter_key
from dentalsim.errors import Refused
from dentalsim.library.models import LiteratureEntry, LiteratureEntryVersion, Source, StudyType

FORMAT: Literal["dentalsim.library/1"] = "dentalsim.library/1"
SNAPSHOT_HEADER = "# Approved literature library snapshot, written by `dentalsim library export`. Do not edit.\n"
DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
SNAPSHOT_ENTRY_KEYS = ("version", "content_hash", "approved_at")

Problem = dict[str, Any]
"""One import problem: `code` and `message`, plus `entry`, `source`, `field` and `line` when known."""
YamlPath = tuple[str | int, ...]


def _number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    return value


Number = Annotated[float, BeforeValidator(_number)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposalRange(_Model):
    min: Number
    max: Number


class ProposalValue(_Model):
    source: str = Field(min_length=1)
    """The `id` of a source in the same file."""
    value: Number
    note: str | None = None


class ProposalEntry(_Model):
    parameter_key: str
    category: LibraryCategory
    label: str | None = None
    """Defaults to the parameter key."""
    units: str
    typical: Number
    range: ProposalRange
    values_by_source: list[ProposalValue] = Field(min_length=1)
    notes: str | None = None
    version: int | None = Field(default=None, ge=1)
    """Snapshot only, like `content_hash` and `approved_at`."""
    content_hash: str | None = None
    approved_at: AwareDatetime | None = None

    @field_validator("units", mode="before")
    @classmethod
    def _units_as_text(cls, value: Any) -> Any:
        # YAML reads `units: 1` (a dimensionless quantity) as a number.
        if isinstance(value, int | float) and not isinstance(value, bool):
            return f"{value:g}"
        return value.strip() if isinstance(value, str) else value


class ProposalSource(_Model):
    id: str = Field(min_length=1)
    """Local to the file; the service maps it to the stored source."""
    authors: list[str] = Field(min_length=1)
    year: int = Field(ge=1800, le=2100)
    title: str = Field(min_length=1)
    journal: str | None = None
    doi: str | None = None
    pmid: str | None = None
    study_type: StudyType

    @field_validator("doi", mode="before")
    @classmethod
    def _bare_doi(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        for prefix in DOI_PREFIXES:
            if text.lower().startswith(prefix):
                text = text[len(prefix) :].strip()
        return text or None

    @field_validator("pmid", mode="before")
    @classmethod
    def _pmid_as_text(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("authors")
    @classmethod
    def _stripped(cls, value: list[str]) -> list[str]:
        return [name.strip() for name in value]


class _Header(_Model):
    format: Literal["dentalsim.library/1"]
    prepared_by: Literal["claude", "owner"] | None = None
    prepared_at: date | None = None
    snapshot_of: Literal["approved"] | None = None
    exported_at: AwareDatetime | None = None


class ProposalFile(_Header):
    """A parsed `dentalsim.library/1` file. Use `parse_proposal` or `proposal_problems` to apply the import rules."""

    entries: list[ProposalEntry]
    sources: list[ProposalSource]

    @property
    def is_snapshot(self) -> bool:
        return self.snapshot_of is not None


class SnapshotItem(_Model):
    """One approved version for `write_snapshot`, with the sources it cites."""

    entry: LiteratureEntry
    version: LiteratureEntryVersion
    sources: tuple[Source, ...]


# Problems ------------------------------------------------------------------------------------------------------


def _problem(
    code: str,
    message: str,
    *,
    entry: str | None = None,
    source: str | None = None,
    field: str | None = None,
    line: int | None = None,
) -> Problem:
    where = [f"line {line}"] if line is not None else []
    if entry is not None:
        where.append(f"entry {entry}")
    elif source is not None:
        where.append(f"source {source}")
    problem: Problem = {"code": code, "message": f"{', '.join(where)}: {message}" if where else message}
    for key, value in (("entry", entry), ("source", source), ("field", field), ("line", line)):
        if value is not None:
            problem[key] = value
    return problem


class _Lines:
    """Line numbers (1-based) of the nodes of a composed YAML document, by path such as ("entries", 2, "range")."""

    def __init__(self, root: Node | None) -> None:
        self.root = root

    def __call__(self, path: YamlPath) -> int | None:
        node = self.root
        if node is None:
            return None
        line = node.start_mark.line + 1
        for part in path:
            child: Node | None = None
            if isinstance(node, MappingNode) and isinstance(part, str):
                for key, value in node.value:
                    if isinstance(key, ScalarNode) and key.value == part:
                        line, child = key.start_mark.line + 1, value
                        break
            elif isinstance(node, SequenceNode) and isinstance(part, int) and 0 <= part < len(node.value):
                child = node.value[part]
                line = child.start_mark.line + 1
            if child is None:
                break
            node = child
        return line


def _no_lines(path: YamlPath) -> int | None:
    return None


def _validation_problems(
    exc: ValidationError,
    *,
    path: YamlPath,
    lines: _Lines | None,
    entry: str | None = None,
    source: str | None = None,
) -> list[Problem]:
    problems = []
    for error in exc.errors():
        loc = tuple(error["loc"])
        field = ".".join(str(part) for part in loc) or None
        line = (lines or _no_lines)((*path, *loc))
        kind = error["type"]
        text = str(error["msg"]).removeprefix("Value error, ")
        if kind == "missing":
            code, message = "missing_field", f"the required field '{field}' is missing"
        elif kind == "extra_forbidden":
            code, message = "unknown_field", f"unknown field '{field}'"
        elif field is None:
            code, message = "invalid_value", text
        else:
            code, message = "invalid_value", f"{field}: {text}"
        problems.append(_problem(code, message, entry=entry, source=source, field=field, line=line))
    return problems


def units_problem(category: str, parameter_key: str, units: str, conventions: Conventions) -> str | None:
    """Why `units` do not suit the key under the conventions' `library_units` table, or None when they do."""
    row = conventions.library_unit_for(category, parameter_key)
    if row is None:
        patterns = [r.pattern for r in conventions.library_units if r.category == category]
        allowed = ", ".join(patterns) if patterns else "none"
        return (
            f"the key matches no library-units row of category {category} in the conventions "
            f"(allowed patterns: {allowed})"
        )
    if units != row.units:
        return f"units must be {row.units!r} for {row.quantity} (pattern {row.pattern}), not {units!r}"
    return None


def range_problems(
    typical: float, range_min: float, range_max: float, values: Sequence[tuple[str, float]]
) -> list[tuple[str, str, str]]:
    """(code, field, message) for a typical value or source value outside the range, or a reversed range."""
    if range_min > range_max:
        return [("range", "range", f"range.min {range_min:g} is greater than range.max {range_max:g}")]
    span = f"{range_min:g}–{range_max:g}"
    found = []
    if not range_min <= typical <= range_max:
        found.append(("range", "typical", f"typical {typical:g} lies outside the range {span}"))
    for index, (source_id, value) in enumerate(values):
        if not range_min <= value <= range_max:
            found.append(
                (
                    "range",
                    f"values_by_source.{index}.value",
                    f"the value {value:g} from source {source_id!r} lies outside the range {span}",
                )
            )
    return found


def _add(
    problems: list[Problem],
    lines: _Lines,
    path: YamlPath,
    code: str,
    message: str,
    field: str | None = None,
    *,
    entry: str | None = None,
    source: str | None = None,
) -> None:
    line = lines((*path, *_split(field)) if field else path)
    problems.append(_problem(code, message, entry=entry, source=source, field=field, line=line))


def _split(field: str) -> YamlPath:
    return tuple(int(part) if part.isdigit() else part for part in field.split("."))


def _source_problems(sources: Sequence[tuple[YamlPath, ProposalSource]], lines: _Lines) -> list[Problem]:
    problems: list[Problem] = []
    seen_ids: set[str] = set()
    seen_doi: dict[str, str] = {}
    seen_pmid: dict[str, str] = {}
    for path, source in sources:
        sid = source.id
        if sid in seen_ids:
            _add(
                problems,
                lines,
                path,
                "duplicate_source",
                f"the source id {sid!r} is used more than once",
                "id",
                source=sid,
            )
        seen_ids.add(sid)
        if not (source.doi or source.pmid):
            _add(problems, lines, path, "no_identifier", "a source needs a DOI or a PMID", source=sid)
        if source.doi is not None:
            if not re.match(r"^10\.\d{4,9}/\S+$", source.doi):
                message = f"{source.doi!r} is not a DOI (expected 10.<prefix>/<suffix>)"
                _add(problems, lines, path, "invalid_doi", message, "doi", source=sid)
            other = seen_doi.setdefault(source.doi.casefold(), sid)
            if other != sid:
                message = f"the DOI {source.doi} is also given for source {other!r}"
                _add(problems, lines, path, "duplicate_source", message, "doi", source=sid)
        if source.pmid is not None:
            if not source.pmid.isdigit():
                message = f"{source.pmid!r} is not a PMID (expected digits only)"
                _add(problems, lines, path, "invalid_pmid", message, "pmid", source=sid)
            other = seen_pmid.setdefault(source.pmid, sid)
            if other != sid:
                message = f"the PMID {source.pmid} is also given for source {other!r}"
                _add(problems, lines, path, "duplicate_source", message, "pmid", source=sid)
        if not source.authors[0]:
            _add(
                problems, lines, path, "missing_field", "the first author's surname is required", "authors", source=sid
            )
    return problems


def _entry_problems(
    entries: Sequence[tuple[YamlPath, ProposalEntry]],
    known_sources: set[str],
    conventions: Conventions,
    lines: _Lines,
) -> list[Problem]:
    problems: list[Problem] = []
    first_line: dict[str, int | None] = {}
    for path, entry in entries:
        key = entry.parameter_key
        if key in first_line:
            where = f" (first at line {first_line[key]})" if first_line[key] is not None else ""
            message = f"the parameter key appears more than once in the file{where}"
            _add(problems, lines, path, "duplicate_key", message, "parameter_key", entry=key)
        else:
            first_line[key] = lines((*path, "parameter_key"))
        if not key or not is_parameter_key(key):
            message = (
                "a parameter key is dot-separated parts of letters, digits and underscores, e.g. bone.cancellous.E.D4"
            )
            _add(problems, lines, path, "invalid_key", message, "parameter_key", entry=key)
        elif (why := units_problem(entry.category, key, entry.units, conventions)) is not None:
            _add(problems, lines, path, "units", why, "units", entry=key)
        values = [(item.source, item.value) for item in entry.values_by_source]
        for code, field, message in range_problems(entry.typical, entry.range.min, entry.range.max, values):
            _add(problems, lines, path, code, message, field, entry=key)
        cited: set[str] = set()
        for index, item in enumerate(entry.values_by_source):
            field = f"values_by_source.{index}.source"
            if item.source not in known_sources:
                message = f"the source {item.source!r} is not listed under sources"
                _add(problems, lines, path, "unknown_source", message, field, entry=key)
            if item.source in cited:
                message = f"the source {item.source!r} is cited more than once in this entry"
                _add(problems, lines, path, "duplicate_source", message, field, entry=key)
            cited.add(item.source)
    return problems


def _header_problems(
    header: Mapping[str, Any], entries: Sequence[tuple[YamlPath, ProposalEntry]], lines: _Lines
) -> list[Problem]:
    """Proposals need `prepared_by` and `prepared_at`; the snapshot-only keys need `snapshot_of`."""
    problems = []
    if header.get("snapshot_of") is None:
        for name in ("prepared_by", "prepared_at"):
            if header.get(name) is None:
                problems.append(
                    _problem("missing_field", f"the required field '{name}' is missing", field=name, line=lines(()))
                )
        if header.get("exported_at") is not None:
            problems.append(
                _problem(
                    "snapshot_only",
                    "'exported_at' is only allowed in a snapshot (snapshot_of: approved)",
                    field="exported_at",
                    line=lines(("exported_at",)),
                )
            )
        if not entries:
            problems.append(
                _problem("no_entries", "the file has no entries", field="entries", line=lines(("entries",)))
            )
        for path, entry in entries:
            for name in SNAPSHOT_ENTRY_KEYS:
                if getattr(entry, name) is not None:
                    problems.append(
                        _problem(
                            "snapshot_only",
                            f"'{name}' is only allowed in a snapshot (snapshot_of: approved)",
                            entry=entry.parameter_key,
                            field=name,
                            line=lines((*path, name)),
                        )
                    )
        return problems
    if header.get("exported_at") is None:
        problems.append(
            _problem("missing_field", "a snapshot needs 'exported_at'", field="exported_at", line=lines(()))
        )
    for path, entry in entries:
        for name in SNAPSHOT_ENTRY_KEYS:
            if getattr(entry, name) is None:
                problems.append(
                    _problem(
                        "missing_field",
                        f"a snapshot entry needs '{name}'",
                        entry=entry.parameter_key,
                        field=name,
                        line=lines(path),
                    )
                )
    return problems


def _header_fields(header: _Header) -> dict[str, Any]:
    return {name: getattr(header, name) for name in _Header.model_fields}


def proposal_problems(proposal: ProposalFile, *, conventions: Conventions | None = None) -> list[Problem]:
    """Every import-rule problem of a parsed or code-built file (without line numbers)."""
    conventions = conventions or get_conventions()
    lines = _Lines(None)
    entries = [(("entries", i), e) for i, e in enumerate(proposal.entries)]
    sources = [(("sources", i), s) for i, s in enumerate(proposal.sources)]
    return [
        *_header_problems(_header_fields(proposal), entries, lines),
        *_source_problems(sources, lines),
        *_entry_problems(entries, {s.id for s in proposal.sources}, conventions, lines),
    ]


def check_proposal(proposal: ProposalFile, *, conventions: Conventions | None = None, name: str = "the file") -> None:
    """Raise `Refused(code="validation_failed")` listing every problem, if there are any."""
    problems = proposal_problems(proposal, conventions=conventions)
    if problems:
        raise _refused(name, problems)


def _refused(name: str, problems: list[Problem]) -> Refused:
    count = f"{len(problems)} problem" + ("" if len(problems) == 1 else "s")
    return Refused(
        f"{name} does not meet the library import rules ({count})", code="validation_failed", problems=problems
    )


# Reading -------------------------------------------------------------------------------------------------------


def _compose(text: str) -> tuple[Any, Node | None]:
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        data = loader.construct_document(node) if node is not None else None
    finally:
        loader.dispose()
    return data, node


def parse_proposal(text: str, *, conventions: Conventions | None = None, name: str = "the file") -> ProposalFile:
    """Parse and validate a `dentalsim.library/1` document; raises `Refused` listing every problem."""
    try:
        data, root = _compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        raise _refused(name, [_problem("invalid_yaml", f"not valid YAML: {exc}", line=line)]) from exc
    if not isinstance(data, dict):
        raise _refused(
            name, [_problem("invalid_yaml", "the file must be a YAML mapping with format, entries, sources")]
        )
    lines = _Lines(root)
    problems: list[Problem] = []

    header: _Header | None = None
    try:
        header = _Header.model_validate({k: v for k, v in data.items() if k not in ("entries", "sources")})
    except ValidationError as exc:
        problems += _validation_problems(exc, path=(), lines=lines)

    def items(section: str) -> list[Any]:
        raw = data.get(section)
        if raw is None:
            message = f"the required field '{section}' is missing"
            problems.append(_problem("missing_field", message, field=section, line=lines(())))
            return []
        if not isinstance(raw, list):
            problems.append(
                _problem("invalid_value", f"'{section}' must be a list", field=section, line=lines((section,)))
            )
            return []
        return raw

    raw_entries, raw_sources = items("entries"), items("sources")
    sources: list[tuple[YamlPath, ProposalSource]] = []
    for index, raw in enumerate(raw_sources):
        sid = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None
        try:
            sources.append((("sources", index), ProposalSource.model_validate(raw)))
        except ValidationError as exc:
            label = sid or f"#{index + 1}"
            problems += _validation_problems(exc, path=("sources", index), lines=lines, source=label)
    entries: list[tuple[YamlPath, ProposalEntry]] = []
    for index, raw in enumerate(raw_entries):
        key = raw.get("parameter_key") if isinstance(raw, dict) else None
        try:
            entries.append((("entries", index), ProposalEntry.model_validate(raw)))
        except ValidationError as exc:
            label = key if isinstance(key, str) else f"#{index + 1}"
            problems += _validation_problems(exc, path=("entries", index), lines=lines, entry=label)

    # Sources that failed validation are reported already; citing them is not a second problem.
    known = {raw["id"] for raw in raw_sources if isinstance(raw, dict) and isinstance(raw.get("id"), str)}
    # An invalid header field is reported already; the remaining header rules still apply to the fields as given.
    given = _header_fields(header) if header is not None else {name: data.get(name) for name in _Header.model_fields}
    problems += _header_problems(given, entries, lines)
    problems += _source_problems(sources, lines)
    problems += _entry_problems(entries, known, conventions or get_conventions(), lines)
    if problems or header is None:
        raise _refused(name, problems)
    return ProposalFile(**header.model_dump(), entries=[e for _, e in entries], sources=[s for _, s in sources])


def load_proposal(path: Path, *, conventions: Conventions | None = None) -> ProposalFile:
    """Read and validate a proposal or snapshot file."""
    return parse_proposal(path.read_text(encoding="utf-8-sig"), conventions=conventions, name=path.name)


# Writing -------------------------------------------------------------------------------------------------------


def _plain(value: float) -> int | float:
    """690.0 is written as 690, so snapshots read like the proposals they came from."""
    return int(value) if float(value).is_integer() and abs(value) < 1e15 else value


def _entry_dict(item: SnapshotItem) -> dict[str, Any]:
    entry, version = item.entry, item.version
    if version.status != "approved" or version.approved_at is None:
        raise ValueError(f"{entry.parameter_key} version {version.version} is {version.status}, not approved")
    values = []
    for value in version.values_by_source:
        row: dict[str, Any] = {"source": value.source_id, "value": _plain(value.value)}
        if value.note is not None:
            row["note"] = value.note
        values.append(row)
    data: dict[str, Any] = {
        "parameter_key": entry.parameter_key,
        "category": entry.category,
        "label": entry.label,
        "units": version.units,
        "typical": _plain(version.typical),
        "range": {"min": _plain(version.range_min), "max": _plain(version.range_max)},
        "values_by_source": values,
    }
    if version.notes is not None:
        data["notes"] = version.notes
    data["version"] = version.version
    data["content_hash"] = version.content_hash
    data["approved_at"] = version.approved_at.isoformat()
    return data


def _source_dict(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "authors": list(source.authors),
        "year": source.year,
        "title": source.title,
        "journal": source.journal,
        "doi": source.doi,
        "pmid": source.pmid,
        "study_type": source.study_type,
    }


def snapshot_text(items: Iterable[SnapshotItem], *, exported_at: datetime) -> str:
    """The snapshot document: entries sorted by parameter key, sources by id."""
    ordered = sorted(items, key=lambda item: item.entry.parameter_key)
    sources = {s.id: s for item in ordered for s in item.sources}
    document = {
        "format": FORMAT,
        "snapshot_of": "approved",
        "exported_at": exported_at.isoformat(),
        "entries": [_entry_dict(item) for item in ordered],
        "sources": [_source_dict(sources[sid]) for sid in sorted(sources)],
    }
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120, default_flow_style=False)
    return SNAPSHOT_HEADER + body


def write_snapshot(items: Iterable[SnapshotItem], path: Path, *, exported_at: datetime | None = None) -> Path:
    """Write the approved versions to `path` (LF line endings, UTF-8) and return it."""
    text = snapshot_text(items, exported_at=exported_at or utc_now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
