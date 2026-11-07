"""Studies (task T123; FR-020, FR-021, FR-057; research R4): the format, the sweep and sensitivity plans, load-only
axes that reuse one solve, queuing, the status rule, the spread summary, and the pipeline's reuse check."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from dentalsim import cli
from dentalsim import study as study_module
from dentalsim.commands import study as study_command
from dentalsim.errors import Refused
from dentalsim.estimate.model import BuildFit, CoefficientsFile, PowerLaw
from dentalsim.estimate.predict import RunEstimate
from dentalsim.library.service import LibraryService
from dentalsim.model.results import FieldArtefact, KeyNumber, ResultSet
from dentalsim.model.run import Actual, Convergence, ForceBalance
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import DesignValue, LibraryValue
from dentalsim.persistence import CapabilityStatus, Repos, ScenarioRecord, StudyAxis, open_repos
from dentalsim.physics.registry import read_case_headers, required_cases_in
from dentalsim.pipeline import RESULT, MeshSummary, PipelineResult, Versions, stored_unit_cases
from dentalsim.store.cas import ContentStore
from dentalsim.study import (
    RunOutcome,
    StudyDefinition,
    model_key,
    plan_study,
    queue_study,
    resolve_path,
    study_status,
    summarise,
)
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import seed_library

BUILD = "ccx-test-build"


@pytest.fixture
def repos(data_dir: Path) -> Iterator[Repos]:
    opened = open_repos(data_dir)
    seed_library(opened)
    yield opened
    opened.close()


@pytest.fixture
def library(repos: Repos) -> LibraryService:
    return LibraryService(repos)


@pytest.fixture
def base(library: LibraryService) -> Scenario:
    return open_template(builtin_template("single_crown_implant"), library).scenario


def design(scenario: Scenario, block: str, setting: str) -> Any:
    value = scenario.block(block).settings[setting]
    assert isinstance(value, DesignValue)
    return value.value


def library_value(scenario: Scenario, block: str, setting: str) -> LibraryValue:
    value = scenario.block(block).settings[setting]
    assert isinstance(value, LibraryValue)
    return value


# The format.


def test_a_sweep_needs_axes_and_a_sensitivity_study_a_parameter() -> None:
    sweep = {"base_scenario_id": "S", "kind": "sweep", "axes": [{"path": "implant-1.length_mm", "values": [8, 10]}]}
    assert StudyDefinition.model_validate(sweep).format == "dentalsim.study/1"
    for wrong in (
        {"base_scenario_id": "S", "kind": "sweep"},
        {**sweep, "parameter": "jaw.cancellous_E"},
        {**sweep, "axes": [{"path": "a.b", "values": []}]},
        {**sweep, "axes": [{"path": "a.b", "values": [1]}, {"path": "a.b", "values": [2]}]},
        {"base_scenario_id": "S", "kind": "sensitivity"},
        {**sweep, "detail_level": "preview"},
        {**sweep, "patient": "x"},
    ):
        with pytest.raises(ValidationError):
            StudyDefinition.model_validate(wrong)


# Paths.


def test_paths_name_settings_placements_and_loads(base: Scenario) -> None:
    assert resolve_path(base, "implant-1.length").path == "implant-1.length_mm"  # the unit suffix may be left out
    assert resolve_path(base, "implant-1.thread").path == "implant-1.thread"  # an exact id wins over a prefix
    assert resolve_path(base, "implant-1.placement.rotation_deg").keys[-1] == "rotation_deg"
    assert resolve_path(base, "load:oblique.direction.angle_deg").load_only
    assert resolve_path(base, "load:axial.force").load_only
    assert not resolve_path(base, "jaw.cancellous_E").load_only
    for wrong in ("implant-9.length", "implant-1.colour", "implant-1", "load:none.force", "load:axial.weight",
                  "implant-1.placement.nowhere"):  # fmt: skip
        with pytest.raises(Refused) as refused:
            resolve_path(base, wrong)
        assert refused.value.code == "validation_failed"


# Sweeps.


def test_a_sweep_is_the_product_of_its_axes_first_axis_slowest(base: Scenario, library: LibraryService) -> None:
    axes = [StudyAxis(path="implant-1.length", values=[8, 10, 11.5]), StudyAxis(path="jaw.density_class",
                                                                                 values=["D1", "D3"])]  # fmt: skip
    written, runs = plan_study(base, "sweep", axes, library)
    assert [a.path for a in written] == ["implant-1.length_mm", "jaw.density_class"]
    assert [(r.values["implant-1.length_mm"], r.values["jaw.density_class"]) for r in runs] == [
        (8, "D1"), (8, "D3"), (10, "D1"), (10, "D3"), (11.5, "D1"), (11.5, "D3"),
    ]  # fmt: skip
    first = runs[0].scenario
    assert design(first, "implant-1", "length_mm") == 8.0 and isinstance(design(first, "implant-1", "length_mm"), float)
    assert runs[0].label == "implant-1.length_mm 8, jaw.density_class D1"
    assert first.name == f"{base.name} [{runs[0].label}]"
    # A new density class names other library entries: their typical values are taken.
    d1 = library.get_entry(library_value(first, "jaw", "cancellous_E").entry_id)
    assert d1 is not None and d1.parameter_key == "bone.cancellous.E.D1"
    assert library_value(first, "jaw", "cancellous_E").value == 9500.0
    assert all(r.reuse_of is None for r in runs)  # no load-only axis: every run solves


def test_a_library_axis_takes_custom_values_and_flags_those_outside_the_range(
    base: Scenario, library: LibraryService
) -> None:
    _, runs = plan_study(base, "sweep", [StudyAxis(path="jaw.cancellous_E", values=[5500, 3000, 9000])], library)
    values = [library_value(r.scenario, "jaw", "cancellous_E") for r in runs]
    assert [(v.choice, v.value, v.outside_range) for v in values] == [
        ("typical", 5500.0, False), ("custom", 3000.0, False), ("custom", 9000.0, True),
    ]  # fmt: skip
    with pytest.raises(Refused, match="must be numbers"):
        plan_study(base, "sweep", [StudyAxis(path="jaw.cancellous_E", values=["soft"])], library)


def test_load_only_axes_reuse_the_first_solve_of_their_group(base: Scenario, library: LibraryService) -> None:
    axes = [StudyAxis(path="load:oblique.force", values=[200, 400]), StudyAxis(path="implant-1.length",
                                                                               values=[8, 10, 13])]  # fmt: skip
    _, runs = plan_study(base, "sweep", axes, library)
    # force 200: lengths 8, 10, 13 solve; force 400 reuses the run of the same length.
    assert [r.reuse_of for r in runs] == [None, None, None, 0, 1, 2]
    oblique = next(load for load in runs[3].scenario.loads if load.id == "oblique")
    assert (oblique.force.choice, oblique.force.value) == ("custom", 400.0)
    _, angles = plan_study(base, "sweep", [StudyAxis(path="load:oblique.direction.angle_deg",
                                                     values=[0, 15, 30, 45])], library)  # fmt: skip
    assert [r.reuse_of for r in angles] == [None, 0, 0, 0]
    assert next(x for x in angles[1].scenario.loads if x.id == "oblique").direction.angle_deg == 15.0  # type: ignore[union-attr]


def test_a_study_of_too_many_runs_is_refused(base: Scenario, library: LibraryService) -> None:
    diameters = [3.5, 3.75, 4.0, 4.1, 4.3, 4.5, 4.8, 5.0, 5.5, 6.0]
    axes = [StudyAxis(path="implant-1.length", values=list(range(8, 19))),
            StudyAxis(path="implant-1.diameter", values=diameters)]  # fmt: skip
    with pytest.raises(Refused, match="at most 100"):
        plan_study(base, "sweep", axes, library)


# Sensitivity.


def test_sensitivity_makes_the_minimum_typical_and_maximum_runs(base: Scenario, library: LibraryService) -> None:
    from dentalsim.study import sensitivity_axis

    axis = sensitivity_axis(base, "jaw.cancellous_E", library)
    version = library.get_version(library_value(base, "jaw", "cancellous_E").entry_id, 1)
    assert axis.values == [version.range_min, version.typical, version.range_max]
    _, runs = plan_study(base, "sensitivity", [axis], library)
    assert [r.label.split(":")[0] for r in runs] == ["minimum", "typical", "maximum"]
    assert [library_value(r.scenario, "jaw", "cancellous_E").outside_range for r in runs] == [False] * 3
    with pytest.raises(Refused, match="not a library setting"):
        sensitivity_axis(base, "implant-1.length", library)


# Queuing.


def make_ready(repos: Repos) -> None:
    repos.verification.add_report({"id": "R1", "engine_version": "x", "solver_build_id": BUILD,
                                   "conventions_version": "1.1.0", "overall": "pass",
                                   "created_at": "2026-11-06T00:00:00+00:00"})  # fmt: skip
    required = list(required_cases_in("bonded_elastic", read_case_headers()))
    repos.capabilities.set(
        CapabilityStatus(capability_id="bonded_elastic", status="ready", report_id="R1", cases=required)
    )


@pytest.fixture
def stored_base(repos: Repos, base: Scenario) -> ScenarioRecord:
    from dentalsim.clock import new_ulid, utc_now

    make_ready(repos)
    project = repos.scenarios.add_project("Overnight")
    record = ScenarioRecord(id=new_ulid(), project_id=project.id, name="Single crown", scenario=base,
                            created_at=utc_now(), updated_at=utc_now())  # fmt: skip
    repos.scenarios.add(record)
    return record


def coefficients() -> CoefficientsFile:
    fit = BuildFit(
        solver_build_id=BUILD, fitted_at="2026-11-06T00:00:00+00:00", fit_kind="fixed_exponent",
        time_per_case=PowerLaw(coefficient=1e-3, exponent=1.0), memory=PowerLaw(coefficient=1e9, exponent=0.0),
        disk_per_dof_case=1000.0, samples=(),
    )  # fmt: skip
    return CoefficientsFile(builds={BUILD: fit})


def fake_estimates(monkeypatch: pytest.MonkeyPatch, *, memory_gb: float = 2.0) -> list[Scenario]:
    """Building each model to estimate it takes seconds, so these tests stand in a fixed estimate; the list records
    the scenarios estimated."""
    estimated: list[Scenario] = []

    def estimate(scenario: Scenario, level: Any, **kwargs: Any) -> RunEstimate:
        estimated.append(scenario)
        return RunEstimate(detail_level=level, dof=200_000, unit_cases=6, time_s=600.0, memory_gb=memory_gb,
                           disk_gb=1.5, fits=memory_gb < 27, warnings=[], memory_limit_gb=27.0,
                           solver_build_id=BUILD)  # fmt: skip

    monkeypatch.setattr(study_module, "estimate_run", estimate)
    return estimated


def test_runs_whose_meshes_cannot_differ_share_one_estimate(base: Scenario, library: LibraryService) -> None:
    def keys(path: str, values: list[Any]) -> set[str]:
        _, runs = plan_study(base, "sweep", [StudyAxis(path=path, values=values)], library)
        return {model_key(r.scenario, library) for r in runs}

    assert len(keys("jaw.cancellous_E", [1370, 5500, 9500])) == 1  # a modulus (MPa) does not shape the model
    assert len(keys("load:oblique.force", [200, 400])) == 1
    assert len(keys("jaw.cortical_thickness", [1.0, 2.0])) == 2  # a length (mm) does
    assert len(keys("implant-1.length", [8, 10])) == 2


def test_queuing_adds_the_study_its_runs_and_their_jobs_in_order(
    repos: Repos, stored_base: ScenarioRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    estimated = fake_estimates(monkeypatch)
    definition = StudyDefinition(
        name="Lengths and forces", base_scenario_id=stored_base.id, kind="sweep",
        axes=[StudyAxis(path="implant-1.length", values=[8, 10]), StudyAxis(path="load:axial.force",
                                                                            values=[300, 600])],
    )  # fmt: skip
    study = queue_study(definition, repos, solver_build_id=BUILD, coefficients=coefficients(),
                        total_memory_bytes=32 * 10**9, free_disk_bytes=10**15)  # fmt: skip
    assert len(estimated) == 2  # one per length; the second force reuses the first's solve
    assert study.status == "queued" and study.project_id == stored_base.project_id
    assert [p.label for p in study.points] == [
        "implant-1.length_mm 8, load:axial.force 300", "implant-1.length_mm 8, load:axial.force 600",
        "implant-1.length_mm 10, load:axial.force 300", "implant-1.length_mm 10, load:axial.force 600",
    ]  # fmt: skip
    assert [p.reuse_of for p in study.points] == [None, study.points[0].run_id, None, study.points[2].run_id]
    runs = [repos.runs.get(run_id) for run_id in study.run_ids]
    assert all(r is not None and r.study_id == study.id and r.scenario_id == stored_base.id for r in runs)
    assert [r.estimate is not None for r in runs if r is not None] == [True, False, True, False]
    jobs = repos.jobs.list_jobs(lane="heavy")
    assert [j.target_id for j in jobs] == study.run_ids
    assert jobs[1].payload["reuse_of"] == study.points[0].run_id
    stored = repos.studies.get(study.id)
    assert stored is not None and stored.estimate is not None and stored.estimate["solves"] == 2
    assert (stored.estimate["time_s"], stored.estimate["memory_gb"], stored.estimate["disk_gb"]) == (1200, 2, 6)
    assert repos.events.since(0)[-1].type == "study.progress"


def test_queuing_refuses_a_run_too_large_for_memory_and_an_invalid_run(
    repos: Repos, stored_base: ScenarioRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_estimates(monkeypatch, memory_gb=40.0)
    sweep = StudyDefinition(base_scenario_id=stored_base.id, kind="sweep",
                            axes=[StudyAxis(path="implant-1.length", values=[8, 10])])  # fmt: skip
    with pytest.raises(Refused) as refused:
        queue_study(sweep, repos, solver_build_id=BUILD, coefficients=coefficients(),
                    total_memory_bytes=32 * 10**9, free_disk_bytes=10**15)  # fmt: skip
    assert refused.value.code == "estimate_too_large" and "implant-1.length_mm 8" in refused.value.message
    invalid = sweep.model_copy(update={"axes": [StudyAxis(path="implant-1.length", values=[10, 40])]})
    with pytest.raises(Refused) as refused:
        queue_study(invalid, repos, solver_build_id=BUILD, coefficients=coefficients(),
                    total_memory_bytes=32 * 10**9, free_disk_bytes=10**15)  # fmt: skip
    assert refused.value.code == "run_refused" and "implant-1.length_mm 40" in refused.value.problems[0]["message"]
    assert repos.jobs.list_jobs() == [] and repos.studies.list_for_project(stored_base.project_id) == []


def test_short_disk_space_is_a_warning_on_the_study(
    repos: Repos, stored_base: ScenarioRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    estimated = fake_estimates(monkeypatch)
    sensitivity = StudyDefinition(base_scenario_id=stored_base.id, kind="sensitivity", parameter="jaw.cancellous_E")
    study = queue_study(sensitivity, repos, solver_build_id=BUILD, coefficients=coefficients(),
                        total_memory_bytes=32 * 10**9, free_disk_bytes=1000)  # fmt: skip
    assert study.estimate is not None and len(study.points) == 3 and len(estimated) == 1
    assert any("Free disk space" in w for w in study.estimate["warnings"])


def test_the_study_command_queues_a_study_file(
    repos: Repos, stored_base: ScenarioRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.setattr(study_command, "solver_build", lambda: SimpleNamespace(build_id=BUILD, path=None))
    path = tmp_path / "study.yaml"
    document = {"format": "dentalsim.study/1", "base_scenario_id": stored_base.id, "kind": "sensitivity",
                "parameter": "jaw.cancellous_E"}  # fmt: skip
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert cli.main(["study", str(path)]) == 0
    text = capsys.readouterr().out
    assert "3 runs at standard detail" in text and "minimum" in text and "no time and memory estimates" in text
    path.write_text("format: dentalsim.study/1\nkind: sweep\n", encoding="utf-8")
    assert cli.main(["study", str(path)]) == 1


# Status and summary.


def test_the_study_status_follows_its_runs() -> None:
    assert study_status("queued", ["queued", "queued"]) == "queued"
    assert study_status("queued", ["succeeded", "queued"]) == "running"
    assert study_status("running", ["interrupted", "succeeded"]) == "running"
    assert study_status("running", ["succeeded", "succeeded"]) == "finished"
    assert study_status("running", ["succeeded", "failed"]) == "finished_with_failures"
    assert study_status("cancelled", ["succeeded", "running"]) == "cancelled"


def number(value: float, *, load_case: str = "axial", number_id: str = "crestal_mean") -> KeyNumber:
    return KeyNumber(id=number_id, label="Crestal strain", load_case=load_case, block="implant-1", region="crestal",
                     statistic="mean", value=value, unit="µε")  # fmt: skip


def test_the_summary_gives_the_spread_of_each_key_number_per_axis() -> None:
    axes = [StudyAxis(path="length", values=[8, 10]), StudyAxis(path="density", values=["D1", "D3"])]
    outcomes = [
        RunOutcome({"length": 8, "density": "D1"}, "succeeded", [number(1000)]),
        RunOutcome({"length": 8, "density": "D3"}, "succeeded", [number(1400)]),
        RunOutcome({"length": 10, "density": "D1"}, "succeeded", [number(800)]),
        RunOutcome({"length": 10, "density": "D3"}, "failed", None),
    ]
    summary = summarise("sweep", axes, outcomes)
    assert (summary.runs_total, summary.runs_succeeded, summary.runs_failed, summary.runs_pending) == (4, 3, 1, 0)
    length, density = summary.axes
    (row,) = length.key_numbers
    assert [(m.value, m.mean, m.runs) for m in row.by_value] == [(8, 1200.0, 2), (10, 800.0, 1)]
    assert (row.min, row.max, row.range, row.reference) == (800.0, 1200.0, 400.0, 1200.0)
    assert row.percent_change == pytest.approx(100 * 400 / 1200)
    (row,) = density.key_numbers
    assert [m.mean for m in row.by_value] == [900.0, 1400.0] and row.reference == 900.0


def test_a_sensitivity_summary_is_relative_to_the_typical_run() -> None:
    axis = StudyAxis(path="jaw.cancellous_E", values=[1370.0, 5500.0, 9500.0])
    outcomes = [RunOutcome({"jaw.cancellous_E": v}, "succeeded", [number(s), number(2 * s, load_case="oblique")])
                for v, s in ((1370.0, 1500.0), (5500.0, 1000.0), (9500.0, 900.0))]  # fmt: skip
    (spread,) = summarise("sensitivity", [axis], outcomes).axes
    axial, oblique = spread.key_numbers
    assert spread.reference_value == 5500.0 and axial.reference == 1000.0
    assert axial.range == 600.0 and axial.percent_change == pytest.approx(60.0)
    assert oblique.load_case == "oblique" and oblique.percent_change == pytest.approx(60.0)
    empty = summarise("sensitivity", [axis], [RunOutcome({"jaw.cancellous_E": 1370.0}, "queued", None)])
    assert empty.runs_pending == 1 and empty.axes[0].key_numbers == []


# The pipeline's reuse check.


def stored_run(store: ContentStore, key: str, names: list[str], coordinates: np.ndarray) -> None:
    fields = []
    for name in names:
        for field in ("u", "stress", "strain"):
            store.put_array(key, f"unit.{name}.{field}", np.full((4, 3), 1.0))
            fields.append(FieldArtefact(name=field, part="*", unit_case=name, store_key=f"unit.{name}.{field}",
                                        location="nodes" if field == "u" else "integration_points"))  # fmt: skip
    store.put_array(key, "mesh.coordinates", coordinates)
    store.put_array(key, "mesh.connectivity", np.array([[1, 2, 3, 4]]))
    store.put_array(key, "mesh.element_part", np.array([0]))
    convergence = Convergence(
        force_balance=[ForceBalance(load_case="U1", applied_n=(1, 0, 0), reaction_n=(-1, 0, 0), relative_error=0)]
    )
    result = PipelineResult(
        input_hash=key, purpose="run", detail_level="standard",
        result=ResultSet(input_hash=key, fields=fields, combined_cases=[], key_numbers=[], visual_assets=[]),
        convergence=convergence, actual=Actual(dof=12, time_s=1, peak_memory_gb=0, disk_gb=0),
        mesh=MeshSummary(dof=12, elements=1, min_quality=1, straightened=0), locked_results=[], library_versions=[],
        versions=Versions(engine_version="0", engine_source_hash="f" * 64, conventions_version="1.1.0",
                          solver_build_id=BUILD, gmsh_version="4.15.2", dev_build=True),
        timings_s={},
    )  # fmt: skip
    store.put_json(key, RESULT, result.model_dump(mode="json"))
    store.complete(key)


def test_unit_cases_are_reused_only_from_the_same_mesh_and_cases(tmp_path: Path) -> None:
    store = ContentStore(tmp_path / "store")
    coordinates = np.arange(12, dtype=np.float64).reshape(4, 3)
    names = ["crown-1.fossa.x", "crown-1.fossa.y", "crown-1.fossa.z"]
    stored_run(store, "a" * 64, names, coordinates)
    mesh: Any = SimpleNamespace(coordinates=coordinates.copy(), connectivity=np.array([[1, 2, 3, 4]]),
                                element_part=np.array([0]))  # fmt: skip
    found = stored_unit_cases(store, "a" * 64, mesh, names)
    assert found is not None
    displacements, stresses, strains, convergence = found
    assert sorted(displacements) == sorted(names) and stresses[names[0]].shape == (4, 3)
    assert convergence.force_balance[0].load_case == "U1"
    assert stored_unit_cases(store, "a" * 64, mesh, names[:2]) is None  # other unit cases
    assert stored_unit_cases(store, "b" * 64, mesh, names) is None  # nothing stored
    moved: Any = SimpleNamespace(coordinates=coordinates + 1e-9, connectivity=mesh.connectivity,
                                 element_part=mesh.element_part)  # fmt: skip
    assert stored_unit_cases(store, "a" * 64, moved, names) is None  # another mesh
