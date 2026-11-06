"""Round trips and validation rules of the model package (data-model.md, contracts/scenario-format.md)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from dentalsim.model.results import KeyNumber, LockedResult, Reference, ResultSet
from dentalsim.model.run import BLOCKING_FLAGS, Convergence, ForceBalance, LibraryVersionRef, Run, RunFlag
from dentalsim.model.scenario import SCENARIO_FORMAT, LoadCase, Scenario
from dentalsim.model.settings import DesignValue, LibraryValue, SettingValue
from dentalsim.model.validity import Problem, ValidityResult
from dentalsim.testing import FixedClock

CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
SETTING = TypeAdapter[SettingValue](SettingValue)


def _library(entry_id: str = "E1", value: float = 13700, **extra: Any) -> dict[str, Any]:
    return {"kind": "library", "entry_id": entry_id, "entry_version": 1, "choice": "typical", "value": value, **extra}


def _scenario_data() -> dict[str, Any]:
    """The contract's example shape, with neutral block type ids."""
    return {
        "format": "dentalsim.scenario/1",
        "name": "Single crown on a fixture, lower first molar",
        "conventions_version": "1.0.0",
        "detail_level": "standard",
        "blocks": [
            {
                "instance_id": "base",
                "type_id": "test_base",
                "type_version": "1.0.0",
                "attach_to": None,
                "placement": {
                    "position_mm": [0, 0, 0],
                    "axis_angles_deg": {"mesiodistal": 0, "buccolingual": 0},
                    "rotation_deg": 0,
                },
                "settings": {
                    "jaw": {"kind": "design", "value": "mandible"},
                    "cortical_thickness": _library("E1", 2.0),
                    "cortical_E": {**_library("E2", 13700), "choice": "source:S1"},
                },
            },
            {
                "instance_id": "fixture-1",
                "type_id": "test_fixture",
                "type_version": "1.0.0",
                "attach_to": {"instance_id": "base", "attach_point": "site"},
                "settings": {"diameter": {"kind": "design", "value": 4.1}, "length": {"kind": "design", "value": 10}},
            },
            {
                "instance_id": "cap-1",
                "type_id": "test_cap",
                "type_version": "1.0.0",
                "attach_to": {"instance_id": "fixture-1", "attach_point": "platform"},
            },
        ],
        "loads": [
            {
                "id": "bite-1",
                "pattern": "single_bite",
                "force": _library("E3", 200),
                "direction": {"angle_deg": 30, "azimuth_deg": 90},
                "contact_points": [{"block": "cap-1", "point": "buccal_cusp", "share": 1.0}],
            },
            {
                "id": "chew-1",
                "pattern": "chewing_cycle",
                "force": _library("E3", 200),
                "cycles": _library("E4", 300000),
                "instants": [
                    {
                        "id": "t1",
                        "direction": {"angle_deg": 0, "azimuth_deg": 0},
                        "force_share": 0.5,
                        "contact_points": [{"block": "cap-1", "point": "central_fossa", "share": 1.0}],
                    },
                    {
                        "id": "t2",
                        "direction": {"angle_deg": 20, "azimuth_deg": 90},
                        "force_share": 1.0,
                        "contact_points": [
                            {"block": "cap-1", "point": "buccal_cusp", "share": 0.6},
                            {"block": "cap-1", "position_mm": [1.0, 2.0, 8.5], "share": 0.4},
                        ],
                    },
                ],
            },
        ],
    }


def _scenario(data: dict[str, Any] | None = None) -> Scenario:
    return Scenario.model_validate(data if data is not None else _scenario_data())


def _invalid(data: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Scenario.model_validate(data)


# Setting values


def test_setting_values_are_told_apart_by_kind() -> None:
    assert isinstance(SETTING.validate_python({"kind": "design", "value": 4.1}), DesignValue)
    library = SETTING.validate_python(_library())
    assert isinstance(library, LibraryValue)
    assert library.outside_range is False
    with pytest.raises(ValidationError):
        SETTING.validate_python({"kind": "other", "value": 1})


@pytest.mark.parametrize("choice", ["typical", "custom", "source:01JABCDEF"])
def test_library_choice_forms(choice: str) -> None:
    value = LibraryValue.model_validate(_library(choice=choice))
    assert value.source_id == ("01JABCDEF" if choice.startswith("source:") else None)


@pytest.mark.parametrize("choice", ["", "best", "source:", "source: S1", "Typical"])
def test_unknown_library_choices_are_refused(choice: str) -> None:
    with pytest.raises(ValidationError, match="choice"):
        LibraryValue.model_validate(_library(choice=choice))


@pytest.mark.parametrize("field", ["entry_id", "entry_version"])
def test_a_library_value_needs_its_entry_version(field: str) -> None:
    data = _library()
    del data[field]
    with pytest.raises(ValidationError, match=field):
        LibraryValue.model_validate(data)
    with pytest.raises(ValidationError):
        LibraryValue.model_validate(_library() | {"entry_version": 0})


@pytest.mark.parametrize("value", ["v_thread", 4, 4.1, True, [1, 2.5], ["a", "b"]])
def test_design_values_take_scalars_and_lists(value: Any) -> None:
    assert DesignValue(value=value).value == value


@pytest.mark.parametrize("value", [None, {"a": 1}, [1, "a"]])
def test_design_values_refuse_other_shapes(value: Any) -> None:
    with pytest.raises(ValidationError):
        DesignValue.model_validate({"kind": "design", "value": value})


def test_design_values_keep_their_type() -> None:
    assert type(DesignValue(value=10).value) is int
    assert type(DesignValue(value="10").value) is str


# Scenario


def test_scenario_round_trips_through_json() -> None:
    scenario = _scenario()
    assert scenario.format == SCENARIO_FORMAT
    again = Scenario.model_validate_json(scenario.model_dump_json())
    assert again == scenario
    assert again.model_dump(mode="json") == scenario.model_dump(mode="json")


def test_scenario_defaults() -> None:
    data = _scenario_data()
    del data["format"], data["detail_level"], data["loads"]
    for block in data["blocks"]:
        block.pop("placement", None)
        block.pop("attach_to", None)
        block.pop("settings", None)
    scenario = _scenario(data)
    assert scenario.format == "dentalsim.scenario/1"
    assert scenario.detail_level == "standard"
    assert scenario.loads == []
    assert scenario.blocks[0].placement.position_mm == (0.0, 0.0, 0.0)
    assert scenario.brand_comparison is None


def test_models_are_frozen() -> None:
    scenario = _scenario()
    with pytest.raises(ValidationError):
        scenario.name = "changed"  # type: ignore[misc]


def test_block_lookup_and_library_values_in_order() -> None:
    scenario = _scenario()
    assert scenario.block("fixture-1").type_id == "test_fixture"
    with pytest.raises(KeyError):
        scenario.block("missing")
    assert [(v.entry_id, v.value) for v in scenario.library_values()] == [
        ("E1", 2.0),
        ("E2", 13700.0),
        ("E3", 200.0),
        ("E3", 200.0),
        ("E4", 300000.0),
    ]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda d: d.update(format="dentalsim.scenario/2"), "format"),
        (lambda d: d.update(name=""), "name"),
        (lambda d: d.update(detail_level="ultra"), "detail_level"),
        (lambda d: d["blocks"][1].update(instance_id="base"), "unique"),
        (lambda d: d["blocks"][1].update(instance_id="bad id"), "instance_id"),
        (lambda d: d["blocks"][1].update(type_version="1.0"), "type_version"),
        (lambda d: d["blocks"][1]["attach_to"].update(instance_id="ghost"), "unknown block 'ghost'"),
        (lambda d: d["blocks"][0].update(attach_to={"instance_id": "cap-1", "attach_point": "x"}), "loop"),
        (lambda d: d["loads"][1].update(id="bite-1"), "load case ids"),
        (lambda d: d["loads"][0]["contact_points"][0].update(block="ghost"), "unknown block 'ghost'"),
    ],
)
def test_scenario_rules(change: Any, match: str) -> None:
    data = copy.deepcopy(_scenario_data())
    change(data)
    _invalid(data, match)


@pytest.mark.parametrize(
    "direction",
    [{"angle_deg": -1, "azimuth_deg": 0}, {"angle_deg": 91, "azimuth_deg": 0}, {"angle_deg": 0, "azimuth_deg": 360}],
)
def test_direction_limits(direction: dict[str, float]) -> None:
    data = _scenario_data()
    data["loads"][0]["direction"] = direction
    _invalid(data, "direction")


def test_a_contact_point_has_a_named_point_or_a_position() -> None:
    data = _scenario_data()
    data["loads"][0]["contact_points"][0]["position_mm"] = [0, 0, 1]
    _invalid(data, "not both")
    del data["loads"][0]["contact_points"][0]["position_mm"], data["loads"][0]["contact_points"][0]["point"]
    _invalid(data, "not both")


def test_contact_shares_must_add_up_to_one() -> None:
    data = _scenario_data()
    data["loads"][0]["contact_points"][0]["share"] = 0.5
    _invalid(data, "add up to 1")
    data = _scenario_data()
    data["loads"][1]["instants"][1]["contact_points"][1]["share"] = 0.3
    _invalid(data, "add up to 1")


def _load(**changes: Any) -> dict[str, Any]:
    return {
        "id": "bite-1",
        "pattern": "single_bite",
        "force": _library("E3", 200),
        "direction": {"angle_deg": 30, "azimuth_deg": 90},
        "contact_points": [{"block": "cap-1", "point": "buccal_cusp", "share": 1.0}],
        **changes,
    }


@pytest.mark.parametrize(
    ("load", "match"),
    [
        (_load(direction=None), "needs a direction"),
        (_load(contact_points=[]), "at least one contact point"),
        (_load(cycles=_library("E4", 1000)), "only a chewing cycle"),
        (_load(pattern="clenching", cycles=_library("E4", 1000)), "only a chewing cycle"),
        (_load(force={"kind": "design", "value": 200}), "force"),
    ],
)
def test_single_bite_and_clenching_rules(load: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        LoadCase.model_validate(load)


def test_clenching_has_one_direction() -> None:
    assert LoadCase.model_validate(_load(pattern="clenching")).pattern == "clenching"


def _instant(instant_id: str) -> dict[str, Any]:
    return {
        "id": instant_id,
        "direction": {"angle_deg": 0, "azimuth_deg": 0},
        "force_share": 1.0,
        "contact_points": [{"block": "cap-1", "point": "central_fossa", "share": 1.0}],
    }


@pytest.mark.parametrize(
    ("load", "match"),
    [
        ({"instants": [], "cycles": _library("E4", 1000)}, "at least one instant"),
        ({"instants": [_instant("t1")]}, "cycle count"),
        ({"instants": [_instant("t1"), _instant("t1")], "cycles": _library("E4", 1000)}, "unique ids"),
        (
            {
                "instants": [_instant("t1")],
                "cycles": _library("E4", 1000),
                "direction": {"angle_deg": 0, "azimuth_deg": 0},
            },
            "per instant",
        ),
    ],
)
def test_chewing_cycle_rules(load: dict[str, Any], match: str) -> None:
    data = {"id": "chew-1", "pattern": "chewing_cycle", "force": _library("E3", 200), **load}
    with pytest.raises(ValidationError, match=match):
        LoadCase.model_validate(data)


def test_all_contact_points_of_a_chewing_cycle() -> None:
    chew = _scenario().loads[1]
    assert [p.point for p in chew.all_contact_points()] == ["central_fossa", "buccal_cusp", None]


def test_brand_comparison_needs_two_products_and_disclosures() -> None:
    data = _scenario_data()
    data["brand_comparison"] = {"products": [{"name": "A", "geometry_source": "catalogue"}], "disclosures": "none"}
    _invalid(data, "products")
    data["brand_comparison"]["products"].append({"name": "B", "geometry_source": "catalogue"})
    data["brand_comparison"]["disclosures"] = ""
    _invalid(data, "disclosures")
    data["brand_comparison"]["disclosures"] = "No funding from either maker."
    assert _scenario(data).brand_comparison is not None


# Validity


def test_validity_is_ok_when_only_warnings_remain(fixed_time: FixedClock) -> None:
    warning = Problem(code="design_limit", message="Close to the limit.", severity="warning")
    error = Problem(
        code="in_nerve_canal", message="Apex 0.6 mm in the canal.", block="fixture-1", location_mm=(1, 2, 3)
    )
    assert ValidityResult.from_problems([warning]).ok
    result = ValidityResult.from_problems([warning, error], scenario_revision=4)
    assert not result.ok
    assert result.errors == [error]
    assert result.checked_at == fixed_time.now
    assert result.scenario_revision == 4
    assert ValidityResult.model_validate_json(result.model_dump_json()) == result


def test_unknown_problem_codes_are_refused() -> None:
    with pytest.raises(ValidationError):
        Problem.model_validate({"code": "too_pretty", "message": "x"})


# Results and runs


def _key_number() -> KeyNumber:
    return KeyNumber(
        id="crestal_strain",
        label="Crestal strain",
        load_case="bite-1",
        block="fixture-1",
        region="crestal_ring",
        side="buccal",
        statistic="mean",
        value=1850.0,
        unit="microstrain",
        reference=Reference(
            kind="frost_window",
            parameter_key="frost.window.mild_overload",
            entry_id="E9",
            entry_version=1,
            value=3000.0,
            units="microstrain",
        ),
        percent_of_reference=61.7,
        frost_category="adapted",
    )


def test_result_set_round_trips() -> None:
    results = ResultSet(
        input_hash="a" * 64,
        fields=[],
        combined_cases=["bite-1"],
        key_numbers=[_key_number()],
        visual_assets=[],
        locked_results=[
            LockedResult(
                result_type="screw_stress",
                block="fixture-1",
                missing_capability="screw_preload",
                message="not reported: needs screw preload",
            )
        ],
    )
    assert ResultSet.model_validate_json(results.model_dump_json()) == results


def test_key_numbers_report_only_means_and_95th_percentiles() -> None:
    data = _key_number().model_dump() | {"statistic": "max"}
    with pytest.raises(ValidationError, match="statistic"):
        KeyNumber.model_validate(data)


def _run(**changes: Any) -> Run:
    data: dict[str, Any] = {
        "id": "01J0000000000000000000RUN1",
        "scenario_id": None,
        "scenario_revision": None,
        "snapshot": _scenario_data(),
        "detail_level": "standard",
        "input_hash": "a" * 64,
        "library_versions": [{"entry_id": "E1", "parameter_key": "bone.cortical.t", "version": 1, "content_hash": "h"}],
        "engine_version": "0.1.0+gabc1234",
        "engine_source_hash": "b" * 64,
        "conventions_version": "1.0.0",
        "solver_build_id": "ccx-2.23-pardiso",
        "gmsh_version": "4.15.2",
        "created_at": CREATED,
    }
    data.update(changes)
    return Run.model_validate(data)


def test_run_round_trips_with_flags_and_convergence() -> None:
    run = _run(
        status="succeeded",
        result_ref="a" * 64,
        flags={"dev_build", "out_of_date"},
        convergence=Convergence(
            force_balance=[
                ForceBalance(load_case="u1", applied_n=(1, 0, 0), reaction_n=(-1, 0, 0), relative_error=1e-9)
            ]
        ),
    )
    again = Run.model_validate_json(run.model_dump_json())
    assert again == run
    assert again.flags == {"dev_build", "out_of_date"}


def test_only_a_succeeded_run_has_results() -> None:
    with pytest.raises(ValidationError, match="only a succeeded run"):
        _run(status="failed", failure_reason="Solver diverged.", result_ref="a" * 64)


def test_a_failed_run_records_its_reason() -> None:
    with pytest.raises(ValidationError, match="reason"):
        _run(status="failed")
    assert _run(status="failed", failure_reason="Out of memory.").failure_reason == "Out of memory."


def test_publishable_needs_success_and_no_blocking_flag() -> None:
    assert not _run().is_publishable()
    assert _run(status="succeeded", result_ref="a" * 64).is_publishable()
    for flag in BLOCKING_FLAGS:
        assert not _run(status="succeeded", result_ref="a" * 64, flags={flag}).is_publishable()


def test_blocking_flags_are_every_run_flag() -> None:
    """data-model.md "Publishable": every one of the five flags blocks publication."""
    expected = {"out_of_date", "uses_rejected_entry", "outside_published_range", "dev_build", "needs_reverification"}
    assert expected == set(get_args(RunFlag)) == BLOCKING_FLAGS
    with pytest.raises(ValidationError):
        _run(flags={"looks_fine"})


def test_library_version_refs_start_at_version_one() -> None:
    with pytest.raises(ValidationError):
        LibraryVersionRef(entry_id="E1", parameter_key="k", version=0, content_hash="h")
