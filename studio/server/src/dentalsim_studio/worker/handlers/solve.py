"""The `run` and `preview` job handlers (task T088; research R9; FR-019, FR-022, FR-056, FR-057).

Both call the engine pipeline (`dentalsim.pipeline`) with the job's lane threads and stop check:

- **run**: the run's frozen snapshot at its detail level, every unit case; progress becomes `run.progress`; the run
  record ends succeeded (with its convergence, actual cost and locked results), failed (with its plain reason) or
  cancelled, and `run.finished` is emitted; the estimate coefficients are refitted after each succeeded run.
- **preview**: the scenario snapshot in the job's payload, its active load cases only; `preview.ready` is emitted
  when the preview ends, whatever its outcome.

A solver stopped by the memory watchdog fails with the worker's out-of-memory reason. A run whose worker restarted is
recorded as interrupted, then queued again at the front (FR-056).

A study's run (task T124) may name, as `reuse_of` in its payload, an earlier run of the study whose unit load cases it
reuses (a load-only change; research R4); the pipeline solves them itself when that run has no usable result. When a
study's run starts or ends, the study's status and spread summary are recomputed and `study.progress` is emitted.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dentalsim.config import ensure_data_folder
from dentalsim.errors import DentalsimError, Refused
from dentalsim.estimate.model import EstimateSample, refit
from dentalsim.library.service import LibraryService
from dentalsim.model.run import Run
from dentalsim.model.scenario import Scenario
from dentalsim.persistence import JobRecord, Repos
from dentalsim.pipeline import PipelineFailed, PipelineProgress, PipelineResult, run_pipeline
from dentalsim.runs import failed_run, inputs_for_run, library_inputs, succeeded_run
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.solvers.calculix.runner import ResourceSample, SolverCancelled
from dentalsim.store.cas import ContentStore
from dentalsim.study import refresh_study
from dentalsim_studio.db import SqliteRepos
from dentalsim_studio.events import bus
from dentalsim_studio.worker.handlers import OUT_OF_MEMORY_REASON, JobContext, on_requeued, register
from dentalsim_studio.worker.watchdog import MEMORY_LIMIT_PERCENT, memory_percent


def _watchdog(ctx: JobContext) -> Callable[[ResourceSample], str | None]:
    def check(sample: ResourceSample) -> str | None:
        if memory_percent() > MEMORY_LIMIT_PERCENT:
            ctx.mark_out_of_memory()
            return OUT_OF_MEMORY_REASON
        return None

    return check


def _progress(ctx: JobContext) -> Callable[[PipelineProgress], None]:
    def report(event: PipelineProgress) -> None:
        ctx.progress(event.percent, event.message, event.remaining_s)

    return report


def _reason(exc: BaseException) -> str:
    if isinstance(exc, PipelineFailed):
        return exc.reason
    if isinstance(exc, Refused):
        return exc.message
    return str(exc) or type(exc).__name__


def _repos(ctx: JobContext) -> SqliteRepos:
    repos = ctx.repos
    assert isinstance(repos, SqliteRepos)
    return repos


def _unit_source(ctx: JobContext, repos: SqliteRepos) -> str | None:
    """The stored result of the run this study run may reuse the unit load cases of, if it succeeded."""
    reuse_of = ctx.payload.get("reuse_of")
    source = repos.runs.get(reuse_of) if isinstance(reuse_of, str) else None
    if source is None or source.status != "succeeded":
        return None
    return source.result_ref


def _study_progress(ctx: JobContext, repos: SqliteRepos, study_id: str | None, store: ContentStore) -> None:
    """Recompute the run's study; a failure here is logged and never fails the run."""
    if study_id is None:
        return
    try:
        refresh_study(repos, study_id, store)
    except Exception:
        ctx.logger.exception("study %s: progress could not be recorded", study_id)


@register("run")
def run_job(ctx: JobContext) -> None:
    repos = _repos(ctx)
    assert ctx.target_id is not None
    run = repos.runs.require(ctx.target_id)
    if run.status in ("succeeded", "failed", "cancelled"):
        return
    run = repos.runs.set_status(run.id, "running", started=True)
    data_dir = ensure_data_folder()
    store = ContentStore(data_dir / "store")
    _study_progress(ctx, repos, run.study_id, store)
    try:
        _solve_run(ctx, repos, run, store, data_dir)
    finally:
        if not ctx.shutting_down:
            _study_progress(ctx, repos, run.study_id, store)


def _solve_run(ctx: JobContext, repos: SqliteRepos, run: Run, store: ContentStore, data_dir: Path) -> None:
    build = solver_build()
    try:
        inputs = inputs_for_run(run, LibraryService(repos))
        result: PipelineResult = run_pipeline(
            inputs.request(run.snapshot, run.detail_level, "run"),
            capabilities=repos.capabilities,
            store=store,
            work_root=data_dir / "work",
            solver_build_id=build.build_id,
            ccx=build.path,
            threads=ctx.threads,
            progress=_progress(ctx),
            cancel=ctx.should_stop,
            watchdog=_watchdog(ctx),
            unit_source=_unit_source(ctx, repos),
        )
    except SolverCancelled:
        if ctx.shutting_down:
            return
        if ctx.out_of_memory:
            repos.runs.update(failed_run(run, OUT_OF_MEMORY_REASON))
            bus.emit(repos, "run.finished", {"run_id": run.id, "status": "failed", "reason": OUT_OF_MEMORY_REASON})
            raise DentalsimError(OUT_OF_MEMORY_REASON) from None
        repos.runs.update(failed_run(run, "the run was cancelled", cancelled=True))
        bus.emit(repos, "run.finished", {"run_id": run.id, "status": "cancelled"})
        return
    except (PipelineFailed, DentalsimError) as exc:
        reason = OUT_OF_MEMORY_REASON if ctx.out_of_memory else _reason(exc)
        repos.runs.update(failed_run(run, reason))
        bus.emit(repos, "run.finished", {"run_id": run.id, "status": "failed", "reason": reason})
        raise
    repos.runs.update(succeeded_run(run, result))
    bus.emit(repos, "run.finished", {"run_id": run.id, "status": "succeeded"})
    if not result.cached and result.unit_source is None:
        refit(
            EstimateSample(
                dof=result.actual.dof,
                cases=len(result.convergence.force_balance),
                time_s=max(result.timings_s.get("solve", 0.0), 1e-3),
                memory_bytes=max(1, int(result.actual.peak_memory_gb * 1e9)),
                disk_bytes=max(1, int(result.actual.disk_gb * 1e9)),
                threads=ctx.threads,
                source="run",
            ),
            build.build_id,
        )


@on_requeued("run")
def run_requeued(job: JobRecord, repos: Repos) -> None:
    """FR-056: a run left running by a stopped worker is interrupted, then queued again at the front."""
    if job.target_id is None:
        return
    run = repos.runs.get(job.target_id)
    if run is None or run.status in ("succeeded", "failed", "cancelled"):
        return
    repos.runs.update(run.model_copy(update={"status": "interrupted"}))
    repos.runs.update(run.model_copy(update={"status": "queued", "started_at": None}))


@register("preview")
def preview_job(ctx: JobContext) -> None:
    repos = _repos(ctx)
    assert ctx.target_id is not None
    previews = repos.previews
    preview = previews.get(ctx.target_id)
    if preview is None:
        return
    previews.set_status(preview.id, "running")
    scenario = Scenario.model_validate(ctx.payload["scenario"])
    load_ids = ctx.payload.get("load_ids")
    data_dir = ensure_data_folder()
    build = solver_build()
    event = {"preview_id": preview.id, "scenario_id": preview.scenario_id}
    try:
        inputs = library_inputs(scenario, LibraryService(repos))
        result = run_pipeline(
            inputs.request(scenario, "preview", "preview", load_ids),
            capabilities=repos.capabilities,
            store=ContentStore(data_dir / "store"),
            work_root=data_dir / "work",
            solver_build_id=build.build_id,
            ccx=build.path,
            threads=ctx.threads,
            cancel=ctx.should_stop,
        )
    except SolverCancelled:
        previews.finish(preview.id, "cancelled")
        bus.emit(repos, "preview.ready", {**event, "status": "cancelled"})
        return
    except (PipelineFailed, DentalsimError) as exc:
        reason = _reason(exc)
        previews.finish(preview.id, "failed", failure_reason=reason)
        bus.emit(repos, "preview.ready", {**event, "status": "failed", "reason": reason})
        raise
    previews.finish(preview.id, "succeeded", result_ref=result.input_hash)
    bus.emit(repos, "preview.ready", {**event, "status": "succeeded"})
