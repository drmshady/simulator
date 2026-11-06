"""Abutment block (task T065): volume, bounding box, the screw and its channel, the crown seat, validity rules."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dentalsim.errors import Refused
from dentalsim.geometry.occ import bounding_box, volume_mm3


def frustum(height: float, r0: float, r1: float) -> float:
    return math.pi * height / 3 * (r0 * r0 + r0 * r1 + r1 * r1)


def _built(kit, **abutment):
    chain = kit.chain(abutment=abutment, implant={"connection": abutment.pop("connection", "internal_conical")})
    return chain, kit.block("abutment").build(chain.abutment)


def test_an_abutment_needs_an_implant_platform(kit, occ_model) -> None:
    with pytest.raises(Refused, match="must be attached to a 'platform' socket"):
        kit.block("abutment").build(kit.context("abutment", "abutment-1"))


@pytest.mark.parametrize(
    ("connection", "below"),
    [
        ("external_hex", 0.0),
        ("internal_conical", frustum(3.0, 0.32 * 4.1, 0.32 * 4.1 - 3.0 * math.tan(math.radians(8.0)))),
        ("internal_hex", math.sqrt(3) / 2 * (0.6 * 4.1) ** 2 * 2.0),
    ],
)
def test_body_volume_is_collar_post_and_connection(kit, occ_model, connection: str, below: float) -> None:
    chain, parts = _built(kit, connection=connection)
    s = chain.abutment.settings
    collar = frustum(s.collar_mm, 2.05, s.margin_radius)
    post = frustum(s.height_mm, s.post_base_radius, s.post_top_radius)
    assert volume_mm3(parts.part("body").volumes) == pytest.approx(collar + post + below, rel=1e-6)


def test_parts_screw_and_access_channel(kit, occ_model) -> None:
    chain, parts = _built(kit)
    body, screw = parts.part("body"), parts.part("screw")
    assert (body.component, body.material, body.strength_setting) == ("abutment", "titanium_g5", "strength")
    assert body.displaces == {"crown", "bone", "implant"}
    assert (screw.component, screw.material, screw.strength_setting) == ("screw", "titanium_g5", "screw_strength")
    assert screw.displaces == {"abutment", "implant"}
    (channel,) = parts.cutters
    assert channel.name == "screw_access" and channel.cuts == {"abutment"}

    (bx0, by0, bz0), (bx1, by1, bz1) = bounding_box(body.volumes)
    assert (bz0, bz1) == pytest.approx((-3.0, 2.0 + 4.0), abs=1e-3)
    assert (bx0, bx1, by0, by1) == pytest.approx((-2.75, 2.75, -2.75, 2.75), abs=2e-3)
    head = 1.5 * 0.82
    (sx0, _, sz0), (sx1, _, sz1) = bounding_box(screw.volumes)
    assert (sz0, sz1) == pytest.approx((-7.0, 0.5 + 1.2), abs=1e-3)
    assert (sx0, sx1) == pytest.approx((-head, head), abs=2e-3)
    (_, _, cz0), (cx1, _, cz1) = bounding_box(channel.volumes)
    assert (cz0, cz1) == pytest.approx((1.7, 31.7), abs=1e-3)
    assert cx1 == pytest.approx(head + 0.1, abs=2e-3)


def test_an_external_hex_seats_the_screw_above_the_boss(kit, occ_model) -> None:
    _, parts = _built(kit, connection="external_hex")
    assert parts.part("body").displaces == {"crown", "bone"}
    _, (_, _, top) = bounding_box(parts.part("screw").volumes)
    assert top == pytest.approx(0.5 + 0.7 + 1.2, abs=1e-3)
    (_, _, bottom), _ = bounding_box(parts.part("body").volumes)
    assert bottom == pytest.approx(0.0, abs=1e-3)


def test_a_straight_crown_seat_is_on_the_margin(kit) -> None:
    chain = kit.chain(abutment={"collar_mm": 2.5, "retention": "screw"})
    seat = kit.block("abutment").sockets(chain.abutment)[0]
    assert seat.name == "crown_seat"
    assert seat.frame.origin == pytest.approx((0.0, 0.0, 2.5))
    assert np.allclose(seat.frame.axes, np.eye(3))
    assert seat.number("margin_radius_mm") == pytest.approx(2.75)
    assert seat.number("margin_tilt_deg") == 0.0
    assert seat.number("core_height_mm") == 4.0
    assert seat.text("retention") == "screw"
    assert seat.number("channel_radius_mm") == pytest.approx(1.5 * 0.82 + 0.1)


def test_an_angled_post_leans_lingually(kit, occ_model) -> None:
    chain, parts = _built(kit, abutment_type="angled", angle_deg=20.0)
    seat = kit.block("abutment").sockets(chain.abutment)[0]
    a = math.radians(20.0)
    assert seat.frame.axes[2] == pytest.approx((0.0, -math.sin(a), math.cos(a)))
    assert seat.number("margin_tilt_deg") == 20.0
    (_, y0, _), (_, y1, _) = bounding_box(parts.part("body").volumes)
    assert -y0 > y1  # the post reaches further toward lingual (-y) than buccal


@pytest.mark.parametrize(
    ("design", "message"),
    [
        ({"angle_deg": 10.0}, "a straight abutment has no angle"),
        ({"abutment_type": "angled"}, "an angled abutment needs an angle above 0°"),
        ({"margin_diameter_mm": 3.0, "height_mm": 9.0}, "the post narrows to"),
        ({"abutment_type": "angled", "angle_deg": 30.0, "collar_mm": 0.5}, "needs a collar of at least"),
        ({"retention": "glue"}, "must be one of cement, screw"),
    ],
)
def test_invalid_abutment_settings_are_design_problems(kit, design: dict, message: str) -> None:
    reading = kit.block("abutment").read_settings(kit.raw("abutment", **design), instance_id="abutment-1")
    assert not reading.ok
    assert all(p.code == "design_limit" for p in reading.problems)
    assert any(message in p.message for p in reading.problems), [p.message for p in reading.problems]


def test_a_thin_post_around_the_screw_channel_is_a_problem(kit) -> None:
    chain = kit.chain(abutment={"margin_diameter_mm": 4.0, "height_mm": 3.0})
    (problem,) = kit.block("abutment").check(chain.abutment)
    assert problem.code == "design_limit" and "the screw channel leaves" in problem.message
    assert problem.location_mm == pytest.approx((0.0, 0.0, 5.0))


def test_the_default_abutment_is_valid(kit) -> None:
    chain = kit.chain()
    assert kit.block("abutment").check(chain.abutment) == []
    assert kit.block("abutment").check(kit.context("abutment", "loose")) == []
