"""Solve a `SolverModel` with CalculiX and read its results back, step by step (research R3, R4).

Writes `<work_dir>/<jobname>.inp`, runs ccx (`runner.run_ccx`), and returns per step (unit load case): the nodal
displacements in the model's node order (from the .frd file) and the integration-point coordinates, stress and
strain, the support reaction totals and the nodal reactions (from the .dat file).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from dentalsim.errors import SolverFailed
from dentalsim.solvers.calculix.dat_reader import DatStep, NodalValues, read_dat
from dentalsim.solvers.calculix.frd_reader import read_frd
from dentalsim.solvers.calculix.inp_writer import DEFAULT_SOLVER, InpExtension, SolverModel, write_inp
from dentalsim.solvers.calculix.runner import (
    CancelSignal,
    ProgressCallback,
    SolverRunResult,
    Watchdog,
    run_ccx,
)

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True, eq=False)
class StepOutput:
    """One solved step. Integration-point rows are sorted by (element, point)."""

    name: str
    displacements: FloatArray
    """(n, 3), in the model's node order."""
    ip_element_ids: IntArray
    ip_numbers: IntArray
    ip_coordinates: FloatArray
    stress: FloatArray
    strain: FloatArray
    reaction_totals: Mapping[str, FloatArray]
    """By upper-case support set name."""
    reactions: Mapping[str, NodalValues]
    residual: float | None


@dataclass(frozen=True, eq=False)
class SolverOutput:
    run: SolverRunResult
    steps: tuple[StepOutput, ...]


def _aligned(model: SolverModel, node_ids: IntArray, values: FloatArray) -> FloatArray:
    order = np.argsort(node_ids)
    where = order[np.searchsorted(node_ids, model.node_ids, sorter=order)]
    if not np.array_equal(node_ids[where], model.node_ids):
        raise SolverFailed("the solver's result file lacks displacements for some nodes")
    return np.asarray(values[where], dtype=np.float64)


def _step(model: SolverModel, name: str, dat: DatStep, displacements: FloatArray, residual: float | None) -> StepOutput:
    if dat.stress.shape[0] != 4 * model.element_ids.size:
        raise SolverFailed("the solver's printed results lack integration-point values for some elements")
    return StepOutput(
        name=name,
        displacements=displacements,
        ip_element_ids=dat.element_ids,
        ip_numbers=dat.ip_numbers,
        ip_coordinates=dat.ip_coordinates,
        stress=dat.stress,
        strain=dat.strain,
        reaction_totals=dat.reaction_totals,
        reactions=dat.reactions,
        residual=residual,
    )


def solve_model(
    model: SolverModel,
    work_dir: Path,
    *,
    jobname: str = "job",
    threads: int = 1,
    ccx: Path | None = None,
    command: Sequence[str] | None = None,
    solver: str = DEFAULT_SOLVER,
    extensions: Sequence[InpExtension] = (),
    progress: ProgressCallback | None = None,
    cancel: CancelSignal | None = None,
    watchdog: Watchdog | None = None,
    timeout_s: float | None = None,
    unthrottled: bool = False,
) -> SolverOutput:
    work_dir.mkdir(parents=True, exist_ok=True)
    write_inp(model, work_dir / f"{jobname}.inp", solver=solver, extensions=extensions)
    run = run_ccx(
        work_dir,
        jobname,
        ccx=ccx,
        command=command,
        threads=threads,
        progress=progress,
        cancel=cancel,
        watchdog=watchdog,
        timeout_s=timeout_s,
        steps_total=len(model.load_cases),
        unthrottled=unthrottled,
    )
    dat_steps = read_dat(run.dat_path)
    frd_steps = read_frd(run.frd_path).steps
    cases = model.load_cases
    if len(dat_steps) != len(cases) or len(frd_steps) != len(cases):
        raise SolverFailed(
            f"the solver wrote results for {len(frd_steps)} of {len(cases)} load cases; see {run.log_path.name}"
        )
    residuals = list(run.step_residuals) + [None] * (len(cases) - len(run.step_residuals))
    steps = tuple(
        _step(model, case.name, dat, _aligned(model, frd.node_ids, frd.displacements), residual)
        for case, dat, frd, residual in zip(cases, dat_steps, frd_steps, residuals, strict=True)
    )
    return SolverOutput(run=run, steps=steps)
