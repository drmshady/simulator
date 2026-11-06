"""Cantilever extension block (task T102): its joined end at the edge of its site, its length toward the free end,
the length limit, and the rule that its joined end is held by a supported unit."""

from __future__ import annotations

import dataclasses

import pytest

from dentalsim.assembly.placement import place
from dentalsim.assembly.validity import check_assembly
from dentalsim.blocks.cantilever_extension import MAX_LENGTH_MM
from dentalsim.blocks.jaw_segment import TOOTH_WIDTHS_MM
from dentalsim.geometry.occ import bounding_box
from dentalsim.geometry.transform import IDENTITY

from .test_crown import surface_distance


def cantilevered(kit, *, without: tuple[str, ...] = (), **cantilever):
    """Crowns on implants at 35 and 36 and a cantilever extension at 37, as the cantilever-bridge template."""
    blocks = [
        ("jaw", "jaw_segment", None, {"region": ("35", "36", "37"), "site": "36", "neighbours": ("34",)}),
        *kit.unit_on_implant(1, "36", {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
        *kit.unit_on_implant(2, "35"),
        ("cantilever-1", "cantilever_extension", ("jaw", "site:37"), cantilever),
        ("connector-1", "splinting_connector", ("crown-1", "connector_mesial"), {}),
        ("connector-2", "splinting_connector", ("cantilever-1", "connector_mesial"), {}),
    ]
    return kit.scenario(*(b for b in blocks if b[0] not in without))


@pytest.mark.parametrize("free_end", ["distal", "mesial"])
def test_its_joined_end_is_at_the_edge_of_its_site(kit, occ_model, free_end: str) -> None:
    placed = place(cantilevered(kit, free_end=free_end, length_mm=12.0)).block("cantilever-1").context
    ctx = dataclasses.replace(placed, to_frame=IDENTITY)  # measured in its own frame
    block = kit.block("cantilever_extension")
    body = block.build(ctx).part("body")
    (x0, _, z0), (x1, _, z1) = bounding_box(body.volumes)
    half_site = TOOTH_WIDTHS_MM["mandible"][6] / 2  # tooth 37
    expected = (half_site - 12.0, half_site) if free_end == "distal" else (-half_site, 12.0 - half_site)
    assert (x0, x1) == pytest.approx(expected, abs=0.15)
    assert (z0, z1) == pytest.approx((2.0, 2.0 + ctx.settings.height_mm), abs=2e-3)
    (socket,) = block.sockets(ctx)
    assert socket.name == ("connector_mesial" if free_end == "distal" else "connector_distal")
    assert surface_distance(body.volumes, socket.frame.origin) < 1e-6
    assert body.component == "cantilever" and body.displaces == frozenset({"connector"})


def test_a_cantilever_joined_to_a_crown_is_held(kit) -> None:
    assert check_assembly(place(cantilevered(kit)), mode="fast") == []


def test_a_cantilever_whose_crown_is_deleted_is_reported(kit) -> None:
    problems = check_assembly(
        place(cantilevered(kit, without=("implant-1", "abutment-1", "crown-1", "connector-1"))), mode="fast"
    )
    found = {p.block: p for p in problems}
    assert set(found) == {"cantilever-1", "connector-2"}
    assert found["cantilever-1"].code == "unsupported_pontic"
    assert "not joined to a supported unit at its mesial end" in found["cantilever-1"].message


def test_the_cantilever_length_is_a_design_limit(kit) -> None:
    reading = kit.block("cantilever_extension").read_settings(
        kit.raw("cantilever_extension", length_mm=MAX_LENGTH_MM + 1), instance_id="cantilever-1"
    )
    assert not reading.ok
    assert reading.problems[0].code == "design_limit" and "Cantilever length" in reading.problems[0].message
