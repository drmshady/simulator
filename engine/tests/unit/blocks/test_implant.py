"""Implant block (task T065): volume, bounding box, attach position, region anchor and every validity rule."""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

from dentalsim.blocks.base import Environment
from dentalsim.blocks.implant import EXTERNAL_HEX, ImplantShape, revolve_outline
from dentalsim.geometry.occ import bounding_box, volume_mm3
from dentalsim.geometry.transform import Transform


def pappus_volume(outline: list[tuple[float, float]]) -> float:
    """Volume of the solid swept by a closed (r, z) polygon about the z-axis: 2π × area × centroid radius."""
    total = 0.0
    for (r0, z0), (r1, z1) in zip(outline, outline[1:] + outline[:1], strict=True):
        total += (r0 + r1) * (r0 * z1 - r1 * z0)
    return abs(math.pi * total / 3)


@pytest.mark.parametrize("thread", ["v_thread", "buttress", "square", "none"])
def test_volume_is_the_revolved_outline(kit, occ_model, thread: str) -> None:
    ctx = kit.context("implant", "implant-1", thread=thread)
    body = kit.block("implant").build(ctx).part("body")
    expected = pappus_volume(revolve_outline(ctx.settings, ImplantShape.of(ctx.settings)))
    assert volume_mm3(body.volumes) == pytest.approx(expected, rel=1e-6)
    shape = ImplantShape.of(ctx.settings)
    assert expected < math.pi * shape.radius**2 * shape.length


def test_threads_remove_material_between_the_teeth(kit, occ_model) -> None:
    implant = kit.block("implant")
    threaded = volume_mm3(implant.build(kit.context("implant", "a", thread="v_thread")).part("body").volumes)
    plain = volume_mm3(implant.build(kit.context("implant", "b", thread="none")).part("body").volumes)
    assert threaded < plain


def test_an_external_hex_adds_its_boss(kit, occ_model) -> None:
    implant = kit.block("implant")
    internal = implant.build(kit.context("implant", "a", connection="internal_hex")).part("body")
    external = implant.build(kit.context("implant", "b", connection="external_hex")).part("body")
    across = EXTERNAL_HEX["across_flats"] * 4.1
    boss = math.sqrt(3) / 2 * across**2 * EXTERNAL_HEX["height_mm"]
    assert volume_mm3(external.volumes) == pytest.approx(volume_mm3(internal.volumes) + boss, rel=1e-6)
    assert bounding_box(external.volumes)[1][2] == pytest.approx(EXTERNAL_HEX["height_mm"], abs=1e-3)
    assert external.displaces == {"bone", "abutment"} and internal.displaces == {"bone"}


def test_bounding_box_and_part_tags(kit, occ_model) -> None:
    ctx = kit.context("implant", "implant-1", diameter_mm=4.8, length_mm=11.5, material="titanium_g5")
    parts = kit.block("implant").build(ctx)
    body = parts.part("body")
    (x0, y0, z0), (x1, y1, z1) = bounding_box(body.volumes)
    assert (x0, x1, y0, y1) == pytest.approx((-2.4, 2.4, -2.4, 2.4), abs=2e-3)
    assert (z0, z1) == pytest.approx((-11.5, 0.0), abs=1e-3)
    assert (body.component, body.material, body.strength_setting) == ("implant", "titanium_g5", "strength")
    assert (body.e_mpa, body.nu) == (105000.0, 0.37)
    assert [h.part for h in parts.hints] == ["body"]


def test_placement_moves_the_body(kit, occ_model) -> None:
    to_frame = Transform.translation_by((1.0, 2.0, 0.5)) @ Transform.rotation_about((0, 1, 0), 90.0)
    body = kit.block("implant").build(kit.context("implant", "i", to_frame=to_frame)).part("body")
    (x0, y0, z0), (x1, y1, z1) = bounding_box(body.volumes)
    # The axis now runs along +x from the platform at (1, 2, 0.5); the apex is at x = 1 - 10.
    assert (x0, x1) == pytest.approx((-9.0, 1.0), abs=1e-3)
    assert (y0, y1, z0, z1) == pytest.approx((2 - 2.05, 2 + 2.05, 0.5 - 2.05, 0.5 + 2.05), abs=2e-3)


@pytest.mark.parametrize(
    ("connection", "depth", "radius", "boss"),
    [
        ("internal_conical", 3.0, 0.32 * 4.1, 0.0),
        ("internal_hex", 2.0, 0.3 * 4.1, 0.0),
        ("external_hex", 0.0, 0.0, 0.7),
    ],
)
def test_the_platform_socket_describes_the_connection(
    kit, connection: str, depth: float, radius: float, boss: float
) -> None:
    to_frame = Transform.translation_by((0.0, 0.0, 1.0))
    platform = kit.block("implant").sockets(kit.context("implant", "i", to_frame=to_frame, connection=connection))[0]
    assert platform.name == "platform"
    assert platform.frame.origin == pytest.approx((0.0, 0.0, 1.0))
    assert platform.text("connection") == connection
    assert platform.number("platform_radius_mm") == pytest.approx(2.05)
    assert platform.number("connection_depth_mm") == pytest.approx(depth)
    assert platform.number("connection_radius_mm") == pytest.approx(radius)
    assert platform.number("boss_height_mm") == pytest.approx(boss)
    assert platform.number("screw_radius_mm") == pytest.approx(0.82)
    assert platform.number("screw_depth_mm") == pytest.approx(min(depth + 4.0, 10.0 - 2.0))


@pytest.mark.parametrize("thread", ["v_thread", "none"])
def test_the_report_anchor_follows_the_thread_core(kit, thread: str) -> None:
    ctx = kit.context("implant", "implant-1", thread=thread, collar_mm=0.8, thread_depth_mm=0.4)
    (anchor,) = kit.block("implant").report_anchors(ctx)
    depths = [d for d, _ in anchor.radius_profile]
    radii = dict(anchor.radius_profile)
    assert depths[0] == 0.0 and depths[-1] == pytest.approx(10.0)
    assert all(b > a for a, b in zip(depths, depths[1:], strict=False))
    assert radii[0.0] == pytest.approx(2.05)
    core = 2.05 - 0.4 if thread != "none" else 2.05
    assert min(radii.values()) < core
    assert any(math.isclose(r, core) for r in radii.values())
    assert anchor.component == "implant" and anchor.instance_id == "implant-1"
    assert anchor.crest_depth_mm == 0.0  # no bone around it here


# --- checks in a jaw -----------------------------------------------------------------------------------------------


def _codes(kit, chain) -> list[str]:
    return [p.code for p in kit.block("implant").check(chain.implant)]


def test_the_default_single_crown_implant_is_valid(kit) -> None:
    chain = kit.chain()
    assert kit.block("implant").check(chain.implant) == []
    (anchor,) = kit.block("implant").report_anchors(chain.implant)
    assert anchor.crest_depth_mm == pytest.approx(0.0, abs=1e-6)


def test_an_implant_into_the_nerve_canal_is_located(kit) -> None:
    chain = kit.chain(jaw={"nerve_canal_depth_mm": 9.0}, implant={"length_mm": 10.0})
    (problem,) = kit.block("implant").check(chain.implant)
    assert problem.code == "in_nerve_canal" and problem.block == "implant-1"
    assert re.search(r"enters the nerve canal from 9\.\d to 10\.0 mm below its platform", problem.message)
    x, y, z = problem.location_mm
    assert abs(x) < 1.0 and abs(y) < 1.0 and -10.0 < z < -9.0


def test_an_implant_longer_than_the_bone_exceeds_it(kit) -> None:
    chain = kit.chain(
        jaw={"jaw": "maxilla", "region": ("16",), "site": "16", "neighbours": (), "ridge_height_mm": 8.0},
        implant={"length_mm": 10.0},
    )
    (problem,) = kit.block("implant").check(chain.implant)
    assert problem.code == "exceeds_bone"
    assert re.search(r"is not covered by bone from 8\.\d to 10\.0 mm below its platform", problem.message)
    assert -10.0 < problem.location_mm[2] < -8.0


def test_an_implant_through_the_sinus_floor_is_in_the_sinus(kit) -> None:
    chain = kit.chain(
        jaw={
            "jaw": "maxilla",
            "region": ("16",),
            "site": "16",
            "neighbours": (),
            "ridge_height_mm": 8.0,
            "sinus": "present",
        },
        implant={"length_mm": 10.0},
    )
    assert _codes(kit, chain) == ["in_sinus"]


def test_an_implant_just_reaching_the_base_of_the_bone_is_valid(kit) -> None:
    chain = kit.chain(
        jaw={
            "jaw": "maxilla",
            "region": ("16",),
            "site": "16",
            "neighbours": (),
            "ridge_height_mm": 10.0,
            "sinus": "present",
        },
        implant={"length_mm": 10.0},
    )
    assert _codes(kit, chain) == []


def test_an_implant_wider_than_the_ridge_exceeds_the_bone(kit) -> None:
    chain = kit.chain(jaw={"ridge_width_mm": 5.0, "basal_width_mm": 6.0}, implant={"diameter_mm": 5.0})
    problems = kit.block("implant").check(chain.implant)
    assert [p.code for p in problems] == ["exceeds_bone"]
    assert "longer, wider or more tilted than the available bone allows" in problems[0].message


def test_an_implant_against_a_neighbouring_root_is_in_the_root(kit) -> None:
    chain = kit.chain(jaw={"neighbour_root_radius_mm": 5.0}, implant_offset=Transform.translation_by((-5.5, 0.0, 0.0)))
    problems = kit.block("implant").check(chain.implant)
    assert [p.code for p in problems] == ["in_root"]
    assert "enters the root of tooth 37" in problems[0].message
    assert problems[0].location_mm[0] < -5.5


def test_a_tilted_implant_sunk_below_the_crest_is_valid(kit) -> None:
    tilt = Transform.translation_by((0.0, 0.0, -1.0)) @ Transform.rotation_about((0, 1, 0), 20.0)
    chain = kit.chain(implant_offset=tilt)
    assert _codes(kit, chain) == []
    (anchor,) = kit.block("implant").report_anchors(chain.implant)
    assert anchor.axis == pytest.approx((math.sin(math.radians(20)), 0.0, math.cos(math.radians(20))))
    # The axis meets the crest plane z = 0 at 1 / cos 20° above the platform.
    assert anchor.crest_depth_mm == pytest.approx(-1.0 / math.cos(math.radians(20)), abs=1e-6)


def test_a_tilted_implant_at_crest_level_is_exposed_on_its_high_side(kit) -> None:
    chain = kit.chain(implant_offset=Transform.rotation_about((0, 1, 0), 20.0))
    (problem,) = kit.block("implant").check(chain.implant)
    assert problem.code == "exceeds_bone" and "more tilted than the available bone allows" in problem.message
    assert problem.location_mm[0] < 0 and problem.location_mm[2] > -2.0  # distal side, near the neck


@pytest.mark.parametrize(("offset", "loss", "crest"), [(1.0, 0.0, 1.0), (-1.0, 0.0, -1.0), (0.0, 1.5, 1.5)])
def test_the_crest_depth_is_measured_from_the_platform(kit, offset: float, loss: float, crest: float) -> None:
    chain = kit.chain(jaw={"crestal_bone_loss_mm": loss}, implant_offset=Transform.translation_by((0, 0, offset)))
    (anchor,) = kit.block("implant").report_anchors(chain.implant)
    assert anchor.crest_depth_mm == pytest.approx(crest, abs=1e-6)
    assert kit.block("implant").check(chain.implant) == []


def test_an_implant_out_of_the_bone_is_reported(kit) -> None:
    jaw = kit.context("jaw_segment", "jaw")
    above = kit.context(
        "implant", "implant-1", to_frame=Transform.translation_by((0, 0, 30.0)), environment=kit.jaw_environment(jaw)
    )
    (problem,) = kit.block("implant").check(above)
    assert problem.code == "exceeds_bone" and "is not in the bone" in problem.message


def test_without_an_environment_there_is_nothing_to_check(kit) -> None:
    ctx = kit.context("implant", "implant-1", environment=Environment())
    assert kit.block("implant").check(ctx) == []


@pytest.mark.parametrize(
    ("design", "message"),
    [
        ({"thread_pitch_mm": 0.4, "thread_depth_mm": 0.6}, "does not fit a 0.4 mm pitch"),
        ({"diameter_mm": 3.0, "thread_depth_mm": 0.6}, "of implant wall"),
        ({"diameter_mm": 7.0}, "Diameter is 7 mm; it must be 3 mm to 6.5 mm"),
    ],
)
def test_invalid_implant_settings_are_design_problems(kit, design: dict, message: str) -> None:
    reading = kit.block("implant").read_settings(kit.raw("implant", **design), instance_id="implant-1")
    assert not reading.ok
    assert all(p.code == "design_limit" for p in reading.problems)
    assert any(message in p.message for p in reading.problems), [p.message for p in reading.problems]


def test_sample_points_lie_just_inside_the_envelope(kit) -> None:
    ctx = kit.context("implant", "implant-1")
    points, depths = kit.block("implant").surface_samples(ctx)
    radial = np.hypot(points[:, 0], points[:, 1])
    assert radial.max() == pytest.approx(2.05 - 0.02)
    assert depths.max() == pytest.approx(10.0 - 0.02) and depths.min() == 0.0
