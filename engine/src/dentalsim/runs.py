"""Runs and previews as the command line and the studio worker make them (task T083; data-model.md "Run").

- `library_inputs` checks every library value of a scenario against the library: each must point to an approved
  version and match its choice (FR-014), and `outside_range` is recomputed from the version's range (FR-052). It also
  resolves the Frost window edges the key numbers compare with.
- `new_run`, `succeeded_run` and `failed_run` make and complete the run record; a failed run keeps its reason and
  never a result (FR-022).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dentalsim.clock import new_ulid, utc_now
from dentalsim.conventions.loader import get_conventions
from dentalsim.library.service import LibraryService, ResolvedValue
from dentalsim.model.run import Estimate, LibraryVersionRef, Run, RunFlag
from dentalsim.model.scenario import DetailLevel, Scenario
from dentalsim.model.settings import LibraryValue
from dentalsim.pipeline import PipelineRequest, PipelineResult, Purpose
from dentalsim.store.hashing import input_hash, input_record
from dentalsim.version import engine_source_hash, engine_version, gmsh_version, is_dev_build


@dataclass(frozen=True)
class LibraryInputs:
    versions: list[LibraryVersionRef]
    """One per library value of the scenario, in scenario order."""
    frost_edges: list[ResolvedValue]
    outside_range: list[str]
    """Parameter keys whose chosen value lies outside the entry's published range."""

    def request(
        self, scenario: Scenario, detail_level: DetailLevel, purpose: Purpose, load_ids: Sequence[str] | None = None
    ) -> PipelineRequest:
        return PipelineRequest(scenario, detail_level, purpose, self.versions, self.frost_edges, load_ids)

    @property
    def all_versions(self) -> list[LibraryVersionRef]:
        return [*self.versions, *(e.version_ref() for e in self.frost_edges)]


def library_inputs(scenario: Scenario, library: LibraryService) -> LibraryInputs:
    """Refused (`validation_failed`) when a value does not point to an approved version or does not match it."""
    versions, outside = [], []
    for value in scenario.library_values():
        resolved = library.resolve_value(value)
        versions.append(resolved.version_ref())
        if resolved.outside_range:
            outside.append(resolved.parameter_key)
    edges = [library.resolve(key) for key in get_conventions().frost_scale.edges]
    return LibraryInputs(versions, edges, outside)


def inputs_for_run(run: Run, library: LibraryService) -> LibraryInputs:
    """The library inputs a queued run was made with: its scenario's values as they are in the snapshot, and the
    Frost window edges at the versions the run recorded (each still checked to be approved)."""
    versions = [library.resolve_value(value).version_ref() for value in run.snapshot.library_values()]
    edges = []
    by_key = {ref.parameter_key: ref for ref in run.library_versions}
    for key in get_conventions().frost_scale.edges:
        ref = by_key.get(key)
        if ref is None:
            edges.append(library.resolve(key))
            continue
        version = library.get_version(ref.entry_id, ref.version)
        edges.append(
            library.resolve_value(
                LibraryValue(entry_id=ref.entry_id, entry_version=ref.version, choice="typical", value=version.typical)
            )
        )
    outside = [ref.parameter_key for value, ref in zip(run.snapshot.library_values(), versions, strict=True)
               if value.outside_range]  # fmt: skip
    return LibraryInputs(versions, edges, outside)


def run_flags(inputs: LibraryInputs) -> set[RunFlag]:
    flags: set[RunFlag] = set()
    if inputs.outside_range:
        flags.add("outside_published_range")
    if is_dev_build():
        flags.add("dev_build")
    return flags


def new_run(
    scenario: Scenario,
    inputs: LibraryInputs,
    *,
    detail_level: DetailLevel,
    solver_build_id: str,
    scenario_id: str | None = None,
    scenario_revision: int | None = None,
    estimate: Estimate | None = None,
    study_id: str | None = None,
) -> Run:
    """A queued run of a frozen snapshot of `scenario` at `detail_level`."""
    snapshot = scenario.model_copy(update={"detail_level": detail_level})
    record = input_record(
        snapshot, library_versions=inputs.all_versions, detail_level=detail_level, solver_build_id=solver_build_id
    )
    return Run(
        id=new_ulid(),
        scenario_id=scenario_id,
        scenario_revision=scenario_revision,
        snapshot=snapshot,
        detail_level=detail_level,
        input_hash=input_hash(record),
        library_versions=list(record.library_versions),
        engine_version=engine_version(),
        engine_source_hash=engine_source_hash(),
        conventions_version=get_conventions().version,
        solver_build_id=solver_build_id,
        gmsh_version=gmsh_version(),
        estimate=estimate,
        flags=run_flags(inputs),
        study_id=study_id,
        created_at=utc_now(),
    )


def succeeded_run(run: Run, result: PipelineResult) -> Run:
    return Run.model_validate(
        run.model_dump()
        | {
            "status": "succeeded",
            "input_hash": result.input_hash,
            "result_ref": result.input_hash,
            "convergence": result.convergence.model_dump(),
            "actual": result.actual.model_dump(),
            "locked_results": [r.model_dump() for r in result.locked_results],
            "failure_reason": None,
            "finished_at": utc_now(),
        }
    )


def failed_run(run: Run, reason: str, *, cancelled: bool = False) -> Run:
    return Run.model_validate(
        run.model_dump()
        | {
            "status": "cancelled" if cancelled else "failed",
            "failure_reason": None if cancelled else reason,
            "result_ref": None,
            "finished_at": utc_now(),
        }
    )
