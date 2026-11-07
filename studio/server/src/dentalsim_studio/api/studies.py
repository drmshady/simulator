"""Studies: parameter sweeps and sensitivity checks (task T124; contracts/studio-api.md "Compare and studies";
FR-020, FR-021, FR-056, FR-057).

- `POST /api/studies`: a sweep (`kind: "sweep"`, `axes`) or a sensitivity study (`kind: "sensitivity"`,
  `parameter`) of a base scenario. Every run is planned, checked (fast validity checks, physics gate) and the whole
  study is estimated before anything is queued (`dentalsim.study.queue_study`): a run that does not fit in memory
  refuses the study (`estimate_too_large`), and short disk space is a warning on the study. The runs then go to the
  heavy lane in order, one at a time.
- `GET /api/studies/{id}`: the study, each run's status (with its failure reason), progress and the spread summary.
  The worker updates the status and summary after each run and emits `study.progress`; the study ends `finished`,
  or `finished_with_failures` when a run failed.
- `POST /api/studies/{id}/cancel`: the queued runs are cancelled, the running one is asked to stop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import AwareDatetime, BaseModel

from dentalsim.model.run import Estimate, RunStatus
from dentalsim.model.scenario import DetailLevel
from dentalsim.persistence import StudyAxis, StudyKind, StudyRecord, StudyStatus
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.study import SpreadSummary, StudyDefinition, StudyEstimate, cancel_study, queue_study
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.api.runs import store_of
from dentalsim_studio.db import SqliteRepos

router = APIRouter(prefix="/api", tags=["studies"])


class StudyRun(BaseModel):
    run_id: str
    label: str
    values: dict[str, Any]
    reuse_of: str | None
    """The run whose unit load cases this one reuses (a load-only change)."""
    status: RunStatus
    failure_reason: str | None
    flags: list[str]
    estimate: Estimate | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None


class StudyProgress(BaseModel):
    total: int
    done: int
    failed: int
    running: int


class StudyView(BaseModel):
    id: str
    project_id: str
    base_scenario_id: str
    base_scenario_name: str | None
    name: str
    kind: StudyKind
    detail_level: DetailLevel
    axes: list[StudyAxis]
    status: StudyStatus
    runs: list[StudyRun]
    progress: StudyProgress
    summary: SpreadSummary | None
    estimate: StudyEstimate | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


def study_view(study: StudyRecord, repos: SqliteRepos) -> StudyView:
    runs: list[StudyRun] = []
    for point in study.points:
        run = repos.runs.get(point.run_id)
        if run is None:
            continue
        runs.append(
            StudyRun(
                run_id=run.id,
                label=point.label,
                values=point.values,
                reuse_of=point.reuse_of,
                status=run.status,
                failure_reason=run.failure_reason,
                flags=sorted(run.flags),
                estimate=run.estimate,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )  # fmt: skip
        )
    base = repos.scenarios.get(study.base_scenario_id)
    statuses = [run.status for run in runs]
    return StudyView(
        id=study.id,
        project_id=study.project_id,
        base_scenario_id=study.base_scenario_id,
        base_scenario_name=None if base is None else base.name,
        name=study.name,
        kind=study.kind,
        detail_level=study.detail_level,
        axes=study.axes,
        status=study.status,
        runs=runs,
        progress=StudyProgress(
            total=len(statuses),
            done=sum(s in ("succeeded", "failed", "cancelled") for s in statuses),
            failed=sum(s in ("failed", "cancelled") for s in statuses),
            running=sum(s == "running" for s in statuses),
        ),
        summary=None if study.summary is None else SpreadSummary.model_validate(study.summary),
        estimate=None if study.estimate is None else StudyEstimate.model_validate(study.estimate),
        created_at=study.created_at,
        updated_at=study.updated_at,
    )


@router.post("/studies", response_model=StudyView, status_code=202)
def create_study(body: StudyDefinition, repos: ReposDep) -> StudyView:
    study = queue_study(body, repos, solver_build_id=solver_build().build_id)
    return study_view(study, repos)


@router.get("/studies/{study_id}", response_model=StudyView)
def get_study(study_id: str, repos: ReposDep) -> StudyView:
    return study_view(repos.studies.require(study_id), repos)


@router.post("/studies/{study_id}/cancel", response_model=StudyView)
def cancel(study_id: str, request: Request, repos: ReposDep) -> StudyView:
    return study_view(cancel_study(repos, study_id, store_of(request)), repos)
