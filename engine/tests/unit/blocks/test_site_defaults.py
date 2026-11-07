"""Site-sized defaults: a crown or pontic newly added on a jaw site starts at its tooth's mean width less 0.4 mm,
not at the fixed (molar) default; a block already saved, or a width already set, is left alone."""

from __future__ import annotations

import pytest

from dentalsim.assembly.placement import place
from dentalsim.assembly.site_defaults import site_tooth_width, with_site_defaults
from dentalsim.assembly.validity import check_assembly


def two_crowns(kit, second: dict):
    return kit.scenario(
        ("jaw", "jaw_segment", None, {"region": ("35", "36", "37"), "site": "36", "neighbours": ("34",)}),
        *kit.unit_on_implant(1, "36", {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
        *kit.unit_on_implant(2, "35", second),
        ("pontic-1", "pontic", ("jaw", "site:37"), {}),
        ("connector-1", "splinting_connector", ("crown-2", "connector_distal"), {}),
    )


def width_of(scenario, instance_id: str):
    setting = {b.instance_id: b for b in scenario.blocks}[instance_id].settings.get("mesiodistal_mm")
    return None if setting is None else setting.value


def test_a_new_crown_and_pontic_are_sized_to_their_sites(kit) -> None:
    scenario = two_crowns(kit, {"buccolingual_mm": 8.0})
    assert site_tooth_width(place(scenario), "crown-2") == 7.0  # through abutment and implant to site 35
    filled = with_site_defaults(scenario, {"crown-2", "pontic-1"})
    assert width_of(filled, "crown-2") == pytest.approx(6.6)
    assert width_of(filled, "pontic-1") == pytest.approx(10.1)  # tooth 37
    assert width_of(filled, "crown-1") == 10.5


def test_only_new_blocks_and_unset_widths_are_filled(kit) -> None:
    scenario = two_crowns(kit, {"buccolingual_mm": 8.0})
    assert width_of(with_site_defaults(scenario, set()), "crown-2") is None
    assert width_of(with_site_defaults(scenario, {"implant-2"}), "crown-2") is None
    chosen = two_crowns(kit, {"mesiodistal_mm": 7.2, "buccolingual_mm": 8.0})
    assert width_of(with_site_defaults(chosen, {"crown-2"}), "crown-2") == 7.2


def test_a_connector_between_overlapping_crowns_says_they_overlap(kit) -> None:
    # A crown left at the molar default on a premolar site runs into its neighbour: the connector has no gap.
    problems = check_assembly(place(two_crowns(kit, {"buccolingual_mm": 8.0})), mode="fast")
    (connector,) = [p for p in problems if p.block == "connector-1"]
    assert connector.code == "unsupported_pontic"
    assert "the two units overlap" in connector.message and "Crown 'crown-1'" in connector.message
