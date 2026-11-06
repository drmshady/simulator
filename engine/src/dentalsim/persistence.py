"""The storage interfaces engine modules depend on (research R12).

The engine never talks to SQLite directly. It uses these Protocols, and `open_repos(data_dir)` returns the studio's
SQLite implementation, imported lazily, so that the command line and the studio share one database
(`<data>/studio.db`) and the engine has no import-time dependency on the studio package.

Record types that have no other home in the engine (capability status, jobs, events, scenario records) are
defined here.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict

from dentalsim.library.models import EntryStatus, LiteratureEntry, LiteratureEntryVersion, Source, SourceCheck
from dentalsim.model.run import Run, RunFlag
from dentalsim.model.scenario import Scenario
from dentalsim.model.validity import ValidityResult

CapabilityState = Literal["awaiting_benchmark", "ready"]
JobLane = Literal["heavy", "preview"]
JobKind = Literal["run", "preview", "mesh_check", "verification", "export", "promo", "interactive_data"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityStatus(_Record):
    capability_id: str
    status: CapabilityState = "awaiting_benchmark"
    report_id: str | None = None
    """The verification report that made it ready, when it is ready."""
    updated_at: AwareDatetime | None = None


class JobRecord(_Record):
    id: str
    lane: JobLane
    kind: JobKind
    payload: dict[str, Any]
    target_id: str | None = None
    """The run, preview, export or other record the job works on."""
    position: int
    status: JobStatus = "queued"
    pid: int | None = None
    heartbeat_at: AwareDatetime | None = None
    attempts: int = 0
    failure_reason: str | None = None
    cancel_requested: bool = False
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class EventRecord(_Record):
    id: int
    type: str
    payload: dict[str, Any]
    created_at: AwareDatetime


class DerivedFrom(_Record):
    kind: Literal["template", "scenario"]
    id: str


class ScenarioRecord(_Record):
    id: str
    project_id: str
    name: str
    notes: str = ""
    tags: list[str] = []
    derived_from: DerivedFrom | None = None
    revision: int = 1
    scenario: Scenario
    validity: ValidityResult | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EventSink(Protocol):
    def emit(self, type: str, payload: Mapping[str, Any]) -> int:
        """Append an event and return its id."""
        ...


class EventRepo(EventSink, Protocol):
    def since(self, after_id: int, *, limit: int = 100) -> list[EventRecord]: ...

    def latest_id(self) -> int: ...


class SettingsRepo(Protocol):
    """Key-value settings: the owner's password hash, the last complete `dentalsim check` pass, and so on."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...

    def delete(self, key: str) -> None: ...


class CapabilityRepo(Protocol):
    def get(self, capability_id: str) -> CapabilityStatus | None: ...

    def set(self, status: CapabilityStatus) -> None: ...

    def list_all(self) -> list[CapabilityStatus]: ...


class LibraryRepo(Protocol):
    def add_source(self, source: Source) -> None: ...

    def get_source(self, source_id: str) -> Source | None: ...

    def find_source(self, *, doi: str | None = None, pmid: str | None = None) -> Source | None:
        """The stored source with this DOI (compared case-insensitively) or PMID."""
        ...

    def update_source_check(self, source_id: str, check: SourceCheck) -> None: ...

    def add_entry(self, entry: LiteratureEntry) -> None: ...

    def get_entry(self, entry_id: str) -> LiteratureEntry | None: ...

    def get_entry_by_key(self, parameter_key: str) -> LiteratureEntry | None: ...

    def set_current_version(self, entry_id: str, version: int | None) -> None: ...

    def list_entries(
        self, *, status: EntryStatus | None = None, category: str | None = None, text: str | None = None
    ) -> list[LiteratureEntry]:
        """Entries with at least one version of `status`, in `category`, whose key or label contains `text`."""
        ...

    def add_version(self, version: LiteratureEntryVersion) -> None:
        """Stores the version and its links to the cited sources (`values_by_source`)."""
        ...

    def get_version(self, entry_id: str, version: int) -> LiteratureEntryVersion | None: ...

    def list_versions(self, entry_id: str) -> list[LiteratureEntryVersion]: ...

    def update_version_status(self, version: LiteratureEntryVersion) -> None:
        """Stores a status change (status, its dates and the rejection reason). Values never change."""
        ...


class ScenarioRepo(Protocol):
    """Extended with projects, owner templates and search in tasks T084 and T114."""

    def get(self, scenario_id: str) -> ScenarioRecord | None: ...

    def add(self, record: ScenarioRecord) -> None: ...

    def save(self, record: ScenarioRecord, *, expected_revision: int) -> ScenarioRecord:
        """Stores the record with revision + 1; raises `Conflict` when the stored revision is not the expected one."""
        ...


class RunRepo(Protocol):
    def add(self, run: Run) -> None: ...

    def get(self, run_id: str) -> Run | None: ...

    def update(self, run: Run) -> None: ...

    def list_for_scenario(self, scenario_id: str) -> list[Run]: ...

    def runs_using(self, entry_id: str, version: int) -> list[str]:
        """Ids of the runs that used this library entry version (FR-055)."""
        ...

    def add_flag(self, run_ids: Sequence[str], flag: RunFlag) -> None: ...


class JobRepo(Protocol):
    def enqueue(
        self,
        kind: JobKind,
        lane: JobLane,
        payload: Mapping[str, Any],
        *,
        target_id: str | None = None,
        front: bool = False,
    ) -> JobRecord: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def list_jobs(self, *, lane: JobLane | None = None, statuses: Sequence[JobStatus] | None = None) -> list[JobRecord]:
        """Jobs in queue order (by lane, then position)."""
        ...

    def claim(self, lane: JobLane) -> JobRecord | None:
        """Atomically mark the first queued job of the lane running and return it."""
        ...

    def heartbeat(self, job_id: str, *, pid: int | None = None) -> None: ...

    def finish(self, job_id: str, status: JobStatus, *, failure_reason: str | None = None) -> None: ...

    def request_cancel(self, job_id: str) -> JobRecord: ...

    def requeue_interrupted(self) -> list[JobRecord]:
        """At start-up: jobs left running become interrupted and go back to the front of their lane (FR-056)."""
        ...


class VerificationRepo(Protocol):
    """Verification reports as JSON documents; typed by the verification module in task T071."""

    def add_report(self, report: Mapping[str, Any]) -> str: ...

    def get_report(self, report_id: str) -> dict[str, Any] | None: ...

    def latest_report(self) -> dict[str, Any] | None: ...


class PublicationRepo(Protocol):
    """Publication records and published versions; extended in task T150."""

    def get_record(self, scenario_id: str) -> dict[str, Any] | None: ...

    def put_record(self, scenario_id: str, record: Mapping[str, Any]) -> None: ...


class Repos(Protocol):
    """One connection's worth of repositories. Use one `Repos` per thread, and close it when done."""

    @property
    def library(self) -> LibraryRepo: ...

    @property
    def capabilities(self) -> CapabilityRepo: ...

    @property
    def scenarios(self) -> ScenarioRepo: ...

    @property
    def runs(self) -> RunRepo: ...

    @property
    def jobs(self) -> JobRepo: ...

    @property
    def verification(self) -> VerificationRepo: ...

    @property
    def publications(self) -> PublicationRepo: ...

    @property
    def events(self) -> EventRepo: ...

    @property
    def settings(self) -> SettingsRepo: ...

    def transaction(self) -> AbstractContextManager[None]:
        """Everything inside commits together or not at all. Nested use joins the outer transaction."""
        ...

    def close(self) -> None: ...


STUDIO_DB_MODULE = "dentalsim_studio.db"


def open_repos(data_dir: Path | None = None) -> Repos:
    """The studio database in `data_dir` (default: the configured data folder), migrated to the current schema."""
    if data_dir is None:
        from dentalsim.config import ensure_data_folder

        data_dir = ensure_data_folder()
    module = importlib.import_module(STUDIO_DB_MODULE)
    return cast(Repos, module.open_repos(data_dir))


@contextmanager
def repos_session(data_dir: Path | None = None) -> Iterator[Repos]:
    """`with repos_session() as repos: ...` opens the database and closes it afterwards."""
    repos = open_repos(data_dir)
    try:
        yield repos
    finally:
        repos.close()
