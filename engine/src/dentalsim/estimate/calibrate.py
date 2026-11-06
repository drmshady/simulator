"""Calibration runs for the time, memory and disk estimates (research R3, R8; tasks T058, T059).

A 20 mm box is meshed with 10-node tetrahedra at several element sizes (gmsh OpenCASCADE, one thread, fixed
random seed, so the same sizes give the same meshes). Each mesh gets its bottom face fixed and 3 unit load cases
(1 N along x, y and z) on a small patch at the centre of the top face, and is solved with the configured solver.
Wall time, peak memory (from the runner) and disk use of the work folder are measured, and the estimate model is
fitted to them for the solver build (`dentalsim.estimate.model`).

Run it on the main PC (T059) and copy the printed table into docs/setup/solver.md:

    uv run python -m dentalsim.estimate.calibrate [--sizes 1.6,1.0,0.6] [--threads N] [--keep] [--no-save]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import gmsh
import numpy as np
from pydantic import BaseModel, ConfigDict

from dentalsim.config import ensure_data_folder
from dentalsim.errors import DentalsimError
from dentalsim.estimate.model import BuildFit, EstimateSample, coefficients_path, fit, record_calibration
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.solvers.calculix.inp_writer import (
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    gmsh_to_ccx_tet10,
    write_inp,
)
from dentalsim.solvers.calculix.runner import run_ccx
from dentalsim.threads import heavy_threads

DEFAULT_SIZES = (1.6, 1.0, 0.6)  # mm; about 45 k, 165 k and 760 k DOF
BOX_MM = (20.0, 20.0, 20.0)
PATCH_RADIUS_MM = 2.0
MESH_SEED = 1
GMSH_TET10 = 11
UNIT_FORCES = (("Fx", (1.0, 0.0, 0.0)), ("Fy", (0.0, 1.0, 0.0)), ("Fz", (0.0, 0.0, 1.0)))


class CalibrationPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    size_mm: float
    nodes: int
    elements: int
    dof: int
    cases: int
    threads: int
    wall_time_s: float
    peak_memory_bytes: int
    disk_bytes: int


class CalibrationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    solver_build_id: str
    points: tuple[CalibrationPoint, ...]
    fit: BuildFit
    saved_to: Path | None


def default_threads() -> int:
    """The heavy lane's thread count (`dentalsim.threads`), so the coefficients fit the runs they predict."""
    return heavy_threads()


def box_model(size_mm: float, box_mm: Sequence[float] = BOX_MM, seed: int = MESH_SEED) -> SolverModel:
    """The calibration model at one element size."""
    lx, ly, lz = (float(v) for v in box_mm)
    started_here = not gmsh.isInitialized()
    if started_here:
        gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("dentalsim-calibration")
        for name, value in (
            ("General.NumThreads", 1),
            ("Mesh.RandomSeed", seed),
            ("Mesh.MeshSizeMin", size_mm),
            ("Mesh.MeshSizeMax", size_mm),
            ("Mesh.ElementOrder", 2),
            ("Mesh.SecondOrderLinear", 1),
            ("Mesh.Algorithm3D", 1),
        ):
            gmsh.option.setNumber(name, value)
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, lx, ly, lz)
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.generate(3)
        node_tags, flat_coordinates, _ = gmsh.model.mesh.getNodes()
        element_tags, flat_nodes = gmsh.model.mesh.getElementsByType(GMSH_TET10)
        gmsh.model.remove()
    finally:
        if started_here:
            gmsh.finalize()

    node_ids = np.asarray(node_tags, dtype=np.int64)
    order = np.argsort(node_ids)
    node_ids = node_ids[order]
    coordinates = np.asarray(flat_coordinates, dtype=np.float64).reshape(-1, 3)[order]
    connectivity = gmsh_to_ccx_tet10(np.asarray(flat_nodes, dtype=np.int64).reshape(-1, 10))
    element_ids = np.arange(1, len(element_tags) + 1, dtype=np.int64)

    tolerance = 1e-6 * max(lx, ly, lz)
    bottom = node_ids[coordinates[:, 2] <= tolerance]
    on_top = coordinates[:, 2] >= lz - tolerance
    radius2 = (coordinates[:, 0] - lx / 2) ** 2 + (coordinates[:, 1] - ly / 2) ** 2
    patch_nodes = node_ids[on_top & (radius2 <= PATCH_RADIUS_MM**2)]
    if not patch_nodes.size:
        raise ValueError(f"no top-face nodes within {PATCH_RADIUS_MM} mm of the centre at size {size_mm} mm")
    patch = tuple((int(node), 1.0 / patch_nodes.size) for node in patch_nodes)
    return SolverModel(
        node_ids=node_ids,
        coordinates=coordinates,
        element_ids=element_ids,
        connectivity=connectivity,
        materials=(MaterialDef("CALIBRATION", 20000.0, 0.3),),
        parts=(PartSet("BOX", "CALIBRATION", element_ids),),
        supports=(SupportSet("BOTTOM", bottom),),
        load_cases=tuple(UnitLoadCase(name, patch, force) for name, force in UNIT_FORCES),
        heading=f"dentalsim calibration box, element size {size_mm} mm",
    )


def calibrate(
    sizes: Sequence[float] = DEFAULT_SIZES,
    *,
    threads: int | None = None,
    ccx: Path | None = None,
    work_root: Path | None = None,
    keep: bool = False,
    save: bool = True,
    log: Callable[[str], None] | None = None,
) -> CalibrationResult:
    """Solve the calibration box at each size and fit the estimate model for the configured solver build."""
    if len(sizes) < 2:
        raise ValueError("calibration needs at least two element sizes")
    say = log or (lambda _message: None)
    threads = threads or default_threads()
    build = solver_build(ccx)
    root = work_root or ensure_data_folder() / "work" / "calibration"
    points: list[CalibrationPoint] = []
    for size in sizes:
        say(f"meshing the box at {size:g} mm ...")
        model = box_model(size)
        work = root / f"h{size:g}mm"
        shutil.rmtree(work, ignore_errors=True)
        write_inp(model, work / "calibration.inp", solver=build.equation_solver)
        say(f"solving {model.dof:,} DOF x {len(model.load_cases)} cases with {threads} threads ...")
        result = run_ccx(work, "calibration", ccx=build.path, threads=threads)
        points.append(
            CalibrationPoint(
                size_mm=size,
                nodes=int(model.node_ids.size),
                elements=int(model.element_ids.size),
                dof=model.dof,
                cases=len(model.load_cases),
                threads=threads,
                wall_time_s=result.wall_time_s,
                peak_memory_bytes=result.peak_memory_bytes,
                disk_bytes=result.disk_bytes,
            )
        )
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    samples = [
        EstimateSample(
            dof=p.dof,
            cases=p.cases,
            time_s=p.wall_time_s,
            memory_bytes=max(1, p.peak_memory_bytes),
            disk_bytes=max(1, p.disk_bytes),
            threads=p.threads,
            source="calibration",
            label=f"calibration box {p.size_mm:g} mm",
        )
        for p in points
    ]
    if save:
        path = coefficients_path()
        fitted = record_calibration(samples, build.build_id, path=path)
    else:
        path, fitted = None, fit(samples, build.build_id)
    return CalibrationResult(solver_build_id=build.build_id, points=tuple(points), fit=fitted, saved_to=path)


def format_table(result: CalibrationResult) -> str:
    mb = 1024.0**2
    lines = [
        f"Solver build: {result.solver_build_id}",
        "",
        "| Size (mm) | Nodes | Elements | DOF | Cases | Threads | Wall time (s) | Peak memory (MB) | Disk (MB) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p in result.points:
        lines.append(
            f"| {p.size_mm:g} | {p.nodes:,} | {p.elements:,} | {p.dof:,} | {p.cases} | {p.threads} "
            f"| {p.wall_time_s:.2f} | {p.peak_memory_bytes / mb:,.0f} | {p.disk_bytes / mb:,.1f} |"
        )
    fitted = result.fit
    lines += [
        "",
        f"Fit ({fitted.fit_kind}, {fitted.fitted_at:%Y-%m-%d %H:%M} UTC):",
        f"  time   = cases x {fitted.time_per_case.coefficient:.4g} x DOF^{fitted.time_per_case.exponent:.4f} s",
        f"  memory = {fitted.memory.coefficient:.4g} x DOF^{fitted.memory.exponent:.4f} bytes",
        f"  disk   = {fitted.disk_per_dof_case:.4g} x DOF x cases bytes",
        f"Saved to: {result.saved_to}" if result.saved_to else "Not saved (--no-save).",
    ]
    return "\n".join(lines)


def _sizes(text: str) -> tuple[float, ...]:
    try:
        sizes = tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"sizes must be numbers separated by commas: {text!r}") from exc
    if len(sizes) < 2 or any(s <= 0 for s in sizes):
        raise argparse.ArgumentTypeError("give at least two positive element sizes in mm, such as 1.6,1.0,0.6")
    return sizes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dentalsim.estimate.calibrate",
        description="Calibrate the run time, memory and disk estimates for the configured CalculiX build.",
    )
    sizes_help = f"element sizes in mm (default: {','.join(f'{s:g}' for s in DEFAULT_SIZES)})"
    parser.add_argument("--sizes", type=_sizes, default=DEFAULT_SIZES, help=sizes_help)
    parser.add_argument("--threads", type=int, default=None, help=f"solver threads (default: {default_threads()})")
    parser.add_argument("--ccx", type=Path, default=None, help="path of the ccx binary (default: DENTALSIM_CCX)")
    parser.add_argument("--keep", action="store_true", help="keep the solver work folders")
    parser.add_argument("--no-save", action="store_true", help="print the fit without saving coefficients.json")
    args = parser.parse_args(argv)
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be at least 1")
    try:
        result = calibrate(
            args.sizes,
            threads=args.threads,
            ccx=args.ccx,
            keep=args.keep,
            save=not args.no_save,
            log=lambda message: print(message, flush=True),
        )
    except DentalsimError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print()
    print(format_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
