"""Generic geometry: rigid transforms, analytic volumes and their OpenCASCADE twins."""

from __future__ import annotations

import math

import gmsh
import numpy as np
import pytest

from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.shapes import Circle2D, Cylinder, Frustum, PathSweep, Placed, PlanarPath, RoundedPolygon
from dentalsim.geometry.transform import Transform


@pytest.fixture
def occ_model():
    with occ_tools.session("geometry-test"):
        yield


def test_transforms_compose_and_invert() -> None:
    a = Transform.rotation_about((0, 0, 1), 90.0, centre=(1.0, 0.0, 0.0))
    b = Transform.translation_by((0.0, 2.0, 3.0))
    p = np.array([2.0, 0.0, 0.0])
    assert a.apply(p) == pytest.approx([1.0, 1.0, 0.0])
    assert (a @ b).apply(p) == pytest.approx(a.apply(b.apply(p)))
    assert (a @ b).inverse().apply((a @ b).apply(p)) == pytest.approx(p)
    assert a.direction((1, 0, 0)) == pytest.approx((0.0, 1.0, 0.0))


def test_a_frame_must_be_right_handed() -> None:
    frame = Transform.from_axes((1, 2, 3), (0, 1, 0), (-1, 0, 0))
    assert frame.axes[2] == pytest.approx((0.0, 0.0, 1.0))
    with pytest.raises(ValueError, match="right-handed"):
        Transform.from_axes((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, -1))
    with pytest.raises(ValueError, match="proper"):
        Transform(np.diag([1.0, 1.0, -1.0]), np.zeros(3))


def test_placing_a_solid_keeps_its_volume_exactly(occ_model) -> None:
    cone = Frustum((0, 0, 0), (0, 0, 4), 2.25, 1.8)
    exact = math.pi * 4 / 3 * (2.25**2 + 2.25 * 1.8 + 1.8**2)
    placed = Placed(cone, Transform.translation_by((5, 1, 2)) @ Transform.rotation_about((1, 1, 0), 33.0))
    tags = placed.build_occ()
    assert occ_tools.volume_mm3(tags) == pytest.approx(exact, rel=1e-12)
    assert placed.contains([placed.transform.point((0, 0, 3.9)), placed.transform.point((0, 0, 4.1))]).tolist() == [
        True,
        False,
    ]


def test_frustum_containment_and_bounds() -> None:
    cone = Frustum((0, 0, 0), (0, 0, -10), 3.0, 1.0)
    assert cone.contains([(2.9, 0, -0.1), (2.9, 0, -9.9), (0, 0, 0.1), (0, 0, -10.1)]).tolist() == [
        True,
        False,
        False,
        False,
    ]
    low, high = cone.bounds()
    assert low == pytest.approx((-3.0, -3.0, -10.0)) and high == pytest.approx((3.0, 3.0, 0.0))
    assert Cylinder((0, 0, 0), (1, 0, 0), 2.0).contains([(0.5, 0, 1.99)]).all()


def test_rounded_polygon_area_offset_and_outline(occ_model) -> None:
    shape = RoundedPolygon(((-5, -10), (5, -10), (4, 0), (-4, 0)), (2.0, 2.0, 1.5, 1.5))
    face = gmsh.model.occ.addPlaneSurface([shape.occ_loop(Transform.identity())])
    assert gmsh.model.occ.getMass(2, face) == pytest.approx(shape.area, rel=1e-12)
    inner = shape.offset(1.0)
    assert inner.radii == (1.0, 1.0, 0.5, 0.5)
    (u0, v0), (u1, v1) = inner.uv_bounds()
    assert (v0, v1) == pytest.approx((-9.0, -1.0))
    u = np.array([0.0, 3.9, 4.6, 0.0])
    v = np.array([-0.5, -0.4, -0.4, 0.5])
    assert shape.contains_uv(u, v).tolist() == [True, False, False, False]
    with pytest.raises(ValueError, match="larger than the shape"):
        shape.offset(6.0)


def test_a_path_sweep_matches_its_loft(occ_model) -> None:
    angles = np.linspace(-0.6, 0.6, 400)
    path = PlanarPath(np.column_stack([40 * np.sin(angles), 40 * np.cos(angles) - 40]), s0=0.0)
    middle = path.start + (path.end - path.start) / 2
    sweep = PathSweep(path, Circle2D((1.0, -3.0), 1.5), middle - 10, middle + 10)
    tags = sweep.build_occ()
    # +u is right of travel, here toward the centre of the 40 mm path, so the circle's centre travels 1 - 1/40 as far.
    assert occ_tools.volume_mm3(tags) == pytest.approx(math.pi * 1.5**2 * 20.0 * (1 - 1 / 40), rel=1e-5)
    rng = np.random.default_rng(1)
    low, high = sweep.bounds()
    points = rng.uniform(low, high, size=(400, 3))
    gmsh.model.occ.synchronize()
    in_loft = np.array([gmsh.model.isInside(3, tags[0], list(p)) > 0 for p in points])
    in_sweep = sweep.contains(points)
    assert np.mean(in_loft == in_sweep) > 0.99
