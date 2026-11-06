"""Report regions as geometric tests on integration points (research R5, FR-024).

A region is a test on integration-point positions plus the component and material group of the element that owns
the point, never a mesh partition. The same region therefore exists at every detail level, which is what makes
the mesh check (FR-037) compare like with like.

Nothing here knows a block type. Region ids, component and material-group names, the distance and depth limits and
the sectors all come from the conventions block; block geometry enters only through each block's report anchors
(`ReportAnchor`: an axis and a radius profile), which `BlockType.report_anchors(ctx)` returns (task T060).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, field_validator

from dentalsim.conventions.frame import frame_axes
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import (
    ComponentBandRegion,
    ComponentRegion,
    Conventions,
    CrestalRingRegion,
    Region,
)
from dentalsim.errors import DentalsimError

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
StrArray = NDArray[np.str_]

INSIDE_TOLERANCE_MM = 0.01
"""How far inside a body's surface, or above the crest, a point may lie and still count (integration points of a
curved surface meshed with flat-faced elements can sit a few micrometres on the wrong side)."""


class RegionError(DentalsimError):
    """A region cannot be evaluated: an unknown region id, a missing or mismatched anchor, or no points."""


@dataclass(frozen=True, eq=False)
class PointSet:
    """Integration points of one solved model, one row per point.

    `weights` is the integration-point volume (element volume / 4 for 10-node tetrahedra); `component` and
    `material_group` are the tags of the owning element; `instance` is the id of the block instance that owns it.
    """

    coords: FloatArray
    weights: FloatArray
    component: StrArray
    material_group: StrArray
    instance: StrArray

    def __post_init__(self) -> None:
        coords = np.asarray(self.coords, dtype=np.float64)
        if coords.ndim != 2 or coords.shape[1] != 3:
            raise ValueError(f"coords must have shape (N, 3), not {coords.shape}")
        count = coords.shape[0]
        object.__setattr__(self, "coords", coords)
        weights = np.asarray(self.weights, dtype=np.float64)
        if weights.shape != (count,):
            raise ValueError(f"weights must have shape ({count},), not {weights.shape}")
        object.__setattr__(self, "weights", weights)
        for name in ("component", "material_group", "instance"):
            tags = np.asarray(getattr(self, name), dtype=np.str_)
            if tags.shape != (count,):
                raise ValueError(f"{name} must have shape ({count},), not {tags.shape}")
            object.__setattr__(self, name, tags)

    def __len__(self) -> int:
        return int(self.coords.shape[0])

    def select(self, mask: BoolArray) -> PointSet:
        return PointSet(
            coords=self.coords[mask],
            weights=self.weights[mask],
            component=self.component[mask],
            material_group=self.material_group[mask],
            instance=self.instance[mask],
        )


class ReportAnchor(BaseModel):
    """Where a block's body is, for the region tests. `BlockType.report_anchors(ctx)` returns these (task T060).

    The body is taken as a solid of revolution about `axis` through `platform_mm`:

    - `platform_mm`: the centre of the implant platform, or of a tooth's cervical section.
    - `axis`: points occlusal (coronal); it is normalised on input.
    - `radius_profile`: `(depth_mm, radius_mm)` pairs, depth measured from the platform toward apical (≥ 0 and
      strictly rising), radius from the axis. For a threaded body, give the core (minor) radius, so that bone
      between the threads lies at a positive distance from the surface.
    - `crest_depth_mm`: the depth of the bone crest below the platform, along the axis. Positive when the platform
      stands above the crest (supracrestal), 0 at crest level, negative when the platform is subcrestal. The crest
      is taken as the plane through that point at right angles to the axis.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str
    component: str
    platform_mm: tuple[float, float, float]
    axis: tuple[float, float, float]
    radius_profile: tuple[tuple[float, float], ...]
    crest_depth_mm: float

    @field_validator("axis")
    @classmethod
    def _unit_axis(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        norm = math.sqrt(sum(c * c for c in value))
        if not math.isfinite(norm) or norm < 1e-12:
            raise ValueError("the axis must be a finite, non-zero vector")
        return (value[0] / norm, value[1] / norm, value[2] / norm)

    @field_validator("radius_profile")
    @classmethod
    def _profile(cls, value: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
        if len(value) < 2:
            raise ValueError("a radius profile needs at least two (depth_mm, radius_mm) points")
        depths = [depth for depth, _ in value]
        if depths[0] < 0:
            raise ValueError("profile depths are measured from the platform toward apical, so they must be >= 0")
        if any(later <= earlier for earlier, later in zip(depths, depths[1:], strict=False)):
            raise ValueError("profile depths must rise strictly")
        if any(radius < 0 for _, radius in value):
            raise ValueError("profile radii must be >= 0")
        return value


def _coords(points: PointSet | FloatArray) -> FloatArray:
    coords = points.coords if isinstance(points, PointSet) else np.asarray(points, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] != 3:
        raise ValueError(f"coords must have shape (N, 3), not {coords.shape}")
    return coords


def _depth_and_radial(points: PointSet | FloatArray, anchor: ReportAnchor) -> tuple[FloatArray, FloatArray]:
    """Depth below the platform along the axis (apical positive), and the offset from the axis, shape (N, 3)."""
    axis = np.asarray(anchor.axis, dtype=np.float64)
    offset = _coords(points) - np.asarray(anchor.platform_mm, dtype=np.float64)
    along = offset @ axis
    return -along, offset - np.outer(along, axis)


def depth_below_platform(points: PointSet | FloatArray, anchor: ReportAnchor) -> FloatArray:
    """Depth of each point below the anchor's platform, along its axis, in mm (apical positive)."""
    return _depth_and_radial(points, anchor)[0]


def surface_distance(points: PointSet | FloatArray, anchor: ReportAnchor) -> FloatArray:
    """Approximate signed distance of each point from the anchor's surface, in mm (negative inside the body).

    - Between the first and last profile depths: the radial distance from the axis minus the profile radius at the
      point's depth (linear interpolation of the profile). This is the true distance for a cylinder and close to it
      for the slowly varying, axisymmetric profiles blocks use: on a flank of slope s (radius change per mm of
      depth) it overstates the distance by a factor sqrt(1 + s^2), under 1% for s < 0.14 (a half-angle of 8°).
    - Beyond either end: the distance to the end disc, its face when the point lies within the end radius, and its
      edge otherwise.
    - Inside the body: minus the distance to the nearer of the side and the end faces.
    """
    depth, radial = _depth_and_radial(points, anchor)
    radius = np.linalg.norm(radial, axis=1)
    depths = np.array([d for d, _ in anchor.radius_profile], dtype=np.float64)
    radii = np.array([r for _, r in anchor.radius_profile], dtype=np.float64)
    top, apex = depths[0], depths[-1]

    side = radius - np.interp(np.clip(depth, top, apex), depths, radii)
    beyond = np.maximum(top - depth, depth - apex)  # > 0 above the first or below the last profile depth
    end_radius = np.where(depth < top, radii[0], radii[-1])
    to_end_disc = np.where(radius <= end_radius, beyond, np.hypot(radius - end_radius, beyond))
    distance = np.where(beyond > 0, to_end_disc, side)
    inside = (beyond <= 0) & (side < 0)
    return np.asarray(np.where(inside, np.maximum(side, beyond), distance), dtype=np.float64)


def _region_of_kind[R: (CrestalRingRegion, ComponentBandRegion, ComponentRegion)](
    conventions: Conventions, region_type: type[R], region_id: str | None, kind: str
) -> tuple[str, R]:
    if region_id is not None:
        region = _region(conventions, region_id)
        if not isinstance(region, region_type):
            raise RegionError(f"region {region_id!r} is a {region.kind} region, not a {kind} region")
        return region_id, region
    found = [(rid, r) for rid, r in conventions.regions.items() if isinstance(r, region_type)]
    if len(found) != 1:
        names = ", ".join(rid for rid, _ in found) or "none"
        raise RegionError(f"name the {kind} region to use: the conventions define {names}")
    return found[0]


def _region(conventions: Conventions, region_id: str) -> Region:
    try:
        return conventions.regions[region_id]
    except KeyError:
        known = ", ".join(sorted(conventions.regions))
        raise RegionError(f"unknown region {region_id!r}: the conventions define {known}") from None


def crestal_ring_mask(
    points: PointSet,
    anchor: ReportAnchor,
    conventions: Conventions | None = None,
    *,
    region_id: str | None = None,
    inside_tolerance_mm: float = INSIDE_TOLERANCE_MM,
) -> BoolArray:
    """Points of the region's material group near the anchor's surface and in the band just below the crest.

    A point counts when its surface distance is in [-tolerance, max_distance_from_surface_mm] and its depth below
    the crest is in [-tolerance, max_depth_below_crest_mm]. `region_id` is needed only when the conventions define
    more than one crestal-ring region.
    """
    conventions = conventions or get_conventions()
    _, region = _region_of_kind(conventions, CrestalRingRegion, region_id, "crestal_ring")
    depth, _ = _depth_and_radial(points, anchor)
    below_crest = depth - anchor.crest_depth_mm
    distance = surface_distance(points, anchor)
    mask = (
        (points.material_group == region.material_group)
        & (distance >= -inside_tolerance_mm)
        & (distance <= region.max_distance_from_surface_mm)
        & (below_crest >= -inside_tolerance_mm)
        & (below_crest <= region.max_depth_below_crest_mm)
    )
    return np.asarray(mask, dtype=np.bool_)


def component_band_mask(
    points: PointSet,
    anchor: ReportAnchor,
    conventions: Conventions | None = None,
    *,
    region_id: str | None = None,
    inside_tolerance_mm: float = INSIDE_TOLERANCE_MM,
) -> BoolArray:
    """Points of the anchor's own instance and the region's component, from the platform to `length_mm` below it."""
    conventions = conventions or get_conventions()
    rid, region = _region_of_kind(conventions, ComponentBandRegion, region_id, "component_band")
    if anchor.component != region.component:
        raise RegionError(
            f"region {rid!r} is measured on a {region.component}, but anchor {anchor.instance_id!r} is a "
            f"{anchor.component}"
        )
    depth = depth_below_platform(points, anchor)
    mask = (
        (points.component == region.component)
        & (points.instance == anchor.instance_id)
        & (depth >= -inside_tolerance_mm)
        & (depth <= region.length_mm)
    )
    return np.asarray(mask, dtype=np.bool_)


def component_mask(
    points: PointSet, region_id: str, conventions: Conventions | None = None, *, instance: str | None = None
) -> BoolArray:
    """Every point of the region's component, or only those of block instance `instance`."""
    conventions = conventions or get_conventions()
    _, region = _region_of_kind(conventions, ComponentRegion, region_id, "component")
    mask = points.component == region.component
    if instance is not None:
        mask = mask & (points.instance == instance)
    return np.asarray(mask, dtype=np.bool_)


def region_mask(
    region_id: str,
    points: PointSet,
    anchor: ReportAnchor | None = None,
    instance: str | None = None,
    conventions: Conventions | None = None,
) -> BoolArray:
    """The points of region `region_id`, whatever its kind.

    Crestal-ring and component-band regions are measured around `anchor`, which they need. A whole-component region
    takes every point of its component, or only those of `instance`.
    """
    conventions = conventions or get_conventions()
    region = _region(conventions, region_id)
    if isinstance(region, ComponentRegion):
        return component_mask(points, region_id, conventions, instance=instance)
    if anchor is None:
        raise RegionError(f"region {region_id!r} is measured around a block's report anchor, and none was given")
    if isinstance(region, CrestalRingRegion):
        return crestal_ring_mask(points, anchor, conventions, region_id=region_id)
    return component_band_mask(points, anchor, conventions, region_id=region_id)


def sector_angle_deg(
    points: PointSet | FloatArray, anchor: ReportAnchor, conventions: Conventions | None = None
) -> FloatArray:
    """The direction of each point from the anchor's axis, seen from +z, in degrees from +x toward +y, in [0, 360).

    The offset at right angles to the axis is projected onto the frame's xy-plane, so a tilted implant gets the same
    sectors as an upright one. A point on the axis has angle 0.
    """
    x, y, _ = frame_axes(conventions or get_conventions())
    _, radial = _depth_and_radial(points, anchor)
    return np.asarray(np.degrees(np.arctan2(radial @ y, radial @ x)) % 360.0, dtype=np.float64)


def sector_of(
    points: PointSet | FloatArray,
    anchor: ReportAnchor,
    conventions: Conventions | None = None,
    *,
    region_id: str | None = None,
) -> StrArray:
    """The sector name of each point, from the sectors of the crestal-ring region.

    Each sector covers the half-open interval [centre - half width, centre + half width), so a point exactly on a
    boundary belongs to the sector that starts there: 45° is buccal and 315° is mesial with the default sectors.
    """
    conventions = conventions or get_conventions()
    rid, region = _region_of_kind(conventions, CrestalRingRegion, region_id, "crestal_ring")
    angle = sector_angle_deg(points, anchor, conventions)
    names = np.full(angle.shape, "", dtype=f"<U{max(len(name) for name in region.sectors)}")
    assigned = np.zeros(angle.shape, dtype=np.bool_)
    for name, sector in region.sectors.items():
        start = sector.centre_deg - sector.half_width_deg
        inside = np.mod(angle - start, 360.0) < 2 * sector.half_width_deg
        take = inside & ~assigned
        names[take] = name
        assigned |= take
    if not assigned.all():
        first = float(angle[~assigned][0])
        raise RegionError(f"the sectors of region {rid!r} do not cover the direction {first:.3f}°")
    return names


def worse_side(
    values: FloatArray | Sequence[float],
    weights: FloatArray | Sequence[float],
    sectors: StrArray | Sequence[str],
) -> tuple[str, dict[str, float]]:
    """The worse side and the volume-weighted mean of `values` in each sector (`worse_side: highest_mean`).

    Sectors without points, or with zero total weight, are left out. A tie goes to the sector whose name sorts
    first.
    """
    values = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    sectors = np.asarray(sectors, dtype=np.str_)
    if not values.shape == weights.shape == sectors.shape or values.ndim != 1:
        raise ValueError("values, weights and sectors must be 1-D arrays of the same length")
    means: dict[str, float] = {}
    for name in np.unique(sectors):
        in_sector = sectors == name
        total = float(weights[in_sector].sum())
        if total > 0:
            means[str(name)] = float((values[in_sector] * weights[in_sector]).sum() / total)
    if not means:
        raise RegionError("no integration points in any sector, so there is no worse side to name")
    worst = max(means, key=lambda name: means[name])
    return worst, means
