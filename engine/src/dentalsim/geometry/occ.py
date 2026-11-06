"""Small helpers around gmsh's OpenCASCADE kernel, shared by the block builders and the assembly.

Every builder works in the current gmsh model; `session()` opens one when none is open. Meshing settings do not
belong here (see `dentalsim.mesh`).

gmsh keeps one global state per process, so one thread at a time may use it: `session()` holds `GMSH_LOCK` for its
whole length. The studio server builds scenes and runs the full validity checks on several threads, and the worker
runs its preview and heavy lanes on two; without the lock, two sessions at once failed ("Gmsh has not been
initialized", task T098). A preview therefore waits while a run builds its geometry and mesh, not while it solves.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import gmsh
import numpy as np
from scipy.spatial.transform import Rotation  # type: ignore[import-untyped]

from dentalsim.geometry.transform import Point, Transform

DimTag = tuple[int, int]

GMSH_LOCK = threading.RLock()
"""Held by every gmsh session; re-entrant, so a session may open inside another on the same thread."""


@contextmanager
def session(name: str = "dentalsim") -> Iterator[None]:
    """A gmsh model named `name` for the duration of the block. gmsh is started (quietly, without reading the
    user's configuration files) when it is not running, and stopped again afterwards; a model that was already
    current is restored. Only one thread at a time holds a session (`GMSH_LOCK`)."""
    with GMSH_LOCK:
        started = not gmsh.isInitialized()
        if started:
            gmsh.initialize(readConfigFiles=False, interruptible=False)
            gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Geometry.OCCBoundsUseStl", 1)
        previous = None if started else gmsh.model.getCurrent()
        gmsh.model.add(name)
        try:
            yield
        finally:
            gmsh.model.remove()
            if started:
                gmsh.finalize()
            elif previous:
                gmsh.model.setCurrent(previous)


def occ() -> Any:
    """`gmsh.model.occ`, refusing clearly when there is no open session."""
    if not gmsh.isInitialized():
        raise RuntimeError("no gmsh session is open: use dentalsim.geometry.occ.session()")
    return gmsh.model.occ


def volumes(tags: Sequence[int]) -> list[DimTag]:
    return [(3, int(t)) for t in tags]


def transform_volumes(tags: Sequence[int], transform: Transform) -> None:
    """Move volumes in place by a rigid transform: a rotation about the origin, then the translation.

    gmsh's `affineTransform` is not used: OpenCASCADE applies it as a general transform, which turns every surface
    into an approximating B-spline (a translated cone gained 0.9% of its volume). Rotation and translation are exact.
    """
    if not tags or transform.is_identity():
        return
    kernel = occ()
    rotation = Rotation.from_matrix(transform.rotation).as_rotvec()
    angle = float(np.linalg.norm(rotation))
    if angle > 1e-15:
        kernel.rotate(volumes(tags), 0.0, 0.0, 0.0, *(float(c) for c in rotation / angle), angle)
    if np.any(transform.translation != 0.0):
        kernel.translate(volumes(tags), *(float(c) for c in transform.translation))


def remove_orphans() -> None:
    """Remove points, curves and surfaces that bound no volume (construction leftovers such as arc centres).

    gmsh removes an entity only when it is not part of a higher-dimensional one, so the boundaries of volumes stay.
    """
    kernel = occ()
    loose = [entity for dim in (2, 1, 0) for entity in kernel.getEntities(dim)]
    if loose:
        kernel.remove(loose, recursive=True)


def volume_mm3(tags: Sequence[int]) -> float:
    kernel = occ()
    return float(sum(kernel.getMass(3, t) for t in tags))


def bounding_box(tags: Sequence[int]) -> tuple[Point, Point]:
    """The axis-aligned box of the volumes, from their surface tessellation: OpenCASCADE's own boxes of curved
    surfaces can be millimetres too large. Smooth surfaces may lie up to a few micrometres outside it."""
    kernel = occ()
    gmsh.option.setNumber("Geometry.OCCBoundsUseStl", 1)
    boxes = np.array([kernel.getBoundingBox(3, t) for t in tags], dtype=np.float64)
    low, high = boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)
    return (float(low[0]), float(low[1]), float(low[2])), (float(high[0]), float(high[1]), float(high[2]))


def rough_box(tags: Sequence[int]) -> tuple[Point, Point]:
    """OpenCASCADE's own box of the volumes: quick, and never smaller than the solids, but up to millimetres larger
    around curved surfaces. Good for ruling out that two solids meet, not for measuring them."""
    kernel = occ()
    gmsh.option.setNumber("Geometry.OCCBoundsUseStl", 0)
    try:
        boxes = np.array([kernel.getBoundingBox(3, t) for t in tags], dtype=np.float64)
    finally:
        gmsh.option.setNumber("Geometry.OCCBoundsUseStl", 1)
    low, high = boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)
    return (float(low[0]), float(low[1]), float(low[2])), (float(high[0]), float(high[1]), float(high[2]))


def revolve_rz(profile: Sequence[tuple[float, float]]) -> list[int]:
    """A solid of revolution about the z-axis from a closed (r, z) polygon in the xz-plane (r ≥ 0).

    Points on the axis (r = 0) are allowed; the polygon must not cross itself.
    """
    kernel = occ()
    points = [kernel.addPoint(float(r), 0.0, float(z)) for r, z in profile]
    lines = [kernel.addLine(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
    face = kernel.addPlaneSurface([kernel.addCurveLoop(lines)])
    made = kernel.revolve([(2, face)], 0, 0, 0, 0, 0, 1, 2 * math.pi)
    return [tag for dim, tag in made if dim == 3]


def ellipsoid(centre: Point, semi_axes: Point) -> int:
    """An ellipsoid with the given semi-axes along x, y and z: a sphere scaled by OpenCASCADE, which stores it as a
    B-spline surface within about 0.05% of the exact volume."""
    kernel = occ()
    tag = int(kernel.addSphere(*centre, 1.0))
    kernel.dilate([(3, tag)], *centre, *semi_axes)
    return tag


def ellipse_loop(centre: Point, rx: float, ry: float) -> int:
    """A closed wire: the exact ellipse with semi-axes `rx` (along x) and `ry` (along y) in the plane z = centre z,
    running counter-clockwise from +x whichever semi-axis is larger, so that lofts line up their seams."""
    kernel = occ()
    if rx >= ry:
        curve = kernel.addEllipse(*centre, rx, ry)
    else:  # gmsh wants the major axis first: put it on y and start the full turn a quarter back, at +x
        curve = kernel.addEllipse(*centre, ry, rx, -1, -math.pi / 2, 1.5 * math.pi, [0, 0, 1], [0, 1, 0])
    return int(kernel.addCurveLoop([curve]))


def circle_loop(centre: Point, radius: float, normal: Point, x_axis: Point = (1.0, 0.0, 0.0)) -> int:
    """A closed circular wire with the given normal; it starts on `x_axis`, which must lie in its plane."""
    kernel = occ()
    circle = kernel.addCircle(*centre, radius, zAxis=list(normal), xAxis=list(x_axis))
    return int(kernel.addCurveLoop([circle]))


def hexagon_prism(across_flats: float, z0: float, z1: float) -> int:
    """A hexagonal prism about the z-axis, its flats facing ±y, from z = z0 to z = z1."""
    kernel = occ()
    circumradius = across_flats / math.sqrt(3)
    corners = [math.radians(60 * k) for k in range(6)]
    points = [kernel.addPoint(circumradius * math.cos(a), circumradius * math.sin(a), z0) for a in corners]
    lines = [kernel.addLine(points[k], points[(k + 1) % 6]) for k in range(6)]
    face = kernel.addPlaneSurface([kernel.addCurveLoop(lines)])
    made = kernel.extrude([(2, face)], 0, 0, z1 - z0)
    return int(next(tag for dim, tag in made if dim == 3))


def loft(loops: Sequence[int]) -> list[int]:
    """A smooth solid through closed wires, in order."""
    made = occ().addThruSections(list(loops), makeSolid=True, makeRuled=False)
    return [tag for dim, tag in made if dim == 3]


def fuse(tags: Sequence[int]) -> list[int]:
    """One solid from overlapping solids (internal faces removed)."""
    if len(tags) < 2:
        return list(tags)
    made, _ = occ().fuse(volumes(tags[:1]), volumes(tags[1:]))
    return [tag for dim, tag in made if dim == 3]


def cut(objects: Sequence[int], tools: Sequence[int], *, keep_tools: bool = False) -> list[int]:
    """`objects` minus `tools`."""
    made, _ = occ().cut(volumes(objects), volumes(tools), removeObject=True, removeTool=not keep_tools)
    return [tag for dim, tag in made if dim == 3]
