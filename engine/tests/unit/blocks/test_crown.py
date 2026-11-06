"""Crown block (task T065): bounding box, occlusal points on its surface, connector sockets, validity rules."""

from __future__ import annotations

import gmsh
import numpy as np
import pytest

from dentalsim.blocks.crown import SEAL_MM, CrownShape
from dentalsim.errors import Refused
from dentalsim.geometry.occ import bounding_box, volume_mm3


def surface_distance(tags, point) -> float:
    """Distance from `point` to the nearest boundary surface of the volumes `tags`."""
    gmsh.model.occ.synchronize()
    surfaces = gmsh.model.getBoundary([(3, t) for t in tags], oriented=False)
    return min(
        float(np.linalg.norm(np.subtract(gmsh.model.getClosestPoint(2, tag, list(point))[0], point)))
        for _, tag in surfaces
    )


def inside(tags, point) -> bool:
    gmsh.model.occ.synchronize()
    return any(gmsh.model.isInside(3, t, list(point)) for t in tags)


def test_a_crown_needs_a_crown_seat(kit, occ_model) -> None:
    with pytest.raises(Refused, match="must be attached to a 'crown_seat' socket"):
        kit.block("crown").build(kit.context("crown", "crown-1"))


def test_bounding_box_and_part_tags(kit, occ_model) -> None:
    chain = kit.chain(crown={"height_mm": 8.0, "mesiodistal_mm": 10.0, "buccolingual_mm": 9.0, "material": "cocr"})
    body = kit.block("crown").build(chain.crown).part("body")
    (x0, y0, z0), (x1, y1, z1) = bounding_box(body.volumes)
    collar = chain.abutment.settings.collar_mm
    assert (z0, z1) == pytest.approx((collar, collar + 8.0), abs=2e-3)
    # The smooth loft may bulge slightly past its widest section.
    assert 5.0 <= -x0 <= 5.25 and 5.0 <= x1 <= 5.25
    assert 4.5 <= -y0 <= 4.75 and 4.5 <= y1 <= 4.75
    assert (body.component, body.material, body.strength_setting) == ("crown", "cocr", "strength")
    assert body.displaces == frozenset({"connector"})  # a splinting connector entering it gives way (slice 1b)
    assert volume_mm3(body.volumes) < 10.0 * 9.0 * 8.0


@pytest.mark.parametrize(
    "abutment",
    [{}, {"abutment_type": "angled", "angle_deg": 20.0}, {"retention": "screw"}],
    ids=["straight", "angled", "screw-retained"],
)
def test_the_occlusal_points_lie_on_the_crown_surface(kit, occ_model, abutment: dict) -> None:
    chain = kit.chain(abutment=abutment)
    crown = kit.block("crown")
    body = crown.build(chain.crown).part("body")
    points = {p.name: p for p in crown.occlusal_points(chain.crown)}
    assert set(points) == {
        "buccal_cusp",
        "lingual_cusp",
        "central_fossa",
        "mesial_marginal_ridge",
        "distal_marginal_ridge",
    }
    for point in points.values():
        assert surface_distance(body.volumes, point.position_mm) < 1e-6, point.name
        assert point.normal == pytest.approx(chain.crown.to_frame.direction((0, 0, 1)))
    local = chain.crown.to_frame.inverse()
    buccal, lingual = (local.point(points[n].position_mm) for n in ("buccal_cusp", "lingual_cusp"))
    assert buccal[1] > 0 > lingual[1]  # +y is buccal in the crown's own frame


def test_the_cusp_tips_and_marginal_ridges_are_where_the_shape_puts_them(kit) -> None:
    chain = kit.chain()
    shape = CrownShape.of(chain.crown.settings)
    points = {p.name: p.position_mm for p in kit.block("crown").occlusal_points(chain.crown)}
    seat = chain.crown.to_frame.origin
    assert points["buccal_cusp"][2] - seat[2] == pytest.approx(chain.crown.settings.height_mm)
    assert points["mesial_marginal_ridge"][0] > 0 > points["distal_marginal_ridge"][0]
    assert points["central_fossa"][2] - seat[2] == pytest.approx(shape.fossa_z)
    assert shape.table_z < shape.fossa_z < shape.table_z + shape.cusp_height


def test_a_screw_retained_crown_has_a_sealed_access_channel(kit, occ_model) -> None:
    crown = kit.block("crown")
    cemented = kit.chain()
    screwed = kit.chain(abutment={"retention": "screw"})
    cemented_body = crown.build(cemented.crown).part("body")
    screwed_body = crown.build(screwed.crown).part("body")
    assert volume_mm3(screwed_body.volumes) < volume_mm3(cemented_body.volumes)
    shape = CrownShape.of(screwed.crown.settings)
    channel = screwed.crown.to_frame.point((0.0, 0.0, shape.table_z - SEAL_MM - 0.3))
    seal = screwed.crown.to_frame.point((0.0, 0.0, shape.table_z - SEAL_MM + 0.3))
    assert not inside(screwed_body.volumes, channel)
    assert inside(screwed_body.volumes, seal)
    assert inside(cemented_body.volumes, channel)


def test_the_connector_sockets_face_out_of_the_contact_areas(kit, occ_model) -> None:
    chain = kit.chain()
    crown = kit.block("crown")
    body = crown.build(chain.crown).part("body")
    sockets = {s.name: s for s in crown.sockets(chain.crown)}
    assert set(sockets) == {"connector_mesial", "connector_distal"}
    mesial, distal = sockets["connector_mesial"].frame, sockets["connector_distal"].frame
    assert mesial.axes[2] == pytest.approx((1.0, 0.0, 0.0)) and distal.axes[2] == pytest.approx((-1.0, 0.0, 0.0))
    assert mesial.axes[1] == pytest.approx((0.0, 1.0, 0.0))
    assert mesial.origin[0] == pytest.approx(chain.crown.settings.mesiodistal_mm / 2)
    for socket in sockets.values():
        assert surface_distance(body.volumes, socket.frame.origin) < 1e-6


def test_the_default_crown_on_the_default_abutment_is_valid(kit) -> None:
    chain = kit.chain()
    assert kit.block("crown").check(chain.crown) == []
    assert kit.block("crown").check(kit.context("crown", "loose")) == []


@pytest.mark.parametrize(
    ("crown", "abutment", "message"),
    [
        (
            {"height_mm": 6.0},
            {},
            "leaves 0.0 mm over the abutment post (at least 1 mm); raise the crown to at least 7.0",
        ),
        ({"mesiodistal_mm": 5.5}, {}, "narrower than the abutment margin"),
        ({"buccolingual_mm": 5.5, "mesiodistal_mm": 9.0}, {}, "occlusal table is narrower than the top of the"),
    ],
)
def test_a_crown_that_does_not_fit_its_abutment_is_a_problem(kit, crown: dict, abutment: dict, message: str) -> None:
    chain = kit.chain(crown=crown, abutment=abutment)
    problems = kit.block("crown").check(chain.crown)
    assert problems and all(p.code == "design_limit" and p.block == "crown-1" for p in problems)
    assert any(message in p.message for p in problems), [p.message for p in problems]
    assert problems[0].location_mm is not None


def test_cusps_too_high_for_the_crown_are_a_design_problem(kit) -> None:
    reading = kit.block("crown").read_settings(
        kit.raw("crown", height_mm=5.0, cusp_height_mm=3.5), instance_id="crown-1"
    )
    assert not reading.ok
    assert "cusps are too high" in reading.problems[0].message
