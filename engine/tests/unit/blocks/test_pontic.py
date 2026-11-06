"""Pontic block (task T102): geometry, occlusal points, connector sockets level with the crowns, and the rule that a
pontic is held on both sides (`unsupported_pontic`)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from dentalsim.assembly.placement import place
from dentalsim.assembly.validity import check_assembly
from dentalsim.errors import Refused
from dentalsim.geometry.occ import bounding_box, volume_mm3
from dentalsim.geometry.transform import IDENTITY

from .test_crown import surface_distance


def problems_of(kit, scenario, code: str = "unsupported_pontic"):
    return [p for p in check_assembly(place(scenario), mode="fast") if p.code == code]


def test_a_pontic_needs_a_site(kit, occ_model) -> None:
    with pytest.raises(Refused, match="must be attached to a 'site' socket"):
        kit.block("pontic").build(kit.context("pontic", "pontic-1"))


def test_the_pontic_stands_clear_of_the_ridge(kit, occ_model) -> None:
    placed = place(kit.bridge()).block("pontic-1").context
    ctx = dataclasses.replace(placed, to_frame=IDENTITY)  # measured in its own frame
    body = kit.block("pontic").build(ctx).part("body")
    (x0, y0, z0), (x1, y1, z1) = bounding_box(body.volumes)
    s = ctx.settings
    assert (z0, z1) == pytest.approx((s.base_clearance_mm, s.base_clearance_mm + s.height_mm), abs=2e-3)
    assert (x0, x1) == pytest.approx((-s.mesiodistal_mm / 2, s.mesiodistal_mm / 2), abs=0.15)  # the loft may bulge
    assert (y0, y1) == pytest.approx((-s.buccolingual_mm / 2, s.buccolingual_mm / 2), abs=0.15)
    assert (body.component, body.material, body.strength_setting) == ("pontic", "zirconia", "strength")
    assert body.displaces == frozenset({"connector"})
    assert volume_mm3(body.volumes) < s.mesiodistal_mm * s.buccolingual_mm * s.height_mm


def test_the_occlusal_points_and_sockets_lie_on_its_surface(kit, occ_model) -> None:
    ctx = place(kit.bridge()).block("pontic-1").context
    pontic = kit.block("pontic")
    body = pontic.build(ctx).part("body")
    points = {p.name: p.position_mm for p in pontic.occlusal_points(ctx)}
    assert set(points) == {
        "buccal_cusp",
        "lingual_cusp",
        "central_fossa",
        "mesial_marginal_ridge",
        "distal_marginal_ridge",
    }
    for name, point in points.items():
        assert surface_distance(body.volumes, point) < 1e-6, name
    for socket in pontic.sockets(ctx):
        assert surface_distance(body.volumes, socket.frame.origin) < 1e-6, socket.name


def test_its_contact_areas_are_level_with_the_crowns_beside_it(kit) -> None:
    assembly = place(kit.bridge())
    pontic = {s.name: s for s in kit.block("pontic").sockets(assembly.block("pontic-1").context)}
    crown = {s.name: s for s in kit.block("crown").sockets(assembly.block("crown-1").context)}
    assert pontic["connector_mesial"].frame.origin[2] == pytest.approx(crown["connector_distal"].frame.origin[2])
    # The pontic at 35 lies distal of the reference site 34: its mesial contact area faces the crown on 34.
    gap = np.subtract(crown["connector_distal"].frame.origin, pontic["connector_mesial"].frame.origin)
    assert np.linalg.norm(gap) < 0.6
    assert np.dot(pontic["connector_mesial"].frame.axes[2], gap) > 0


def test_a_bridge_holds_its_pontic_on_both_sides(kit) -> None:
    assert problems_of(kit, kit.bridge()) == []
    links = {link.by: (link.a, link.b) for link in place(kit.bridge()).block("pontic-1").context.environment.links}
    assert links == {
        "connector-1": (("pontic-1", "connector_mesial"), ("crown-1", "connector_distal")),
        "connector-2": (("crown-2", "connector_mesial"), ("pontic-1", "connector_distal")),
    }


@pytest.mark.parametrize(
    ("without", "side"),
    [
        (("implant-1", "abutment-1", "crown-1"), "on its mesial side"),
        (("implant-2", "abutment-2", "crown-2", "connector-2"), "on its distal side"),
        (("connector-1", "connector-2"), "on either side"),
    ],
    ids=["mesial-implant-deleted", "distal-implant-deleted", "no-connectors"],
)
def test_a_pontic_left_unheld_is_reported_with_its_side(kit, without: tuple[str, ...], side: str) -> None:
    found = [p for p in problems_of(kit, kit.bridge(without=without)) if p.block == "pontic-1"]
    assert len(found) == 1 and side in found[0].message, found
    assert found[0].location_mm is not None


def test_two_pontics_side_by_side_are_held_through_each_other(kit) -> None:
    def span(*leave_out: str):
        blocks = [
            ("jaw", "jaw_segment", None, {"region": ("34", "35", "36", "37"), "site": "34", "neighbours": ("33",)}),
            *kit.unit_on_implant(1, "34"),
            *kit.unit_on_implant(2, "37", {"mesiodistal_mm": 10.0, "buccolingual_mm": 10.0}),
            ("pontic-1", "pontic", ("jaw", "site:35"), {"mesiodistal_mm": 6.6}),
            ("pontic-2", "pontic", ("jaw", "site:36"), {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
            ("connector-1", "splinting_connector", ("pontic-1", "connector_mesial"), {}),
            ("connector-2", "splinting_connector", ("pontic-2", "connector_mesial"), {}),
            ("connector-3", "splinting_connector", ("crown-2", "connector_mesial"), {}),
        ]
        return kit.scenario(*(b for b in blocks if b[0] not in leave_out))

    assert problems_of(kit, span()) == []
    # Without the molar crown, both pontics hang from the premolar alone: each is unheld distally.
    loose = problems_of(kit, span("implant-2", "abutment-2", "crown-2", "connector-3"))
    assert sorted(p.block for p in loose) == ["pontic-1", "pontic-2"]
    assert all("on its distal side" in p.message for p in loose)


def test_cusps_too_high_for_the_pontic_are_a_design_problem(kit) -> None:
    pontic = kit.block("pontic")
    reading = pontic.read_settings(kit.raw("pontic", height_mm=5.0, cusp_height_mm=3.5), instance_id="pontic-1")
    assert not reading.ok and "cusps are too high" in reading.problems[0].message
    flat = pontic.read_settings(
        kit.raw("pontic", height_mm=5.0, cusp_height_mm=3.5, occlusal_shape="flat"), instance_id="pontic-1"
    )
    assert flat.ok
