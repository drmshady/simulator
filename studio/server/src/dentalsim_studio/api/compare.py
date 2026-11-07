"""`POST /api/compare` (task T117; contracts/studio-api.md "Compare and studies"; FR-027, FR-033; SC-008).

Two or more finished runs, side by side, from their stored results only: nothing is queued or solved, so the
answer comes in seconds. The first run is the base. The answer holds:

- the shared colour scale per field and load case, the Frost window edges, and one deformation scale
  (`dentalsim.post.compare`)
- the key numbers of every run with their differences from the base
- the settings that differ between the base's snapshot and each other run's (`dentalsim.model.diff`)
- each run's name, level, flags (out-of-date flags computed now) and `.glb` links, so the page needs no other
  request, and the teaching-only notice
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from dentalsim.conventions.loader import get_conventions
from dentalsim.errors import Refused
from dentalsim.model.diff import Difference, diff_scenarios
from dentalsim.model.run import Run
from dentalsim.pipeline import load_result
from dentalsim.post.compare import FIELDS, Comparison, FrostEdge, compare, stored_run
from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.api.runs import AssetLink, staleness_of, store_of
from dentalsim_studio.db import SqliteRepos

router = APIRouter(prefix="/api", tags=["compare"])

MAX_RUNS = 6


class CompareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str] = Field(min_length=2, max_length=MAX_RUNS)
    fields: list[str] | None = None
    """Field ids (`frost`, `strain`, `strain_maxp`, `stress`, `pct_strength`); default: all."""


class ComparedRunView(BaseModel):
    run_id: str
    name: str
    scenario_id: str | None
    detail_level: str
    flags: list[str]
    assets: list[AssetLink]


class SettingsDifference(BaseModel):
    run_id: str
    """The run compared with the base."""
    differences: list[Difference]


class CompareView(Comparison):
    runs: list[ComparedRunView]
    settings: list[SettingsDifference]
    """One per run after the base: the settings of its snapshot that differ from the base's."""
    teaching_notice: str


def _refuse(message: str) -> Refused:
    return Refused(message, code="validation_failed")


def frost_edges_of(run: Run, repos: SqliteRepos) -> list[FrostEdge]:
    """The Frost window edges the run recorded, with their values, in the conventions order."""
    by_key = {ref.parameter_key: ref for ref in run.library_versions}
    edges = []
    for key in get_conventions().frost_scale.edges:
        ref = by_key.get(key)
        version = None if ref is None else repos.library.get_version(ref.entry_id, ref.version)
        if ref is not None and version is not None:
            edges.append(FrostEdge(parameter_key=key, entry_version=ref.version, value=version.typical))
    return edges


@router.post("/compare", response_model=CompareView)
def compare_runs(body: CompareBody, request: Request, repos: ReposDep) -> CompareView:
    if len(set(body.run_ids)) != len(body.run_ids):
        raise _refuse("each run can be compared only once")
    unknown = [f for f in body.fields or [] if f not in FIELDS]
    if unknown:
        raise _refuse(f"unknown fields {', '.join(unknown)}; the fields are {', '.join(FIELDS)}")
    runs = [repos.runs.require(run_id) for run_id in body.run_ids]
    unfinished = [run for run in runs if run.status != "succeeded" or run.result_ref is None]
    if unfinished:
        raise Refused(
            "Only finished runs can be compared: "
            + "; ".join(f"{run.snapshot.name} ({run.id}) is {run.status}" for run in unfinished),
            code="not_finished",
        )
    store = store_of(request)
    missing = [run for run in runs if not store.is_complete(run.result_ref or "")]
    if missing:
        raise Refused(
            "The stored results of " + ", ".join(run.id for run in missing) + " are missing; run them again.",
            code="not_finished",
        )

    compared = [
        stored_run(store, run.id, run.result_ref or "", frost_edges_of(run, repos), body.fields) for run in runs
    ]
    comparison = compare(compared, body.fields)
    views = []
    for run in runs:
        flags, _ = staleness_of(run, repos)
        result = load_result(store, run.result_ref or "")
        assets = [AssetLink(**a.model_dump(), url=f"/api/runs/{run.id}/assets/{a.name}")
                  for a in result.result.visual_assets]  # fmt: skip
        views.append(
            ComparedRunView(
                run_id=run.id,
                name=run.snapshot.name,
                scenario_id=run.scenario_id,
                detail_level=run.detail_level,
                flags=sorted(set(run.flags) | flags),
                assets=assets,
            )  # fmt: skip
        )
    base = runs[0].snapshot
    settings = [SettingsDifference(run_id=run.id, differences=diff_scenarios(base, run.snapshot).differences)
                for run in runs[1:]]  # fmt: skip
    return CompareView(
        **comparison.model_dump(),
        runs=views,
        settings=settings,
        teaching_notice=request.app.state.conventions.conventions.teaching_notice,
    )
