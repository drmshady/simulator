"""The literature library (contracts/studio-api.md "Literature library"; FR-012 to FR-016, FR-053 to FR-055).

Every route goes through `dentalsim.library.service`, so the studio applies the same rules as `dentalsim library`:

- **Read**: list (status, category and text filters) and one entry with every version, every source and, per
  version, whether it can be approved now and how many runs used it.
- **Add**: an owner entry from a paper (FR-053) and a bulk YAML import (multipart); both arrive `proposed`.
- **Review**: check a version's sources at Crossref and NCBI (FR-054), approve, or reject with a reason, which
  flags the runs that used it (FR-055).
- **Edit**: `PUT` creates a new proposed version (FR-016); the edited version is unchanged.
- **Export**: the approved library as a snapshot in `content/library/snapshots/`.

`library_uses` gives a run record's library versions for the results page (task T112).
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dentalsim.library.models import EntryStatus, LiteratureEntry, LiteratureEntryVersion, Source, SourceCheck
from dentalsim.library.service import (
    ApprovalResult,
    EntryChanges,
    EntryDetail,
    ImportedVersion,
    ImportResult,
    LibraryService,
    RejectResult,
    cite,
)
from dentalsim.library.yaml_io import ProposalEntry, ProposalSource
from dentalsim.model.run import Run
from dentalsim.model.settings import LibraryValue
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.api.errors import ApiError, ErrorItem
from dentalsim_studio.db import SqliteRepos

router = APIRouter(prefix="/api/library", tags=["library"])

MAX_IMPORT_BYTES = 2_000_000


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionState(BaseModel):
    """What the owner may do with one version now."""

    version: int
    approvable: bool
    problems: list[ErrorItem]
    """Why it cannot be approved now; empty when it can, and for a version that is no longer proposed."""
    runs_using: int
    """Runs that used the version; rejecting it flags them (FR-055)."""


class EntryView(BaseModel):
    entry: LiteratureEntry
    versions: list[LiteratureEntryVersion]
    sources: list[Source]
    """Every source cited by any version, with its latest check result."""
    states: list[VersionState]


class OwnerEntry(_Body):
    """One paper's values typed in by the owner (FR-053). `entry.values_by_source` cites `sources` by `id`."""

    entry: ProposalEntry
    sources: list[ProposalSource] = Field(min_length=1)


class CheckedSource(BaseModel):
    source: Source
    check: SourceCheck


class SourceCheckView(BaseModel):
    entry_id: str
    version: int
    sources: list[CheckedSource]
    approvable: bool
    problems: list[ErrorItem]


class RejectBody(_Body):
    reason: str = Field(min_length=1)


class EditBody(_Body):
    changes: EntryChanges


class SnapshotWritten(BaseModel):
    path: str
    entries: int


def _view(service: LibraryService, repos: SqliteRepos, detail: EntryDetail) -> EntryView:
    states = []
    for version in detail.versions:
        problems = service.approval_problems(detail.entry.id, version.version) if version.status == "proposed" else []
        states.append(
            VersionState(
                version=version.version,
                approvable=version.status == "proposed" and not problems,
                problems=[ErrorItem.model_validate(p) for p in problems],
                runs_using=len(repos.runs.runs_using(detail.entry.id, version.version)),
            )
        )
    return EntryView(entry=detail.entry, versions=detail.versions, sources=detail.sources, states=states)


@router.get("/entries", response_model=list[LiteratureEntry])
def list_entries(
    repos: ReposDep, status: EntryStatus | None = None, category: str | None = None, q: str | None = None
) -> list[LiteratureEntry]:
    return LibraryService(repos).list_entries(status=status, category=category, text=q)


@router.get("/entries/{entry_ref}", response_model=EntryView)
def get_entry(entry_ref: str, repos: ReposDep) -> EntryView:
    """The entry with every version and source; `entry_ref` is an entry id or a parameter key."""
    service = LibraryService(repos)
    return _view(service, repos, service.describe(service.find_entry(entry_ref).id))


@router.post("/entries", response_model=ImportedVersion, status_code=201)
def add_entry(body: OwnerEntry, repos: ReposDep) -> ImportedVersion:
    """The owner's entry is stored as `proposed`; a parameter key that exists gets a new version."""
    return LibraryService(repos).add_owner_entry(body.entry, body.sources)


@router.post("/import", response_model=ImportResult)
async def import_proposals(file: UploadFile, repos: ReposDep) -> ImportResult:
    """A `dentalsim.library/1` file (contracts/library-entry-format.md); every entry arrives `proposed`."""
    data = await file.read(MAX_IMPORT_BYTES + 1)
    name = file.filename or "the upload"
    if len(data) > MAX_IMPORT_BYTES:
        raise ApiError(422, "validation_failed", f"{name} is larger than {MAX_IMPORT_BYTES // 1_000_000} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(422, "validation_failed", f"{name} is not a UTF-8 text file") from None
    return LibraryService(repos).import_text(text, name=name)


@router.post("/entries/{entry_id}/versions/{version}/check-sources", response_model=SourceCheckView)
def check_sources(entry_id: str, version: int, repos: ReposDep) -> SourceCheckView:
    """Checks each cited DOI or PMID at Crossref or NCBI and stores the result (FR-054). Needs the internet."""
    service = LibraryService(repos)
    entry = service.find_entry(entry_id)
    checked = service.check_sources(entry.id, version)
    problems = service.approval_problems(entry.id, version)
    return SourceCheckView(
        entry_id=entry.id,
        version=version,
        sources=[CheckedSource(source=s, check=c) for s, c in checked],
        approvable=not problems,
        problems=[ErrorItem.model_validate(p) for p in problems],
    )


@router.post("/entries/{entry_id}/versions/{version}/approve", response_model=ApprovalResult)
def approve(entry_id: str, version: int, repos: ReposDep) -> ApprovalResult:
    """Refused (`validation_failed`, one problem per reason) unless every source is `resolved_match`."""
    service = LibraryService(repos)
    return service.approve(service.find_entry(entry_id).id, version)


@router.post("/entries/{entry_id}/versions/{version}/reject", response_model=RejectResult)
def reject(entry_id: str, version: int, body: RejectBody, repos: ReposDep) -> RejectResult:
    """Rejects a proposed or approved version and flags the runs that used it `uses_rejected_entry` (FR-055)."""
    service = LibraryService(repos)
    return service.reject(service.find_entry(entry_id).id, version, body.reason)


@router.put("/entries/{entry_id}", response_model=LiteratureEntryVersion, status_code=201)
def edit(entry_id: str, body: EditBody, repos: ReposDep) -> LiteratureEntryVersion:
    """A new proposed version from the newest one with `changes` applied (FR-016)."""
    service = LibraryService(repos)
    return service.edit(service.find_entry(entry_id).id, body.changes)


@router.post("/export", response_model=SnapshotWritten)
def export(repos: ReposDep) -> SnapshotWritten:
    service = LibraryService(repos)
    count = len(service.snapshot_items())
    return SnapshotWritten(path=str(service.export_snapshot()), entries=count)


# A run's library versions (task T112) -----------------------------------------------------------------------------


class SourceValue(BaseModel):
    source_id: str
    citation: str
    value: float
    chosen: bool


class LibraryUse(BaseModel):
    """One library version a run used, as the run recorded it, with what the library holds now."""

    entry_id: str
    parameter_key: str
    label: str
    version: int
    status: EntryStatus | None
    """The version's status now; None if it is no longer in the library."""
    units: str | None
    used_in: list[str]
    """Where the run used it: `<block>.<setting>`, `load <id> force`, or "Frost window edge"."""
    values: list[float]
    choice: str
    sources: list[SourceValue]
    newer_version: int | None
    """The entry's current approved version when it is newer than the one used."""


def _uses_in_snapshot(run: Run) -> dict[tuple[str, int], list[tuple[str, LibraryValue]]]:
    found: dict[tuple[str, int], list[tuple[str, LibraryValue]]] = {}
    snapshot = run.snapshot
    for block in snapshot.blocks:
        for name, setting in block.settings.items():
            if isinstance(setting, LibraryValue):
                found.setdefault((setting.entry_id, setting.entry_version), []).append(
                    (f"{block.instance_id}.{name}", setting)
                )
    for load in snapshot.loads:
        pairs = [("force", load.force)] + ([("cycles", load.cycles)] if load.cycles is not None else [])
        for what, setting in pairs:
            found.setdefault((setting.entry_id, setting.entry_version), []).append((f"load {load.id} {what}", setting))
    return found


def library_uses(run: Run, repos: SqliteRepos) -> list[LibraryUse]:
    """Each library version in the run record: entry, version, value and sources, and whether a newer approved
    version exists (Story 2, acceptance 3). The run keeps the version it used whatever happened since."""
    in_snapshot = _uses_in_snapshot(run)
    uses = []
    for ref in run.library_versions:
        entry = repos.library.get_entry(ref.entry_id)
        version = repos.library.get_version(ref.entry_id, ref.version)
        places = in_snapshot.get((ref.entry_id, ref.version), [])
        settings = [setting for _, setting in places]
        values = [s.value for s in settings] or ([version.typical] if version is not None else [])
        choices = {s.choice for s in settings} or {"typical"}
        sources = []
        if version is not None:
            chosen = {s.source_id for s in settings if s.source_id is not None}
            for item in version.values_by_source:
                source = repos.library.get_source(item.source_id)
                sources.append(
                    SourceValue(
                        source_id=item.source_id,
                        citation=cite(source) if source is not None else item.source_id,
                        value=item.value,
                        chosen=item.source_id in chosen,
                    )
                )
        current = entry.current_version if entry is not None else None
        uses.append(
            LibraryUse(
                entry_id=ref.entry_id,
                parameter_key=ref.parameter_key,
                label=entry.label if entry is not None else ref.parameter_key,
                version=ref.version,
                status=version.status if version is not None else None,
                units=version.units if version is not None else None,
                used_in=[where for where, _ in places] or ["Frost window edge"],
                values=values,
                choice=", ".join(sorted(choices)),
                sources=sources,
                newer_version=current if current is not None and current > ref.version else None,
            )
        )
    return uses
