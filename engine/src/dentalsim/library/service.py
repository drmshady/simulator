"""The literature library's rules (FR-012 to FR-016, FR-053 to FR-055, research R13), shared by the studio and the
command line.

- **Import** (FR-053): bulk files and owner entries become `proposed` versions; a key that exists gets a new
  version. Sources are matched to stored ones by DOI or PMID. Nothing is checked online during import.
- **Approve** (FR-054): only a proposed version whose every cited source has a stored `resolved_match` check. The
  previously approved version of the entry is superseded.
- **Reject** (FR-055): a proposed or approved version; the runs that used it are flagged through `on_rejected`.
- **Edit** (FR-016): a new proposed version from the newest one; approving it supersedes the old one.
- **Resolve** (FR-014, FR-015): only approved versions give values: the typical value, one source's value, or a
  custom value (outside the range it sets `outside_range`).

The service works only through `repos.library` and `repos.transaction()`, never SQL.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dentalsim.clock import new_ulid, utc_now
from dentalsim.config import get_settings
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions, LibraryCategory
from dentalsim.errors import DentalsimError, NotFound, Refused
from dentalsim.library.models import (
    EntryStatus,
    LiteratureEntry,
    LiteratureEntryVersion,
    Origin,
    Source,
    SourceCheck,
    ValueBySource,
    version_content_hash,
)
from dentalsim.library.source_check import check_source
from dentalsim.library.yaml_io import (
    FORMAT,
    Problem,
    ProposalEntry,
    ProposalFile,
    ProposalSource,
    SnapshotItem,
    load_proposal,
    parse_proposal,
    proposal_problems,
    range_problems,
    units_problem,
    write_snapshot,
)
from dentalsim.model.run import LibraryVersionRef
from dentalsim.model.settings import CHOICE, LibraryValue
from dentalsim.persistence import Repos

SNAPSHOT_PATH = Path("content") / "library" / "snapshots" / "library-snapshot.yaml"
RUN_FLAGGING_UNAVAILABLE = "run flagging not available yet"

Checker = Callable[[Source], SourceCheck]
RejectionHook = Callable[[str, int], list[str]]
"""Called with (entry_id, version) when a version is rejected; flags the runs that used it and returns their ids.
Raises NotImplementedError while there is no run repository."""

CHECK_TEXT = {
    "not_checked": "has not been checked (run the source check; it needs the internet)",
    "unresolved": "does not resolve to any paper",
    "resolved_mismatch": "resolves to a paper that does not match the citation",
    "resolved_match": "matches",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportedVersion(_Model):
    entry_id: str
    parameter_key: str
    version: int
    new_entry: bool
    content_hash: str


class ImportResult(_Model):
    origin: Origin
    imported: list[ImportedVersion]
    unchanged: list[ImportedVersion] = []
    """Entries whose values equal an existing proposed or approved version; no version was added for them."""
    source_ids: dict[str, str]
    """File-local source id to stored source id."""
    new_sources: list[str] = []
    warnings: list[str] = []


class ApprovalResult(_Model):
    version: LiteratureEntryVersion
    superseded: list[int] = []


class RejectResult(_Model):
    version: LiteratureEntryVersion
    was_current: bool
    flagged_runs: list[str] = []
    run_flagging: Literal["done", "not_available"] = "done"
    note: str | None = None


class EntryChanges(_Model):
    """What an edit changes (FR-016). Fields left out keep the newest version's value; `notes: null` clears notes.

    `values_by_source` replaces the whole list and may cite stored sources only; a new paper is added as an owner
    entry for the same parameter key.
    """

    units: str | None = None
    typical: float | None = None
    range_min: float | None = None
    range_max: float | None = None
    values_by_source: list[ValueBySource] | None = Field(default=None, min_length=1)
    notes: str | None = None


class ResolvedValue(_Model):
    """A value taken from an approved version (FR-014), as a scenario's library setting records it."""

    entry_id: str
    parameter_key: str
    category: LibraryCategory
    version: int
    content_hash: str
    units: str
    choice: str
    value: float
    outside_range: bool
    typical: float
    range_min: float
    range_max: float

    def library_value(self) -> LibraryValue:
        return LibraryValue(
            entry_id=self.entry_id,
            entry_version=self.version,
            choice=self.choice,
            value=self.value,
            outside_range=self.outside_range,
        )

    def version_ref(self) -> LibraryVersionRef:
        return LibraryVersionRef(
            entry_id=self.entry_id,
            parameter_key=self.parameter_key,
            version=self.version,
            content_hash=self.content_hash,
        )


class EntryDetail(_Model):
    entry: LiteratureEntry
    versions: list[LiteratureEntryVersion]
    sources: list[Source]
    """Every source cited by any version, in order of first citation."""


def default_snapshot_path() -> Path:
    root = get_settings().repo_root
    if root is None:
        raise DentalsimError("cannot find the repository root (docs/conventions.md) to write the library snapshot")
    return root / SNAPSHOT_PATH


def cite(source: Source) -> str:
    """ "Surname AB et al. 2000 (doi 10.x/y)", for messages."""
    who = source.authors[0] + (" et al." if len(source.authors) > 1 else "")
    ident = f"doi {source.doi}" if source.doi else f"pmid {source.pmid}"
    return f"{who} {source.year} ({ident})"


def _refused(message: str, problems: Sequence[Problem] = ()) -> Refused:
    return Refused(message, code="validation_failed", problems=problems)


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


class LibraryService:
    def __init__(
        self,
        repos: Repos,
        *,
        checker: Checker = check_source,
        on_rejected: RejectionHook | None = None,
        conventions: Conventions | None = None,
    ) -> None:
        self.repos = repos
        self.checker = checker
        self.on_rejected = on_rejected or self._flag_runs_using
        self._conventions = conventions

    @property
    def conventions(self) -> Conventions:
        if self._conventions is None:
            self._conventions = get_conventions()
        return self._conventions

    # Reading ----------------------------------------------------------------------------------------------------

    def find_entry(self, ref: str) -> LiteratureEntry:
        """The entry with this id or parameter key."""
        entry = self.repos.library.get_entry(ref) or self.repos.library.get_entry_by_key(ref)
        if entry is None:
            raise NotFound(f"no library entry with id or parameter key {ref!r}")
        return entry

    def get_entry(self, entry_id: str) -> LiteratureEntry:
        entry = self.repos.library.get_entry(entry_id)
        if entry is None:
            raise NotFound(f"no library entry {entry_id}")
        return entry

    def get_version(self, entry_id: str, version: int) -> LiteratureEntryVersion:
        found = self.repos.library.get_version(entry_id, version)
        if found is None:
            raise NotFound(f"library entry {entry_id} has no version {version}")
        return found

    def newest_version(self, entry_id: str) -> LiteratureEntryVersion:
        versions = self.repos.library.list_versions(entry_id)
        if not versions:
            raise NotFound(f"library entry {entry_id} has no versions")
        return max(versions, key=lambda v: v.version)

    def list_entries(
        self, *, status: EntryStatus | None = None, category: str | None = None, text: str | None = None
    ) -> list[LiteratureEntry]:
        return self.repos.library.list_entries(status=status, category=category, text=text)

    def cited_sources(self, version: LiteratureEntryVersion) -> list[Source]:
        sources = []
        for item in version.values_by_source:
            source = self.repos.library.get_source(item.source_id)
            if source is None:
                raise NotFound(f"source {item.source_id} cited by {version.entry_id} v{version.version} is missing")
            sources.append(source)
        return sources

    def describe(self, entry_id: str) -> EntryDetail:
        entry = self.get_entry(entry_id)
        versions = self.repos.library.list_versions(entry_id)
        sources: dict[str, Source] = {}
        for version in versions:
            for source in self.cited_sources(version):
                sources.setdefault(source.id, source)
        return EntryDetail(entry=entry, versions=versions, sources=list(sources.values()))

    # Import (FR-053) --------------------------------------------------------------------------------------------

    def import_file(self, source: Path | str | ProposalFile, *, origin: Origin | None = None) -> ImportResult:
        """Validate a `dentalsim.library/1` file and store its entries as proposed versions, all or nothing."""
        if isinstance(source, ProposalFile):
            return self._import(source, origin=origin, name="the proposal")
        path = Path(source)
        return self._import(load_proposal(path, conventions=self.conventions), origin=origin, name=path.name)

    def import_text(self, text: str, *, origin: Origin | None = None, name: str = "the upload") -> ImportResult:
        """As `import_file`, for a document already read, such as a studio upload."""
        return self._import(parse_proposal(text, conventions=self.conventions, name=name), origin=origin, name=name)

    def add_owner_entry(self, entry: ProposalEntry, sources: Sequence[ProposalSource]) -> ImportedVersion:
        """One entry typed in by the owner from a paper (FR-053): the same rules as a bulk import, origin `owner`.

        `entry.values_by_source` cites `sources` by their `id`. A key that exists gets a new version.
        """
        proposal = ProposalFile(
            format=FORMAT,
            prepared_by="owner",
            prepared_at=utc_now().date(),
            entries=[entry],
            sources=list(sources),
        )
        result = self._import(proposal, origin="owner", name="the entry")
        if result.imported:
            return result.imported[0]
        raise _refused(f"{entry.parameter_key}: the values equal version {result.unchanged[0].version}; nothing to add")

    def _import(self, proposal: ProposalFile, *, origin: Origin | None, name: str) -> ImportResult:
        problems = proposal_problems(proposal, conventions=self.conventions)
        if problems:
            count = f"{len(problems)} problem" + ("" if len(problems) == 1 else "s")
            raise _refused(f"{name} does not meet the library import rules ({count})", problems)
        origin = origin or ("claude_proposal" if proposal.prepared_by == "claude" else "owner")
        library = self.repos.library
        now = utc_now()
        with self.repos.transaction():
            existing = {e.parameter_key: library.get_entry_by_key(e.parameter_key) for e in proposal.entries}
            for item in proposal.entries:
                stored = existing[item.parameter_key]
                if stored is not None and stored.category != item.category:
                    message = (
                        f"entry {item.parameter_key}: the stored entry has category {stored.category}, "
                        f"not {item.category}"
                    )
                    problems.append({"code": "category", "entry": item.parameter_key, "message": message})
            if problems:
                raise _refused(f"{name} conflicts with the stored library", problems)

            cited = {v.source for e in proposal.entries for v in e.values_by_source}
            warnings = [
                f"source {s.id} is not cited by any entry and was not imported"
                for s in proposal.sources
                if s.id not in cited
            ]
            id_map: dict[str, str] = {}
            stored_sources: dict[str, Source] = {}
            new_sources: list[str] = []
            for incoming in proposal.sources:
                if incoming.id not in cited:
                    continue
                source = library.find_source(doi=incoming.doi, pmid=incoming.pmid)
                if source is None:
                    source = Source(
                        id=new_ulid(),
                        authors=incoming.authors,
                        year=incoming.year,
                        title=incoming.title,
                        journal=incoming.journal,
                        doi=incoming.doi,
                        pmid=incoming.pmid,
                        study_type=incoming.study_type,
                    )
                    library.add_source(source)
                    new_sources.append(source.id)
                elif (source.year, source.title.casefold(), source.authors[0]) != (
                    incoming.year,
                    incoming.title.casefold(),
                    incoming.authors[0],
                ):
                    warnings.append(
                        f"source {incoming.id} has the DOI or PMID of the stored source {cite(source)}; the stored "
                        "source is used as it is"
                    )
                id_map[incoming.id] = source.id
                stored_sources[source.id] = source

            imported: list[ImportedVersion] = []
            unchanged: list[ImportedVersion] = []
            for item in proposal.entries:
                entry = existing[item.parameter_key]
                new_entry = entry is None
                if entry is None:
                    entry = LiteratureEntry(
                        id=new_ulid(),
                        parameter_key=item.parameter_key,
                        category=item.category,
                        label=item.label or item.parameter_key,
                        created_at=now,
                    )
                    library.add_entry(entry)
                values = [
                    ValueBySource(source_id=id_map[v.source], value=v.value, note=v.note) for v in item.values_by_source
                ]
                content_hash = version_content_hash(
                    parameter_key=entry.parameter_key,
                    category=entry.category,
                    units=item.units,
                    typical=item.typical,
                    range_min=item.range.min,
                    range_max=item.range.max,
                    values_by_source=values,
                    sources=[stored_sources[v.source_id] for v in values],
                    notes=item.notes,
                )
                versions = [] if new_entry else library.list_versions(entry.id)
                same = next(
                    (v for v in versions if v.content_hash == content_hash and v.status in ("proposed", "approved")),
                    None,
                )
                if same is not None:
                    unchanged.append(
                        ImportedVersion(
                            entry_id=entry.id,
                            parameter_key=entry.parameter_key,
                            version=same.version,
                            new_entry=False,
                            content_hash=content_hash,
                        )
                    )
                    continue
                version = LiteratureEntryVersion(
                    entry_id=entry.id,
                    version=max((v.version for v in versions), default=0) + 1,
                    units=item.units,
                    typical=item.typical,
                    range_min=item.range.min,
                    range_max=item.range.max,
                    values_by_source=values,
                    notes=item.notes,
                    status="proposed",
                    origin=origin,
                    proposed_at=now,
                    content_hash=content_hash,
                )
                library.add_version(version)
                imported.append(
                    ImportedVersion(
                        entry_id=entry.id,
                        parameter_key=entry.parameter_key,
                        version=version.version,
                        new_entry=new_entry,
                        content_hash=content_hash,
                    )
                )
        return ImportResult(
            origin=origin,
            imported=imported,
            unchanged=unchanged,
            source_ids=id_map,
            new_sources=new_sources,
            warnings=warnings,
        )

    # Source check and approval (FR-054) -------------------------------------------------------------------------

    def check_sources(self, entry_id: str, version: int) -> list[tuple[Source, SourceCheck]]:
        """Check every source the version cites online, store each result, and return them in citation order."""
        sources = self.cited_sources(self.get_version(entry_id, version))
        checked = [(source, self.checker(source)) for source in sources]  # no transaction held during network calls
        with self.repos.transaction():
            for source, check in checked:
                self.repos.library.update_source_check(source.id, check)
        return [(source.model_copy(update={"check": check}), check) for source, check in checked]

    def approval_problems(self, entry_id: str, version: int) -> list[Problem]:
        """Why the version cannot be approved now; empty when it can."""
        entry = self.get_entry(entry_id)
        found = self.get_version(entry_id, version)
        key = entry.parameter_key
        problems: list[Problem] = []
        if found.status != "proposed":
            message = f"{key} version {version} is {found.status}; only a proposed version can be approved"
            problems.append({"code": "not_proposed", "entry": key, "message": message})
        if entry.current_version is not None and entry.current_version > version:
            message = f"{key} version {entry.current_version} is approved already and is newer than version {version}"
            problems.append({"code": "newer_version_approved", "entry": key, "message": message})
        why = units_problem(entry.category, key, found.units, self.conventions)
        if why is not None:
            problems.append({"code": "units", "entry": key, "message": f"{key}: {why}"})
        for source in self.cited_sources(found):
            status = source.check.status
            if status == "resolved_match":
                continue
            detail = f": {source.check.detail}" if source.check.detail else ""
            problems.append(
                {
                    "code": "source_not_matched",
                    "entry": key,
                    "source": source.id,
                    "status": status,
                    "message": f"source {cite(source)} {CHECK_TEXT[status]}{detail}",
                }
            )
        return problems

    def approve(self, entry_id: str, version: int) -> ApprovalResult:
        """Approve a proposed version whose every source matched (FR-054); it becomes the entry's current version."""
        library = self.repos.library
        with self.repos.transaction():
            problems = self.approval_problems(entry_id, version)
            if problems:
                key = self.get_entry(entry_id).parameter_key
                raise _refused(f"{key} version {version} cannot be approved", problems)
            now = utc_now()
            superseded = []
            for other in library.list_versions(entry_id):
                if other.status == "approved" and other.version != version:
                    library.update_version_status(
                        other.model_copy(update={"status": "superseded", "superseded_at": now})
                    )
                    superseded.append(other.version)
            approved = self.get_version(entry_id, version).model_copy(update={"status": "approved", "approved_at": now})
            library.update_version_status(approved)
            library.set_current_version(entry_id, version)
        return ApprovalResult(version=approved, superseded=superseded)

    # Rejection (FR-055) -----------------------------------------------------------------------------------------

    def _flag_runs_using(self, entry_id: str, version: int) -> list[str]:
        """The default rejection hook: flag the runs that used the version `uses_rejected_entry` (task T109)."""
        runs = self.repos.runs  # raises NotImplementedError until the run repository exists (task T084)
        run_ids = runs.runs_using(entry_id, version)
        if run_ids:
            runs.add_flag(run_ids, "uses_rejected_entry")
        return run_ids

    def reject(self, entry_id: str, version: int, reason: str) -> RejectResult:
        """Reject a proposed or approved version. If it was current, the entry has no current version until another
        version is approved; an older version is never re-approved silently."""
        reason = reason.strip()
        if not reason:
            raise _refused("a rejection needs a reason")
        library = self.repos.library
        with self.repos.transaction():
            entry = self.get_entry(entry_id)
            found = self.get_version(entry_id, version)
            if found.status not in ("proposed", "approved"):
                raise _refused(
                    f"{entry.parameter_key} version {version} is {found.status}; only a proposed or approved version "
                    "can be rejected"
                )
            rejected = found.model_copy(
                update={"status": "rejected", "rejected_at": utc_now(), "rejected_reason": reason}
            )
            library.update_version_status(rejected)
            was_current = entry.current_version == version
            if was_current:
                library.set_current_version(entry_id, None)
            try:
                flagged = self.on_rejected(entry_id, version)
            except NotImplementedError:
                return RejectResult(
                    version=rejected,
                    was_current=was_current,
                    run_flagging="not_available",
                    note=RUN_FLAGGING_UNAVAILABLE,
                )
        return RejectResult(version=rejected, was_current=was_current, flagged_runs=flagged)

    # Editing (FR-016) -------------------------------------------------------------------------------------------

    def edit(self, entry_id: str, changes: EntryChanges | Mapping[str, Any]) -> LiteratureEntryVersion:
        """A new proposed version: the newest version with `changes` applied. The edited version is unchanged until
        the new one is approved, which supersedes it."""
        changes = changes if isinstance(changes, EntryChanges) else EntryChanges.model_validate(changes)
        library = self.repos.library
        with self.repos.transaction():
            entry = self.get_entry(entry_id)
            base = self.newest_version(entry_id)
            given = changes.model_fields_set
            units = changes.units.strip() if changes.units is not None else base.units
            typical = changes.typical if changes.typical is not None else base.typical
            range_min = changes.range_min if changes.range_min is not None else base.range_min
            range_max = changes.range_max if changes.range_max is not None else base.range_max
            values = changes.values_by_source if changes.values_by_source is not None else base.values_by_source
            notes = changes.notes if "notes" in given else base.notes

            key = entry.parameter_key
            problems: list[Problem] = []
            why = units_problem(entry.category, key, units, self.conventions)
            if why is not None:
                problems.append({"code": "units", "entry": key, "field": "units", "message": f"{key}: {why}"})
            pairs = [(v.source_id, v.value) for v in values]
            for code, field, message in range_problems(typical, range_min, range_max, pairs):
                problems.append({"code": code, "entry": key, "field": field, "message": f"{key}: {message}"})
            sources: dict[str, Source] = {}
            for item in values:
                source = library.get_source(item.source_id)
                if source is None:
                    message = f"{key}: the source {item.source_id} is not stored; add a new paper as an owner entry"
                    problems.append({"code": "unknown_source", "entry": key, "message": message})
                elif item.source_id in sources:
                    message = f"{key}: the source {cite(source)} is cited more than once"
                    problems.append({"code": "duplicate_source", "entry": key, "message": message})
                else:
                    sources[item.source_id] = source
            if problems:
                raise _refused(f"the edit of {key} is not valid", problems)
            content_hash = version_content_hash(
                parameter_key=key,
                category=entry.category,
                units=units,
                typical=typical,
                range_min=range_min,
                range_max=range_max,
                values_by_source=list(values),
                sources=list(sources.values()),
                notes=notes,
            )
            if content_hash == base.content_hash:
                raise _refused(f"the edit leaves {key} version {base.version} as it is")
            new = LiteratureEntryVersion(
                entry_id=entry_id,
                version=base.version + 1,
                units=units,
                typical=typical,
                range_min=range_min,
                range_max=range_max,
                values_by_source=list(values),
                notes=notes,
                status="proposed",
                origin="owner",
                proposed_at=utc_now(),
                content_hash=content_hash,
            )
            library.add_version(new)
        return new

    # Resolving values (FR-014, FR-015) ----------------------------------------------------------------------------

    def _approved(self, entry: LiteratureEntry, version: int) -> LiteratureEntryVersion:
        found = self.repos.library.get_version(entry.id, version)
        if found is None:
            raise _refused(f"{entry.parameter_key} has no version {version}")
        if found.status != "approved":
            current = f"; the approved version is {entry.current_version}" if entry.current_version else ""
            raise _refused(
                f"{entry.parameter_key} version {version} is {found.status}; only approved versions can be "
                f"chosen{current}"
            )
        return found

    def _resolved(
        self, entry: LiteratureEntry, version: LiteratureEntryVersion, choice: str, value: float
    ) -> ResolvedValue:
        return ResolvedValue(
            entry_id=entry.id,
            parameter_key=entry.parameter_key,
            category=entry.category,
            version=version.version,
            content_hash=version.content_hash,
            units=version.units,
            choice=choice,
            value=value,
            outside_range=not version.contains(value),
            typical=version.typical,
            range_min=version.range_min,
            range_max=version.range_max,
        )

    def resolve(
        self, parameter_key: str, choice: str = "typical", *, custom_value: float | None = None
    ) -> ResolvedValue:
        """The value of the entry's current approved version: `typical`, `source:<source id>` or `custom`."""
        if not CHOICE.match(choice):
            raise _refused(f"choice must be 'typical', 'source:<id>' or 'custom', not {choice!r}")
        if (choice == "custom") != (custom_value is not None):
            raise _refused("a custom value is given with the choice 'custom', and only then")
        entry = self.repos.library.get_entry_by_key(parameter_key)
        if entry is None:
            raise _refused(f"there is no library entry {parameter_key}")
        if entry.current_version is None:
            raise _refused(f"{parameter_key} has no approved version, so it cannot be chosen yet")
        version = self._approved(entry, entry.current_version)
        if choice == "typical":
            value = version.typical
        elif choice == "custom":
            assert custom_value is not None
            value = float(custom_value)
        else:
            source_id = choice.removeprefix("source:")
            found = version.source_value(source_id)
            if found is None:
                raise _refused(f"{parameter_key} version {version.version} cites no source {source_id}")
            value = found
        return self._resolved(entry, version, choice, value)

    def resolve_value(self, value: LibraryValue) -> ResolvedValue:
        """Check a stored library setting: it must point to an approved version and match its choice. The returned
        `outside_range` is recomputed from the version's range."""
        entry = self.repos.library.get_entry(value.entry_id)
        if entry is None:
            raise _refused(f"there is no library entry {value.entry_id}")
        version = self._approved(entry, value.entry_version)
        key = entry.parameter_key
        if value.choice == "typical":
            if not _same(value.value, version.typical):
                raise _refused(f"{key}: the value {value.value:g} is not the typical value {version.typical:g}")
        elif value.source_id is not None:
            expected = version.source_value(value.source_id)
            if expected is None:
                raise _refused(f"{key} version {version.version} cites no source {value.source_id}")
            if not _same(value.value, expected):
                raise _refused(f"{key}: the value {value.value:g} is not source {value.source_id}'s value {expected:g}")
        return self._resolved(entry, version, value.choice, value.value)

    # Snapshots --------------------------------------------------------------------------------------------------

    def snapshot_items(self) -> list[SnapshotItem]:
        """Every entry's current approved version, with its sources."""
        items = []
        for entry in self.repos.library.list_entries(status="approved"):
            if entry.current_version is None:
                continue
            version = self.repos.library.get_version(entry.id, entry.current_version)
            if version is None or version.status != "approved":
                continue
            items.append(SnapshotItem(entry=entry, version=version, sources=tuple(self.cited_sources(version))))
        return items

    def export_snapshot(self, path: Path | None = None) -> Path:
        """Write the approved library to `path` (default `content/library/snapshots/library-snapshot.yaml`)."""
        return write_snapshot(self.snapshot_items(), path or default_snapshot_path(), exported_at=utc_now())
