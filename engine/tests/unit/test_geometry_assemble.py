"""Joining an assembly into one bonded model (task T069): displacement, cutters, shared faces, supports, names."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import gmsh
import numpy as np
import pytest

from dentalsim.assembly.placement import place
from dentalsim.errors import Refused
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import AssembledModel, assemble
from dentalsim.model.scenario import Placement, Scenario
from dentalsim.model.settings import DesignValue
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import SnapshotLibrary


def scenario_with(library: SnapshotLibrary, **changes: dict[str, Any]) -> Scenario:
    scenario = open_template(builtin_template("single_crown_implant"), library).scenario
    blocks = []
    for block in scenario.blocks:
        design = dict(changes.get(block.instance_id, {}))
        placement = design.pop("placement", block.placement)
        settings = {**block.settings, **{k: DesignValue(value=v) for k, v in design.items()}}
        blocks.append(block.model_copy(update={"settings": settings, "placement": placement}))
    return scenario.model_copy(update={"blocks": blocks})


@pytest.fixture
def model(snapshot_library: SnapshotLibrary) -> Iterator[AssembledModel]:
    with occ_tools.session("assemble-test"):
        yield assemble(place(scenario_with(snapshot_library)))


def surfaces(volumes: list[int]) -> set[int]:
    return {abs(t) for d, t in gmsh.model.getBoundary([(3, v) for v in volumes], oriented=False) if d == 2}


def in_any(model: AssembledModel, point: tuple[float, float, float]) -> list[str]:
    return [p.key for p in model.parts if any(gmsh.model.isInside(3, v, list(point)) for v in p.volumes)]


def test_every_part_is_named_and_tagged(model: AssembledModel) -> None:
    assert [p.key for p in model.parts] == [
        "jaw.cortical",
        "jaw.cancellous",
        "implant-1.body",
        "abutment-1.body",
        "abutment-1.screw",
        "crown-1.body",
    ]
    groups = {gmsh.model.getPhysicalName(3, tag): tag for _, tag in gmsh.model.getPhysicalGroups(3)}
    for part in model.parts:
        assert groups[part.key] == part.physical_tag
        assert sorted(gmsh.model.getEntitiesForPhysicalGroup(3, part.physical_tag).tolist()) == sorted(part.volumes)
    assert {p.set_name for p in model.parts} >= {"P_implant-1_body", "P_jaw_cortical"}


def test_the_implant_takes_its_space_from_the_bone(snapshot_library: SnapshotLibrary) -> None:
    with occ_tools.session("bone-alone"):
        assembly = place(scenario_with(snapshot_library))
        jaw = assembly.root
        assert jaw.context is not None
        bone = jaw.block_type.build(jaw.context)
        bone_alone = occ_tools.volume_mm3([t for p in bone.parts for t in p.volumes])
        implant = assembly.block("implant-1")
        assert implant.context is not None
        implant_alone = occ_tools.volume_mm3(implant.block_type.build(implant.context).part("body").volumes)
    with occ_tools.session("joined"):
        joined = assemble(place(scenario_with(snapshot_library)))
        bone_left = sum(occ_tools.volume_mm3(joined.part(k).volumes) for k in ("jaw.cortical", "jaw.cancellous"))
    # The platform sits at the crest, so the whole implant (with the abutment's connection in it) is in the bone.
    assert bone_alone - bone_left == pytest.approx(implant_alone, rel=1e-6)


def test_touching_parts_share_their_faces(model: AssembledModel) -> None:
    implant = surfaces(model.part("implant-1.body").volumes)
    bone = surfaces(model.part("jaw.cortical").volumes + model.part("jaw.cancellous").volumes)
    abutment = surfaces(model.part("abutment-1.body").volumes)
    crown = surfaces(model.part("crown-1.body").volumes)
    screw = surfaces(model.part("abutment-1.screw").volumes)
    assert implant & bone and implant & abutment and implant & screw and abutment & crown and abutment & screw
    assert not crown & bone
    assert model.part("implant-1.body").surfaces == []  # wholly embedded in bone, abutment and screw


def test_the_screw_access_channel_is_empty_and_the_crown_covers_it(model: AssembledModel) -> None:
    axis = model.anchors[0]
    top = np.asarray(axis.platform_mm)
    up = np.asarray(axis.axis)
    assert in_any(model, tuple(top + 4.0 * up)) == []  # inside the post, above the screw head
    assert in_any(model, tuple(top + 1.0 * up)) == ["abutment-1.screw"]
    assert in_any(model, tuple(top - 5.0 * up)) == ["abutment-1.screw"]  # the shank in the implant's bore
    assert in_any(model, tuple(top + 7.0 * up)) == ["crown-1.body"]  # the crown above the post


def test_the_supports_are_the_cut_faces_of_both_bone_layers(model: AssembledModel) -> None:
    assert [s.name for s in model.supports] == ["mesial_cut", "distal_cut"]
    cortical = set(model.part("jaw.cortical").surfaces)
    cancellous = set(model.part("jaw.cancellous").surfaces)
    for support in model.supports:
        assert len(support.surfaces) == 2
        assert set(support.surfaces) & cortical and set(support.surfaces) & cancellous
    groups = {gmsh.model.getPhysicalName(2, tag) for _, tag in gmsh.model.getPhysicalGroups(2)}
    assert groups == {"support:jaw.mesial_cut", "support:jaw.distal_cut"}


def test_hints_occlusal_points_and_anchors_come_with_the_model(model: AssembledModel) -> None:
    assert {(h.instance_id, h.hint.name) for h in model.hints} == {("implant-1", "threads"), ("abutment-1", "screw")}
    assert all(h.surfaces for h in model.hints)
    assert model.occlusal_point("crown-1", "buccal_cusp").position_mm[1] > 0
    with pytest.raises(Refused, match="has no occlusal point 'apex'"):
        model.occlusal_point("crown-1", "apex")
    assert [a.instance_id for a in model.anchors] == ["implant-1"]
    assert 30 < model.size_mm < 60


def test_an_overlap_left_in_the_scenario_is_refused(snapshot_library: SnapshotLibrary) -> None:
    sunk = scenario_with(snapshot_library, **{"implant-1": {"placement": Placement(position_mm=(0.0, 0.0, -3.0))}})
    with occ_tools.session("overlap"), pytest.raises(Refused, match="overlap by .* check the scenario's validity"):
        assemble(place(sunk))


def test_an_assembly_with_unplaced_blocks_is_refused(snapshot_library: SnapshotLibrary) -> None:
    broken = scenario_with(snapshot_library, **{"implant-1": {"length_mm": 30.0}})
    with occ_tools.session("broken"), pytest.raises(Refused, match="implant-1, abutment-1, crown-1 cannot be placed"):
        assemble(place(broken))
