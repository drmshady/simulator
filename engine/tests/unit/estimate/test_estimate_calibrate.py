"""The calibration box and the calibration flow (T058), with the solver replaced by a fake run."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dentalsim.config import get_settings
from dentalsim.estimate import calibrate
from dentalsim.estimate.model import load_coefficients
from dentalsim.solvers.calculix.build import SolverBuild
from dentalsim.solvers.calculix.inp_writer import validate_model
from dentalsim.solvers.calculix.runner import SolverRunResult

SHA = "cd" * 32
BUILD = SolverBuild(
    path=Path("ccx.exe"),
    version="2.23",
    equation_solver="pastix",
    preferred_solver_ok=True,
    sha256=SHA,
    build_id=f"ccx-2.23-pastix-sha256:{SHA}",
)


def test_box_model_is_valid_deterministic_and_in_calculix_order() -> None:
    model = calibrate.box_model(5.0)
    validate_model(model)
    assert model.dof == 3 * model.node_ids.size > 0
    assert len(model.load_cases) == 3
    assert [case.force for case in model.load_cases] == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    weights = [w for _, w in model.load_cases[0].patch]
    assert sum(weights) == pytest.approx(1.0, abs=1e-12)

    coordinates = dict(zip(model.node_ids.tolist(), model.coordinates.tolist(), strict=True))
    bottom = np.array([coordinates[n] for n in model.supports[0].node_ids.tolist()])
    assert np.allclose(bottom[:, 2], 0.0)
    patch = np.array([coordinates[n] for n, _ in model.load_cases[0].patch])
    assert np.allclose(patch[:, 2], 20.0)

    # Positive volume with CalculiX corner order, and midside nodes at their CalculiX edges.
    first = np.array([coordinates[n] for n in model.connectivity[0].tolist()])
    a, b, c, d = first[:4]
    assert np.dot(np.cross(b - a, c - a), d - a) > 0
    np.testing.assert_allclose(first[8], (b + d) / 2)  # node 9 of C3D10 sits on edge 2-4
    np.testing.assert_allclose(first[9], (c + d) / 2)  # node 10 sits on edge 3-4

    again = calibrate.box_model(5.0)
    assert np.array_equal(again.connectivity, model.connectivity)
    assert np.array_equal(again.coordinates, model.coordinates)


def fake_run(work: Path, jobname: str, **kwargs: Any) -> SolverRunResult:
    text = (work / f"{jobname}.inp").read_text()
    nodes = len(text.split("*NODE\n")[1].split("*ELEMENT")[0].splitlines())
    (work / f"{jobname}.frd").write_bytes(b"x" * 100 * nodes)
    return SolverRunResult(
        jobname=jobname,
        work_dir=work,
        command=("ccx", "-i", jobname),
        threads=kwargs["threads"],
        returncode=0,
        wall_time_s=3 * 1e-6 * (3 * nodes) ** 1.5,
        peak_memory_bytes=round(400 * (3 * nodes) ** 1.1),
        steps_total=3,
        steps_seen=3,
        messages=(),
        relative_residual=None,
        frd_path=work / f"{jobname}.frd",
        dat_path=work / f"{jobname}.dat",
        log_path=work / f"{jobname}.log",
        disk_bytes=100 * nodes,
    )


def test_calibration_measures_fits_and_saves(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibrate, "solver_build", lambda ccx=None: BUILD)
    monkeypatch.setattr(calibrate, "run_ccx", fake_run)
    messages: list[str] = []
    result = calibrate.calibrate((8.0, 5.0), threads=2, log=messages.append)

    assert [p.size_mm for p in result.points] == [8.0, 5.0]
    assert result.points[0].dof < result.points[1].dof
    assert all(p.cases == 3 and p.threads == 2 for p in result.points)
    assert result.fit.time_per_case.exponent == pytest.approx(1.5, rel=1e-6)
    assert result.fit.memory.exponent == pytest.approx(1.1, rel=1e-6)
    assert result.saved_to == data_dir / "estimate" / "coefficients.json"
    assert load_coefficients().builds[BUILD.build_id] == result.fit
    assert not any((data_dir / "work" / "calibration").iterdir())  # work folders removed without --keep
    assert any("solving" in message for message in messages)

    table = calibrate.format_table(result)
    assert BUILD.build_id in table
    assert table.count("\n| ") == 3  # header and one row per size


def test_calibration_without_saving(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibrate, "solver_build", lambda ccx=None: BUILD)
    monkeypatch.setattr(calibrate, "run_ccx", fake_run)
    result = calibrate.calibrate((8.0, 5.0), threads=1, save=False, keep=True)
    assert result.saved_to is None
    assert not (data_dir / "estimate" / "coefficients.json").exists()
    assert (data_dir / "work" / "calibration" / "h5mm" / "calibration.inp").is_file()
    with pytest.raises(ValueError):
        calibrate.calibrate((5.0,))


def test_command_line_without_a_solver(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DENTALSIM_CCX", raising=False)
    get_settings.cache_clear()
    assert calibrate.main(["--sizes", "8,5"]) == 2
    with pytest.raises(SystemExit):
        calibrate.main(["--sizes", "8"])
