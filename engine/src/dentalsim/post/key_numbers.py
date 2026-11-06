"""Key numbers of a solved load case (task T079; FR-024; conventions "Regions", "Bone strain", "Stress compared with
strength").

Every number is a region mean or a volume-weighted 95th percentile over integration points; no nodal peak is ever
reported (constitution, Principle II). Which regions a block reports comes from its result types; a result type whose
physics is not ready is locked and reported as such, never computed (FR-011a).

- **Crestal strain**, around each report anchor (each implant): the equivalent strain (µε) in the crestal ring, on
  the worse side (the sector with the highest mean, named), as mean and p95, each with its Frost window.
- **Component stress**: the stress measure of the component's material (conventions strength rules: von Mises for
  metals, maximum principal for brittle materials), as mean and p95, and as a percentage of the material's strength
  reference (the block's library value, with its entry and version). A component carrying the load case's contact
  points leaves out the points within `crown_stats_exclude_radius_mm` of them (conventions load patch).
- **Displacement** of each component, mean and p95 of the displacement magnitude in µm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from dentalsim.conventions.regions import (
    PointSet,
    ReportAnchor,
    component_band_mask,
    component_mask,
    crestal_ring_mask,
    sector_of,
    worse_side,
)
from dentalsim.conventions.schema import ComponentBandRegion, ComponentRegion, Conventions, CrestalRingRegion
from dentalsim.model.results import KeyNumber, Reference
from dentalsim.post.fields import equivalent_strain_ue, stress_measure
from dentalsim.post.statistics import away_from, region_percentile, weighted_mean

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
MEASURE_LABELS = {"von_mises": "von Mises stress", "max_principal": "maximum principal stress"}


@dataclass(frozen=True)
class ResultRegions:
    """A block's reported result type: its id, label and region ids."""

    result_type: str
    label: str
    regions: tuple[str, ...]


@dataclass(frozen=True)
class BlockReport:
    instance_id: str
    label: str
    results: tuple[ResultRegions, ...]
    """Only the result types that are not locked."""


@dataclass(frozen=True, eq=False)
class PointData:
    """Integration points of the solved model: the region tags (`points`), each point's part, material and ν."""

    points: PointSet
    part: NDArray[np.int64]
    nu: FloatArray

    @property
    def material(self) -> NDArray[np.str_]:
        return self.points.material_group


@dataclass(frozen=True)
class KeyNumberInputs:
    data: PointData
    anchors: Sequence[ReportAnchor]
    blocks: Sequence[BlockReport]
    strength: Mapping[int, Reference]
    """The strength reference of each part (by part index) that has one."""
    frost_edges: Sequence[Reference]
    """The Frost window edges, in the conventions' order (lowest first)."""
    conventions: Conventions


def frost_category(value: float, edges: Sequence[Reference], conventions: Conventions) -> str | None:
    if not edges:
        return None
    categories = conventions.frost_scale.categories
    for category, edge in zip(categories, edges, strict=False):
        if value < edge.value:
            return category.id
    return categories[-1].id


def _stats(values: FloatArray, weights: FloatArray, conventions: Conventions) -> dict[str, float]:
    return {"mean": weighted_mean(values, weights), "p95": region_percentile(values, weights, conventions)}


def _crestal(
    inputs: KeyNumberInputs, region_id: str, anchor: ReportAnchor, label: str, strain: FloatArray, instant: str | None
) -> list[KeyNumber]:
    points, c = inputs.data.points, inputs.conventions
    mask = crestal_ring_mask(points, anchor, c, region_id=region_id)
    if not mask.any():
        return []
    ring = points.select(mask)
    values = equivalent_strain_ue(strain[mask], inputs.data.nu[mask], c)
    sectors = sector_of(ring, anchor, c, region_id=region_id)
    side, _ = worse_side(values, ring.weights, sectors)
    on_side = sectors == side
    region = c.regions[region_id]
    found = []
    for statistic, value in _stats(values[on_side], ring.weights[on_side], c).items():
        found.append(
            KeyNumber(
                id=f"{anchor.instance_id}.{region_id}.{statistic}",
                label=f"{region.label}, {side} side",
                load_case=label,
                block=anchor.instance_id,
                region=region_id,
                side=side,
                statistic=statistic,
                value=value,
                unit="µε",
                frost_category=frost_category(value, inputs.frost_edges, c),
                instant=instant,
            )
        )
    return found


def _component(
    inputs: KeyNumberInputs,
    region_id: str,
    mask: BoolArray,
    block: BlockReport,
    label: str,
    stress: FloatArray,
    strain: FloatArray,
    instant: str | None,
) -> list[KeyNumber]:
    c, data = inputs.conventions, inputs.data
    region = c.regions[region_id]
    found = []
    parts = np.unique(data.part[mask])
    for part in parts:
        selected = mask & (data.part == part)
        material = str(data.material[selected][0])
        rule = c.strength_rule_for(material)
        weights = data.points.weights[selected]
        suffix = "" if len(parts) == 1 else f".part{int(part)}"
        if rule is None or rule.stress_measure == "none":
            values, unit, what, reference = (
                equivalent_strain_ue(strain[selected], data.nu[selected], c),
                "µε",
                "equivalent strain",
                None,
            )
        else:
            values = stress_measure(stress[selected], rule.stress_measure)
            unit, what, reference = "MPa", MEASURE_LABELS[rule.stress_measure], inputs.strength.get(int(part))
        for statistic, value in _stats(values, weights, c).items():
            found.append(
                KeyNumber(
                    id=f"{block.instance_id}.{region_id}{suffix}.{statistic}",
                    label=f"{region.label} {what}",
                    load_case=label,
                    block=block.instance_id,
                    region=region_id,
                    statistic=statistic,
                    value=value,
                    unit=unit,
                    reference=reference,
                    percent_of_reference=None if reference is None else 100.0 * value / reference.value,
                    instant=instant,
                )
            )
    return found


def _displacement(
    inputs: KeyNumberInputs, block: BlockReport, label: str, displacement: FloatArray, instant: str | None
) -> list[KeyNumber]:
    c, points = inputs.conventions, inputs.data.points
    found = []
    for region_id, region in c.regions.items():
        if not isinstance(region, ComponentRegion):
            continue
        mask = component_mask(points, region_id, c, instance=block.instance_id)
        if not mask.any():
            continue
        magnitude = 1000.0 * np.linalg.norm(displacement[mask], axis=1)
        for statistic, value in _stats(magnitude, points.weights[mask], c).items():
            found.append(
                KeyNumber(
                    id=f"{block.instance_id}.{region_id}.displacement.{statistic}",
                    label=f"{region.label} displacement",
                    load_case=label,
                    block=block.instance_id,
                    region=region_id,
                    statistic=statistic,
                    value=float(value),
                    unit="µm",
                    instant=instant,
                )
            )
    return found


def key_numbers(
    inputs: KeyNumberInputs,
    *,
    label: str,
    stress: FloatArray,
    strain: FloatArray,
    displacement: FloatArray,
    contacts: Sequence[tuple[str, tuple[float, float, float]]] = (),
    instant: str | None = None,
) -> list[KeyNumber]:
    """The key numbers of one combined load case (or instant). `displacement` is at the integration points;
    `contacts` are (block instance, position) of the load case's contact points."""
    c, points = inputs.conventions, inputs.data.points
    exclude = c.load_patch.crown_stats_exclude_radius_mm
    found: list[KeyNumber] = []
    done_rings: set[tuple[str, str]] = set()
    for block in inputs.blocks:
        loaded = [position for instance, position in contacts if instance == block.instance_id]
        for result in block.results:
            for region_id in result.regions:
                region = c.regions[region_id]
                if isinstance(region, CrestalRingRegion):
                    for anchor in inputs.anchors:
                        if (anchor.instance_id, region_id) not in done_rings:
                            done_rings.add((anchor.instance_id, region_id))
                            found += _crestal(inputs, region_id, anchor, label, strain, instant)
                    continue
                if isinstance(region, ComponentBandRegion):
                    mask = np.zeros(len(points), dtype=np.bool_)
                    for anchor in (a for a in inputs.anchors if a.instance_id == block.instance_id):
                        mask |= component_band_mask(points, anchor, c, region_id=region_id)
                else:
                    mask = component_mask(points, region_id, c, instance=block.instance_id)
                if loaded:
                    mask &= away_from(points.coords, loaded, exclude)
                if mask.any():
                    found += _component(inputs, region_id, mask, block, label, stress, strain, instant)
        found += _displacement(inputs, block, label, displacement, instant)
    return found


def worst_instants(numbers: Sequence[KeyNumber]) -> list[KeyNumber]:
    """For a chewing cycle: each key number at the instant where it is largest (FR-008)."""
    best: dict[tuple[str, str], KeyNumber] = {}
    for number in numbers:
        key = (number.load_case, number.id)
        if key not in best or number.value > best[key].value:
            best[key] = number
    return list(best.values())
