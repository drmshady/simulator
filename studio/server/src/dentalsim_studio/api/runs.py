"""Previews, estimates and runs (task T087; contracts/studio-api.md "Preview, estimate, run"; FR-009, FR-011,
FR-011a, FR-017, FR-022, FR-057, FR-058).

- `POST /api/scenarios/{id}/preview`: refused (`run_refused`) when the physics is not ready, naming the benchmark, or
  when the scenario is not valid. A stored preview of the same inputs is answered at once; otherwise the preview is
  queued in the preview lane and `preview.ready` follows.
- `GET /api/previews/{id}`: labelled `preview`, with the accuracy note of the latest verification report.
- `POST /api/scenarios/{id}/estimate`: expected DOF, time, memory and disk, whether it fits, and warnings.
- `POST /api/scenarios/{id}/runs`: refused for validity, the physics gate or a run too large for memory
  (`estimate_too_large`); a run records `outside_published_range` and `dev_build` when they apply.
- `GET /api/runs/{id}`: the run record, its out-of-date flags computed now (FR-058).
- `POST /api/runs/{id}/cancel`, `GET /api/runs/{id}/results`, `GET /api/runs/{id}/assets/{name}.glb`,
  `GET /api/queue`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from dentalsim.assembly.validity import check_validity
from dentalsim.errors import NotFound, Refused
from dentalsim.estimate.predict import RunEstimate, estimate_run, require_fits
from dentalsim.library.service import LibraryService
from dentalsim.model.results import KeyNumber, LockedResult, VisualAsset
from dentalsim.model.run import Convergence, Estimate, Run
from dentalsim.model.scenario import DetailLevel, Scenario
from dentalsim.persistence import JobRecord
from dentalsim.physics.gate import GateResult, check_gate
from dentalsim.pipeline import PipelineResult, load_result, request_key
from dentalsim.runs import library_inputs, new_run
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.store.cas import ContentStore
from dentalsim.store.hashing import input_record
from dentalsim.store.staleness import CurrentState, check_staleness
from dentalsim.verification.report import VerificationReport
from dentalsim.version import engine_source_hash, gmsh_version
from dentalsim_studio.api.deps import ReposDep, data_dir_of
from dentalsim_studio.api.library import LibraryUse, library_uses
from dentalsim_studio.db import SqliteRepos
from dentalsim_studio.db.repos.runs import PreviewRecord
from dentalsim_studio.events import bus
from dentalsim_studio.jobs import queue

router = APIRouter(prefix="/api", tags=["runs"])

NO_ACCURACY_NOTE = (
    "Preview accuracy has not been measured yet: the verification cases have not been run at the preview level."
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def store_of(request: Request) -> ContentStore:
    return ContentStore(data_dir_of(request) / "store")


def accuracy_note(repos: SqliteRepos) -> str:
    latest = repos.verification.latest_report()
    if latest is None:
        return NO_ACCURACY_NOTE
    note = VerificationReport.model_validate(latest).accuracy_note()
    return note or NO_ACCURACY_NOTE


def _refuse_invalid(scenario: Scenario, *, full: bool) -> None:
    result = check_validity(scenario, mode="full" if full else "fast")
    if not result.ok:
        raise Refused(
            "The scenario is not valid, so it cannot be solved: " + " ".join(p.message for p in result.errors),
            code="run_refused",
            problems=[p.model_dump(mode="json") for p in result.errors],
        )


def _gate(scenario: Scenario, repos: SqliteRepos) -> GateResult:
    gate = check_gate(scenario, repo=repos.capabilities)
    refusal = gate.refusal()
    if refusal is not None:
        raise refusal
    return gate


# --- previews ----------------------------------------------------------------------------------------------------


class PreviewBody(_Body):
    load_ids: list[str] | None = None
    """The active load cases (default: every load case)."""


class PreviewQueued(BaseModel):
    preview_id: str
    status: str
    cached: bool


class AssetLink(VisualAsset):
    url: str


class PreviewView(BaseModel):
    id: str
    label: str = "preview"
    status: str
    failure_reason: str | None
    accuracy_note: str | None
    scenario_id: str | None
    scenario_revision: int | None
    key_numbers: list[KeyNumber] = []
    locked_results: list[LockedResult] = []
    assets: list[AssetLink] = []
    dof: int | None = None
    time_s: float | None = None


def _preview(scenario_id: str, body: PreviewBody, store: ContentStore, repos: SqliteRepos) -> PreviewQueued:
    record = repos.scenarios.require(scenario_id)
    scenario = record.scenario
    _gate(scenario, repos)
    _refuse_invalid(scenario, full=False)
    unknown = sorted(set(body.load_ids or []) - {load.id for load in scenario.loads})
    if unknown:
        raise Refused(f"the scenario has no load case {', '.join(unknown)}", code="validation_failed")
    inputs = library_inputs(scenario, LibraryService(repos))
    key = request_key(inputs.request(scenario, "preview", "preview", body.load_ids), solver_build().build_id)
    preview_record = repos.previews.add(
        input_hash=key, scenario_id=record.id, scenario_revision=record.revision, accuracy_note=accuracy_note(repos)
    )
    if store.is_complete(key):
        repos.previews.finish(preview_record.id, "succeeded", result_ref=key)
        bus.emit(
            repos, "preview.ready", {"preview_id": preview_record.id, "scenario_id": record.id, "status": "succeeded"}
        )
        return PreviewQueued(preview_id=preview_record.id, status="succeeded", cached=True)
    queue.enqueue(
        repos,
        "preview",
        {"preview_id": preview_record.id, "scenario": scenario.model_dump(mode="json"), "load_ids": body.load_ids},
        target_id=preview_record.id,
    )
    return PreviewQueued(preview_id=preview_record.id, status="queued", cached=False)


def _links(result: PipelineResult, prefix: str) -> list[AssetLink]:
    return [AssetLink(**a.model_dump(), url=f"{prefix}/assets/{a.name}") for a in result.result.visual_assets]


def preview_view(record: PreviewRecord, store: ContentStore) -> PreviewView:
    view = PreviewView(
        id=record.id,
        status=record.status,
        failure_reason=record.failure_reason,
        accuracy_note=record.accuracy_note,
        scenario_id=record.scenario_id,
        scenario_revision=record.scenario_revision,
    )
    if record.status == "succeeded" and record.result_ref and store.is_complete(record.result_ref):
        result = load_result(store, record.result_ref)
        view = view.model_copy(
            update={
                "key_numbers": result.result.key_numbers,
                "locked_results": result.locked_results,
                "assets": _links(result, f"/api/previews/{record.id}"),
                "dof": result.mesh.dof,
                "time_s": result.timings_s.get("total"),
            }
        )
    return view


# --- estimates and runs ------------------------------------------------------------------------------------------


class LevelBody(_Body):
    detail_level: DetailLevel = "standard"


class RunQueued(BaseModel):
    run_id: str
    status: str
    flags: list[str]
    locked_results: list[LockedResult]
    estimate: Estimate | None


class RunView(BaseModel):
    run: Run
    flags: list[str]
    """The stored flags and the out-of-date flags computed now."""
    stale_reasons: list[str]
    library: list[LibraryUse]
    """The library versions the run used, with their values and sources (FR-016)."""


class ResultsView(BaseModel):
    run_id: str
    key_numbers: list[KeyNumber]
    locked_results: list[LockedResult]
    assets: list[AssetLink]
    combined_cases: list[str]
    convergence: Convergence
    teaching_notice: str


def _estimate(scenario: Scenario, level: DetailLevel, build_id: str) -> RunEstimate:
    return estimate_run(scenario, level, solver_build_id=build_id)


def staleness_of(run: Run, repos: SqliteRepos) -> tuple[set[str], list[str]]:
    try:
        current_build: str | None = solver_build().build_id
    except Exception:
        current_build = None
    record = input_record(
        run.snapshot,
        library_versions=run.library_versions,
        detail_level=run.detail_level,
        solver_build_id=run.solver_build_id,
        conventions_version=run.conventions_version,
        engine_source_hash=run.engine_source_hash,
        gmsh_version=run.gmsh_version,
    )
    current = CurrentState.from_repos(
        repos, solver_build_id=current_build, engine_source_hash=engine_source_hash(), gmsh_version=gmsh_version()
    )
    staleness = check_staleness(record, current)
    return set(staleness.flags), staleness.reasons


def _job_of(repos: SqliteRepos, target_id: str) -> JobRecord | None:
    jobs = [j for j in repos.jobs.list_jobs() if j.target_id == target_id]
    return jobs[-1] if jobs else None


@router.post("/scenarios/{scenario_id}/preview", response_model=PreviewQueued, status_code=202)
def preview_route(scenario_id: str, body: PreviewBody, request: Request, repos: ReposDep) -> PreviewQueued:
    return _preview(scenario_id, body, store_of(request), repos)


@router.get("/previews/{preview_id}", response_model=PreviewView)
def get_preview(preview_id: str, request: Request, repos: ReposDep) -> PreviewView:
    record = repos.previews.get(preview_id)
    if record is None:
        raise NotFound(f"there is no preview {preview_id}")
    return preview_view(record, store_of(request))


@router.get("/previews/{preview_id}/assets/{name}")
def preview_asset(preview_id: str, name: str, request: Request, repos: ReposDep) -> Response:
    record = repos.previews.get(preview_id)
    if record is None or record.result_ref is None:
        raise NotFound(f"preview {preview_id} has no results")
    return _asset(store_of(request), record.result_ref, name)


@router.post("/scenarios/{scenario_id}/estimate", response_model=RunEstimate)
def estimate(scenario_id: str, body: LevelBody, repos: ReposDep) -> RunEstimate:
    record = repos.scenarios.require(scenario_id)
    return _estimate(record.scenario, body.detail_level, solver_build().build_id)


@router.post("/scenarios/{scenario_id}/runs", response_model=RunQueued, status_code=202)
def start_run(scenario_id: str, body: LevelBody, repos: ReposDep) -> RunQueued:
    record = repos.scenarios.require(scenario_id)
    scenario = record.scenario
    _refuse_invalid(scenario, full=True)
    gate = _gate(scenario, repos)
    inputs = library_inputs(scenario, LibraryService(repos))
    build = solver_build()
    expected: Estimate | None = None
    try:
        estimated = _estimate(scenario, body.detail_level, build.build_id)
    except NotFound:
        estimated = None  # no coefficients yet for this solver build: run without an estimate
    if estimated is not None:
        require_fits(estimated)
        expected = Estimate(
            dof=estimated.dof, time_s=estimated.time_s, memory_gb=estimated.memory_gb, disk_gb=estimated.disk_gb
        )
    run = new_run(
        scenario,
        inputs,
        detail_level=body.detail_level,
        solver_build_id=build.build_id,
        scenario_id=record.id,
        scenario_revision=record.revision,
        estimate=expected,
    ).model_copy(update={"locked_results": gate.locked_results})
    repos.runs.add(run)
    queue.enqueue(repos, "run", {"run_id": run.id}, target_id=run.id)
    return RunQueued(
        run_id=run.id, status=run.status, flags=sorted(run.flags), locked_results=run.locked_results,
        estimate=expected,
    )  # fmt: skip


@router.get("/runs/{run_id}", response_model=RunView)
def get_run(run_id: str, repos: ReposDep) -> RunView:
    run = repos.runs.require(run_id)
    flags, reasons = staleness_of(run, repos)
    return RunView(
        run=run, flags=sorted(set(run.flags) | flags), stale_reasons=reasons, library=library_uses(run, repos)
    )


@router.post("/runs/{run_id}/cancel", response_model=Run)
def cancel_run(run_id: str, repos: ReposDep) -> Run:
    run = repos.runs.require(run_id)
    job = _job_of(repos, run_id)
    if job is not None and job.status in ("queued", "running"):
        cancelled = queue.cancel(repos, job.id)
        if cancelled.status == "cancelled" and run.status == "queued":
            run = repos.runs.set_status(run_id, "cancelled")
    return repos.runs.require(run_id) if run.status != "cancelled" else run


@router.get("/runs/{run_id}/results", response_model=ResultsView)
def results(run_id: str, request: Request, repos: ReposDep) -> ResultsView:
    run = repos.runs.require(run_id)
    if run.status != "succeeded" or run.result_ref is None:
        raise NotFound(f"run {run_id} has no results (it is {run.status})")
    result = load_result(store_of(request), run.result_ref)
    return ResultsView(
        run_id=run.id,
        key_numbers=result.result.key_numbers,
        locked_results=result.locked_results,
        assets=_links(result, f"/api/runs/{run.id}"),
        combined_cases=result.result.combined_cases,
        convergence=result.convergence,
        teaching_notice=request.app.state.conventions.conventions.teaching_notice,
    )


@router.get("/runs/{run_id}/assets/{name}")
def run_asset(run_id: str, name: str, request: Request, repos: ReposDep) -> Response:
    run = repos.runs.require(run_id)
    if run.result_ref is None:
        raise NotFound(f"run {run_id} has no results")
    return _asset(store_of(request), run.result_ref, name)


@router.get("/queue")
def queue_view(repos: ReposDep) -> dict[str, list[dict[str, Any]]]:
    """Both lanes in order, and `recent`: the latest finished jobs other than previews, newest first."""
    lanes: dict[str, list[dict[str, Any]]] = {"heavy": [], "preview": [], "recent": []}
    for job in queue.active_jobs(repos):
        lanes[job.lane].append(_queue_item(repos, job))
    lanes["recent"] = [_queue_item(repos, job) for job in queue.recent_jobs(repos)]
    return lanes


def _queue_item(repos: SqliteRepos, job: JobRecord) -> dict[str, Any]:
    item: dict[str, Any] = job.model_dump(mode="json", exclude={"payload"})
    if job.kind == "run" and job.target_id:
        run = repos.runs.get(job.target_id)
        if run is not None:
            item["run"] = {
                "scenario_id": run.scenario_id,
                "detail_level": run.detail_level,
                "estimate": None if run.estimate is None else run.estimate.model_dump(),
                "name": run.snapshot.name,
            }
    return item


def _asset(store: ContentStore, key: str, name: str) -> Response:
    if not name.endswith(".glb") or not store.exists(key, name):
        raise NotFound(f"there is no asset {name}")
    return Response(store.get_bytes(key, name), media_type="model/gltf-binary")
