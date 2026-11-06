"""Several sites along the arch (task T103): the jaw segment offers one site per tooth of its region, spaced by the
tooth widths, and the assembly places blocks on them; past the midline the blocks name their sides anatomically."""

from __future__ import annotations

import numpy as np
import pytest

from dentalsim.assembly.placement import place
from dentalsim.assembly.validity import check_assembly
from dentalsim.blocks.jaw_segment import TOOTH_WIDTHS_MM

FULL_ARCH = ("46", "45", "44", "43", "42", "41", "31", "32", "33", "34", "35", "36")


def sockets_of(kit, **design):
    jaw = kit.block("jaw_segment")
    return {s.name: s for s in jaw.sockets(kit.context("jaw_segment", "jaw", **design))}


def test_there_is_one_site_per_tooth_spaced_by_the_tooth_widths(kit) -> None:
    sites = sockets_of(kit, region=("34", "35", "36"), site="34", crestal_bone_loss_mm=1.0)
    assert list(sites) == ["site:34", "site:35", "site:36"]
    assert sites["site:34"].frame.origin == pytest.approx((0.0, 0.0, 1.0))  # the reference site is the origin
    widths = TOOTH_WIDTHS_MM["mandible"]
    for a, b in (("34", "35"), ("35", "36")):
        spacing = np.linalg.norm(np.subtract(sites[f"site:{b}"].frame.origin, sites[f"site:{a}"].frame.origin))
        arc = (widths[int(a[1]) - 1] + widths[int(b[1]) - 1]) / 2
        assert 0.97 * arc < spacing <= arc  # a chord of the arch
    for socket in sites.values():
        x, y, z = (np.asarray(axis) for axis in socket.frame.axes)
        assert z == pytest.approx((0.0, 0.0, 1.0)) and socket.frame.origin[2] == pytest.approx(1.0)
        assert socket.properties["x_toward"] == "mesial"
        assert socket.properties["tooth_width_mm"] == widths[int(socket.properties["fdi"][1]) - 1]
    # Distal sites turn with the arch, and +x keeps pointing mesial (toward the site before).
    toward_34 = np.subtract(sites["site:34"].frame.origin, sites["site:35"].frame.origin)
    assert np.dot(sites["site:35"].frame.axes[0], toward_34) > 0.99 * np.linalg.norm(toward_34)


def test_past_the_midline_x_runs_on_along_the_arch_and_points_distal(kit) -> None:
    sites = sockets_of(kit, region=FULL_ARCH, site="33")
    assert {sites[f"site:{c}"].properties["x_toward"] for c in ("31", "36")} == {"mesial"}
    assert {sites[f"site:{c}"].properties["x_toward"] for c in ("41", "46")} == {"distal"}
    toward_42 = np.subtract(sites["site:42"].frame.origin, sites["site:41"].frame.origin)
    assert np.dot(sites["site:41"].frame.axes[0], toward_42) > 0  # distal, away from the midline
    # y stays buccal (outward from the arch) on both sides, so the frames stay right-handed without a reflection.
    centre = np.mean([s.frame.origin for s in sites.values()], axis=0)
    for code in ("46", "41", "31", "36"):
        frame = sites[f"site:{code}"].frame
        assert np.dot(frame.axes[1], np.subtract(frame.origin, centre)) > 0, code
        assert np.linalg.det(frame.rotation) == pytest.approx(1.0)


def test_blocks_past_the_midline_name_their_sides_anatomically(kit) -> None:
    scenario = kit.scenario(
        ("jaw", "jaw_segment", None, {"region": FULL_ARCH, "site": "33"}),
        *kit.unit_on_implant(1, "42", {"mesiodistal_mm": 5.1, "buccolingual_mm": 6.0}),
        ("pontic-1", "pontic", ("jaw", "site:41"), {"mesiodistal_mm": 4.6, "buccolingual_mm": 6.0}),
        ("pontic-2", "pontic", ("jaw", "site:31"), {"mesiodistal_mm": 4.6, "buccolingual_mm": 6.0}),
    )
    assembly = place(scenario)
    midline = np.mean(
        [assembly.block(p).context.to_frame.origin for p in ("pontic-1", "pontic-2")], axis=0
    )  # between 41 and 31
    for instance_id in ("crown-1", "pontic-1", "pontic-2"):
        placed = assembly.block(instance_id)
        sockets = {s.name: s.frame for s in placed.block_type.sockets(placed.context)}
        mesial, distal = sockets["connector_mesial"].origin, sockets["connector_distal"].origin
        assert np.linalg.norm(np.subtract(mesial, midline)) < np.linalg.norm(np.subtract(distal, midline)), instance_id
        ridges = {p.name: p.position_mm for p in placed.block_type.occlusal_points(placed.context)}
        assert np.linalg.norm(np.subtract(ridges["mesial_marginal_ridge"], midline)) < np.linalg.norm(
            np.subtract(ridges["distal_marginal_ridge"], midline)
        ), instance_id


def test_a_bare_site_is_the_reference_site(kit) -> None:
    def two_on(first: str, second: str):
        return kit.scenario(
            ("jaw", "jaw_segment", None, {"region": ("35", "36"), "site": "36"}),
            ("implant-1", "implant", ("jaw", first), {}),
            ("implant-2", "implant", ("jaw", second), {}),
        )

    assert check_assembly(place(two_on("site", "site:35")), mode="fast") == []
    clash = check_assembly(place(two_on("site", "site:36")), mode="fast")
    assert [(p.code, p.block) for p in clash] == [("overlap", "implant-2")]
    assert "attached to site 36 of Jaw segment 'jaw', which already holds 'implant-1'" in clash[0].message


def test_a_site_outside_the_region_is_named_and_left_unplaced(kit) -> None:
    scenario = kit.scenario(
        ("jaw", "jaw_segment", None, {"region": ("35", "36"), "site": "36"}),
        *kit.unit_on_implant(1, "37"),
    )
    assembly = place(scenario)
    problems = list(assembly.problems)
    assert [(p.code, p.block) for p in problems] == [("design_limit", "implant-1")]
    assert (
        "attached to site 37 of Jaw segment 'jaw', which offers no such socket (it offers 35, 36)"
        in problems[0].message
    )
    assert not assembly.complete
    assert {b.instance_id for b in assembly.blocks if b.context is None} == {"implant-1", "abutment-1", "crown-1"}
