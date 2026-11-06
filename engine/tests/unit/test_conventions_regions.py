"""Region predicates on integration points (conventions/regions.py, research R5).

The point clouds are synthetic: points placed at a chosen radius, depth and direction around an upright or a tilted
cylinder standing in for an implant.
"""

import math
from collections.abc import Callable, Sequence

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import ValidationError

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.regions import (
    PointSet,
    RegionError,
    ReportAnchor,
    component_band_mask,
    component_mask,
    crestal_ring_mask,
    depth_below_platform,
    region_mask,
    sector_of,
    surface_distance,
    worse_side,
)
from dentalsim.conventions.schema import ComponentBandRegion, Conventions, CrestalRingRegion

TILT = math.radians(20)

# (radius_mm, depth_mm, direction_deg) around an anchor; direction 0 is mesial (+x) for an upright anchor.
Placement = tuple[float, float, float]
MakeAnchor = Callable[[], ReportAnchor]


@pytest.fixture
def conventions() -> Conventions:
    return get_conventions()


@pytest.fixture
def ring(conventions: Conventions) -> CrestalRingRegion:
    region = conventions.regions["crestal_ring"]
    assert isinstance(region, CrestalRingRegion)
    return region


def _anchor(**changes: object) -> ReportAnchor:
    fields: dict[str, object] = {
        "instance_id": "implant-1",
        "component": "implant",
        "platform_mm": (0.0, 0.0, 0.0),
        "axis": (0.0, 0.0, 1.0),
        "radius_profile": ((0.0, 2.0), (10.0, 2.0)),
        "crest_depth_mm": 0.0,
    }
    return ReportAnchor.model_validate(fields | changes)


def _tilted_anchor(**changes: object) -> ReportAnchor:
    """Tilted 20° toward buccal (+y), with its platform away from the origin."""
    return _anchor(platform_mm=(5.0, -3.0, 1.0), axis=(0.0, math.sin(TILT), math.cos(TILT)), **changes)


def _place(anchor: ReportAnchor, placements: Sequence[Placement]) -> NDArray[np.float64]:
    axis = np.array(anchor.axis)
    u = np.array([1.0, 0.0, 0.0]) - axis[0] * axis
    u /= np.linalg.norm(u)
    w = np.cross(axis, u)
    rows = [
        np.array(anchor.platform_mm)
        + r * (math.cos(math.radians(phi)) * u + math.sin(math.radians(phi)) * w)
        - depth * axis
        for r, depth, phi in placements
    ]
    return np.array(rows, dtype=np.float64).reshape(-1, 3)


def _points(coords: NDArray[np.float64], *, component: str, material_group: str, instance: str = "jaw") -> PointSet:
    count = len(coords)
    return PointSet(
        coords=coords,
        weights=np.ones(count),
        component=np.full(count, component),
        material_group=np.full(count, material_group),
        instance=np.full(count, instance),
    )


def _concat(*sets: PointSet) -> PointSet:
    return PointSet(
        coords=np.concatenate([s.coords for s in sets]),
        weights=np.concatenate([s.weights for s in sets]),
        component=np.concatenate([s.component for s in sets]),
        material_group=np.concatenate([s.material_group for s in sets]),
        instance=np.concatenate([s.instance for s in sets]),
    )


# --- report anchors and point sets -------------------------------------------------------------------------------


def test_anchor_axis_is_normalised() -> None:
    assert _anchor(axis=(0.0, 0.0, 5.0)).axis == (0.0, 0.0, 1.0)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"axis": (0.0, 0.0, 0.0)}, "non-zero"),
        ({"radius_profile": ((0.0, 2.0),)}, "at least two"),
        ({"radius_profile": ((-1.0, 2.0), (10.0, 2.0))}, ">= 0"),
        ({"radius_profile": ((0.0, 2.0), (5.0, 2.0), (5.0, 1.0))}, "rise strictly"),
        ({"radius_profile": ((0.0, 2.0), (10.0, -1.0))}, "radii"),
        ({"diameter": 4.1}, "Extra inputs"),
    ],
)
def test_invalid_anchors_are_refused(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _anchor(**changes)


def test_point_set_checks_shapes_and_selects() -> None:
    coords = np.zeros((3, 3))
    with pytest.raises(ValueError, match="weights"):
        PointSet(coords, np.ones(2), np.full(3, "a"), np.full(3, "b"), np.full(3, "c"))
    with pytest.raises(ValueError, match=r"\(N, 3\)"):
        PointSet(np.zeros((3, 2)), np.ones(3), np.full(3, "a"), np.full(3, "b"), np.full(3, "c"))
    points = PointSet(coords, np.array([1.0, 2.0, 3.0]), np.array(["a", "b", "a"]), np.full(3, "m"), np.full(3, "i"))
    picked = points.select(points.component == "a")
    assert len(picked) == 2
    assert picked.weights.tolist() == [1.0, 3.0]


# --- surface distance --------------------------------------------------------------------------------------------


UPRIGHT_DISTANCES: list[tuple[Placement, float]] = [
    ((3.0, 5.0, 0.0), 1.0),  # beside the side wall
    ((2.0, 5.0, 37.0), 0.0),  # on the side wall
    ((1.0, 5.0, 90.0), -1.0),  # inside, nearest the side wall
    ((1.0, 9.5, 180.0), -0.5),  # inside, nearest the apical face
    ((1.0, 11.0, 0.0), 1.0),  # below the apex, within its radius: the apical face
    ((3.0, 11.0, 270.0), math.sqrt(2.0)),  # below and outside the apex: the apical edge
    ((0.0, -1.0, 0.0), 1.0),  # above the platform face
    ((5.0, -4.0, 45.0), 5.0),  # above and outside the platform edge
]


@pytest.mark.parametrize("make_anchor", [_anchor, _tilted_anchor], ids=["upright", "tilted"])
def test_surface_distance_of_a_cylinder(make_anchor: MakeAnchor) -> None:
    anchor = make_anchor()
    placements, expected = zip(*UPRIGHT_DISTANCES, strict=True)
    coords = _place(anchor, placements)
    assert surface_distance(coords, anchor) == pytest.approx(expected, abs=1e-12)
    assert depth_below_platform(coords, anchor) == pytest.approx([p[1] for p in placements], abs=1e-12)


def test_surface_distance_interpolates_a_tapered_profile() -> None:
    anchor = _anchor(radius_profile=((0.0, 2.0), (8.0, 2.0), (10.0, 1.5)))
    coords = _place(anchor, [(3.0, 4.0, 0.0), (2.75, 9.0, 90.0), (1.5, 10.5, 0.0), (2.5, 11.0, 0.0)])
    assert surface_distance(coords, anchor) == pytest.approx([1.0, 1.0, 0.5, math.sqrt(2.0)], abs=1e-12)


def test_surface_distance_accepts_a_point_set() -> None:
    anchor = _anchor()
    points = _points(_place(anchor, [(3.5, 1.0, 0.0)]), component="bone", material_group="cortical_bone")
    assert surface_distance(points, anchor) == pytest.approx([1.5])


# --- crestal ring ------------------------------------------------------------------------------------------------


def test_the_crestal_ring_limits_are_the_conventions_values(ring: CrestalRingRegion) -> None:
    assert ring.max_distance_from_surface_mm == 1.5
    assert ring.max_depth_below_crest_mm == 2.0


RING_CASES: list[tuple[Placement, bool]] = [
    ((3.4, 1.9, 0.0), True),
    ((3.5, 2.0, 90.0), True),  # on both limits: 1.5 mm from the surface, 2.0 mm below the crest
    ((2.01, 0.5, 180.0), True),
    ((3.6, 1.0, 270.0), False),  # 1.6 mm from the surface
    ((3.0, 2.1, 0.0), False),  # 2.1 mm below the crest
    ((3.0, -0.1, 0.0), False),  # above the crest
    ((3.0, -0.005, 0.0), True),  # above the crest by less than the tolerance
    ((1.5, 1.0, 0.0), False),  # inside the implant
]


@pytest.mark.parametrize("make_anchor", [_anchor, _tilted_anchor], ids=["upright", "tilted"])
def test_crestal_ring(make_anchor: MakeAnchor, ring: CrestalRingRegion, conventions: Conventions) -> None:
    anchor = make_anchor()
    placements, expected = zip(*RING_CASES, strict=True)
    cortical = _points(_place(anchor, placements), component="bone", material_group=ring.material_group)
    assert crestal_ring_mask(cortical, anchor, conventions).tolist() == list(expected)


def test_crestal_ring_is_only_the_ring_material(ring: CrestalRingRegion, conventions: Conventions) -> None:
    anchor = _anchor()
    coords = _place(anchor, [(3.0, 1.0, 0.0)])
    cancellous = _points(coords, component="bone", material_group="cancellous_bone")
    implant = _points(coords, component="implant", material_group="titanium_g4", instance="implant-1")
    assert not crestal_ring_mask(_concat(cancellous, implant), anchor, conventions).any()


def test_crestal_ring_starts_at_the_crest_not_the_platform(ring: CrestalRingRegion, conventions: Conventions) -> None:
    anchor = _anchor(crest_depth_mm=0.5)  # platform 0.5 mm above the crest
    coords = _place(anchor, [(3.0, 0.3, 0.0), (3.0, 0.5, 0.0), (3.0, 2.4, 0.0), (3.0, 2.6, 0.0)])
    cortical = _points(coords, component="bone", material_group=ring.material_group)
    assert crestal_ring_mask(cortical, anchor, conventions).tolist() == [False, True, True, False]


def test_region_names_and_limits_come_from_the_conventions(conventions: Conventions) -> None:
    data = conventions.model_dump(by_alias=True)
    data["regions"]["deep_ring"] = data["regions"]["crestal_ring"] | {
        "material_group": "cortex",
        "max_distance_from_surface_mm": 1.0,
        "max_depth_below_crest_mm": 3.0,
    }
    custom = Conventions.model_validate(data)
    anchor = _anchor()
    coords = _place(anchor, [(2.9, 2.5, 0.0), (3.2, 1.0, 0.0)])
    cortex = _points(coords, component="bone", material_group="cortex")
    assert crestal_ring_mask(cortex, anchor, custom, region_id="deep_ring").tolist() == [True, False]
    assert region_mask("deep_ring", cortex, anchor, conventions=custom).tolist() == [True, False]
    with pytest.raises(RegionError, match="crestal_ring, deep_ring"):
        crestal_ring_mask(cortex, anchor, custom)


# --- implant neck and whole components ---------------------------------------------------------------------------


def test_implant_neck_band(conventions: Conventions) -> None:
    band = conventions.regions["implant_neck"]
    assert isinstance(band, ComponentBandRegion)
    assert band.length_mm == 2.0
    anchor = _tilted_anchor()
    own = _points(
        _place(anchor, [(1.0, 0.5, 0.0), (1.9, 1.9, 90.0), (0.5, 2.0, 0.0), (1.0, 2.1, 0.0), (1.0, 8.0, 0.0)]),
        component=band.component,
        material_group="titanium_g4",
        instance=anchor.instance_id,
    )
    other_implant = _points(
        _place(anchor, [(1.0, 1.0, 0.0)]), component=band.component, material_group="titanium_g4", instance="implant-2"
    )
    abutment = _points(
        _place(anchor, [(1.0, 0.5, 0.0)]), component="abutment", material_group="titanium_g5", instance="abutment-1"
    )
    points = _concat(own, other_implant, abutment)
    expected = [True, True, True, False, False, False, False]
    assert component_band_mask(points, anchor, conventions).tolist() == expected
    assert region_mask("implant_neck", points, anchor, conventions=conventions).tolist() == expected


def test_implant_neck_needs_an_anchor_of_its_component(conventions: Conventions) -> None:
    points = _points(np.zeros((1, 3)), component="tooth", material_group="dentine", instance="tooth-1")
    with pytest.raises(RegionError, match="anchor 'tooth-1' is a tooth"):
        component_band_mask(points, _anchor(instance_id="tooth-1", component="tooth"), conventions)


def test_whole_component_regions(conventions: Conventions) -> None:
    coords = np.zeros((1, 3))
    points = _concat(
        _points(coords, component="abutment", material_group="titanium_g5", instance="abutment-1"),
        _points(coords, component="abutment", material_group="titanium_g5", instance="abutment-2"),
        _points(coords, component="screw", material_group="titanium_g5", instance="abutment-1"),
    )
    assert component_mask(points, "abutment", conventions).tolist() == [True, True, False]
    assert region_mask("abutment", points, conventions=conventions).tolist() == [True, True, False]
    assert region_mask("abutment", points, instance="abutment-2", conventions=conventions).tolist() == [
        False,
        True,
        False,
    ]
    assert region_mask("screw", points, conventions=conventions).tolist() == [False, False, True]


def test_region_mask_errors(conventions: Conventions) -> None:
    points = _points(np.zeros((1, 3)), component="bone", material_group="cortical_bone")
    with pytest.raises(RegionError, match="unknown region 'gingiva'.*crestal_ring"):
        region_mask("gingiva", points, _anchor(), conventions=conventions)
    with pytest.raises(RegionError, match="report anchor"):
        region_mask("crestal_ring", points, conventions=conventions)
    with pytest.raises(RegionError, match="is a crestal_ring region, not a component region"):
        component_mask(points, "crestal_ring", conventions)


# --- sectors and the worse side ----------------------------------------------------------------------------------


SECTOR_CASES: list[tuple[float, str]] = [
    (0.0, "mesial"),
    (90.0, "buccal"),
    (180.0, "distal"),
    (270.0, "lingual"),
    (0.1, "mesial"),
    (359.9, "mesial"),
    (44.9, "mesial"),
    (45.1, "buccal"),
    (134.9, "buccal"),
    (135.1, "distal"),
    (224.9, "distal"),
    (225.1, "lingual"),
    (314.9, "lingual"),
    (315.1, "mesial"),
]


def test_sectors(conventions: Conventions) -> None:
    anchor = _anchor(platform_mm=(5.0, -3.0, 1.0))
    directions, expected = zip(*SECTOR_CASES, strict=True)
    coords = _place(anchor, [(3.0, 1.0 + i * 0.1, phi) for i, phi in enumerate(directions)])
    assert sector_of(coords, anchor, conventions).tolist() == list(expected)


TILTED_SECTOR_CASES: list[tuple[float, str]] = [
    (0.0, "mesial"),
    (90.0, "buccal"),
    (180.0, "distal"),
    (270.0, "lingual"),
    (30.0, "mesial"),
    (60.0, "buccal"),
    (150.0, "distal"),
    (300.0, "lingual"),
    # 45.1° around the tilted axis is 43.3° seen from +z: the sectors are judged in the frame's xy-plane.
    (45.1, "mesial"),
]


def test_sectors_of_an_implant_tilted_buccally(conventions: Conventions) -> None:
    anchor = _tilted_anchor()
    directions, expected = zip(*TILTED_SECTOR_CASES, strict=True)
    coords = _place(anchor, [(3.0, 1.0, phi) for phi in directions])
    assert sector_of(coords, anchor, conventions).tolist() == list(expected)


def test_sectors_of_an_implant_tilted_mesially(conventions: Conventions) -> None:
    anchor = _anchor(platform_mm=(-4.0, 2.0, 0.0), axis=(math.sin(TILT), 0.0, math.cos(TILT)))
    coords = _place(anchor, [(3.0, 1.0, phi) for phi in (0.0, 90.0, 180.0, 270.0)])
    assert sector_of(coords, anchor, conventions).tolist() == ["mesial", "buccal", "distal", "lingual"]


def test_sector_boundaries_belong_to_the_sector_that_starts_there(conventions: Conventions) -> None:
    anchor = _anchor(platform_mm=(10.0, 20.0, 0.0))
    coords = np.array([[11.0, 21.0, -1.0], [9.0, 21.0, -1.0], [9.0, 19.0, -1.0], [11.0, 19.0, -1.0]])
    assert sector_of(coords, anchor, conventions).tolist() == ["buccal", "distal", "lingual", "mesial"]


def test_worse_side_is_the_highest_volume_weighted_mean() -> None:
    values = [100.0, 300.0, 400.0, 900.0, 50.0]
    weights = [1.0, 3.0, 1.0, 0.0, 2.0]
    sectors = ["mesial", "mesial", "distal", "buccal", "lingual"]
    side, means = worse_side(values, weights, sectors)
    assert side == "distal"
    assert means == pytest.approx({"mesial": 250.0, "distal": 400.0, "lingual": 50.0})  # buccal has no volume


def test_worse_side_needs_points() -> None:
    with pytest.raises(RegionError, match="no integration points"):
        worse_side([], [], [])
    with pytest.raises(RegionError, match="no integration points"):
        worse_side([5.0], [0.0], ["mesial"])
    with pytest.raises(ValueError, match="same length"):
        worse_side([1.0, 2.0], [1.0], ["mesial", "distal"])


def test_ring_sectors_and_worse_side_together(ring: CrestalRingRegion, conventions: Conventions) -> None:
    anchor = _tilted_anchor()
    placements = [(3.0, 1.0, phi) for phi in (0.0, 90.0, 180.0, 270.0)] + [(6.0, 1.0, 180.0)]
    points = _points(_place(anchor, placements), component="bone", material_group=ring.material_group)
    strain = np.array([800.0, 1200.0, 1500.0, 900.0, 5000.0])  # the last point is outside the ring
    in_ring = region_mask("crestal_ring", points, anchor, conventions=conventions)
    sides = sector_of(points, anchor, conventions)
    side, means = worse_side(strain[in_ring], points.weights[in_ring], sides[in_ring])
    assert in_ring.tolist() == [True, True, True, True, False]
    assert side == "distal"
    assert means == {"mesial": 800.0, "buccal": 1200.0, "distal": 1500.0, "lingual": 900.0}
