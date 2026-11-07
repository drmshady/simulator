"""Comparing two or more stored result sets (task T116; FR-027; Principle VII; SC-008).

Everything comes from stored results; nothing is solved.

- **Colour scales**: one per field and load case, shared by every run. Each run's display range is worked out as the
  viewer works out a single run's (0 to the 98th percentile of the surface values, so a few peak vertices do not
  wash the scale out), and the shared range is their union. The Frost windows are categories at fixed edges and
  have no range: the comparison states the edges, and warns when the runs used different edge values.
- **Deformation scale**: one magnification for every panel, chosen so that the largest displacement of any run is
  drawn as `DEFORMATION_FRACTION` of the model's size, rounded down to 1, 2 or 5 × 10ⁿ.
- **Key numbers**: matched by id and load case. Every run after the first gets its absolute and relative difference
  from the first (relative is None when the first is 0); a key number a run does not have is None there.

The colours are for display; key numbers come from integration points (Principle II).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from dentalsim.model.results import KeyNumber
from dentalsim.pipeline import PipelineResult, load_result, store_asset
from dentalsim.post.gltf import read_attributes
from dentalsim.store.cas import ContentStore

FIELDS: dict[str, tuple[str, str]] = {
    "frost": ("_FROST_CLASS", ""),
    "strain": ("_STRAIN_UE", "µε"),
    "strain_maxp": ("_STRAIN_MAXP_UE", "µε"),
    "stress": ("_STRESS_VM", "MPa"),
    "pct_strength": ("_PCT_STRENGTH", "%"),
}
"""Field id (as the interface names it) → (`.glb` attribute, unit)."""
CATEGORICAL = frozenset({"frost"})
DISPLAY_PERCENTILE = 0.98
DEFORMATION_FRACTION = 0.05
NICE_STEPS = (1.0, 2.0, 5.0)
EDGE_TOLERANCE = 1e-9


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- inputs ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FrostEdge:
    parameter_key: str
    entry_version: int
    value: float


@dataclass(frozen=True)
class SurfaceValues:
    """The surface values of one run under one load case, every part together."""

    load_case: str
    values: Mapping[str, NDArray[Any]]
    """Field id → every vertex value."""
    max_displacement_mm: float
    bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class ComparedRun:
    run_id: str
    key_numbers: Sequence[KeyNumber]
    surfaces: Sequence[SurfaceValues]
    frost_edges: Sequence[FrostEdge] = ()


# --- outputs -----------------------------------------------------------------------------------------------------


class RunRange(_Model):
    run_id: str
    min: float
    max: float


class FieldScale(_Model):
    field: str
    unit: str
    categorical: bool
    min: float
    max: float
    """The shared range; for a categorical field (the Frost windows), the class indices."""
    by_run: list[RunRange]
    """Each run's own display range, before they were joined."""


class FrostWindows(_Model):
    edges: list[FrostEdge]
    """The edges of the first run, lowest first: the windows every panel is drawn with."""
    consistent: bool
    """False when another run used different edge values; its Frost colours are then not comparable."""


class DeformationScale(_Model):
    max_displacement_mm: float
    model_size_mm: float
    magnification: float


class Delta(_Model):
    run_id: str
    absolute: float
    relative: float | None


class KeyNumberRow(_Model):
    id: str
    load_case: str
    label: str
    unit: str
    statistic: str
    block: str | None
    region: str
    values: list[float | None]
    """One per run, in the order of the comparison."""
    percent_of_reference: list[float | None]
    differences: list[Delta | None]
    """One per run after the first: its difference from the first."""


class Comparison(_Model):
    run_ids: list[str]
    load_cases: list[str]
    """The load cases every run has, in the first run's order."""
    scales: dict[str, dict[str, FieldScale]]
    """Load case → field → shared scale."""
    frost: FrostWindows | None
    deformation: DeformationScale
    bounds_mm: tuple[tuple[float, float, float], tuple[float, float, float]]
    """The box around every run's surfaces, for one camera framing."""
    key_numbers: list[KeyNumberRow]
    warnings: list[str]


# --- the comparison ----------------------------------------------------------------------------------------------


def display_range(values: NDArray[Any]) -> tuple[float, float]:
    """0 to the 98th percentile of the finite, non-negative values (−1 marks a surface the field does not apply to);
    (0, 1) when there are none. The same rule as the viewer's `displayRange`."""
    data = np.asarray(values, dtype=np.float64)
    data = np.sort(data[np.isfinite(data) & (data >= 0)])
    if data.size == 0:
        return 0.0, 1.0
    high = float(data[min(data.size - 1, math.floor(DISPLAY_PERCENTILE * (data.size - 1)))])
    return 0.0, high if high > 0 else 1.0


def nice_magnification(raw: float) -> float:
    """The largest of 1, 2 or 5 × 10ⁿ not above `raw` (at least 1)."""
    if not math.isfinite(raw) or raw <= 1:
        return 1.0
    exponent = math.floor(math.log10(raw))
    best = 1.0
    for step in NICE_STEPS:
        candidate = step * 10**exponent
        if candidate <= raw * (1 + 1e-12):
            best = candidate
    return best


def _union_bounds(
    boxes: Sequence[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not boxes:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    low = np.min([b[0] for b in boxes], axis=0)
    high = np.max([b[1] for b in boxes], axis=0)
    return (float(low[0]), float(low[1]), float(low[2])), (float(high[0]), float(high[1]), float(high[2]))


def _edges_equal(a: Sequence[FrostEdge], b: Sequence[FrostEdge]) -> bool:
    return len(a) == len(b) and all(
        x.parameter_key == y.parameter_key and abs(x.value - y.value) <= EDGE_TOLERANCE * max(1.0, abs(x.value))
        for x, y in zip(a, b, strict=True)
    )


def _key_rows(runs: Sequence[ComparedRun]) -> list[KeyNumberRow]:
    tables = [{(n.id, n.load_case): n for n in run.key_numbers} for run in runs]
    order: list[tuple[str, str]] = []
    for table in tables:
        order += [key for key in table if key not in order]
    rows = []
    for key in order:
        numbers = [table.get(key) for table in tables]
        shown = next(n for n in numbers if n is not None)
        base = numbers[0]
        differences: list[Delta | None] = []
        for run, number in zip(runs[1:], numbers[1:], strict=True):
            if base is None or number is None:
                differences.append(None)
                continue
            absolute = number.value - base.value
            relative = absolute / abs(base.value) if base.value != 0 else None
            differences.append(Delta(run_id=run.run_id, absolute=absolute, relative=relative))
        rows.append(
            KeyNumberRow(
                id=key[0],
                load_case=key[1],
                label=shown.label,
                unit=shown.unit,
                statistic=shown.statistic,
                block=shown.block,
                region=shown.region,
                values=[None if n is None else n.value for n in numbers],
                percent_of_reference=[None if n is None else n.percent_of_reference for n in numbers],
                differences=differences,
            )
        )
    return rows


def compare(runs: Sequence[ComparedRun], fields: Sequence[str] | None = None) -> Comparison:
    """The shared scales and the key-number differences of two or more runs (the first is the base)."""
    if len(runs) < 2:
        raise ValueError("a comparison needs at least two runs")
    wanted = list(fields or FIELDS)
    unknown = [f for f in wanted if f not in FIELDS]
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}; known: {', '.join(FIELDS)}")
    warnings: list[str] = []

    by_case = [{s.load_case: s for s in run.surfaces} for run in runs]
    load_cases = [case for case in by_case[0] if all(case in other for other in by_case[1:])]
    left_out = sorted({case for table in by_case for case in table} - set(load_cases))
    if left_out:
        warnings.append(f"Load cases not in every run are not compared: {', '.join(left_out)}.")

    scales: dict[str, dict[str, FieldScale]] = {}
    for case in load_cases:
        scales[case] = {}
        for field in wanted:
            unit = FIELDS[field][1]
            if field in CATEGORICAL:
                classes = float(max(0, len(runs[0].frost_edges)))
                scales[case][field] = FieldScale(field=field, unit=unit, categorical=True, min=0.0, max=classes,
                                                 by_run=[])  # fmt: skip
                continue
            ranges = []
            for run, table in zip(runs, by_case, strict=True):
                values = table[case].values.get(field)
                low, high = display_range(values if values is not None else np.empty(0))
                ranges.append(RunRange(run_id=run.run_id, min=low, max=high))
            scales[case][field] = FieldScale(
                field=field, unit=unit, categorical=False, min=min(r.min for r in ranges),
                max=max(r.max for r in ranges), by_run=ranges,
            )  # fmt: skip

    frost: FrostWindows | None = None
    if "frost" in wanted:
        consistent = all(_edges_equal(runs[0].frost_edges, run.frost_edges) for run in runs[1:])
        frost = FrostWindows(edges=list(runs[0].frost_edges), consistent=consistent)
        if not consistent:
            warnings.append(
                "The runs used different Frost window edges (library versions), so their Frost colours are not "
                "comparable; compare the strain values instead."
            )

    surfaces = [s for table in by_case for case, s in table.items() if case in load_cases]
    bounds = _union_bounds([s.bounds_mm for s in surfaces])
    size = float(np.linalg.norm(np.subtract(bounds[1], bounds[0])))
    largest = max((s.max_displacement_mm for s in surfaces), default=0.0)
    magnification = nice_magnification(DEFORMATION_FRACTION * size / largest) if largest > 0 else 1.0
    deformation = DeformationScale(max_displacement_mm=largest, model_size_mm=size, magnification=magnification)

    return Comparison(
        run_ids=[run.run_id for run in runs],
        load_cases=load_cases,
        scales=scales,
        frost=frost,
        deformation=deformation,
        bounds_mm=bounds,
        key_numbers=_key_rows(runs),
        warnings=warnings,
    )


# --- reading stored results --------------------------------------------------------------------------------------


def surfaces_of(
    store: ContentStore, result: PipelineResult, fields: Sequence[str] | None = None
) -> list[SurfaceValues]:
    """Each load case's surface values from a stored result's `.glb` files, every part together."""
    wanted = list(fields or FIELDS)
    grouped: dict[str, list[dict[str, NDArray[Any]]]] = {}
    for asset in result.result.visual_assets:
        _, arrays = read_attributes(store_asset(store, result.input_hash, asset.name))
        grouped.setdefault(asset.load_case, []).append(arrays)
    surfaces = []
    for case, parts in grouped.items():
        values = {
            field: np.concatenate([p[FIELDS[field][0]] for p in parts if FIELDS[field][0] in p] or [np.empty(0)])
            for field in wanted
        }
        positions = np.concatenate([p["POSITION"] for p in parts])
        displacement = np.concatenate([p["MORPH"] for p in parts])
        low, high = positions.min(axis=0), positions.max(axis=0)
        surfaces.append(
            SurfaceValues(
                load_case=case,
                values=values,
                max_displacement_mm=float(np.linalg.norm(displacement, axis=1).max()) if len(displacement) else 0.0,
                bounds_mm=(
                    (float(low[0]), float(low[1]), float(low[2])),
                    (float(high[0]), float(high[1]), float(high[2])),
                ),
            )  # fmt: skip
        )
    return surfaces


def stored_run(
    store: ContentStore, run_id: str, result_ref: str, frost_edges: Sequence[FrostEdge] = (),
    fields: Sequence[str] | None = None,
) -> ComparedRun:  # fmt: skip
    """A run's stored result, read for a comparison."""
    result = load_result(store, result_ref)
    return ComparedRun(
        run_id=run_id,
        key_numbers=result.result.key_numbers,
        surfaces=surfaces_of(store, result, fields),
        frost_edges=frost_edges,
    )
