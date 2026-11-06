"""The verification machinery (tasks T071–T074): case files, builders, the scikit-fem cross-check and the runner.

The committed case files are only read and meshed here, never solved: a case is first run by
`dentalsim verify` once its file is committed (research R15). The solving paths are tested on problems of these
tests' own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from dentalsim.errors import Refused
from dentalsim.persistence import CapabilityStatus, open_repos
from dentalsim.physics.registry import capability_state, summarise
from dentalsim.solvers.calculix.inp_writer import SolverModel, inp_lines, validate_model
from dentalsim.solvers.skfem.crosscheck import solve_skfem
from dentalsim.verification.builders import BUILDERS, builder_for
from dentalsim.verification.cases import Quantity, load_case, load_cases, select_cases, uncommitted
from dentalsim.verification.runner import run_verification
from dentalsim.verification.solution import CaseSolution

TOY = {
    "id": "toy-bars",
    "title": "Two bars (test only)",
    "kind": "textbook",
    "capability": "bonded_elastic",
    "model": {
        "builder": "bonded_bars",
        "parameters": {
            "length_1_mm": 6.0,
            "length_2_mm": 4.0,
            "side_mm": 2.0,
            "E1_mpa": 1000.0,
            "E2_mpa": 500.0,
            "nu1": 0.25,
            "nu2": 0.125,
            "load_n": 4.0,
            "mesh_size_mm": {"standard": 1.0, "preview": 2.0},
        },
    },
    "quantities": [
        {
            "id": "end_displacement",
            "description": "end",
            "unit": "mm",
            "expected": 4.0 * 6.0 / (1000 * 4) + 4.0 * 4.0 / (500 * 4),
            "expected_source": "test",
            "compare": "relative",
            "tolerance": 1e-6,
        },
        {
            "id": "stress_bar_1",
            "description": "stress",
            "unit": "MPa",
            "expected": 1.0,
            "expected_source": "test",
            "compare": "relative",
            "tolerance": 1e-6,
        },
        {
            "id": "stress_bar_2",
            "description": "stress",
            "unit": "MPa",
            "expected": 1.0,
            "expected_source": "test",
            "compare": "relative",
            "tolerance": 1e-6,
        },
    ],
    "source": {"citation": "test only"},
}


def write_case(folder: Path, data: dict[str, Any]) -> Path:
    path = folder / f"{data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def skfem_solver(model: SolverModel, work_dir: Path) -> CaseSolution:
    return solve_skfem(model)


# --- the case files ----------------------------------------------------------------------------------------------


def test_the_textbook_case_files_are_valid() -> None:
    cases = load_cases()
    assert [c.case.id for c in cases] == [
        "textbook-bonded-bars",
        "textbook-cantilever",
        "textbook-cylinder-convergence",
        "textbook-lame-cylinder",
        "textbook-nafems-le10",
        "textbook-patch-test",
    ]
    for loaded in cases:
        case = loaded.case
        assert case.kind == "textbook" and case.capability == "bonded_elastic" and case.required
        assert case.model.builder in BUILDERS
        assert len(loaded.sha256) == 64
        assert case.levels[0] == "standard"


@pytest.mark.parametrize(
    ("compare", "tolerance", "computed", "ok"),
    [
        ("absolute", 0.1, 1.09, True),
        ("absolute", 0.1, 1.11, False),
        ("relative", 0.1, 1.09, True),
        ("relative", 0.1, 0.89, False),
        ("ratio", 1.0, 1.99, True),
        ("ratio", 1.0, 0.51, True),
        ("ratio", 1.0, 0.49, False),
        ("relative", 0.1, float("nan"), False),
    ],
)
def test_quantities_compare_as_their_case_says(compare: str, tolerance: float, computed: float, ok: bool) -> None:
    quantity = Quantity(
        id="q", description="", unit="mm", expected=1.0, expected_source="x", compare=compare, tolerance=tolerance
    )
    assert quantity.passes(computed) is ok


def test_cases_are_chosen_by_id_or_physics(tmp_path: Path) -> None:
    write_case(tmp_path, TOY)
    cases = load_cases(tmp_path)
    assert select_cases(cases, physics="bonded_elastic") == cases
    with pytest.raises(Refused, match="no verification case matches"):
        select_cases(cases, physics="contact")
    with pytest.raises(Refused, match="there is no verification case nope"):
        select_cases(cases, case_ids=["nope"])


def test_a_case_id_must_match_its_file_name(tmp_path: Path) -> None:
    path = write_case(tmp_path, TOY)
    path.rename(tmp_path / "other.yaml")
    with pytest.raises(Exception, match="must match the file name"):
        load_case(tmp_path / "other.yaml")


def test_uncommitted_case_files_are_found(tmp_path: Path) -> None:
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    path = write_case(tmp_path, TOY)
    assert uncommitted([path]) == [path]
    subprocess.run([*git, "add", path.name], cwd=tmp_path, check=True)
    assert uncommitted([path]) == [path]  # staged is not committed
    subprocess.run([*git, "commit", "-q", "-m", "case"], cwd=tmp_path, check=True)
    assert uncommitted([path]) == []
    path.write_text(path.read_text(encoding="utf-8").replace("1.0e-06", "2.0e-06"), encoding="utf-8")
    assert uncommitted([path]) == [path]


# --- the builders (meshed, not solved) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    [
        "textbook-cantilever",
        "textbook-lame-cylinder",
        "textbook-nafems-le10",
        "textbook-patch-test",
        "textbook-bonded-bars",
    ],
)
def test_each_case_model_builds_at_its_preview_level(case_id: str) -> None:
    loaded = next(c for c in load_cases() if c.case.id == case_id)
    built = builder_for(loaded.case.model.builder)(loaded.case.model.parameters, "preview")
    (model,) = built.models
    validate_model(model)
    case = model.load_cases[0]
    if case.patch:
        assert sum(w for _, w in case.patch) == pytest.approx(1.0)
    lines = list(inp_lines(model))
    assert ("*DLOAD" in lines) == bool(case.pressures)
    assert ("*BOUNDARY" in lines[lines.index("*STEP") :]) == bool(case.prescribed)


def test_the_le10_pressure_covers_the_top_face() -> None:
    loaded = next(c for c in load_cases() if c.case.id == "textbook-nafems-le10")
    (model,) = builder_for("nafems_le10")(loaded.case.model.parameters, "preview").models
    faces = model.load_cases[0].pressures
    from dentalsim.mesh.load_patch import TET10_FACES, face_area_vectors

    rows = {int(e): i for i, e in enumerate(model.element_ids)}
    total = np.zeros(3)
    for element, face, pressure in faces:
        nodes = model.connectivity[rows[element]][list(TET10_FACES[face - 1])]
        total += pressure * face_area_vectors(model.coordinates[nodes - 1]).sum(axis=0)
    quarter_ring = np.pi / 4 * (3250 * 2750 - 2000 * 1000)
    # Straight-sided faces cut the curved edges a little short on the coarse preview mesh.
    assert total == pytest.approx((0.0, 0.0, -quarter_ring), rel=5e-3, abs=1e-6 * quarter_ring)


# --- the cross-check and the runner, on this test's own problems ---------------------------------------------------


def test_the_skfem_cross_check_solves_two_bars_exactly(tmp_path: Path) -> None:
    write_case(tmp_path, TOY)
    (loaded,) = load_cases(tmp_path)
    built = builder_for("bonded_bars")(loaded.case.model.parameters, "standard")
    values = built.evaluate([solve_skfem(model) for model in built.models])
    for quantity in loaded.case.quantities:
        assert values[quantity.id] == pytest.approx(quantity.expected, rel=1e-9)


def test_the_patch_test_passes_on_the_cross_check(tmp_path: Path) -> None:
    params = {
        "side_mm": 3.0,
        "E_mpa": 1000.0,
        "nu": 0.2,
        "strain": [1e-3, 2e-4, -3e-4, 1e-4, 0.0, -2e-4],
        "mesh_size_mm": {"standard": 1.5},
    }
    built = builder_for("patch_test")(params, "standard")
    values = built.evaluate([solve_skfem(model) for model in built.models])
    assert values["max_strain_error"] < 1e-9 and values["max_stress_error"] < 1e-9


def test_the_runner_reports_and_makes_the_capability_ready(tmp_path: Path, data_dir: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    write_case(cases_dir, TOY)
    cases = load_cases(cases_dir)
    repos = open_repos(data_dir)
    try:
        report = run_verification(
            cases,
            solver=skfem_solver,
            solver_build_id="test-build",
            work_dir=tmp_path / "work",
            repos=repos,
            crosscheck=solve_skfem,
            require_committed=False,
            cases_dir=cases_dir,
        )
        assert report.overall == "pass", report.failures
        assert [r.level for r in report.case_results] == ["standard"] * 3 + ["preview"] * 3
        assert all(r.judged == (r.level == "standard") for r in report.case_results)
        assert all(c.agrees for c in report.crosscheck) and len(report.crosscheck) == 3
        assert report.preview_accuracy.measured_on == ["textbook"]
        assert report.preview_accuracy.max_relative_difference is not None
        assert report.accuracy_note() is not None and "textbook verification cases" in report.accuracy_note()
        (outcome,) = report.capabilities
        assert (outcome.capability_id, outcome.status, outcome.required_cases) == (
            "bonded_elastic",
            "ready",
            ["toy-bars"],
        )
        assert repos.verification.get_report(report.id) is not None
        assert repos.verification.latest_report()["id"] == report.id  # type: ignore[index]
        assert capability_state("bonded_elastic", repos.capabilities, cases_dir) == "ready"
        assert [e.type for e in repos.events.since(0)] == ["capability.changed"]

        # A new required case returns the capability to awaiting benchmark until a report covers it.
        write_case(cases_dir, {**TOY, "id": "toy-bars-2"})
        summary = summarise("bonded_elastic", repos.capabilities, cases_dir)
        assert summary.status == "awaiting_benchmark"
        assert summary.message is not None and "toy-bars-2 has not passed yet" in summary.message

        # A failing required case locks it again (FR-062).
        failing = {**TOY, "quantities": [{**TOY["quantities"][0], "expected": 1.0}]}
        write_case(cases_dir, failing)
        report = run_verification(
            select_cases(load_cases(cases_dir), case_ids=["toy-bars"]),
            solver=skfem_solver,
            solver_build_id="test-build",
            work_dir=tmp_path / "work2",
            repos=repos,
            require_committed=False,
            cases_dir=cases_dir,
        )
        assert report.overall == "fail" and report.capabilities[0].status == "awaiting_benchmark"
        stored = repos.capabilities.get("bonded_elastic")
        assert stored is not None and stored.status == "awaiting_benchmark" and stored.report_id == report.id
        assert [e.type for e in repos.events.since(0)] == ["capability.changed", "capability.changed"]
    finally:
        repos.close()


def test_the_runner_refuses_uncommitted_case_files(tmp_path: Path) -> None:
    write_case(tmp_path, TOY)
    with pytest.raises(Refused, match="Commit the verification case files before running them"):
        run_verification(load_cases(tmp_path), solver=skfem_solver, solver_build_id="x", work_dir=tmp_path / "w")


def test_a_case_that_cannot_be_solved_fails_with_its_reason(tmp_path: Path) -> None:
    write_case(tmp_path, {**TOY, "model": {"builder": "nope", "parameters": {}}})
    report = run_verification(
        load_cases(tmp_path), solver=skfem_solver, solver_build_id="x", work_dir=tmp_path / "w", require_committed=False
    )
    assert report.overall == "fail"
    assert report.failures == [
        "toy-bars: there is no verification model builder 'nope'; the known ones are " + ", ".join(sorted(BUILDERS))
    ]


def test_a_ready_status_needs_the_report_cases(tmp_path: Path, data_dir: Path) -> None:
    write_case(tmp_path, TOY)
    repos = open_repos(data_dir)
    try:
        repos.verification.add_report(
            {
                "id": "R1",
                "engine_version": "x",
                "solver_build_id": "y",
                "conventions_version": "1.1.0",
                "overall": "pass",
                "created_at": "2026-11-06T00:00:00+00:00",
            }
        )
        repos.capabilities.set(
            CapabilityStatus(capability_id="bonded_elastic", status="ready", report_id="R1", cases=["toy-bars"])
        )
        assert repos.capabilities.get("bonded_elastic").cases == ["toy-bars"]  # type: ignore[union-attr]
        assert capability_state("bonded_elastic", repos.capabilities, tmp_path) == "ready"
    finally:
        repos.close()
