"""Volumes that are both analytic and buildable in gmsh OpenCASCADE.

Keep-out volumes, the bone envelope and similar spaces are tested on points many times while an assembly is being
built (validity checks must take under a second), and are also shown and intersected as solids. Each volume here
therefore has:

- `contains(points)`: an exact point test, with no gmsh session needed;
- `bounds()`: an axis-aligned box that holds it;
- `build_occ()`: the same volume as OpenCASCADE solids in the current gmsh model, returning their volume tags.

A `PathSweep` is a planar profile swept along a planar path in the xy-plane, with the profile standing upright
(its v axis is +z). The jaw segment uses it for the bone along the dental arch, the nerve canal and the sinus floor.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.spatial import cKDTree  # type: ignore[import-untyped]

from dentalsim.geometry.occ import occ, transform_volumes
from dentalsim.geometry.transform import FloatArray, Point, Transform, unit

BoolArray = NDArray[np.bool_]
Bounds = tuple[Point, Point]

SWEEP_STATION_SPACING_MM = 2.0
"""Largest distance between the loft sections of a `PathSweep`; between sections OpenCASCADE interpolates
smoothly, which stays within a few micrometres of the exact sweep for the arch curvatures of a jaw."""


def as_points(points: ArrayLike) -> FloatArray:
    """Points as an (N, 3) array; a single point (3,) becomes (1, 3)."""
    p = np.asarray(points, dtype=np.float64)
    if p.ndim == 1:
        p = p[None, :]
    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError(f"points must have shape (N, 3), not {p.shape}")
    return p


def _point(values: Sequence[float] | FloatArray) -> Point:
    return (float(values[0]), float(values[1]), float(values[2]))


class Volume(Protocol):
    def contains(self, points: ArrayLike) -> BoolArray:
        """For each point, shape (N, 3), whether it lies inside (the boundary counts as inside)."""
        ...

    def bounds(self) -> Bounds:
        """The lowest and highest corner of an axis-aligned box that holds the volume."""
        ...

    def build_occ(self) -> list[int]:
        """Build the volume in the current gmsh model with the OpenCASCADE kernel; returns its volume tags."""
        ...


# --- solids of revolution ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Frustum:
    """A truncated cone from `start` (radius `start_radius`) to `end` (radius `end_radius`); a cylinder when the two
    radii are equal. Either radius may be 0 (a cone)."""

    start: Point
    end: Point
    start_radius: float
    end_radius: float

    def __post_init__(self) -> None:
        if self.start_radius < 0 or self.end_radius < 0 or max(self.start_radius, self.end_radius) <= 0:
            raise ValueError("a frustum needs radii >= 0, at least one of them positive")
        if self.length <= 0:
            raise ValueError("a frustum needs distinct start and end points")

    @property
    def length(self) -> float:
        return float(np.linalg.norm(np.subtract(self.end, self.start)))

    @property
    def axis(self) -> FloatArray:
        return unit(np.subtract(self.end, self.start), "frustum axis")

    def radius_at(self, along: FloatArray) -> FloatArray:
        fraction = np.clip(along / self.length, 0.0, 1.0)
        return np.asarray(self.start_radius + (self.end_radius - self.start_radius) * fraction, dtype=np.float64)

    def contains(self, points: ArrayLike) -> BoolArray:
        p = as_points(points) - np.asarray(self.start)
        along = p @ self.axis
        radial = np.linalg.norm(p - np.outer(along, self.axis), axis=1)
        return np.asarray((along >= 0) & (along <= self.length) & (radial <= self.radius_at(along)), dtype=np.bool_)

    def bounds(self) -> Bounds:
        a = self.axis
        # A disc of radius r with normal a reaches r * sqrt(1 - a_i^2) along axis i.
        reach = np.sqrt(np.clip(1.0 - a * a, 0.0, 1.0))
        ends = [(np.asarray(self.start), self.start_radius), (np.asarray(self.end), self.end_radius)]
        low = np.asarray(np.min([c - r * reach for c, r in ends], axis=0), dtype=np.float64)
        high = np.asarray(np.max([c + r * reach for c, r in ends], axis=0), dtype=np.float64)
        return _point(low), _point(high)

    def build_occ(self) -> list[int]:
        dx, dy, dz = np.subtract(self.end, self.start)
        if math.isclose(self.start_radius, self.end_radius):
            return [occ().addCylinder(*self.start, dx, dy, dz, self.start_radius)]
        return [occ().addCone(*self.start, dx, dy, dz, self.start_radius, self.end_radius)]


def Cylinder(start: Point, end: Point, radius: float) -> Frustum:
    """A frustum with equal radii."""
    return Frustum(start, end, radius, radius)


@dataclass(frozen=True, eq=False)
class Placed:
    """`volume`, defined in local coordinates, placed by `transform` (local → target)."""

    volume: Volume
    transform: Transform

    def contains(self, points: ArrayLike) -> BoolArray:
        return self.volume.contains(self.transform.inverse().apply(as_points(points)))

    def bounds(self) -> Bounds:
        (x0, y0, z0), (x1, y1, z1) = self.volume.bounds()
        corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
        placed = self.transform.apply(corners)
        return _point(placed.min(axis=0)), _point(placed.max(axis=0))

    def build_occ(self) -> list[int]:
        tags = self.volume.build_occ()
        transform_volumes(tags, self.transform)
        return tags


# --- planar profiles ---------------------------------------------------------------------------------------------


class Profile(Protocol):
    """A planar region in (u, v) coordinates, for sweeps."""

    def contains_uv(self, u: FloatArray, v: FloatArray) -> BoolArray: ...

    def uv_bounds(self) -> tuple[tuple[float, float], tuple[float, float]]: ...

    @property
    def area(self) -> float: ...

    def occ_loop(self, section: Transform) -> int:
        """Build the outline as a closed wire in the plane `section` maps (u, v, 0) into; returns the wire tag."""
        ...


@dataclass(frozen=True)
class Circle2D:
    centre: tuple[float, float]
    radius: float

    def __post_init__(self) -> None:
        if self.radius <= 0:
            raise ValueError("a circle needs a positive radius")

    def contains_uv(self, u: FloatArray, v: FloatArray) -> BoolArray:
        du, dv = u - self.centre[0], v - self.centre[1]
        return np.asarray(du * du + dv * dv <= self.radius**2, dtype=np.bool_)

    def uv_bounds(self) -> tuple[tuple[float, float], tuple[float, float]]:
        (cu, cv), r = self.centre, self.radius
        return (cu - r, cv - r), (cu + r, cv + r)

    @property
    def area(self) -> float:
        return math.pi * self.radius**2

    def occ_loop(self, section: Transform) -> int:
        kernel = occ()
        centre = section.point((self.centre[0], self.centre[1], 0.0))
        circle = kernel.addCircle(
            *centre, self.radius, zAxis=list(section.direction((0, 0, 1))), xAxis=list(section.direction((1, 0, 0)))
        )
        return int(kernel.addCurveLoop([circle]))


@dataclass(frozen=True)
class _Corner:
    vertex: FloatArray
    radius: float
    centre: FloatArray
    start: FloatArray  # tangent point on the incoming edge
    end: FloatArray  # tangent point on the outgoing edge
    normal_in: FloatArray  # outward normal of the incoming edge
    normal_out: FloatArray  # outward normal of the outgoing edge


@dataclass(frozen=True, eq=False)
class RoundedPolygon:
    """A convex polygon, vertices counter-clockwise in (u, v), each corner rounded with its own radius (0 = sharp)."""

    vertices: tuple[tuple[float, float], ...]
    radii: tuple[float, ...]
    _corners: tuple[_Corner, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.vertices) < 3 or len(self.radii) != len(self.vertices):
            raise ValueError("a rounded polygon needs at least 3 vertices and one radius per vertex")
        if any(r < 0 for r in self.radii):
            raise ValueError("corner radii must be >= 0")
        v = np.asarray(self.vertices, dtype=np.float64)
        edges = np.roll(v, -1, axis=0) - v
        if np.any(np.linalg.norm(edges, axis=1) < 1e-9):
            raise ValueError("polygon edges must have positive length")
        turns = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
        if np.any(turns <= 0):
            raise ValueError("the polygon must be convex with its vertices counter-clockwise")
        normals = np.column_stack([edges[:, 1], -edges[:, 0]]) / np.linalg.norm(edges, axis=1)[:, None]
        corners = []
        for i, radius in enumerate(self.radii):
            n_in, n_out = normals[i - 1], normals[i]
            centre = _line_intersection(v[i - 1] - radius * n_in, edges[i - 1], v[i] - radius * n_out, edges[i])
            corners.append(_Corner(v[i], radius, centre, centre + radius * n_in, centre + radius * n_out, n_in, n_out))
        for i, corner in enumerate(corners):
            following = corners[(i + 1) % len(corners)]
            edge = edges[i]
            reach = (corner.end - v[i]) @ edge + (v[(i + 1) % len(v)] - following.start) @ edge
            if reach > float(edge @ edge) + 1e-9:
                raise ValueError(f"the corner radii at both ends of edge {i} are too large for its length")
        object.__setattr__(self, "_corners", tuple(corners))

    def contains_uv(self, u: FloatArray, v: FloatArray) -> BoolArray:
        p = np.column_stack([np.ravel(u), np.ravel(v)])
        inside = np.ones(len(p), dtype=np.bool_)
        for corner in self._corners:
            inside &= (p - corner.vertex) @ corner.normal_out <= 1e-12
        for corner in self._corners:
            if corner.radius > 0:
                offset = p - corner.centre
                in_wedge = (offset @ corner.normal_in > 0) & (offset @ corner.normal_out > 0)
                inside &= ~in_wedge | (np.einsum("ij,ij->i", offset, offset) <= corner.radius**2 + 1e-12)
        return inside

    def uv_bounds(self) -> tuple[tuple[float, float], tuple[float, float]]:
        v = np.asarray(self.vertices)
        (u0, v0), (u1, v1) = v.min(axis=0), v.max(axis=0)
        return (float(u0), float(v0)), (float(u1), float(v1))

    @property
    def area(self) -> float:
        v = np.asarray(self.vertices)
        polygon = 0.5 * float(np.sum(v[:, 0] * np.roll(v[:, 1], -1) - np.roll(v[:, 0], -1) * v[:, 1]))
        cut = 0.0
        for corner in self._corners:
            turn = math.acos(float(np.clip(corner.normal_in @ corner.normal_out, -1.0, 1.0)))
            cut += corner.radius**2 * (math.tan(turn / 2) - turn / 2)
        return polygon - cut

    def offset(self, distance: float) -> RoundedPolygon:
        """The shape shrunk by `distance` (> 0) everywhere: edges move inward, each corner radius drops by
        `distance` (a corner whose radius is smaller becomes sharp)."""
        if distance <= 0:
            raise ValueError("the offset distance must be positive")
        v = np.asarray(self.vertices, dtype=np.float64)
        edges = np.roll(v, -1, axis=0) - v
        normals = [c.normal_out for c in self._corners]
        points = []
        for i in range(len(v)):
            points.append(
                _line_intersection(
                    v[i - 1] - distance * normals[i - 1], edges[i - 1], v[i] - distance * normals[i], edges[i]
                )
            )
        new_edges = np.roll(np.asarray(points), -1, axis=0) - np.asarray(points)
        if np.any(np.einsum("ij,ij->i", new_edges, edges) <= 0):
            raise ValueError(f"an offset of {distance:g} mm is larger than the shape")
        return RoundedPolygon(
            tuple((float(p[0]), float(p[1])) for p in points), tuple(max(r - distance, 0.0) for r in self.radii)
        )

    def occ_loop(self, section: Transform) -> int:
        kernel = occ()

        def point(uv: FloatArray) -> int:
            return int(kernel.addPoint(*section.point((float(uv[0]), float(uv[1]), 0.0))))

        curves: list[int] = []
        first_start: int | None = None
        previous_end: int | None = None
        for corner in self._corners:
            if corner.radius > 0:
                start, centre, end = point(corner.start), point(corner.centre), point(corner.end)
            else:
                start = end = point(corner.vertex)
                centre = None
            if previous_end is not None:
                curves.append(kernel.addLine(previous_end, start))
            else:
                first_start = start
            if centre is not None:
                curves.append(kernel.addCircleArc(start, centre, end))
                kernel.remove([(0, centre)])
            previous_end = end
        assert first_start is not None and previous_end is not None
        curves.append(kernel.addLine(previous_end, first_start))
        return int(kernel.addCurveLoop(curves))


def _line_intersection(p: FloatArray, d: FloatArray, q: FloatArray, e: FloatArray) -> FloatArray:
    """Where the line p + s·d meets the line q + t·e (2-D)."""
    matrix = np.column_stack([d, -e])
    if abs(np.linalg.det(matrix)) < 1e-12:
        raise ValueError("parallel edges have no corner")
    s, _ = np.linalg.solve(matrix, q - p)
    return np.asarray(p + s * d, dtype=np.float64)


# --- a planar path and sweeps along it ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PlanarPath:
    """A smooth path in the xy-plane, sampled densely, parametrised by arc length `s`.

    `points` are (K, 2) samples in path order, closer than the scale of any profile swept along it; `s0` is the arc
    length at the first sample. The right-hand side of the direction of travel, `tangent × z`, is the sweep's +u.
    """

    points: FloatArray
    s0: float = 0.0
    _s: FloatArray = field(init=False, repr=False)
    _vertex_tangents: FloatArray = field(init=False, repr=False)
    _tree: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        points = np.asarray(self.points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
            raise ValueError("a path needs at least two (x, y) samples")
        steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
        if np.any(steps < 1e-9):
            raise ValueError("path samples must be distinct")
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "_s", self.s0 + np.concatenate([[0.0], np.cumsum(steps)]))
        directions = np.diff(points, axis=0) / steps[:, None]
        at_vertices = np.vstack([directions[:1], directions[:-1] + directions[1:], directions[-1:]])
        object.__setattr__(self, "_vertex_tangents", at_vertices / np.linalg.norm(at_vertices, axis=1)[:, None])
        object.__setattr__(self, "_tree", cKDTree(points))

    @property
    def start(self) -> float:
        return float(self._s[0])

    @property
    def end(self) -> float:
        return float(self._s[-1])

    def at(self, s: float) -> tuple[FloatArray, FloatArray]:
        """The point (x, y, 0) and the unit tangent (x, y, 0) at arc length `s`."""
        if not self.start - 1e-9 <= s <= self.end + 1e-9:
            raise ValueError(f"arc length {s:g} is outside the path ({self.start:g} to {self.end:g})")
        i = int(np.clip(np.searchsorted(self._s, s) - 1, 0, len(self._s) - 2))
        a, b = self.points[i], self.points[i + 1]
        fraction = (s - self._s[i]) / (self._s[i + 1] - self._s[i])
        tangent = self._tangent(i, fraction)
        return np.array([*(a + fraction * (b - a)), 0.0]), np.array([*tangent, 0.0])

    def _tangent(self, i: int, fraction: float) -> FloatArray:
        """The tangent blended linearly between the vertex tangents, so it turns smoothly along the samples."""
        blended = (1.0 - fraction) * self._vertex_tangents[i] + fraction * self._vertex_tangents[i + 1]
        return np.asarray(blended / np.linalg.norm(blended), dtype=np.float64)

    def section(self, s: float) -> Transform:
        """The frame of the cross-section at `s`: x = +u (right of travel), y = +z, z = −tangent."""
        point, tangent = self.at(s)
        right = np.cross(tangent, [0.0, 0.0, 1.0])
        return Transform.from_axes(point, right, (0.0, 0.0, 1.0))

    def project(self, points: ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
        """For each point: the arc length of its foot on the path, its u (right of travel) and its v (= z)."""
        p = as_points(points)
        _, nearest = self._tree.query(p[:, :2])
        best_s = np.empty(len(p))
        best_u = np.empty(len(p))
        best_d = np.full(len(p), np.inf)
        for offset in (-1, 0):
            seg = np.clip(nearest + offset, 0, len(self.points) - 2)
            a, b = self.points[seg], self.points[seg + 1]
            ab = b - a
            length2 = np.einsum("ij,ij->i", ab, ab)
            t = np.clip(np.einsum("ij,ij->i", p[:, :2] - a, ab) / length2, 0.0, 1.0)
            foot = a + t[:, None] * ab
            delta = p[:, :2] - foot
            distance = np.einsum("ij,ij->i", delta, delta)
            better = distance < best_d
            best_d[better] = distance[better]
            best_s[better] = (self._s[seg] + t * np.sqrt(length2))[better]
            # right of travel = (ab_y, -ab_x) / |ab|
            u = (delta[:, 0] * ab[:, 1] - delta[:, 1] * ab[:, 0]) / np.sqrt(length2)
            best_u[better] = u[better]
        return best_s, best_u, p[:, 2].copy()


@dataclass(frozen=True, eq=False)
class PathSweep:
    """`profile` swept along `path` from arc length `s_start` to `s_end`, standing upright (v = z).

    The path must run past both ends by more than the profile's reach, so that every point near an end face has its
    true perpendicular foot on the path; then a point is inside when its foot lies in [s_start, s_end] and its
    (u, v) lies in the profile. The end faces are planar, at right angles to the path.
    """

    path: PlanarPath
    profile: Profile
    s_start: float
    s_end: float

    def __post_init__(self) -> None:
        if not self.s_start < self.s_end:
            raise ValueError("a sweep needs s_start < s_end")
        (u0, _), (u1, _) = self.profile.uv_bounds()
        reach = max(abs(u0), abs(u1))  # only the sideways reach moves a point's foot along the path
        if self.s_start - reach < self.path.start or self.s_end + reach > self.path.end:
            raise ValueError("the path must run past both ends of the sweep by more than the profile's reach")

    @property
    def length(self) -> float:
        return self.s_end - self.s_start

    def contains(self, points: ArrayLike) -> BoolArray:
        s, u, v = self.path.project(points)
        within = (s >= self.s_start - 1e-9) & (s <= self.s_end + 1e-9)
        return np.asarray(within & self.profile.contains_uv(u, v), dtype=np.bool_)

    def stations(self) -> FloatArray:
        count = max(2, math.ceil(self.length / SWEEP_STATION_SPACING_MM) + 1)
        return np.linspace(self.s_start, self.s_end, count)

    def bounds(self) -> Bounds:
        (u0, v0), (u1, v1) = self.profile.uv_bounds()
        corners = np.array([[u, v, 0.0] for u in (u0, u1) for v in (v0, v1)])
        samples = [self.path.section(float(s)).apply(corners) for s in np.linspace(self.s_start, self.s_end, 64)]
        stacked = np.vstack(samples)
        # Between samples the outer edge can bulge by at most the chord sag; 64 samples keep it negligible.
        return _point(stacked.min(axis=0)), _point(stacked.max(axis=0))

    def build_occ(self) -> list[int]:
        loops = [self.profile.occ_loop(self.path.section(float(s))) for s in self.stations()]
        made = occ().addThruSections(loops, makeSolid=True, makeRuled=False)
        return [tag for dim, tag in made if dim == 3]
