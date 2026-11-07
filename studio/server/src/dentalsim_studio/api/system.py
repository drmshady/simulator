"""`GET /api/system`, backup and restore, and `GET /api/conventions` (contracts/studio-api.md "Auth and system" and
"Catalogue").

`/api/system` feeds the interface's system bar: engine version and development-build flag (research R14), solver
build id, conventions version, memory and free disk on the main PC, the queue summary, and the last backup and
restore. A missing solver never fails it: `solver_build_id` is then null and `solver_error` says why.

`POST /api/system/backup {to?}` and `POST /api/system/restore {archive}` queue a `backup` or `restore` job in the
heavy lane (task T121, FR-034); the paths are folders and files on the main PC.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import psutil
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from dentalsim.backup import LAST_BACKUP, LAST_RESTORE, BackupResult, RestoreResult
from dentalsim.conventions.loader import LoadedConventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import Refused, SolverNotFound
from dentalsim.persistence import Repos
from dentalsim.version import version_info
from dentalsim_studio.api.deps import ReposDep, data_dir_of
from dentalsim_studio.jobs import queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])

GB = 1024**3
SOLVER_BUILD_MODULE = "dentalsim.solvers.calculix.build"


class MemoryInfo(BaseModel):
    total_gb: float
    available_gb: float
    used_percent: float


class DiskInfo(BaseModel):
    path: str
    total_gb: float
    free_gb: float


class LaneCounts(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    queued: int = 0
    running: int = 0


class QueueSummary(BaseModel):
    heavy: LaneCounts
    preview: LaneCounts


class SystemInfo(BaseModel):
    engine_version: str
    dev_build: bool
    engine_source_hash: str
    gmsh_version: str
    solver_build_id: str | None
    solver_error: str | None
    conventions_version: str
    conventions_hash: str
    memory: MemoryInfo
    disk: DiskInfo
    queue: QueueSummary
    last_backup: BackupResult | None = None
    last_restore: RestoreResult | None = None


class BackupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = None
    """A folder on the main PC, such as an external drive; default: the data folder's backups folder."""


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive: str
    """A backup archive on the main PC."""


class JobQueued(BaseModel):
    job_id: str
    status: str


class ConventionsInfo(Conventions):
    """The parsed conventions block, with the content hash that runs and packages record."""

    content_hash: str


def lookup_solver_build_id() -> str:
    """The configured solver's build id; raises `SolverNotFound` when no solver is configured."""
    module = importlib.import_module(SOLVER_BUILD_MODULE)
    return str(module.solver_build().build_id)


def solver_status() -> tuple[str | None, str | None]:
    """`(build id, None)`, or `(None, plain reason)` when the solver is missing or cannot be checked."""
    try:
        return lookup_solver_build_id(), None
    except SolverNotFound as exc:
        return None, str(exc) or "No CalculiX solver is configured: set DENTALSIM_CCX."
    except Exception as exc:
        logger.warning("could not check the solver build", exc_info=exc)
        return None, f"The solver could not be checked: {exc or type(exc).__name__}"


def memory_info() -> MemoryInfo:
    memory = psutil.virtual_memory()
    return MemoryInfo(
        total_gb=round(memory.total / GB, 1),
        available_gb=round(memory.available / GB, 1),
        used_percent=round(memory.percent, 1),
    )


def disk_info(path: Path) -> DiskInfo:
    usage = psutil.disk_usage(str(path))
    return DiskInfo(path=str(path), total_gb=round(usage.total / GB, 1), free_gb=round(usage.free / GB, 1))


def queue_summary(repos: Repos) -> QueueSummary:
    return QueueSummary.model_validate(queue.queue_summary(repos))


def loaded_conventions(request: Request) -> LoadedConventions:
    loaded: LoadedConventions = request.app.state.conventions
    return loaded


@router.get("/system", response_model=SystemInfo)
def system(request: Request, repos: ReposDep) -> SystemInfo:
    versions = version_info()
    solver_build_id, solver_error = solver_status()
    conventions = loaded_conventions(request)
    return SystemInfo(
        engine_version=versions.engine_version,
        dev_build=versions.dev_build,
        engine_source_hash=versions.engine_source_hash,
        gmsh_version=versions.gmsh_version,
        solver_build_id=solver_build_id,
        solver_error=solver_error,
        conventions_version=conventions.version,
        conventions_hash=conventions.content_hash,
        memory=memory_info(),
        disk=disk_info(data_dir_of(request)),
        queue=queue_summary(repos),
        last_backup=_stored(repos, LAST_BACKUP, BackupResult),
        last_restore=_stored(repos, LAST_RESTORE, RestoreResult),
    )


def _stored[T: BaseModel](repos: Repos, key: str, model: type[T]) -> T | None:
    value = repos.settings.get(key)
    return None if value is None else model.model_validate(value)


@router.post("/system/backup", response_model=JobQueued, status_code=202)
def backup(body: BackupBody, repos: ReposDep) -> JobQueued:
    if body.to:
        folder = Path(body.to)
        if not folder.is_absolute() or (folder.exists() and not folder.is_dir()):
            raise Refused(f"{body.to} is not a folder on the main PC", code="validation_failed")
    job = queue.enqueue(repos, "backup", {"to": body.to or None})
    return JobQueued(job_id=job.id, status=job.status)


@router.post("/system/restore", response_model=JobQueued, status_code=202)
def restore(body: RestoreBody, repos: ReposDep) -> JobQueued:
    archive = Path(body.archive)
    if not archive.is_absolute() or not archive.is_file():
        raise Refused(f"There is no backup archive at {body.archive} on the main PC.", code="validation_failed")
    job = queue.enqueue(repos, "restore", {"archive": str(archive)})
    return JobQueued(job_id=job.id, status=job.status)


@router.get("/conventions", response_model=ConventionsInfo, response_model_by_alias=True)
def conventions(request: Request) -> ConventionsInfo:
    loaded = loaded_conventions(request)
    block = loaded.conventions.model_dump(mode="json", by_alias=True)
    return ConventionsInfo.model_validate({**block, "content_hash": loaded.content_hash})
