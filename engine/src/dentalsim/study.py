"""Studies: parameter sweeps and sensitivity checks, format `dentalsim.study/1` (task T123; FR-020, FR-021, FR-057;
research R4). The studio (`POST /api/studies`) and the command line (`dentalsim study <study.yaml>`) both queue them
here, so both make the same runs.

- **Format**: a base scenario (a studio scenario id) and either `kind: sweep` with `axes` (each a setting path and
  its values) or `kind: sensitivity` with a `parameter` (the path of a library setting).
- **Paths** are written as the diff and the search write them: `<block>.<setting>` (the unit suffix may be left out
  when only one setting matches: `implant-1.length` is `implant-1.length_mm`), `<block>.placement.<field>`, and
  `load:<id>.<field>` (`load:axial.force`, `load:oblique.direction.angle_deg`). No block type is named here (SC-012).
- **Sweep**: the Cartesian product of the axes' values, the first axis varying slowest. Each run is a resolved
  snapshot: a library axis takes the value as `custom` (or `typical` when it is the typical value), and a design
  change that names other library entries (another density class, say) takes their typical values
  (`dentalsim.resolve.checked_scenario`).
- **Sensitivity**: three runs at the minimum, typical and maximum of the setting's approved version's range.
- **Load-only axes** (a load's force, bite angle or azimuth) change neither the model nor its unit load cases, so
  only the first run of each group of runs that differ only in them solves; the rest reuse its unit cases by
  superposition (research R4; `dentalsim.pipeline.stored_unit_cases`).
- **Before queuing**: the fast validity checks and the physics gate for every run, then one estimate for the whole
  study (FR-057): refused when a run does not fit in memory, warned when the free disk space is less than twice what
  all the runs together are expected to use. Building a model to estimate it takes seconds, so runs whose meshes
  cannot differ share one estimate: those that differ only in load forces and directions, or in library values that
  are not lengths or angles (moduli, Poisson's ratios, strengths). The full validity checks run with each run, which
  fails with its reason while the others continue.
- **Summary**: for each axis and each key number, the mean over the finished runs at each of the axis's values, and
  their spread: minimum, maximum, range and the range as a percentage of the reference (the typical value for a
  sensitivity study, the axis's first value for a sweep).
"""

from __future__ import annotations

import itertools
import json
import math
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dentalsim.assembly.validity import check_validity
from dentalsim.blocks.registry import catalogue
from dentalsim.clock import new_ulid, utc_now
from dentalsim.config import get_settings
from dentalsim.errors import DentalsimError, NotFound, Refused
from dentalsim.estimate.model import CoefficientsFile, load_coefficients
from dentalsim.estimate.predict import DISK_MARGIN, GB, MEMORY_LIMIT, RunEstimate, estimate_run
from dentalsim.library.service import LibraryService
from dentalsim.model.results import KeyNumber
from dentalsim.model.run import FINISHED, Estimate, Run, RunStatus
from dentalsim.model.scenario import DetailLevel, Scenario
from dentalsim.model.settings import LibraryValue
from dentalsim.persistence import Repos, StudyAxis, StudyKind, StudyPoint, StudyRecord, StudyStatus
from dentalsim.physics.gate import check_gate
from dentalsim.pipeline import load_result
from dentalsim.resolve import checked_scenario
from dentalsim.runs import failed_run, library_inputs, new_run
from dentalsim.store.cas import ContentStore

STUDY_FORMAT: Literal["dentalsim.study/1"] = "dentalsim.study/1"
MAX_RUNS = 100
"""A larger study is almost certainly a mistake in its values (100 standard runs take about two days)."""
SENSITIVITY_LABELS = ("minimum", "typical", "maximum")
LOAD_ONLY_FIELDS = ("force", "direction.angle_deg", "direction.azimuth_deg")
GEOMETRY_UNITS = frozenset({"mm", "deg", "°"})
"""Library values in these units may shape the model; values in any other unit (MPa, 1, N, ...) cannot."""
REL_TOLERANCE = 1e-9


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyDefinition(_Model):
    """A study as the owner asks for it: the body of `POST /api/studies`, or a `study.yaml` file."""

    format: Literal["dentalsim.study/1"] = STUDY_FORMAT
    name: str = ""
    project_id: str | None = None
    """Default: the base scenario's project."""
    base_scenario_id: str = Field(min_length=1)
    kind: StudyKind
    detail_level: DetailLevel = "standard"
    axes: list[StudyAxis] = []
    """A sweep's settings and values."""
    parameter: str | None = None
    """A sensitivity study's library setting."""

    @model_validator(mode="after")
    def _kind_fields(self) -> StudyDefinition:
        if self.detail_level == "preview":
            raise ValueError("a study runs detailed runs; choose standard, fine or extra_fine")
        if self.kind == "sweep":
            if not self.axes or self.parameter is not None:
                raise ValueError("a sweep lists its axes (setting paths and values) and no parameter")
            paths = [axis.path for axis in self.axes]
            if len(paths) != len(set(paths)):
                raise ValueError("each setting may be swept by one axis only")
            empty = [axis.path for axis in self.axes if not axis.values]
            if empty:
                raise ValueError(f"the axes {', '.join(empty)} have no values")
        elif not self.parameter or self.axes:
            raise ValueError("a sensitivity study names one library setting as its parameter, and no axes")
        return self


# --- setting paths -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingPath:
    """Where a path points in the scenario's JSON form: `keys` from the scenario down to the value."""

    path: str
    keys: tuple[str | int, ...]
    load_only: bool


def _refuse(message: str) -> Refused:
    return Refused(message, code="validation_failed")


def resolve_path(scenario: Scenario, path: str) -> SettingPath:
    """The place `path` names in `scenario`; refused (`validation_failed`) when it names nothing."""
    if path.startswith("load:"):
        load_id, _, field = path.removeprefix("load:").partition(".")
        index = next((i for i, load in enumerate(scenario.loads) if load.id == load_id), None)
        if index is None:
            raise _refuse(f"{path}: the scenario has no load case {load_id!r}")
        keys: tuple[str | int, ...] = ("loads", index, *field.split("."))
        if not field or not _is_leaf(scenario.model_dump(mode="json"), keys):
            raise _refuse(f"{path}: load case {load_id!r} has no setting {field!r}")
        return SettingPath(path, keys, field in LOAD_ONLY_FIELDS)
    block_id, _, field = path.partition(".")
    index = next((i for i, block in enumerate(scenario.blocks) if block.instance_id == block_id), None)
    if index is None or not field:
        raise _refuse(f"{path}: the scenario has no block {block_id!r}")
    if field.startswith("placement."):
        parts = field.split(".")
        keys = ("blocks", index, "placement", *(int(p) if p.isdigit() else p for p in parts[1:]))
        if not _is_leaf(scenario.model_dump(mode="json"), keys):
            raise _refuse(f"{path}: there is no placement field {field.removeprefix('placement.')!r}")
        return SettingPath(path, keys, False)
    block = scenario.blocks[index]
    ids = [spec.id for spec in catalogue().for_instance(block).setting_specs()]
    matches = [field] if field in ids else [i for i in ids if i.startswith(f"{field}_")]
    if len(matches) != 1:
        found = f"; it could be {', '.join(matches)}" if matches else ""
        raise _refuse(f"{path}: block {block_id!r} has no setting {field!r}{found}")
    return SettingPath(f"{block_id}.{matches[0]}", ("blocks", index, "settings", matches[0]), False)


def _is_leaf(data: Any, keys: Sequence[str | int]) -> bool:
    for key in keys:
        in_dict = isinstance(data, dict) and isinstance(key, str) and key in data
        in_list = isinstance(data, list) and isinstance(key, int) and 0 <= key < len(data)
        if not (in_dict or in_list):
            return False
        data = data[key]
    return not isinstance(data, dict | list) or _is_setting(data)


def _is_setting(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") in ("design", "library")


def library_value_at(scenario: Scenario, path: str) -> tuple[SettingPath, LibraryValue]:
    """The library value `path` names; refused when it names a design value or nothing."""
    where = resolve_path(scenario, path)
    value = _get(scenario.model_dump(mode="json"), where.keys)
    if not (isinstance(value, dict) and value.get("kind") == "library"):
        raise _refuse(f"{where.path} is not a library setting, so it has no published range")
    return where, LibraryValue.model_validate(value)


def _get(data: Any, keys: Sequence[str | int]) -> Any:
    for key in keys:
        data = data[key]
    return data


def _typed(old: Any, new: Any) -> Any:
    """`new` in the type of the value it replaces: 8 for a length in mm becomes 8.0, and 3.0 for a count becomes 3."""
    if isinstance(old, bool) or isinstance(new, bool):
        return new
    if isinstance(old, float) and isinstance(new, int):
        return float(new)
    if isinstance(old, int) and isinstance(new, float) and new.is_integer():
        return int(new)
    return new


def _set(data: dict[str, Any], where: SettingPath, value: Any, library: LibraryService) -> None:
    *parents, last = where.keys
    container = _get(data, parents)
    current: Any = container.get(last) if isinstance(container, dict) else container[last]
    if _is_setting(current) and current["kind"] == "library":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _refuse(f"{where.path} is a library setting: its values must be numbers, not {value!r}")
        version = library.get_version(current["entry_id"], current["entry_version"])
        typical = math.isclose(value, version.typical, rel_tol=REL_TOLERANCE, abs_tol=1e-12)
        container[last] = {
            **current,
            "choice": "typical" if typical else "custom",
            "value": version.typical if typical else float(value),
        }
    elif _is_setting(current) or current is None:
        old = current["value"] if current is not None else None
        container[last] = {"kind": "design", "value": _typed(old, value)}
    else:
        container[last] = _typed(current, value)


def apply_values(base: Scenario, values: Mapping[str, Any], library: LibraryService, *, name: str) -> Scenario:
    """`base` with each path set to its value, checked against the library; refused when a value cannot be set."""
    data = base.model_dump(mode="json")
    for path, value in values.items():
        _set(data, resolve_path(base, path), value, library)
    data["name"] = name
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise _refuse(f"{name}: the values cannot be set: {problems}") from exc
    return checked_scenario(scenario, library)


# --- planning ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedRun:
    label: str
    values: dict[str, Any]
    scenario: Scenario
    reuse_of: int | None
    """The index of the run in the plan whose unit load cases this one reuses."""


def sensitivity_axis(base: Scenario, parameter: str, library: LibraryService) -> StudyAxis:
    """The minimum, typical and maximum of the approved range of the library setting `parameter`."""
    where, value = library_value_at(base, parameter)
    resolved = library.resolve_value(value)  # refused unless the version is approved
    if math.isclose(resolved.range_min, resolved.range_max, rel_tol=REL_TOLERANCE, abs_tol=1e-12):
        raise _refuse(f"{resolved.parameter_key} has no published range (its minimum is its maximum)")
    return StudyAxis(path=where.path, values=[resolved.range_min, resolved.typical, resolved.range_max])


def _format(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def plan_study(
    base: Scenario, kind: StudyKind, axes: Sequence[StudyAxis], library: LibraryService
) -> tuple[list[StudyAxis], list[PlannedRun]]:
    """The axes with their paths written in full, and the runs in queue order."""
    paths = [resolve_path(base, axis.path) for axis in axes]
    axes = [StudyAxis(path=where.path, values=list(axis.values)) for where, axis in zip(paths, axes, strict=True)]
    if len({axis.path for axis in axes}) != len(axes):
        raise _refuse("two axes name the same setting")
    count = math.prod(len(axis.values) for axis in axes)
    if count > MAX_RUNS:
        raise _refuse(f"the study would make {count} runs; a study may have at most {MAX_RUNS}")
    runs: list[PlannedRun] = []
    groups: dict[str, int] = {}
    any_load_only = any(where.load_only for where in paths)
    for combination in itertools.product(*(axis.values for axis in axes)):
        values = {axis.path: value for axis, value in zip(axes, combination, strict=True)}
        if kind == "sensitivity":
            label = f"{SENSITIVITY_LABELS[len(runs)]}: {_format(combination[0])}"
        else:
            label = ", ".join(f"{path} {_format(value)}" for path, value in values.items())
        scenario = apply_values(base, values, library, name=f"{base.name} [{label}]")
        group = repr([(w.path, values[w.path]) for w in paths if not w.load_only])
        reuse_of = groups.get(group) if any_load_only else None
        groups.setdefault(group, len(runs))
        runs.append(PlannedRun(label, values, scenario, reuse_of))
    return axes, runs


# --- estimate (FR-057) -------------------------------------------------------------------------------------------


class StudyEstimate(_Model):
    runs: int
    solves: int
    """Runs that solve their own unit load cases; the others reuse one of them."""
    time_s: float | None
    """Expected solve time of every run together, one after another; None without coefficients for this build."""
    memory_gb: float | None
    """The largest run's expected peak memory (runs go one at a time)."""
    disk_gb: float | None
    """Every run's expected disk use together."""
    memory_limit_gb: float | None
    fits: bool
    warnings: list[str]


def _disk_gb(each: Sequence[Estimate | None], index: int) -> float:
    found = each[index]
    return 0.0 if found is None else found.disk_gb


def model_key(scenario: Scenario, library: LibraryService) -> str:
    """What a run's mesh can depend on: the scenario without its name, its loads' forces, directions and cycle
    counts, and its library values that are not lengths or angles. Runs with the same key share one estimate."""
    data = scenario.model_dump(mode="json", exclude={"name"})
    for block in data["blocks"]:
        for setting_id, value in block["settings"].items():
            if value.get("kind") != "library":
                continue
            if library.get_version(value["entry_id"], value["entry_version"]).units not in GEOMETRY_UNITS:
                block["settings"][setting_id] = "not geometry"
    for load in data["loads"]:
        for field in ("force", "cycles", "direction"):
            load.pop(field, None)
        for instant in load.get("instants", []):
            instant.pop("direction", None)
            instant.pop("force_share", None)
    return json.dumps(data, sort_keys=True)


def estimate_study(
    runs: Sequence[PlannedRun],
    detail_level: DetailLevel,
    *,
    library: LibraryService,
    solver_build_id: str,
    coefficients: CoefficientsFile | None = None,
    total_memory_bytes: int | None = None,
    free_disk_bytes: int | None = None,
    data_dir: Path | None = None,
) -> tuple[StudyEstimate, list[Estimate | None]]:
    """The whole study's estimate, and each run's (None for a run that reuses another's unit cases)."""
    coefficients = coefficients or load_coefficients()
    solving = [i for i, run in enumerate(runs) if run.reuse_of is None]
    if solver_build_id not in coefficients.builds:
        warning = (
            f"There are no time and memory estimates for the solver build {solver_build_id} yet, so the study's "
            "memory and disk use could not be checked before queuing."
        )
        return StudyEstimate(runs=len(runs), solves=len(solving), time_s=None, memory_gb=None, disk_gb=None,
                             memory_limit_gb=None, fits=True, warnings=[warning]), [None] * len(runs)  # fmt: skip
    memory = total_memory_bytes if total_memory_bytes is not None else psutil.virtual_memory().total
    each: list[Estimate | None] = [None] * len(runs)
    too_large: list[str] = []
    known: dict[str, RunEstimate] = {}
    for index in solving:
        run = runs[index]
        key = model_key(run.scenario, library)
        found = known.get(key)
        if found is None:
            found = known[key] = estimate_run(
                run.scenario, detail_level, solver_build_id=solver_build_id, coefficients=coefficients,
                total_memory_bytes=memory, free_disk_bytes=2**62,
            )  # fmt: skip
        each[index] = Estimate(dof=found.dof, time_s=found.time_s, memory_gb=found.memory_gb, disk_gb=found.disk_gb)
        if not found.fits:
            too_large.append(f"{run.label} ({found.memory_gb:.1f} GB)")
    solved = [e for e in each if e is not None]
    disk = sum(_disk_gb(each, i if run.reuse_of is None else run.reuse_of) for i, run in enumerate(runs))
    folder = data_dir or get_settings().data_dir
    free = (
        free_disk_bytes
        if free_disk_bytes is not None
        else shutil.disk_usage(folder if folder.exists() else Path.cwd()).free
    )
    limit = MEMORY_LIMIT * memory
    warnings = []
    if too_large:
        warnings.append(
            f"These runs need more than 85% of this machine's {memory / GB:.1f} GB of memory: "
            f"{'; '.join(too_large)}. Choose a lower detail level."
        )
    if free < DISK_MARGIN * disk * GB:
        warnings.append(
            f"Free disk space ({free / GB:.1f} GB) is less than twice what the study's runs are expected to use "
            f"({disk:.1f} GB)."
        )
    estimate = StudyEstimate(
        runs=len(runs),
        solves=len(solving),
        time_s=sum(e.time_s for e in solved),
        memory_gb=max(e.memory_gb for e in solved),
        disk_gb=disk,
        memory_limit_gb=limit / GB,
        fits=not too_large,
        warnings=warnings,
    )
    return estimate, each


# --- queuing -----------------------------------------------------------------------------------------------------


def queue_study(
    definition: StudyDefinition,
    repos: Repos,
    *,
    solver_build_id: str,
    coefficients: CoefficientsFile | None = None,
    total_memory_bytes: int | None = None,
    free_disk_bytes: int | None = None,
) -> StudyRecord:
    """Plan, check and queue the study's runs in the heavy lane, in order; returns the stored study.

    Refused (`validation_failed`) for a path or value the base scenario cannot take, (`run_refused`) when a run is
    not valid or its physics is not ready, and (`estimate_too_large`) when a run does not fit in memory.
    """
    record = repos.scenarios.get(definition.base_scenario_id)
    if record is None:
        raise NotFound(f"there is no scenario {definition.base_scenario_id}")
    project_id = definition.project_id or record.project_id
    if project_id != record.project_id:
        raise _refuse("the base scenario belongs to another project")
    library = LibraryService(repos)
    base = record.scenario
    wanted = definition.axes
    if definition.kind == "sensitivity":
        assert definition.parameter is not None
        wanted = [sensitivity_axis(base, definition.parameter, library)]
    axes, runs = plan_study(base, definition.kind, wanted, library)

    problems: list[dict[str, Any]] = []
    for run in runs:
        validity = check_validity(run.scenario, mode="fast")
        problems += [{**p.model_dump(mode="json"), "message": f"{run.label}: {p.message}"} for p in validity.errors]
    if problems:
        raise Refused(
            "Some of the study's runs are not valid, so nothing was queued: "
            + " ".join(p["message"] for p in problems),
            code="run_refused",
            problems=problems,
        )
    gates = []
    for run in runs:
        gate = check_gate(run.scenario, repo=repos.capabilities)
        refusal = gate.refusal()
        if refusal is not None:
            raise refusal
        gates.append(gate)
    inputs = [library_inputs(run.scenario, library) for run in runs]
    estimate, each = estimate_study(
        runs, definition.detail_level, library=library, solver_build_id=solver_build_id, coefficients=coefficients,
        total_memory_bytes=total_memory_bytes, free_disk_bytes=free_disk_bytes,
    )  # fmt: skip
    if not estimate.fits:
        raise Refused(next(w for w in estimate.warnings if "memory" in w), code="estimate_too_large")

    now = utc_now()
    study = StudyRecord(
        id=new_ulid(), project_id=project_id, base_scenario_id=record.id, name=definition.name,
        kind=definition.kind, detail_level=definition.detail_level, axes=axes,
        estimate=estimate.model_dump(mode="json"), created_at=now, updated_at=now,
    )  # fmt: skip
    made: list[Run] = []
    for run, run_inputs, gate, expected in zip(runs, inputs, gates, each, strict=True):
        made.append(
            new_run(
                run.scenario, run_inputs, detail_level=definition.detail_level, solver_build_id=solver_build_id,
                scenario_id=record.id, scenario_revision=record.revision, estimate=expected, study_id=study.id,
            ).model_copy(update={"locked_results": gate.locked_results})
        )  # fmt: skip
    points = [
        StudyPoint(
            run_id=made_run.id,
            label=run.label,
            values=run.values,
            reuse_of=None if run.reuse_of is None else made[run.reuse_of].id,
        )  # fmt: skip
        for made_run, run in zip(made, runs, strict=True)
    ]
    study = study.model_copy(update={"points": points})
    with repos.transaction():
        repos.studies.add(study)
        for made_run, point in zip(made, points, strict=True):
            repos.runs.add(made_run)
            payload = {"run_id": made_run.id, "study_id": study.id, "reuse_of": point.reuse_of}
            repos.jobs.enqueue("run", "heavy", payload, target_id=made_run.id)
        repos.events.emit("study.progress", progress_event(study, [r.status for r in made]))
    return study


# --- progress and summary ----------------------------------------------------------------------------------------


def study_status(current: StudyStatus, statuses: Sequence[RunStatus]) -> StudyStatus:
    """A cancelled study stays cancelled; otherwise finished (with failures, when any run did not succeed) once every
    run has ended, queued while none has started, and running in between."""
    if current == "cancelled":
        return current
    if all(status in FINISHED for status in statuses):
        return "finished" if all(status == "succeeded" for status in statuses) else "finished_with_failures"
    if all(status == "queued" for status in statuses):
        return "queued"
    return "running"


def progress_event(study: StudyRecord, statuses: Sequence[RunStatus]) -> dict[str, Any]:
    return {
        "study_id": study.id,
        "status": study.status,
        "total": len(statuses),
        "done": sum(status in FINISHED for status in statuses),
        "failed": sum(status in ("failed", "cancelled") for status in statuses),
    }


class ValueMean(_Model):
    value: Any
    mean: float
    runs: int


class Spread(_Model):
    """One key number's spread over one axis."""

    id: str
    label: str
    load_case: str
    block: str | None
    region: str
    statistic: str
    unit: str
    by_value: list[ValueMean]
    """The mean over the finished runs at each of the axis's values, in the axis's order."""
    min: float
    max: float
    range: float
    reference: float | None
    """The mean at the reference value; None when no finished run has it."""
    percent_change: float | None
    """The range as a percentage of the reference; None without a reference or when it is zero."""


class AxisSpread(_Model):
    path: str
    values: list[Any]
    reference_value: Any
    key_numbers: list[Spread]


class SpreadSummary(_Model):
    runs_total: int
    runs_succeeded: int
    runs_failed: int
    """Failed or cancelled."""
    runs_pending: int
    axes: list[AxisSpread]


@dataclass(frozen=True)
class RunOutcome:
    values: Mapping[str, Any]
    status: RunStatus
    key_numbers: Sequence[KeyNumber] | None
    """Present only for a succeeded run."""


def _same_value(a: Any, b: Any) -> bool:
    numbers = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (a, b))
    if numbers:
        return math.isclose(a, b, rel_tol=REL_TOLERANCE, abs_tol=1e-12)
    return bool(a == b)


def _distinct(values: Sequence[Any]) -> list[Any]:
    """The values in order, each once (a range whose typical value is its maximum lists it twice)."""
    found: list[Any] = []
    for value in values:
        if not any(_same_value(value, seen) for seen in found):
            found.append(value)
    return found


def summarise(kind: StudyKind, axes: Sequence[StudyAxis], outcomes: Sequence[RunOutcome]) -> SpreadSummary:
    """The spread of each key number per axis, over the runs that succeeded."""
    finished = [o for o in outcomes if o.status == "succeeded" and o.key_numbers is not None]
    identities: dict[tuple[str, str], KeyNumber] = {}
    for outcome in finished:
        for number in outcome.key_numbers or ():
            identities.setdefault((number.id, number.load_case), number)
    spreads: list[AxisSpread] = []
    for axis in axes:
        reference_value = axis.values[1] if kind == "sensitivity" else axis.values[0]
        rows: list[Spread] = []
        for (number_id, load_case), first in identities.items():
            means: list[ValueMean] = []
            for value in _distinct(axis.values):
                found = [
                    n.value
                    for o in finished
                    if _same_value(o.values.get(axis.path), value)
                    for n in o.key_numbers or ()
                    if n.id == number_id and n.load_case == load_case
                ]
                if found:
                    means.append(ValueMean(value=value, mean=sum(found) / len(found), runs=len(found)))
            if not means:
                continue
            low, high = min(m.mean for m in means), max(m.mean for m in means)
            reference = next((m.mean for m in means if _same_value(m.value, reference_value)), None)
            rows.append(
                Spread(
                    id=number_id,
                    label=first.label,
                    load_case=load_case,
                    block=first.block,
                    region=first.region,
                    statistic=first.statistic,
                    unit=first.unit,
                    by_value=means,
                    min=low,
                    max=high,
                    range=high - low,
                    reference=reference,
                    percent_change=None if not reference else 100 * (high - low) / abs(reference),
                )  # fmt: skip
            )
        spreads.append(AxisSpread(path=axis.path, values=list(axis.values), reference_value=reference_value,
                                  key_numbers=rows))  # fmt: skip
    statuses = [o.status for o in outcomes]
    return SpreadSummary(
        runs_total=len(outcomes),
        runs_succeeded=sum(s == "succeeded" for s in statuses),
        runs_failed=sum(s in ("failed", "cancelled") for s in statuses),
        runs_pending=sum(s not in FINISHED for s in statuses),
        axes=spreads,
    )


def _key_numbers(run: Run, store: ContentStore) -> list[KeyNumber] | None:
    if run.status != "succeeded" or run.result_ref is None:
        return None
    try:
        return list(load_result(store, run.result_ref).result.key_numbers)
    except DentalsimError:
        return None


def refresh_study(repos: Repos, study_id: str, store: ContentStore) -> StudyRecord | None:
    """Recompute the study's status and summary from its runs, store them and emit `study.progress`."""
    study = repos.studies.get(study_id)
    if study is None:
        return None
    runs = [repos.runs.get(run_id) for run_id in study.run_ids]
    known = [(point, run) for point, run in zip(study.points, runs, strict=True) if run is not None]
    statuses = [run.status for _, run in known]
    outcomes = [RunOutcome(point.values, run.status, _key_numbers(run, store)) for point, run in known]
    summary = summarise(study.kind, study.axes, outcomes)
    changed = study.model_copy(
        update={"status": study_status(study.status, statuses), "summary": summary.model_dump(mode="json")}
    )
    with repos.transaction():
        repos.studies.update(changed)
        repos.events.emit("study.progress", progress_event(changed, statuses))
    return repos.studies.get(study_id)


def cancel_study(repos: Repos, study_id: str, store: ContentStore) -> StudyRecord:
    """Cancel every queued run of the study and ask the worker to stop the running one; the study is cancelled."""
    study = repos.studies.get(study_id)
    if study is None:
        raise NotFound(f"there is no study {study_id}")
    if study.status in ("finished", "finished_with_failures", "cancelled"):
        return study
    targets = set(study.run_ids)
    with repos.transaction():
        for job in repos.jobs.list_jobs(statuses=("queued", "running", "interrupted")):
            if job.target_id in targets:
                repos.jobs.request_cancel(job.id)
        for run_id in study.run_ids:
            run = repos.runs.get(run_id)
            if run is not None and run.status in ("queued", "interrupted"):
                repos.runs.update(failed_run(run, "the study was cancelled", cancelled=True))
        repos.studies.update(study.model_copy(update={"status": "cancelled"}))
    refreshed = refresh_study(repos, study_id, store)
    assert refreshed is not None
    return refreshed
