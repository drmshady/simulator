"""The generic settings diff between two scenarios (task T115; FR-027, FR-032)."""

from __future__ import annotations

from dentalsim.model.diff import diff_scenarios
from dentalsim.model.scenario import BlockInstance, Direction, LoadCase, Placement, Scenario
from dentalsim.model.settings import DesignValue, LibraryValue


def force(value: float = 200.0, version: int = 1) -> LibraryValue:
    return LibraryValue(entry_id="E-FORCE", entry_version=version, choice="typical", value=value)


def scenario(*, length: float = 10.0, name: str = "Ten", angle: float = 0.0) -> Scenario:
    return Scenario(
        name=name,
        conventions_version="1.1.0",
        blocks=[
            BlockInstance(instance_id="jaw", type_id="jaw_segment", type_version="1.0.0"),
            BlockInstance(
                instance_id="post-1",
                type_id="post",
                type_version="1.0.0",
                attach_to={"instance_id": "jaw", "attach_point": "site:36"},  # type: ignore[arg-type]
                settings={
                    "length_mm": DesignValue(value=length),
                    "material": DesignValue(value="titanium_g4"),
                    "modulus": LibraryValue(entry_id="E-TI", entry_version=1, choice="typical", value=110000.0),
                },
            ),
        ],
        loads=[
            LoadCase(
                id="axial",
                pattern="single_bite",
                force=force(),
                direction=Direction(angle_deg=angle, azimuth_deg=0),
                contact_points=[{"block": "post-1", "point": "centre", "share": 1.0}],  # type: ignore[list-item]
            )
        ],
    )


def test_identical_scenarios_have_no_differences_and_the_name_is_not_a_setting() -> None:
    assert diff_scenarios(scenario(), scenario(name="Ten (copy)")).differences == []


def test_one_changed_design_setting_is_listed_once_with_both_values() -> None:
    diff = diff_scenarios(scenario(length=10.0), scenario(length=8.0))
    assert [(d.path, d.change, d.before, d.after, d.block, d.group) for d in diff.differences] == [
        ("post-1.length_mm", "changed", 10.0, 8.0, "post-1", "setting")
    ]


def test_library_values_compare_version_choice_and_value() -> None:
    first = scenario()
    second_load = first.loads[0].model_copy(update={"force": force(value=200.0, version=2)})
    second = first.model_copy(update={"loads": [second_load]})
    (difference,) = diff_scenarios(first, second).differences
    assert difference.path == "load:axial.force"
    assert difference.before["entry_version"] == 1 and difference.after["entry_version"] == 2
    assert difference.before["value"] == difference.after["value"] == 200.0


def test_placement_direction_and_detail_level_are_listed_by_path() -> None:
    first = scenario()
    moved = first.blocks[1].model_copy(update={"placement": Placement(position_mm=(0.5, 0.0, 0.0))})
    second = first.model_copy(update={"blocks": [first.blocks[0], moved], "detail_level": "fine"})
    second = second.model_copy(update={"loads": scenario(angle=30.0).loads})
    paths = diff_scenarios(first, second).paths
    assert paths == ["detail_level", "post-1.placement.position_mm", "load:axial.direction.angle_deg"]


def test_added_and_removed_blocks_and_loads_are_one_difference_each() -> None:
    first = scenario()
    extra = BlockInstance(
        instance_id="cap-1",
        type_id="cap",
        type_version="1.0.0",
        attach_to={"instance_id": "post-1", "attach_point": "top"},  # type: ignore[arg-type]
        settings={"height_mm": DesignValue(value=2.0)},
    )
    second = first.model_copy(update={"blocks": [*first.blocks, extra], "loads": []})
    diff = diff_scenarios(first, second)
    assert [(d.path, d.change) for d in diff.differences] == [("cap-1", "added"), ("load:axial", "removed")]
    assert diff.for_block("cap-1")[0].after["settings"]["height_mm"]["value"] == 2.0
    reverse = diff_scenarios(second, first)
    assert [(d.path, d.change) for d in reverse.differences] == [("cap-1", "removed"), ("load:axial", "added")]


def test_a_setting_present_in_only_one_block_version_is_added_or_removed() -> None:
    first = scenario()
    block = first.blocks[1]
    settings = {k: v for k, v in block.settings.items() if k != "material"}
    second = first.model_copy(update={"blocks": [first.blocks[0], block.model_copy(update={"settings": settings})]})
    (difference,) = diff_scenarios(first, second).differences
    assert (difference.path, difference.change, difference.before) == ("post-1.material", "removed", "titanium_g4")
