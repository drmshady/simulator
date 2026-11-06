"""Splinting connector block (task T102): it finds the facing contact area of the unit beside it, spans the gap with
its elliptical section, and reports a connector that reaches nothing or duplicates another."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dentalsim.assembly.placement import place
from dentalsim.assembly.validity import check_assembly
from dentalsim.blocks.splinting_connector import EMBED_MM, JOIN_FEATURE_MM, MAX_GAP_MM, ROOM_MARGIN_MM, STUB_MM
from dentalsim.geometry.occ import bounding_box, volume_mm3


def splinted(kit, *, crown_2: dict | None = None, extra: tuple = ()):
    """Crowns on implants at 35 and 36, joined by a connector hanging from the molar crown."""
    return kit.scenario(
        ("jaw", "jaw_segment", None, {"region": ("35", "36"), "site": "36", "neighbours": ("34", "37")}),
        *kit.unit_on_implant(1, "36", {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
        *kit.unit_on_implant(2, "35", crown_2),
        ("connector-1", "splinting_connector", ("crown-1", "connector_mesial"), {}),
        *extra,
    )


def test_it_joins_the_facing_contact_area_of_the_unit_beside_it(kit, occ_model) -> None:
    assembly = place(splinted(kit))
    ctx = assembly.block("connector-1").context
    connector = kit.block("splinting_connector")
    partner = connector.partner(ctx)
    assert (partner.owner, partner.socket) == ("crown-2", "connector_distal")
    assert connector.links(ctx)[0].a == ("crown-1", "connector_mesial")
    gap = float(np.linalg.norm(np.subtract(partner.position_mm, ctx.to_frame.origin)))
    assert 0.0 < gap < 0.6  # the units are 0.4–0.5 mm narrower than their teeth
    made = connector.build(ctx)
    body = made.part("body")
    s = ctx.settings
    here = ctx.environment.joint("crown-1", "connector_mesial")
    reach = gap + connector._embed(s, here) + connector._embed(s, partner)
    # An exact elliptical section extruded over the gap and into both units, its end rims inside them.
    assert volume_mm3(body.volumes) == pytest.approx(math.pi * s.height_mm * s.width_mm / 4 * reach, rel=1e-6)
    assert connector._embed(s, here) > EMBED_MM and connector._embed(s, partner) <= partner.max_depth_mm
    assert (body.component, body.displaces) == ("connector", frozenset())
    assert [(h.part, h.feature_mm) for h in made.hints] == [("body", JOIN_FEATURE_MM)]
    assert check_assembly(assembly, mode="full") == []


def test_a_connector_that_reaches_nothing_is_reported_and_built_as_a_stub(kit, occ_model) -> None:
    scenario = kit.scenario(
        ("jaw", "jaw_segment", None, {"region": ("36",), "site": "36"}),
        *kit.unit_on_implant(1, "36", {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
        ("connector-1", "splinting_connector", ("crown-1", "connector_mesial"), {}),
    )
    assembly = place(scenario)
    problems = check_assembly(assembly, mode="fast")
    assert [(p.code, p.block) for p in problems] == [("unsupported_pontic", "connector-1")]
    assert f"on the mesial side of Crown 'crown-1' reaches no unit within {MAX_GAP_MM:g} mm" in problems[0].message
    ctx = assembly.block("connector-1").context
    body = kit.block("splinting_connector").build(ctx).part("body")
    s = ctx.settings
    here = ctx.environment.joint("crown-1", "connector_mesial")
    reach = STUB_MM + kit.block("splinting_connector")._embed(s, here) + EMBED_MM
    assert volume_mm3(body.volumes) == pytest.approx(math.pi * s.height_mm * s.width_mm / 4 * reach, rel=1e-6)


def test_a_unit_too_far_away_is_not_reached(kit) -> None:
    near = splinted(kit)
    moved = near.model_copy(
        update={
            "blocks": [
                b.model_copy(update={"placement": b.placement.model_copy(update={"position_mm": (3.0, 0.0, 0.0)})})
                if b.instance_id == "implant-2"
                else b
                for b in near.blocks
            ]
        }
    )  # 3 mm mesially: the gap grows past MAX_GAP_MM
    codes = [(p.code, p.block) for p in check_assembly(place(moved), mode="fast")]
    assert ("unsupported_pontic", "connector-1") in codes


def test_two_connectors_joining_the_same_contact_areas_are_one_too_many(kit) -> None:
    twin = ("connector-2", "splinting_connector", ("crown-2", "connector_distal"), {})
    problems = check_assembly(place(splinted(kit, extra=(twin,))), mode="fast")
    assert [(p.code, p.block) for p in problems] == [("overlap", "connector-2")]
    assert "'connector-1' and 'connector-2' both join" in problems[0].message


def test_its_section_stays_clear_of_the_crown_margins(kit, occ_model) -> None:
    assembly = place(splinted(kit))
    ctx = assembly.block("connector-1").context
    body = kit.block("splinting_connector").build(ctx).part("body")
    (_, _, low), (_, _, high) = bounding_box(body.volumes)
    margin = assembly.block("crown-1").context.to_frame.origin[2]  # the crown seat, at the margin plane
    assert low == pytest.approx(margin + ROOM_MARGIN_MM, abs=0.02)  # lifted: 4 mm would reach 0.2 mm over it
    assert high - low == pytest.approx(ctx.settings.height_mm, abs=0.05)


def test_a_connector_taller_than_its_units_is_a_design_problem(kit) -> None:
    tall = splinted(kit)
    tall = tall.model_copy(
        update={
            "blocks": [
                b.model_copy(update={"settings": {**b.settings, **kit.raw("splinting_connector", height_mm=6.0)}})
                if b.instance_id == "connector-1"
                else b
                for b in tall.blocks
            ]
        }
    )
    problems = check_assembly(place(tall), mode="fast")
    assert [(p.code, p.block) for p in problems] == [("design_limit", "connector-1")]
    assert "6 mm high, but the units it joins leave room for at most 4.5 mm" in problems[0].message
