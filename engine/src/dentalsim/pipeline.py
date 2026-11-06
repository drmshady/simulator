"""The solve pipeline: from a resolved scenario to stored key numbers and result surfaces (task T081; research R4,
R5, R7, R14; FR-009, FR-017, FR-022, FR-023).

Stages, each reported to the progress callback with its share of the time:

1. **resolve**: the input record and its hash (the store key); a complete stored result for the same inputs is
   reused unless the caller asks for a fresh solve.
2. **validate**: the validity checks (the fast checks for a preview, the full ones for a run); any error refuses.
3. **gate**: the physics gate refuses a scenario whose physics is not ready, naming the benchmark; result types whose
   own physics is not ready are listed as locked. Superposition needs linear physics.
4. **geometry** and **mesh**: the joined model, meshed at the detail level with the patches refined.
5. **solver input**: the unit load cases (`dentalsim.post.superpose`), each spread over its contact points' load
   patches; previews use only the active load cases.
6. **solve** and **read**: CalculiX, then its displacements, integration-point stress and strain and reactions.
7. **checks**: for every unit case, the force, moment, energy and residual checks (FR-023); any failure fails the
   run with that reason.
8. **superpose**, **key numbers**, **glTF**, **store**: every load case is combined, measured and drawn, and
   everything is written under the input hash; the set is declared complete last. A failure at any stage removes what
   was written, so nothing partial is ever stored (FR-022).
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import gmsh
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from dentalsim.assembly.placement import Assembly, place
from dentalsim.assembly.validity import check_assembly
from dentalsim.clock import new_ulid
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.regions import PointSet
from dentalsim.errors import DentalsimError, Refused, SolverFailed
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import AssembledModel, assemble
from dentalsim.library.service import ResolvedValue
from dentalsim.mesh.build import MeshedModel, MeshFailed, build_mesh
from dentalsim.mesh.levels import mesh_level
from dentalsim.mesh.load_patch import PatchLoad, patch_load
from dentalsim.model.results import FieldArtefact, KeyNumber, LockedResult, Reference, ResultSet, VisualAsset
from dentalsim.model.run import Actual, CaseResidual, Convergence, EnergyBalance, ForceBalance, LibraryVersionRef
from dentalsim.model.scenario import DetailLevel, Scenario
from dentalsim.model.validity import ValidityResult
from dentalsim.persistence import CapabilityRepo
from dentalsim.physics.gate import check_gate, require_open
from dentalsim.post import gltf
from dentalsim.post.convergence import check_unit_case, strain_energy
from dentalsim.post.fields import equivalent_strain_ue, principal_strains_ue, stress_measure, von_mises
from dentalsim.post.integration import SHAPE_VALUES, integration_points
from dentalsim.post.key_numbers import (
    BlockReport,
    KeyNumberInputs,
    PointData,
    ResultRegions,
    key_numbers,
    worst_instants,
)
from dentalsim.post.superpose import Combination, UnitCase, combine, plan, point_key, require_linear
from dentalsim.qos import unthrottled_thread
from dentalsim.solvers.calculix.inp_writer import MaterialDef, PartSet, SolverModel, SupportSet, UnitLoadCase
from dentalsim.solvers.calculix.runner import CancelSignal, SolverCancelled, SolverProgress, Watchdog
from dentalsim.solvers.calculix.solve import SolverOutput, solve_model
from dentalsim.store.cas import ContentStore
from dentalsim.store.hashing import InputRecord, input_hash, input_record
from dentalsim.version import engine_version, is_dev_build

FloatArray = NDArray[np.float64]
Purpose = Literal["run", "preview"]
Stage = Literal[
    "resolve", "validate", "gate", "geometry", "mesh", "solver_input", "solve", "read", "checks", "superpose",
    "key_numbers", "gltf", "store",
]  # fmt: skip
STAGE_START: dict[Stage, float] = {
    "resolve": 0, "validate": 2, "gate": 6, "geometry": 7, "mesh": 15, "solver_input": 30, "solve": 32, "read": 82,
    "checks": 85, "superpose": 87, "key_numbers": 89, "gltf": 92, "store": 97,
}  # fmt: skip
RESULT = "result.json"
UNITS = {
    "position": "mm",
    "_STRAIN_UE": "µε",
    "_STRAIN_MAXP_UE": "µε",
    "_STRESS_VM": "MPa",
    "_PCT_STRENGTH": "%",
    "_FROST_CLASS": "index",
}
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class PipelineFailed(DentalsimError):
    """The run failed; `reason` is plain language and is recorded on the run (FR-022)."""

    def __init__(self, reason: str, *, stage: Stage, log_tail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage
        self.log_tail = log_tail


@dataclass(frozen=True)
class PipelineProgress:
    stage: Stage
    percent: float
    remaining_s: float | None
    message: str


ProgressCallback = Callable[[PipelineProgress], None]


@dataclass(frozen=True)
class PipelineRequest:
    scenario: Scenario
    detail_level: DetailLevel
    purpose: Purpose
    library_versions: Sequence[LibraryVersionRef]
    """A version reference for every library value in the scenario (from `LibraryService.resolve_value`)."""
    frost_edges: Sequence[ResolvedValue]
    """The Frost window edges (`frost_scale.edges`), resolved from the library, lowest first."""
    load_ids: Sequence[str] | None = None
    """Previews: the active load cases; None for every load case."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Versions(_Model):
    engine_version: str
    engine_source_hash: str
    conventions_version: str
    solver_build_id: str
    gmsh_version: str
    dev_build: bool


class MeshSummary(_Model):
    dof: int
    elements: int
    min_quality: float
    straightened: int


class PipelineResult(_Model):
    input_hash: str
    purpose: Purpose
    detail_level: DetailLevel
    result: ResultSet
    convergence: Convergence
    actual: Actual
    mesh: MeshSummary
    locked_results: list[LockedResult]
    library_versions: list[LibraryVersionRef]
    versions: Versions
    timings_s: dict[str, float]
    cached: bool = False


class _Reporter:
    def __init__(self, callback: ProgressCallback | None) -> None:
        self.callback = callback
        self.start = time.monotonic()
        self.timings: dict[str, float] = {}
        self._stage: Stage | None = None
        self._stage_start = self.start

    def stage(self, stage: Stage, message: str = "") -> None:
        now = time.monotonic()
        if self._stage is not None:
            self.timings[self._stage] = self.timings.get(self._stage, 0.0) + now - self._stage_start
        self._stage, self._stage_start = stage, now
        self.report(stage, STAGE_START[stage], message or stage.replace("_", " "))

    def report(self, stage: Stage, percent: float, message: str) -> None:
        if self.callback is None:
            return
        elapsed = time.monotonic() - self.start
        remaining = elapsed * (100 - percent) / percent if percent >= 5 else None
        self.callback(PipelineProgress(stage, round(percent, 1), remaining, message))

    def finish(self) -> dict[str, float]:
        """Closes the current stage (normally `store`) and adds the total."""
        now = time.monotonic()
        if self._stage is not None:
            self.timings[self._stage] = self.timings.get(self._stage, 0.0) + now - self._stage_start
            self._stage = None
        self.timings["total"] = now - self.start
        return {k: round(v, 3) for k, v in self.timings.items()}


def request_record(request: PipelineRequest, solver_build_id: str) -> InputRecord:
    """The input record of a request: its store key is `input_hash()` of it. A preview of some load cases only is
    kept apart from one of all of them."""
    library_versions = [*request.library_versions, *(e.version_ref() for e in request.frost_edges)]
    record = input_record(
        request.scenario, library_versions=library_versions, detail_level=request.detail_level,
        solver_build_id=solver_build_id, purpose=request.purpose,
    )  # fmt: skip
    if request.load_ids is not None:
        active = re.sub(r"[^a-z0-9_]", "_", "_".join(sorted(request.load_ids)).lower())
        record = record.model_copy(update={"purpose": f"{request.purpose}_{active}"[:60]})
    return record


def request_key(request: PipelineRequest, solver_build_id: str) -> str:
    return input_hash(request_record(request, solver_build_id))


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name)


def _validity_refusal(result: ValidityResult) -> Refused:
    return Refused(
        "The scenario is not valid, so it cannot be solved: " + " ".join(p.message for p in result.errors),
        code="run_refused",
        problems=[p.model_dump(mode="json") for p in result.errors],
    )


def _contacts(
    model: AssembledModel, combinations: Sequence[Combination]
) -> dict[str, tuple[str, tuple[float, float, float]]]:
    """Each contact point's key, block instance and position."""
    found: dict[str, tuple[str, tuple[float, float, float]]] = {}
    for combination in combinations:
        for point in combination.contact_points:
            key = point_key(point)
            if key in found:
                continue
            if point.point is not None:
                position = model.occlusal_point(point.block, point.point).position_mm
            else:
                assert point.position_mm is not None
                position = point.position_mm
            found[key] = (point.block, position)
    return found


def _patches(mesh: MeshedModel, contacts: Mapping[str, tuple[str, tuple[float, float, float]]]) -> dict[str, PatchLoad]:
    patches = {}
    for key, (block, position) in contacts.items():
        faces = mesh.outer_faces_of(block)
        try:
            patches[key] = patch_load(faces, mesh.node_ids, mesh.coordinates, position)
        except ValueError as exc:
            raise PipelineFailed(f"the load at {key} cannot be applied: {exc}", stage="solver_input") from exc
    return patches


def _unit_load(case: UnitCase, index: int, patches: Mapping[str, PatchLoad]) -> UnitLoadCase:
    """All forces of a unit case point the same way, so its patch is the force-weighted sum of its points' patches."""
    weights: dict[int, float] = {}
    total = case.total()
    for point, force in case.forces:
        share = float(np.linalg.norm(force))
        for node, weight in patches[point_key(point)].as_patch():
            weights[node] = weights.get(node, 0.0) + share * weight
    scale = float(np.linalg.norm(total))
    patch = tuple((node, w / scale) for node, w in sorted(weights.items()))
    return UnitLoadCase(f"U{index + 1}", patch, (float(total[0]), float(total[1]), float(total[2])))


def solver_model(mesh: MeshedModel, unit_loads: Sequence[UnitLoadCase], heading: str) -> SolverModel:
    materials: dict[tuple[str, float, float], str] = {}
    for part in mesh.parts:
        materials.setdefault((part.material, part.e_mpa, part.nu), f"M{len(materials) + 1}_{part.material}"[:60])
    return SolverModel(
        node_ids=mesh.node_ids,
        coordinates=mesh.coordinates,
        element_ids=mesh.element_ids,
        connectivity=mesh.connectivity,
        materials=tuple(MaterialDef(name, e, nu) for (_, e, nu), name in materials.items()),
        parts=tuple(PartSet(p.set_name, materials[(p.material, p.e_mpa, p.nu)], p.element_ids) for p in mesh.parts),
        supports=tuple(SupportSet(s.set_name, s.node_ids) for s in mesh.supports),
        load_cases=tuple(unit_loads),
        heading=heading,
        print_coordinates=False,
    )


def _check_cases(
    model: SolverModel, output: SolverOutput, ip_volumes: FloatArray, unit_loads: Sequence[UnitLoadCase]
) -> Convergence:
    rows = {int(n): i for i, n in enumerate(model.node_ids)}
    forces, energies, residuals, failures = [], [], [], []
    for case, step in zip(unit_loads, output.steps, strict=True):
        patch_rows = np.array([rows[n] for n, _ in case.patch])
        weights = np.array([w for _, w in case.patch])
        load_forces = np.outer(weights, case.force)
        reaction_total = np.sum([step.reaction_totals[s.name.upper()] for s in model.supports], axis=0)
        reaction_nodes = [step.reactions[s.name.upper()] for s in model.supports]
        reaction_rows = np.concatenate([[rows[int(n)] for n in r.node_ids] for r in reaction_nodes])
        reaction_forces = np.concatenate([r.values for r in reaction_nodes])
        checks = check_unit_case(
            applied_total=case.force,
            reaction_total=reaction_total,
            load_positions=model.coordinates[patch_rows],
            load_forces=load_forces,
            reaction_positions=model.coordinates[reaction_rows],
            reaction_forces=reaction_forces,
            load_displacements=step.displacements[patch_rows],
            stress=step.stress,
            strain=step.strain,
            ip_volumes=ip_volumes,
            residual=step.residual,
        )
        by_name = {c.name: c for c in checks}
        forces.append(
            ForceBalance(
                load_case=case.name,
                applied_n=(float(case.force[0]), float(case.force[1]), float(case.force[2])),
                reaction_n=(float(reaction_total[0]), float(reaction_total[1]), float(reaction_total[2])),
                relative_error=by_name["force balance"].relative_error,
            )
        )
        energies.append(
            EnergyBalance(
                load_case=case.name,
                external_work=float(np.sum(load_forces * step.displacements[patch_rows])),
                twice_strain_energy=2.0 * strain_energy(step.stress, step.strain, ip_volumes),
                relative_error=by_name["energy"].relative_error,
            )
        )
        residuals.append(CaseResidual(load_case=case.name, relative_residual=step.residual))
        failures += [f"unit case {case.name}: {c.reason()}" for c in checks if not c.ok]
    convergence = Convergence(
        force_balance=forces,
        energy_balance=energies,
        residuals=residuals,
        solver_relative_residual=output.run.relative_residual,
        solver_messages=list(output.run.messages),
    )
    if failures:
        raise PipelineFailed(
            "the solution failed its convergence checks, so the model is probably not fully supported or the "
            "solve went wrong: " + "; ".join(failures),
            stage="checks",
        )
    return convergence


def _references(assembly: Assembly, mesh: MeshedModel) -> dict[int, Reference]:
    """Each part's strength reference: the block's library value named by the part's `strength_setting`."""
    found: dict[int, Reference] = {}
    for index, part in enumerate(mesh.parts):
        if part.strength_setting is None:
            continue
        placed = assembly.block(part.instance_id)
        value = placed.instance.settings.get(part.strength_setting)
        if value is None or value.kind != "library":
            continue
        design = placed.block_type.design_values(placed.instance.settings)
        key = placed.block_type.parameter_keys(design)[part.strength_setting]
        found[index] = Reference(
            kind="strength", parameter_key=key, entry_id=value.entry_id, entry_version=value.entry_version,
            value=value.value, units="MPa",
        )  # fmt: skip
    return found


def _frost(edges: Sequence[ResolvedValue], conventions_edges: Sequence[str]) -> list[Reference]:
    by_key = {e.parameter_key: e for e in edges}
    missing = [key for key in conventions_edges if key not in by_key]
    if missing:
        raise Refused(f"the Frost window edges {', '.join(missing)} are not in the library", code="validation_failed")
    return [
        Reference(
            kind="frost_window",
            parameter_key=key,
            entry_id=by_key[key].entry_id,
            entry_version=by_key[key].version,
            value=by_key[key].value,
            units=by_key[key].units,
        )  # fmt: skip
        for key in conventions_edges
    ]


def _point_data(mesh: MeshedModel, positions: FloatArray, volumes: FloatArray) -> PointData:
    part = np.repeat(mesh.element_part, 4)
    parts = mesh.parts
    return PointData(
        points=PointSet(
            coords=positions,
            weights=volumes,
            component=np.array([p.component for p in parts])[part],
            material_group=np.array([p.material for p in parts])[part],
            instance=np.array([p.instance_id for p in parts])[part],
        ),
        part=part,
        nu=np.array([p.nu for p in parts])[part],
    )


def _ip_displacement(mesh: MeshedModel, displacement: FloatArray) -> FloatArray:
    u = displacement[mesh.connectivity - 1]  # (m, 10, 3)
    return np.asarray(np.einsum("pk,mkc->mpc", SHAPE_VALUES, u).reshape(-1, 3), dtype=np.float64)


def _blocks(assembly: Assembly, locked: Sequence[LockedResult]) -> list[BlockReport]:
    closed = {(r.block, r.result_type) for r in locked}
    return [
        BlockReport(
            placed.instance_id,
            placed.label,
            tuple(
                ResultRegions(r.id, r.label, r.regions)
                for r in placed.block_type.result_types
                if r.regions and (placed.instance_id, r.id) not in closed
            ),
        )
        for placed in assembly.blocks
    ]


def run_pipeline(
    request: PipelineRequest,
    *,
    capabilities: CapabilityRepo,
    store: ContentStore,
    work_root: Path,
    solver_build_id: str,
    ccx: Path | None = None,
    threads: int = 1,
    progress: ProgressCallback | None = None,
    cancel: CancelSignal | None = None,
    watchdog: Watchdog | None = None,
    cases_dir: Path | None = None,
    use_cache: bool = True,
    keep_work: bool = False,
) -> PipelineResult:
    """Solve a scenario, or reuse the stored result of identical inputs. Refused (`run_refused`) for an invalid
    scenario or physics that is not ready; PipelineFailed with a plain reason when the run fails."""
    with unthrottled_thread(enabled=request.purpose == "preview"):
        report = _Reporter(progress)
        conventions = get_conventions()
        scenario, level = request.scenario, request.detail_level

        report.stage("resolve")
        frost_edges = _frost(request.frost_edges, conventions.frost_scale.edges)
        record = request_record(request, solver_build_id)
        key = input_hash(record)
        if use_cache and store.is_complete(key):
            cached = PipelineResult.model_validate(store.get_json(key, RESULT))
            return cached.model_copy(update={"cached": True})

        report.stage("validate")
        assembly = place(scenario)
        problems = check_assembly(assembly, mode="fast" if request.purpose == "preview" else "full")
        validity = ValidityResult.from_problems(problems)
        if not validity.ok:
            raise _validity_refusal(validity)

        report.stage("gate")
        gate = check_gate(scenario, repo=capabilities, cases_dir=cases_dir)
        require_open(gate)
        needed = {c for b in assembly.blocks for c in b.block_type.needs_physics(b.instance.settings)}
        require_linear(sorted(needed))
        loads = [load for load in scenario.loads if request.load_ids is None or load.id in request.load_ids]
        if not loads:
            raise Refused("the scenario has no load case to solve; add a bite or a clench", code="run_refused")
        unit_cases, combinations = plan(loads, per_axis=request.purpose == "run", conventions=conventions)

        work_dir = work_root / f"{request.purpose}-{key[:12]}-{new_ulid()}"
        written = succeeded = False
        try:
            with occ_tools.session("pipeline"):
                report.stage("geometry")
                model = assemble(assembly)
                contacts = _contacts(model, combinations)
                report.stage("mesh")
                mesh = build_mesh(model, mesh_level(level), load_points=[p for _, p in contacts.values()])
                gmsh.model.mesh.clear()

            report.stage("solver_input")
            patches = _patches(mesh, contacts)
            unit_loads = [_unit_load(case, i, patches) for i, case in enumerate(unit_cases)]
            solver_input = solver_model(mesh, unit_loads, f"dentalsim {request.purpose}: {scenario.name}")

            report.stage("solve", f"solving {len(unit_loads)} unit load case(s), {mesh.dof} DOF")
            total_steps = len(unit_loads)

            def on_solver(event: SolverProgress) -> None:
                done = ((event.step or 1) - 1) / total_steps
                report.report(
                    "solve", STAGE_START["solve"] + done * (STAGE_START["read"] - STAGE_START["solve"]), event.stage
                )

            output = solve_model(
                solver_input,
                work_dir,
                threads=threads,
                ccx=ccx,
                progress=on_solver,
                cancel=cancel,
                watchdog=watchdog,
                unthrottled=request.purpose == "preview",
            )

            report.stage("read")
            points = integration_points(mesh.node_ids, mesh.coordinates, mesh.element_ids, mesh.connectivity)
            expected = (np.repeat(mesh.element_ids, 4), np.tile(np.arange(1, 5), mesh.element_ids.size))
            for step in output.steps:
                if not (
                    np.array_equal(step.ip_element_ids, expected[0]) and np.array_equal(step.ip_numbers, expected[1])
                ):
                    raise PipelineFailed(
                        "the solver's printed results are not one row per integration point", stage="read"
                    )

            report.stage("checks")
            convergence = _check_cases(solver_input, output, points.volumes, unit_loads)

            report.stage("superpose")
            names = [u.name for u in unit_cases]
            renamed = {case.name: unit.name for case, unit in zip(unit_loads, unit_cases, strict=True)}
            stresses = {renamed[s.name]: s.stress for s in output.steps}
            strains = {renamed[s.name]: s.strain for s in output.steps}
            displacements = {renamed[s.name]: s.displacements for s in output.steps}
            combined = [
                (
                    c,
                    combine(stresses, c.coefficients),
                    combine(strains, c.coefficients),
                    combine(displacements, c.coefficients),
                )
                for c in combinations
            ]

            report.stage("key_numbers")
            data = _point_data(mesh, points.positions, points.volumes)
            strength = _references(assembly, mesh)
            inputs = KeyNumberInputs(
                data, model.anchors, _blocks(assembly, gate.locked_results), strength, frost_edges, conventions
            )
            numbers: list[KeyNumber] = []
            for c, stress, strain, displacement in combined:
                chewing = c.instant is not None
                numbers += key_numbers(
                    inputs,
                    label=c.load_case if chewing else c.label,
                    stress=stress,
                    strain=strain,
                    displacement=_ip_displacement(mesh, displacement),
                    contacts=[contacts[point_key(p)] for p in c.contact_points],
                    instant=c.instant,
                )
            numbers = worst_instants(numbers)

            report.stage("gltf")
            edges = [e.value for e in frost_edges]
            assets: list[tuple[str, VisualAsset, bytes]] = []
            bone = np.array([conventions.material_class_of(p.material) == "bone" for p in mesh.parts])[data.part]
            for c, stress, strain, displacement in combined:
                strain_ue = equivalent_strain_ue(strain, data.nu, conventions)
                percent = np.full(strain_ue.shape, gltf.NOT_COMPARED)
                for index, reference in strength.items():
                    rule = conventions.strength_rule_for(mesh.parts[index].material)
                    selected = data.part == index
                    if rule is not None and rule.stress_measure != "none":
                        percent[selected] = (
                            100 * stress_measure(stress[selected], rule.stress_measure) / reference.value
                        )
                fields = {
                    "_STRAIN_UE": strain_ue,
                    "_STRAIN_MAXP_UE": np.abs(principal_strains_ue(strain)[:, 0]),
                    "_STRESS_VM": von_mises(stress),
                    "_PCT_STRENGTH": percent,
                    "_FROST_CLASS": np.where(bone, gltf.frost_index(strain_ue, edges), gltf.NOT_COMPARED),
                }
                for index, part in enumerate(mesh.parts):
                    name = _safe(f"{part.key}.{c.label}.glb")
                    extras = {
                        "part": part.key,
                        "component": part.component,
                        "material": part.material,
                        "block": part.instance_id,
                        "load_case": c.label,
                        "units": UNITS,
                    }
                    data_bytes = gltf.part_glb(mesh, index, displacement=displacement, ip_fields=fields, extras=extras)
                    assets.append(
                        (name, VisualAsset(name=name, part=part.key, load_case=c.label, store_key=key), data_bytes)
                    )

            report.stage("store")
            written = True
            if store.exists(key, RESULT) or store.names(key):
                store.delete(key)
            artefacts = []
            store.put_array(key, "mesh.coordinates", mesh.coordinates)
            store.put_array(key, "mesh.connectivity", mesh.connectivity)
            store.put_array(key, "mesh.element_part", mesh.element_part)
            store.put_array(key, "ip.volumes", points.volumes)
            for name in names:
                for field, values in (
                    ("u", displacements[name]),
                    ("stress", stresses[name]),
                    ("strain", strains[name]),
                ):
                    artefact = _safe(f"unit.{name}.{field}")
                    store.put_array(key, artefact, values)
                    artefacts.append(
                        FieldArtefact(
                            name=field,
                            part="*",
                            unit_case=name,
                            location="nodes" if field == "u" else "integration_points",
                            store_key=artefact,
                        )
                    )
            for name, _, glb in assets:
                store.put_bytes(key, name, glb)
            result = ResultSet(
                input_hash=key,
                fields=artefacts,
                combined_cases=[c.label for c in combinations],
                key_numbers=numbers,
                visual_assets=[asset for _, asset, _ in assets],
                locked_results=gate.locked_results,
            )
            timings = report.finish()
            outcome = PipelineResult(
                input_hash=key,
                purpose=request.purpose,
                detail_level=level,
                result=result,
                convergence=convergence,
                actual=Actual(
                    dof=mesh.dof,
                    time_s=timings["total"],
                    peak_memory_gb=output.run.peak_memory_bytes / 1e9,
                    disk_gb=(store.size_bytes(key) + output.run.disk_bytes) / 1e9,
                ),
                mesh=MeshSummary(
                    dof=mesh.dof,
                    elements=int(mesh.element_ids.size),
                    min_quality=mesh.min_quality,
                    straightened=mesh.straightened,
                ),  # fmt: skip
                locked_results=gate.locked_results,
                library_versions=list(record.library_versions),
                versions=Versions(
                    engine_version=engine_version(),
                    engine_source_hash=record.engine_source_hash,
                    conventions_version=record.conventions_version,
                    solver_build_id=solver_build_id,
                    gmsh_version=record.gmsh_version,
                    dev_build=is_dev_build(),
                ),
                timings_s=timings,
            )
            store.put_json(key, RESULT, outcome.model_dump(mode="json"))
            store.complete(key, meta={"purpose": request.purpose, "detail_level": level})
            succeeded = True
        except MeshFailed as exc:
            _discard(store, key, written)
            raise PipelineFailed(exc.reason, stage="mesh") from exc
        except SolverCancelled:
            _discard(store, key, written)
            raise
        except SolverFailed as exc:
            _discard(store, key, written)
            raise PipelineFailed(exc.reason, stage="solve", log_tail=exc.log_tail) from exc
        except BaseException:
            _discard(store, key, written)
            raise
        finally:
            if not keep_work:
                _clean(work_dir, succeeded)
        return outcome


def _discard(store: ContentStore, key: str, written: bool) -> None:
    if written:
        store.delete(key)


def _clean(work_dir: Path, succeeded: bool) -> None:
    """Remove the solver's work folder; a run that did not succeed keeps its log, never its fields (research R9)."""
    if not work_dir.exists():
        return
    if not succeeded:
        for path in work_dir.iterdir():
            if path.suffix != ".log":
                path.unlink(missing_ok=True)
        return
    shutil.rmtree(work_dir, ignore_errors=True)


def load_result(store: ContentStore, key: str) -> PipelineResult:
    """A stored, complete result."""
    if not store.is_complete(key):
        raise DentalsimError(f"no complete result is stored under {key}")
    return PipelineResult.model_validate(store.get_json(key, RESULT))


def store_asset(store: ContentStore, key: str, name: str) -> bytes:
    return store.get_bytes(key, name)
