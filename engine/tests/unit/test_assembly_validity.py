"""Assembly placement and validity (tasks T066–T068; FR-011, Story 1 Acceptance 3).

The scenarios come from the built-in single-crown template, opened with the approved library snapshot's values.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from dentalsim.assembly.placement import axis_alignment, place, placement_transform
from dentalsim.assembly.validity import check_assembly, check_validity
from dentalsim.errors import Refused
from dentalsim.model.scenario import AttachTo, AxisAngles, BlockInstance, Placement, Scenario
from dentalsim.model.settings import DesignValue
from dentalsim.templates.loader import builtin_template, builtin_templates, open_template, template_from_scenario
from dentalsim.testing import SnapshotLibrary


def template_scenario(library: SnapshotLibrary, **jaw: Any) -> Scenario:
    settings = {k: DesignValue(value=list(v) if isinstance(v, tuple) else v) for k, v in jaw.items()}
    return open_template(builtin_template("single_crown_implant"), library, jaw_settings=settings).scenario


def changed(scenario: Scenario, instance_id: str, *, placement: Placement | None = None, **design: Any) -> Scenario:
    blocks = []
    for block in scenario.blocks:
        if block.instance_id == instance_id:
            settings = {**block.settings, **{k: DesignValue(value=v) for k, v in design.items()}}
            block = block.model_copy(update={"settings": settings, "placement": placement or block.placement})
        blocks.append(block)
    return scenario.model_copy(update={"blocks": blocks})


def codes(problems: list[Any]) -> set[str]:
    return {p.code for p in problems}


# --- the template ------------------------------------------------------------------------------------------------


def test_the_template_as_delivered_is_valid(snapshot_library: SnapshotLibrary) -> None:
    template = builtin_template("single_crown_implant")
    opened = open_template(template, snapshot_library)
    assert opened.misfits == []
    assert [b.instance_id for b in opened.scenario.blocks] == ["jaw", "implant-1", "abutment-1", "crown-1"]
    assert {r.parameter_key for r in opened.library} >= {"bone.cortical.thickness.D2", "load.bite_force.molar"}
    assert all(r.choice == "typical" and not r.outside_range for r in opened.library)
    result = check_validity(opened.scenario, scenario_revision=3)
    assert result.ok, [p.message for p in result.problems]
    assert result.problems == [] and result.scenario_revision == 3


def test_every_builtin_template_loads() -> None:
    templates = builtin_templates()
    assert {t.id for t in templates} == {
        "single_crown_implant",
        "splinted_crowns",
        "bridge_implant",
        "cantilever_bridge",
        "full_arch_fixed",
    }
    assert all(t.approval is not None and t.approval.status in ("proposed", "approved") for t in templates)


@pytest.mark.parametrize(
    ("jaw", "message"),
    [
        ({"region": ("35", "36", "37"), "neighbours": ("34",)}, "spanning 1 tooth, but the region spans 3"),
        ({"region": ("34",), "site": "34", "neighbours": ("33", "35")}, "is for a first or second molar site"),
        ({"region": ("34",), "site": "34", "neighbours": ("33", "35")}, "make it at most 7.5 mm wide"),
        ({"nerve_canal_depth_mm": 11.0}, "choose an implant of at most 9 mm"),
    ],
)
def test_a_template_on_an_unsuitable_segment_is_a_misfit(
    snapshot_library: SnapshotLibrary, jaw: dict[str, Any], message: str
) -> None:
    settings = {k: DesignValue(value=list(v) if isinstance(v, tuple) else v) for k, v in jaw.items()}
    opened = open_template(builtin_template("single_crown_implant"), snapshot_library, jaw_settings=settings)
    assert opened.misfits and all(p.code == "template_misfit" and p.block == "jaw" for p in opened.misfits)
    assert any(message in p.message for p in opened.misfits), [p.message for p in opened.misfits]
    result = check_validity(opened.scenario, mode="fast", extra=opened.misfits)
    assert not result.ok and "template_misfit" in codes(result.problems)


def test_an_owner_template_round_trips_a_scenario(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    template = template_from_scenario(
        scenario,
        template_id="my-crown",
        name="My crown",
        branch="implant",
        parameter_key_of=snapshot_library.parameter_key_of,
    )
    assert template.origin == "owner" and template.approval is None
    reopened = open_template(template, snapshot_library).scenario
    assert reopened.blocks == scenario.blocks and reopened.loads == scenario.loads


# --- placement ---------------------------------------------------------------------------------------------------


def test_placement_offsets_follow_the_socket_axes() -> None:
    offset = placement_transform(
        Placement(position_mm=(1.0, 2.0, 3.0), axis_angles_deg=AxisAngles(mesiodistal=10.0, buccolingual=20.0))
    )
    assert offset.origin == pytest.approx((1.0, 2.0, 3.0))
    axis = offset.direction((0.0, 0.0, 1.0))
    assert axis[0] > 0 and axis[1] > 0  # tilted toward mesial (+x) and buccal (+y)
    spun = placement_transform(Placement(rotation_deg=90.0))
    assert spun.direction((1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0))
    align = axis_alignment((math.sin(0.3), 0.0, math.cos(0.3)))
    assert align.direction((math.sin(0.3), 0.0, math.cos(0.3))) == pytest.approx((0.0, 0.0, 1.0))


def test_a_tilted_reference_implant_sets_the_frame(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(
        template_scenario(snapshot_library),
        "implant-1",
        placement=Placement(axis_angles_deg=AxisAngles(mesiodistal=12.0)),
    )
    assembly = place(scenario)
    assert assembly.complete and assembly.axis_reference == "implant-1"
    implant = assembly.block("implant-1").context
    assert implant is not None and implant.to_frame.direction((0, 0, 1)) == pytest.approx((0.0, 0.0, 1.0))
    jaw = assembly.root.context
    assert jaw is not None
    up = jaw.to_frame.direction((0, 0, 1))
    assert up[0] == pytest.approx(-math.sin(math.radians(12.0)))  # the jaw now leans distally
    assert jaw.to_frame.origin == pytest.approx((0.0, 0.0, 0.0))
    crown = assembly.block("crown-1").context
    assert crown is not None and crown.parent is not None and crown.parent.instance_id == "abutment-1"


def test_the_root_cannot_be_moved(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(template_scenario(snapshot_library), "jaw", placement=Placement(position_mm=(0.0, 0.0, 1.0)))
    result = check_validity(scenario, mode="fast")
    assert not result.ok
    assert any(p.code == "design_limit" and "root of the assembly" in p.message for p in result.problems)


def test_a_block_on_a_block_with_bad_settings_is_not_placed(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(template_scenario(snapshot_library), "implant-1", length_mm=30.0)
    assembly = place(scenario)
    assert not assembly.complete
    assert [b.instance_id for b in assembly.blocks if b.context is None] == ["implant-1", "abutment-1", "crown-1"]
    assert [p.block for p in assembly.problems] == ["implant-1"]


def test_attachments_must_match_the_sockets(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    crown = scenario.block("crown-1")
    wrong = crown.model_copy(update={"attach_to": AttachTo(instance_id="jaw", attach_point="site")})
    blocks = [wrong if b.instance_id == "crown-1" else b for b in scenario.blocks]
    with pytest.raises(Refused, match="attaches to 'crown_seat', not to 'site'"):
        place(scenario.model_copy(update={"blocks": blocks}))
    loose = crown.model_copy(update={"attach_to": None})
    blocks = [loose if b.instance_id == "crown-1" else b for b in scenario.blocks]
    with pytest.raises(Refused, match="exactly one block that attaches to nothing"):
        place(scenario.model_copy(update={"blocks": blocks}))


def test_an_unknown_block_version_is_refused(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    blocks = [
        b.model_copy(update={"type_version": "9.0.0"}) if b.instance_id == "crown-1" else b for b in scenario.blocks
    ]
    with pytest.raises(Refused, match="has no version 9.0.0; upgrade the block"):
        place(scenario.model_copy(update={"blocks": blocks}))


# --- validity (Acceptance 3) -------------------------------------------------------------------------------------


def test_an_implant_moved_into_the_nerve_canal_is_named_with_its_location(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(
        template_scenario(snapshot_library), "implant-1", placement=Placement(position_mm=(0.0, 0.0, -6.0))
    )
    for mode in ("fast", "full"):
        result = check_validity(scenario, mode=mode)
        canal = [p for p in result.problems if p.code == "in_nerve_canal"]
        assert len(canal) == 1 and canal[0].block == "implant-1", result.problems
        assert canal[0].location_mm is not None
        x, y, z = canal[0].location_mm
        assert abs(x) < 0.5 and abs(y) < 1.6 and -16.6 < z < -15.0  # in the canal, 15 mm below the crest


def test_a_second_block_on_an_occupied_socket_is_named_at_once(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    implant = next(b for b in scenario.blocks if b.instance_id == "implant-1")
    scenario = scenario.model_copy(
        update={"blocks": [*scenario.blocks, implant.model_copy(update={"instance_id": "implant-2"})]}
    )
    for mode in ("fast", "full"):
        result = check_validity(scenario, mode=mode)
        assert [(p.code, p.block) for p in result.problems] == [("overlap", "implant-2")], result.problems
        assert "attached to site 36 of Jaw segment 'jaw', which already holds 'implant-1'" in result.problems[0].message
    assert place(scenario).block("implant-2").context is None  # left unplaced: not built, not overlapping twice


def test_overlapping_parts_are_an_overlap(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(
        template_scenario(snapshot_library), "implant-1", placement=Placement(position_mm=(0.0, 0.0, -3.0))
    )
    fast = check_validity(scenario, mode="fast")
    assert "overlap" not in codes(fast.problems)  # the fast mode leaves overlaps to the full check
    full = check_validity(scenario, mode="full")
    overlaps = [p for p in full.problems if p.code == "overlap"]
    assert overlaps and all("Crown 'crown-1'" in p.message and "Jaw segment 'jaw'" in p.message for p in overlaps)
    assert all(p.location_mm is not None and p.location_mm[2] < 0 for p in overlaps)
    assert "in_nerve_canal" not in codes(full.problems)


def test_an_implant_longer_than_the_bone_exceeds_it(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(
        snapshot_library,
        jaw="maxilla",
        region=("26",),
        site="26",
        neighbours=("25", "27"),
        ridge_height_mm=8.0,
        basal_width_mm=9.0,
        density_class="D3",
    )
    scenario = changed(scenario, "crown-1", mesiodistal_mm=10.0)
    result = check_validity(scenario, mode="fast")
    exceeds = [p for p in result.problems if p.code == "exceeds_bone"]
    assert len(exceeds) == 1 and exceeds[0].block == "implant-1"
    assert exceeds[0].location_mm is not None and exceeds[0].location_mm[2] < -7.5


def test_a_root_intrusion_is_reported_for_every_block_that_enters(snapshot_library: SnapshotLibrary) -> None:
    scenario = changed(
        template_scenario(snapshot_library), "implant-1", placement=Placement(position_mm=(4.5, 0.0, -1.0))
    )
    roots = [p for p in check_assembly(place(scenario), mode="full") if p.code == "in_root"]
    assert {p.block for p in roots} == {"implant-1", "abutment-1"}
    assert all("tooth 35" in p.message for p in roots)
    assert all(np.isfinite(p.location_mm).all() for p in roots if p.location_mm is not None)


def test_the_scenario_block_order_does_not_matter(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    shuffled = scenario.model_copy(update={"blocks": list(reversed(scenario.blocks))})
    assembly = place(shuffled)
    assert [b.instance_id for b in assembly.blocks] == ["jaw", "implant-1", "abutment-1", "crown-1"]
    assert assembly.complete


def test_a_jaw_alone_is_placed_with_the_identity_frame(snapshot_library: SnapshotLibrary) -> None:
    scenario = template_scenario(snapshot_library)
    jaw_only = Scenario(
        name="jaw", conventions_version=scenario.conventions_version, blocks=[scenario.block("jaw")], loads=[]
    )
    assembly = place(jaw_only)
    assert assembly.axis_reference is None and assembly.frame.is_identity()
    assert check_assembly(assembly, mode="full") == []


def test_block_instances_need_their_library_values() -> None:
    bare = Scenario(
        name="bare",
        conventions_version="1.1.0",
        blocks=[BlockInstance(instance_id="jaw", type_id="jaw_segment", type_version="1.0.0")],
    )
    with pytest.raises(Refused, match="needs a value from the literature library"):
        place(bare)
