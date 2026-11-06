"""Run time, memory and disk models per solver build, fitted to measured runs (research R8, FR-009, FR-057).

- time:   time_s ≈ cases · a · DOF^b. ccx assembles and factorizes the stiffness matrix again in every `*STEP`
  (its log repeats "Factoring the system of equations" per step), so the time of one unit case follows the power
  law and a run with n unit cases takes about n times as long. With the 3-case calibration runs this is R8's
  a·DOF^b fit, divided by 3.
- memory: peak_bytes ≈ c · DOF^d. Each step frees its factorization, so memory does not grow with the cases.
- disk:   disk_bytes ≈ e · DOF · cases.

DOF is 3 × the number of nodes, before supports are applied. Time and memory are fitted by least squares in log
space; disk is the geometric mean of disk / (DOF · cases). When the samples cover only one DOF value, the exponents
fall back to `DEFAULT_TIME_EXPONENT` and `DEFAULT_MEMORY_EXPONENT`.

The coefficients live in `<data>/estimate/coefficients.json`, keyed by solver build id, with their samples and fit
date. `refit()` adds a finished run and fits again (T082, T088).
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from dentalsim import clock
from dentalsim.config import Settings, get_settings
from dentalsim.errors import NotFound

COEFFICIENTS_FILE = "coefficients.json"
DEFAULT_TIME_EXPONENT = 1.5
DEFAULT_MEMORY_EXPONENT = 1.1
MAX_RUN_SAMPLES = 500


class EstimateSample(BaseModel):
    """One measured solve."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dof: int = Field(gt=0)
    cases: int = Field(ge=1)
    time_s: float = Field(gt=0)
    memory_bytes: int = Field(gt=0)
    disk_bytes: int = Field(gt=0)
    threads: int | None = Field(default=None, ge=1)
    source: Literal["calibration", "run"] = "run"
    label: str = ""
    recorded_at: datetime = Field(default_factory=clock.utc_now)


class PowerLaw(BaseModel):
    """value ≈ coefficient · x^exponent"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    coefficient: float = Field(gt=0)
    exponent: float

    def __call__(self, x: float) -> float:
        return float(self.coefficient * x**self.exponent)


class BuildFit(BaseModel):
    """The fitted coefficients of one solver build and the samples behind them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    solver_build_id: str
    fitted_at: datetime
    fit_kind: Literal["power_law", "fixed_exponent"]
    time_per_case: PowerLaw
    memory: PowerLaw
    disk_per_dof_case: float = Field(gt=0)
    samples: tuple[EstimateSample, ...]


class CoefficientsFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = 1
    builds: dict[str, BuildFit] = Field(default_factory=dict)


class Estimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    solver_build_id: str
    dof: int
    cases: int
    time_s: float
    memory_bytes: int
    disk_bytes: int


def coefficients_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir / "estimate" / COEFFICIENTS_FILE


def load_coefficients(path: Path | None = None) -> CoefficientsFile:
    """The stored coefficients, or an empty set when the file does not exist yet."""
    path = path or coefficients_path()
    if not path.is_file():
        return CoefficientsFile()
    return CoefficientsFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_coefficients(coefficients: CoefficientsFile, path: Path | None = None) -> Path:
    """Write the coefficients atomically (a reader never sees a half-written file)."""
    path = path or coefficients_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = coefficients.model_dump_json(indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=".coefficients-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def _log_fit(x: NDArray[np.float64], y: NDArray[np.float64], default_exponent: float) -> tuple[PowerLaw, bool]:
    """Least squares of log y = log a + b log x. Returns (law, whether the exponent was fitted)."""
    lx, ly = np.log(x), np.log(y)
    if np.unique(x).size >= 2:
        exponent, intercept = np.polyfit(lx, ly, 1)
        return PowerLaw(coefficient=float(math.exp(intercept)), exponent=float(exponent)), True
    intercept = float(np.mean(ly - default_exponent * lx))
    return PowerLaw(coefficient=math.exp(intercept), exponent=default_exponent), False


def fit(samples: Sequence[EstimateSample], solver_build_id: str) -> BuildFit:
    """Fit the time, memory and disk models to `samples` (at least one)."""
    if not samples:
        raise ValueError("fitting needs at least one sample")
    dof = np.array([s.dof for s in samples], dtype=np.float64)
    cases = np.array([s.cases for s in samples], dtype=np.float64)
    time_s = np.array([s.time_s for s in samples], dtype=np.float64)
    memory = np.array([s.memory_bytes for s in samples], dtype=np.float64)
    disk = np.array([s.disk_bytes for s in samples], dtype=np.float64)
    time_law, fitted = _log_fit(dof, time_s / cases, DEFAULT_TIME_EXPONENT)
    memory_law, _ = _log_fit(dof, memory, DEFAULT_MEMORY_EXPONENT)
    disk_per = float(np.exp(np.mean(np.log(disk / (dof * cases)))))
    return BuildFit(
        solver_build_id=solver_build_id,
        fitted_at=clock.utc_now(),
        fit_kind="power_law" if fitted else "fixed_exponent",
        time_per_case=time_law,
        memory=memory_law,
        disk_per_dof_case=disk_per,
        samples=tuple(samples),
    )


def predict_with(build: BuildFit, dof: int, cases: int) -> Estimate:
    if dof <= 0 or cases < 1:
        raise ValueError("dof must be positive and cases at least 1")
    return Estimate(
        solver_build_id=build.solver_build_id,
        dof=dof,
        cases=cases,
        time_s=cases * build.time_per_case(dof),
        memory_bytes=round(build.memory(dof)),
        disk_bytes=round(build.disk_per_dof_case * dof * cases),
    )


def predict(
    dof: int,
    cases: int,
    solver_build_id: str,
    *,
    coefficients: CoefficientsFile | None = None,
    path: Path | None = None,
) -> Estimate:
    """Expected time, peak memory and disk use of a run. Raises NotFound when the build has no coefficients."""
    coefficients = coefficients or load_coefficients(path)
    build = coefficients.builds.get(solver_build_id)
    if build is None:
        raise NotFound(
            f"There are no time and memory estimates for the solver build {solver_build_id}. Run "
            "`uv run python -m dentalsim.estimate.calibrate` on the main PC (docs/setup/solver.md)."
        )
    return predict_with(build, dof, cases)


def _store(solver_build_id: str, samples: Sequence[EstimateSample], path: Path | None) -> BuildFit:
    coefficients = load_coefficients(path)
    calibration = [s for s in samples if s.source == "calibration"]
    runs = [s for s in samples if s.source == "run"][-MAX_RUN_SAMPLES:]
    build = fit([*calibration, *runs], solver_build_id)
    save_coefficients(
        CoefficientsFile(builds={**coefficients.builds, solver_build_id: build}),
        path,
    )
    return build


def refit(sample: EstimateSample, solver_build_id: str, *, path: Path | None = None) -> BuildFit:
    """Add a finished run's measured values to the build's samples, fit again and save."""
    coefficients = load_coefficients(path)
    previous = coefficients.builds.get(solver_build_id)
    samples = [*(previous.samples if previous else ()), sample]
    return _store(solver_build_id, samples, path)


def record_calibration(
    samples: Sequence[EstimateSample], solver_build_id: str, *, path: Path | None = None
) -> BuildFit:
    """Replace the build's calibration samples with `samples` (keeping its run samples), fit and save."""
    coefficients = load_coefficients(path)
    previous = coefficients.builds.get(solver_build_id)
    runs = [s for s in (previous.samples if previous else ()) if s.source == "run"]
    marked = [s.model_copy(update={"source": "calibration"}) for s in samples]
    return _store(solver_build_id, [*marked, *runs], path)
