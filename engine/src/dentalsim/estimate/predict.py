"""The estimate shown before a run starts (task T082; FR-009, FR-057; research R8).

From the scenario: the joined geometry, the degrees of freedom the detail level's size fields predict
(`dentalsim.mesh.predict`) and the number of unit load cases; from them and the solver build's fitted coefficients
(`dentalsim.estimate.model`): the expected time, peak memory and disk use.

- A run whose expected memory is above 85% of the solving machine's physical memory does not fit: it is refused
  (`estimate_too_large`) with a suggestion to lower the detail level, or to use the rented solver machine when one is
  configured (FR-064).
- A warning is given when the free disk space is less than twice the expected use.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import psutil
from pydantic import BaseModel, ConfigDict

from dentalsim.assembly.placement import place
from dentalsim.config import get_settings
from dentalsim.errors import Refused
from dentalsim.estimate.model import CoefficientsFile, predict
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import assemble
from dentalsim.mesh.levels import mesh_level
from dentalsim.mesh.predict import predict_dof
from dentalsim.model.scenario import DETAIL_LEVELS, DetailLevel, Scenario
from dentalsim.post.superpose import plan, point_key

MEMORY_LIMIT = 0.85
DISK_MARGIN = 2.0
GB = 1e9


class RunEstimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detail_level: DetailLevel
    dof: int
    unit_cases: int
    time_s: float
    memory_gb: float
    disk_gb: float
    fits: bool
    warnings: list[str]
    memory_limit_gb: float
    solver_build_id: str


def unit_case_count(scenario: Scenario, *, per_axis: bool, load_ids: Sequence[str] | None = None) -> int:
    loads = [load for load in scenario.loads if load_ids is None or load.id in load_ids]
    cases, _ = plan(loads, per_axis=per_axis)
    return len(cases)


def estimate_run(
    scenario: Scenario,
    detail_level: DetailLevel,
    *,
    solver_build_id: str,
    per_axis: bool = True,
    load_ids: Sequence[str] | None = None,
    total_memory_bytes: int | None = None,
    free_disk_bytes: int | None = None,
    data_dir: Path | None = None,
    coefficients: CoefficientsFile | None = None,
    rented_host: str | None = None,
) -> RunEstimate:
    """Expected DOF, time, memory and disk of solving `scenario` at `detail_level`."""
    loads = [load for load in scenario.loads if load_ids is None or load.id in load_ids]
    cases, combinations = plan(loads, per_axis=per_axis)
    if not cases:
        raise Refused("the scenario has no load case to solve; add a bite or a clench", code="run_refused")
    assembly = place(scenario)
    with occ_tools.session("estimate"):
        model = assemble(assembly)
        points: dict[str, tuple[float, float, float]] = {}
        for combination in combinations:
            for point in combination.contact_points:
                if point.point is not None:
                    points[point_key(point)] = model.occlusal_point(point.block, point.point).position_mm
                elif point.position_mm is not None:
                    points[point_key(point)] = point.position_mm
        dof = predict_dof(model, mesh_level(detail_level), list(points.values()))
    expected = predict(dof, len(cases), solver_build_id, coefficients=coefficients)
    memory = total_memory_bytes if total_memory_bytes is not None else psutil.virtual_memory().total
    folder = data_dir or get_settings().data_dir
    free = (
        free_disk_bytes
        if free_disk_bytes is not None
        else shutil.disk_usage(folder if folder.exists() else Path.cwd()).free
    )
    limit = MEMORY_LIMIT * memory
    fits = expected.memory_bytes <= limit
    warnings = []
    if free < DISK_MARGIN * expected.disk_bytes:
        warnings.append(
            f"Free disk space ({free / GB:.1f} GB) is less than twice the expected use "
            f"({expected.disk_bytes / GB:.1f} GB)."
        )
    if not fits:
        lower = [level for level in DETAIL_LEVELS[: DETAIL_LEVELS.index(detail_level)] if level != "preview"]
        advice = f" Choose a lower detail level ({', '.join(lower)})." if lower else ""
        if rented_host:
            advice += f" Or solve it on the rented solver machine {rented_host}."
        warnings.append(
            f"The run needs about {expected.memory_bytes / GB:.1f} GB of memory, more than 85% of this machine's "
            f"{memory / GB:.1f} GB.{advice}"
        )
    return RunEstimate(
        detail_level=detail_level,
        dof=dof,
        unit_cases=len(cases),
        time_s=expected.time_s,
        memory_gb=expected.memory_bytes / GB,
        disk_gb=expected.disk_bytes / GB,
        fits=fits,
        warnings=warnings,
        memory_limit_gb=limit / GB,
        solver_build_id=solver_build_id,
    )


def require_fits(estimate: RunEstimate) -> None:
    """Refuse a run that does not fit in memory (`estimate_too_large`, FR-057)."""
    if not estimate.fits:
        raise Refused(
            next((w for w in estimate.warnings if "memory" in w), "The run does not fit in memory."),
            code="estimate_too_large",
        )
