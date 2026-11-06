"""Jaw segment block (task T065): volume, bounding box, attach position, supports, keep-out volumes, validity rules."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dentalsim.blocks.jaw_segment import TOOTH_WIDTHS_MM, layout
from dentalsim.geometry.occ import bounding_box, volume_mm3


def _keep_outs(kit, ctx) -> dict:
    return {k.name: k for k in kit.block("jaw_segment").keep_out(ctx)}


def test_bone_volumes_match_the_swept_cross_section(kit, occ_model) -> None:
    ctx = kit.context("jaw_segment", "jaw", region=("35", "36", "37"), site="36")
    parts = kit.block("jaw_segment").build(ctx)
    plan = layout(ctx.settings)
    length = plan.segment[1] - plan.segment[0]
    cortical, cancellous = parts.part("cortical"), parts.part("cancellous")
    # A symmetric profile swept along a planar curve has volume = area × path length (Pappus).
    assert volume_mm3(cancellous.volumes) == pytest.approx(plan.inner.area * length, rel=1e-5)
    assert volume_mm3(cortical.volumes) == pytest.approx((plan.outer.area - plan.inner.area) * length, rel=1e-5)
    assert (cortical.component, cortical.material) == ("bone", "cortical_bone")
    assert (cancellous.component, cancellous.material) == ("bone", "cancellous_bone")
    assert cortical.e_mpa == 13700.0 and cancellous.e_mpa == 1370.0


def test_bounding_box_spans_the_ridge(kit, occ_model) -> None:
    ctx = kit.context("jaw_segment", "jaw", ridge_height_mm=20.0, basal_width_mm=12.0)
    parts = kit.block("jaw_segment").build(ctx)
    (x0, y0, z0), (x1, y1, z1) = bounding_box(parts.part("cortical").volumes)
    assert (z0, z1) == (pytest.approx(-20.0, abs=1e-3), pytest.approx(0.0, abs=1e-3))
    assert y1 - y0 == pytest.approx(12.0, abs=0.6)  # the base is 12 mm wide; the arch curves it a little
    width_36 = TOOTH_WIDTHS_MM["mandible"][5]
    # 29 mm along the arch at the crest; the end faces stand at right angles to the curving arch, so their buccal
    # corners reach a little further in x.
    assert width_36 + 2 * 9.0 <= x1 - x0 <= width_36 + 2 * 9.0 + 2.0
    (_, _, c0), (_, _, c1) = bounding_box(parts.part("cancellous").volumes)
    assert (c0, c1) == (pytest.approx(-18.0, abs=1e-3), pytest.approx(-2.0, abs=1e-3))


def test_the_site_socket_is_the_crest_centre_raised_by_the_bone_loss(kit, occ_model) -> None:
    jaw = kit.block("jaw_segment")
    site = jaw.sockets(kit.context("jaw_segment", "jaw"))[0]
    assert site.name == "site:36" and site.frame.is_identity(1e-9)
    assert site.properties["fdi"] == "36"
    lowered = kit.context("jaw_segment", "jaw", crestal_bone_loss_mm=1.5)
    raised = jaw.sockets(lowered)[0]
    assert raised.frame.origin == pytest.approx((0.0, 0.0, 1.5))
    assert np.allclose(raised.frame.axes, np.eye(3))
    _, (_, _, top) = bounding_box(jaw.build(lowered).part("cortical").volumes)
    assert top == pytest.approx(0.0, abs=1e-3)


def test_the_cut_faces_are_the_supports(kit, occ_model) -> None:
    ctx = kit.context("jaw_segment", "jaw")
    supports = {s.name: s for s in kit.block("jaw_segment").build(ctx).supports}
    assert set(supports) == {"mesial_cut", "distal_cut"}
    mesial, distal = supports["mesial_cut"], supports["distal_cut"]
    assert mesial.point[0] > 0 > distal.point[0]  # +x is mesial
    for support in (mesial, distal):
        assert support.normal[2] == pytest.approx(0.0)
        assert np.dot(support.point, support.normal) > 0  # the normal points out of the segment
        assert support.point[2] == pytest.approx(0.0)


def test_the_bone_volume_matches_the_bone(kit) -> None:
    ctx = kit.context("jaw_segment", "jaw")
    bone = kit.block("jaw_segment").bone_volumes(ctx)[0]
    plan = layout(ctx.settings)
    end = plan.segment[1] - plan.arch.centre(plan.site_key)
    points = [(0, 0, -0.1), (0, 0, 0.1), (0, 0, -23.9), (0, 0, -24.1), (0, 4.55, -2.5), (0, 4.7, -2.5)]
    assert bone.contains(points).tolist() == [True, False, True, False, True, False]
    beyond = plan.path.at(plan.segment[1] + 0.2)[0]
    within = plan.path.at(plan.segment[1] - 0.2)[0]
    assert bone.contains([[*beyond[:2], -5.0], [*within[:2], -5.0]]).tolist() == [False, True]
    assert end > 0


def test_mandible_keep_outs_are_the_nerve_canal_and_neighbouring_roots(kit) -> None:
    ctx = kit.context(
        "jaw_segment", "jaw", neighbours=("35", "37"), nerve_canal_depth_mm=14.0, nerve_canal_offset_mm=-1.0
    )
    keep_outs = _keep_outs(kit, ctx)
    assert set(keep_outs) == {"nerve_canal", "root_35", "root_37"}
    canal = keep_outs["nerve_canal"]
    assert canal.code == "in_nerve_canal" and canal.label == "the nerve canal" and canal.owner == "jaw"
    assert canal.volume.contains(
        [(0, -1.0, -14.05), (0, -1.0, -13.95), (0, -1.0, -16.95), (0, -1.0, -17.05)]
    ).tolist() == [
        True,
        False,
        True,
        False,
    ]
    widths = TOOTH_WIDTHS_MM["mandible"]
    for code, side, along in (("35", 1, widths[4] / 2 + widths[5] / 2), ("37", -1, widths[5] / 2 + widths[6] / 2)):
        root = keep_outs[f"root_{code}"]
        assert root.code == "in_root" and root.label == f"the root of tooth {code}"
        top = root.volume.start
        assert np.sign(top[0]) == side
        assert math.hypot(top[0], top[1]) == pytest.approx(along, rel=0.01)  # the arch bends the chord a little
        assert root.volume.contains([(top[0], top[1], -1.0), (top[0], top[1], -13.5)]).tolist() == [True, False]


def test_maxilla_keep_outs_are_the_sinus_when_present(kit) -> None:
    absent = kit.context("jaw_segment", "jaw", jaw="maxilla", region=("16",), site="16")
    assert _keep_outs(kit, absent) == {}
    present = kit.context("jaw_segment", "jaw", jaw="maxilla", region=("16",), site="16", sinus="present")
    sinus = _keep_outs(kit, present)["sinus"]
    assert sinus.code == "in_sinus"
    assert sinus.volume.contains([(0, 0, -24.5), (0, 0, -23.5), (0, 6.0, -25.0)]).tolist() == [True, False, True]


def test_a_mirrored_quadrant_gives_the_same_model(kit, occ_model) -> None:
    jaw = kit.block("jaw_segment")
    left = kit.context("jaw_segment", "jaw", region=("36",), site="36", neighbours=("35", "37"))
    right = kit.context("jaw_segment", "jaw", region=("46",), site="46", neighbours=("45", "47"))
    left_parts, right_parts = jaw.build(left), jaw.build(right)
    for name in ("cortical", "cancellous"):
        assert volume_mm3(left_parts.part(name).volumes) == pytest.approx(volume_mm3(right_parts.part(name).volumes))
    left_roots, right_roots = _keep_outs(kit, left), _keep_outs(kit, right)
    assert left_roots["root_35"].volume.start == pytest.approx(right_roots["root_45"].volume.start)
    assert left_roots["root_37"].volume.start == pytest.approx(right_roots["root_47"].volume.start)


def test_a_region_across_the_midline_and_a_full_arch_build(kit, occ_model) -> None:
    jaw = kit.block("jaw_segment")
    anterior = jaw.build(kit.context("jaw_segment", "jaw", region=("31", "41"), site="31", neighbours=("32", "42")))
    assert {s.name for s in anterior.supports} == {"contralateral_cut", "distal_cut"}
    full = tuple(f"3{p}" for p in range(7, 0, -1)) + tuple(f"4{p}" for p in range(1, 8))
    ctx = kit.context("jaw_segment", "jaw", region=full, site="36")
    assert kit.block("jaw_segment").rules(ctx.settings, "jaw") == []
    parts = jaw.build(ctx)
    (x0, y0, _), (x1, y1, _) = bounding_box(parts.part("cortical").volumes)
    assert y1 - y0 > 30.0  # the arch curves round through the incisors
    plan = layout(ctx.settings)
    assert volume_mm3(parts.part("cancellous").volumes) == pytest.approx(
        plan.inner.area * (plan.segment[1] - plan.segment[0]), rel=1e-4
    )


@pytest.mark.parametrize(
    ("design", "message"),
    [
        ({"region": ("39",), "site": "39"}, "'39' is not an FDI tooth code"),
        ({"region": ("16",), "site": "16"}, "tooth 16 is in the maxilla, not the mandible"),
        ({"region": ("35", "37"), "site": "35"}, "must follow each other along the arch"),
        ({"region": ("36",), "site": "34"}, "tooth 34 is not in the region"),
        ({"region": ("36",), "neighbours": ("34",)}, "tooth 34 is not next to the region"),
        ({"region": ("36",), "neighbours": ("36",)}, "tooth 36 is in the region"),
        ({"ridge_width_mm": 4.0, "basal_width_mm": 5.0}, "too thin for a 2 mm cortical shell"),
        ({"ridge_width_mm": 4.0, "basal_width_mm": 20.0, "ridge_height_mm": 6.0}, "the sides of the ridge slope"),
        ({"nerve_canal_depth_mm": 22.0}, "the nerve canal lies partly outside the bone"),
        ({"nerve_canal_offset_mm": 4.5}, "the nerve canal lies partly outside the bone"),
        ({"sinus": "present"}, "the maxillary sinus applies to the maxilla only"),
    ],
)
def test_invalid_jaw_settings_are_design_problems(kit, design: dict, message: str) -> None:
    reading = kit.block("jaw_segment").read_settings(kit.raw("jaw_segment", **design), instance_id="jaw")
    assert not reading.ok
    assert all(p.code == "design_limit" and p.block == "jaw" for p in reading.problems)
    assert any(message in p.message for p in reading.problems), [p.message for p in reading.problems]


def test_the_default_jaw_is_valid_and_has_no_assembly_checks(kit) -> None:
    jaw = kit.block("jaw_segment")
    reading = jaw.read_settings(kit.raw("jaw_segment", neighbours=("35", "37")), instance_id="jaw")
    assert reading.ok, reading.problems
    ctx = jaw.context("jaw", reading.settings)
    assert jaw.check(ctx) == [] and jaw.report_anchors(ctx) == () and jaw.occlusal_points(ctx) == ()
