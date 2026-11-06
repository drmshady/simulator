"""The time, memory and disk estimate model (T058): fitting, prediction, refits and the JSON file."""

import json
from pathlib import Path

import pytest

from dentalsim.errors import NotFound
from dentalsim.estimate import model
from dentalsim.estimate.model import EstimateSample
from dentalsim.testing import FIXED_TIME, FixedClock

BUILD = "ccx-2.23-pardiso-sha256:" + "0" * 64


def sample(dof: int, cases: int = 3, source: str = "calibration", **override: float) -> EstimateSample:
    """A sample that follows time = cases·2e-6·DOF^1.4, memory = 500·DOF^1.2, disk = 300·DOF·cases exactly."""
    values = {
        "time_s": cases * 2e-6 * dof**1.4,
        "memory_bytes": round(500 * dof**1.2),
        "disk_bytes": round(300 * dof * cases),
    }
    values.update(override)
    return EstimateSample(dof=dof, cases=cases, source=source, threads=4, **values)


def test_fit_recovers_the_power_laws(fixed_time: FixedClock) -> None:
    fit = model.fit([sample(30_000), sample(150_000), sample(700_000, cases=9)], BUILD)
    assert fit.fit_kind == "power_law"
    assert fit.time_per_case.exponent == pytest.approx(1.4, rel=1e-9)
    assert fit.time_per_case.coefficient == pytest.approx(2e-6, rel=1e-7)
    assert fit.memory.exponent == pytest.approx(1.2, rel=1e-6)
    assert fit.memory.coefficient == pytest.approx(500, rel=1e-5)
    assert fit.disk_per_dof_case == pytest.approx(300, rel=1e-6)
    assert fit.fitted_at == FIXED_TIME


def test_prediction_scales_time_and_disk_with_the_cases() -> None:
    fit = model.fit([sample(30_000), sample(150_000), sample(700_000)], BUILD)
    three = model.predict_with(fit, 1_000_000, 3)
    nine = model.predict_with(fit, 1_000_000, 9)
    assert three.time_s == pytest.approx(3 * 2e-6 * 1e6**1.4, rel=1e-6)
    assert nine.time_s == pytest.approx(3 * three.time_s)
    assert nine.disk_bytes == pytest.approx(3 * three.disk_bytes, rel=1e-9)
    assert nine.memory_bytes == three.memory_bytes == pytest.approx(500 * 1e6**1.2, rel=1e-5)
    with pytest.raises(ValueError):
        model.predict_with(fit, 0, 3)


def test_one_dof_value_uses_the_default_exponents() -> None:
    fit = model.fit([sample(100_000)], BUILD)
    assert fit.fit_kind == "fixed_exponent"
    assert fit.time_per_case.exponent == model.DEFAULT_TIME_EXPONENT
    assert model.predict_with(fit, 100_000, 3).time_s == pytest.approx(sample(100_000).time_s)
    with pytest.raises(ValueError):
        model.fit([], BUILD)


def test_json_round_trip(data_dir: Path, fixed_time: FixedClock) -> None:
    fitted = model.record_calibration([sample(30_000), sample(150_000), sample(700_000)], BUILD)
    path = data_dir / "estimate" / "coefficients.json"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert list(raw["builds"]) == [BUILD]
    assert raw["builds"][BUILD]["fitted_at"] == "2026-01-01T09:00:00Z"
    assert len(raw["builds"][BUILD]["samples"]) == 3
    loaded = model.load_coefficients()
    assert loaded.builds[BUILD] == fitted
    model.save_coefficients(loaded)
    assert model.load_coefficients() == loaded
    assert [p.name for p in path.parent.iterdir()] == ["coefficients.json"]  # no temporary files left behind


def test_predict_reads_the_stored_coefficients(data_dir: Path) -> None:
    model.record_calibration([sample(30_000), sample(150_000)], BUILD)
    estimate = model.predict(400_000, 3, BUILD)
    assert estimate.solver_build_id == BUILD
    assert estimate.time_s == pytest.approx(sample(400_000).time_s, rel=1e-6)
    with pytest.raises(NotFound, match="calibrate"):
        model.predict(400_000, 3, "ccx-2.23-spooles-sha256:" + "1" * 64)


def test_missing_file_means_no_coefficients(data_dir: Path) -> None:
    assert model.load_coefficients().builds == {}


def test_refit_adds_runs_and_recalibration_keeps_them(data_dir: Path, fixed_time: FixedClock) -> None:
    model.record_calibration([sample(30_000), sample(150_000)], BUILD)
    slow_run = sample(500_000, source="run", time_s=2 * sample(500_000).time_s)
    fixed_time.advance(hours=1)
    refitted = model.refit(slow_run, BUILD)
    assert len(refitted.samples) == 3
    assert refitted.fitted_at > FIXED_TIME
    assert model.predict(500_000, 3, BUILD).time_s > sample(500_000).time_s

    recalibrated = model.record_calibration([sample(40_000), sample(160_000), sample(640_000)], BUILD)
    sources = [s.source for s in recalibrated.samples]
    assert sources.count("calibration") == 3 and sources.count("run") == 1


def test_refit_of_a_new_build(data_dir: Path) -> None:
    fitted = model.refit(sample(200_000, source="run"), BUILD)
    assert fitted.fit_kind == "fixed_exponent"
    assert model.coefficients_path() == data_dir / "estimate" / "coefficients.json"


def test_samples_refuse_impossible_values() -> None:
    with pytest.raises(ValueError):
        EstimateSample(dof=0, cases=3, time_s=1.0, memory_bytes=1, disk_bytes=1)
    with pytest.raises(ValueError):
        EstimateSample(dof=10, cases=3, time_s=1.0, memory_bytes=1, disk_bytes=1, patient="x")  # type: ignore[call-arg]
