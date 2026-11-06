"""The block framework and registry (task T060; contracts/scenario-format.md "Block type contract")."""

from __future__ import annotations

import itertools
import json
import string
from pathlib import Path

import pytest
import yaml

from dentalsim.blocks.base import SETTING_GROUPS, resolve_parameter_key
from dentalsim.blocks.registry import UnknownBlockType, all_block_types, catalogue
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import key_matches
from dentalsim.errors import DentalsimError, Refused
from dentalsim.model.scenario import AttachTo, BlockInstance, Scenario
from dentalsim.model.settings import DesignValue, LibraryValue
from dentalsim.persistence import CapabilityStatus
from dentalsim.physics.gate import check_gate

REPO_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT = REPO_ROOT / "content" / "library" / "snapshots" / "library-snapshot.yaml"
SLICE_1A = {"jaw_segment", "implant", "abutment", "crown"}


# --- the registry ------------------------------------------------------------------------------------------------


def test_the_registry_finds_the_slice_1a_block_types() -> None:
    found = {b.type_id: b for b in all_block_types()}
    assert set(found) >= SLICE_1A
    for type_id in SLICE_1A:
        assert found[type_id].slice == "1a"
        assert found[type_id].version == "1.0.0"
    assert catalogue().block_type("implant", "1.0.0") is found["implant"]


def test_an_unknown_type_or_version_is_refused_with_an_upgrade_offer() -> None:
    with pytest.raises(UnknownBlockType, match="no block type 'bridge_thing'") as unknown:
        catalogue().block_type("bridge_thing", "1.0.0")
    assert unknown.value.code == "validation_failed"
    with pytest.raises(UnknownBlockType, match="upgrade the block to 1.0.0"):
        catalogue().block_type("implant", "0.9.0")


def test_each_block_type_declares_its_contract() -> None:
    sockets = {a.name for b in all_block_types() for a in b.attach_points}
    for block in all_block_types():
        assert set(block.attaches_to) <= sockets, block.type_id
        regions = {r for result in block.result_types for r in result.regions}
        assert regions <= set(block.report_regions), block.type_id
        assert "bonded_elastic" in block.physics
        json.dumps(block.describe())
    roots = [b.type_id for b in all_block_types() if not b.attaches_to]
    assert roots == ["jaw_segment"]


def test_the_retention_screw_and_micromotion_need_further_physics() -> None:
    needs = {r.id: r.needs_physics for b in all_block_types() for r in b.result_types}
    assert needs["screw_stress"] == {"screw_preload"}
    assert needs["micromotion"] == {"contact"}
    assert needs["implant_fatigue_life"] == {"fatigue"}
    assert needs["crown_stress"] == frozenset()


# --- settings: specs and schema ----------------------------------------------------------------------------------


@pytest.mark.parametrize("type_id", sorted(SLICE_1A))
def test_every_setting_has_a_group_and_a_kind(type_id: str) -> None:
    block = catalogue().latest(type_id)
    for spec in block.setting_specs():
        assert spec.group in SETTING_GROUPS
        if spec.kind == "library":
            assert spec.parameter_key and spec.default is None
        else:
            assert spec.parameter_key is None
            if spec.value_type in ("number", "integer"):
                assert spec.minimum is not None and spec.maximum is not None, spec.id
                assert spec.minimum <= spec.default <= spec.maximum, spec.id
            if spec.choices is not None:
                assert spec.default in spec.choices


@pytest.mark.parametrize("type_id", sorted(SLICE_1A))
def test_the_settings_schema_carries_group_kind_limits_and_keys(type_id: str) -> None:
    block = catalogue().latest(type_id)
    schema = block.settings_json_schema()
    assert schema["additionalProperties"] is False
    assert [g["id"] for g in schema["groups"]] == list(SETTING_GROUPS)
    library = [s.id for s in block.setting_specs() if s.kind == "library"]
    assert schema["required"] == library
    for spec in block.setting_specs():
        prop = schema["properties"][spec.id]
        assert prop["group"] == spec.group and prop["kind"] == spec.kind
        if spec.kind == "library":
            assert prop["parameter_key"] == spec.parameter_key
        elif spec.choices is not None:
            assert prop["enum"] == list(spec.choices)
    json.dumps(schema)


def _referenced(template: str) -> list[str]:
    return sorted({field for _, field, _, _ in string.Formatter().parse(template) if field})


def _every_parameter_key(type_id: str) -> set[tuple[str, str]]:
    """(setting id, parameter key) for every combination of the choices the parameter keys depend on."""
    block = catalogue().latest(type_id)
    specs = {s.id: s for s in block.setting_specs()}
    found = set()
    for spec in specs.values():
        if spec.kind != "library":
            continue
        names = _referenced(spec.parameter_key or "")
        options = [specs[n].choices or (specs[n].default,) for n in names]
        for combination in itertools.product(*options):
            design = {n: specs[n].default for n in specs if specs[n].kind == "design"} | dict(
                zip(names, combination, strict=True)
            )
            found.add((spec.id, resolve_parameter_key(spec.parameter_key or "", design)))
    return found


@pytest.mark.parametrize("type_id", sorted(SLICE_1A))
def test_every_parameter_key_has_its_units_in_the_conventions(type_id: str) -> None:
    conventions = get_conventions()
    block = catalogue().latest(type_id)
    for setting, key in _every_parameter_key(type_id):
        rows = [row for row in conventions.library_units if key_matches(row.pattern, key)]
        assert rows, f"{type_id}.{setting}: {key} matches no library units row"
        assert block.setting_spec(setting).unit == rows[0].units, key


@pytest.mark.skipif(not SNAPSHOT.is_file(), reason="no approved library snapshot")
@pytest.mark.parametrize("type_id", sorted(SLICE_1A))
def test_every_parameter_key_is_in_the_approved_library(type_id: str) -> None:
    approved = {e["parameter_key"] for e in yaml.safe_load(SNAPSHOT.read_text(encoding="utf-8"))["entries"]}
    missing = sorted(key for _, key in _every_parameter_key(type_id) if key not in approved)
    assert not missing, f"{type_id} can ask for library entries that are not approved: {missing}"


def test_every_material_choice_is_a_conventions_material() -> None:
    conventions = get_conventions()
    for block in all_block_types():
        for spec in block.setting_specs():
            if spec.id.endswith("material"):
                for choice in spec.choices or ():
                    assert conventions.material_class_of(choice) is not None, (block.type_id, choice)


def test_the_strength_placeholder_follows_the_conventions_rules() -> None:
    template = "material.{material}.{material:strength}"
    assert resolve_parameter_key(template, {"material": "titanium_g5"}) == "material.titanium_g5.yield_strength"
    assert resolve_parameter_key(template, {"material": "zirconia"}) == "material.zirconia.flexural_strength"
    with pytest.raises(DentalsimError, match="no strength reference"):
        resolve_parameter_key(template, {"material": "cortical_bone"})
    with pytest.raises(DentalsimError, match="not a design setting"):
        resolve_parameter_key("bone.{side}.E", {"material": "x"})


def test_parameter_keys_follow_the_design_values(kit) -> None:
    jaw = kit.block("jaw_segment")
    keys = jaw.parameter_keys(jaw.design_values(kit.raw("jaw_segment", density_class="D4")))
    assert keys["cancellous_E"] == "bone.cancellous.E.D4"
    assert keys["cortical_thickness"] == "bone.cortical.thickness.D4"
    assert keys["cortical_E"] == "bone.cortical.E"


# --- settings: reading ---------------------------------------------------------------------------------------------


def test_valid_settings_read_with_defaults_filled_in(kit) -> None:
    reading = kit.block("implant").read_settings(kit.raw("implant", length_mm=12), instance_id="implant-1")
    assert reading.ok and not reading.problems
    assert reading.settings.length_mm == 12.0
    assert reading.settings.diameter_mm == 4.1
    assert reading.values["thread"] == "v_thread"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda raw: raw.update(colour=DesignValue(value="red")), "has no setting 'colour'"),
        (lambda raw: raw.pop("E"), "needs a value from the literature library"),
        (lambda raw: raw.update(E=DesignValue(value=100000.0)), "is a library setting, not a design setting"),
        (lambda raw: raw.update(length_mm=DesignValue(value="long")), "must be a number"),
    ],
)
def test_malformed_settings_are_refused(kit, change, message: str) -> None:
    raw = kit.raw("implant")
    change(raw)
    with pytest.raises(Refused, match=message) as refused:
        kit.block("implant").read_settings(raw, instance_id="implant-1")
    assert refused.value.code == "validation_failed"
    assert refused.value.problems[0]["block"] == "implant-1"


def test_a_physically_impossible_library_value_is_refused(kit) -> None:
    raw = kit.raw("implant")
    raw["nu"] = LibraryValue(entry_id="01TESTENTRY", entry_version=1, choice="custom", value=0.6)
    with pytest.raises(Refused, match="Poisson's ratio must be above -1 and below 0.5"):
        kit.block("implant").read_settings(raw, instance_id="implant-1")


def test_design_values_outside_the_limits_are_problems_not_refusals(kit) -> None:
    reading = kit.block("implant").read_settings(
        kit.raw("implant", length_mm=20.0, thread="helical"), instance_id="implant-1"
    )
    assert not reading.ok and reading.settings is None
    codes = {p.code for p in reading.problems}
    assert codes == {"design_limit"}
    messages = " ".join(p.message for p in reading.problems)
    assert "Length is 20 mm; it must be 6 mm to 16 mm" in messages
    assert "Thread design must be one of v_thread, buttress, square, none, not 'helical'" in messages
    assert all(p.block == "implant-1" for p in reading.problems)


def test_list_settings_respect_their_item_counts(kit) -> None:
    reading = kit.block("jaw_segment").read_settings(
        kit.raw("jaw_segment", neighbours=("35", "37", "38")), instance_id="jaw"
    )
    assert [p.code for p in reading.problems] == ["design_limit"]
    assert "at most 2 item(s)" in reading.problems[0].message


def test_an_instance_must_match_the_block_version(kit) -> None:
    instance = BlockInstance(instance_id="i", type_id="implant", type_version="9.9.9", settings=kit.raw("implant"))
    with pytest.raises(DentalsimError, match="not implant 1.0.0"):
        kit.block("implant").read(instance)


# --- physics and the gate ------------------------------------------------------------------------------------------


def test_an_immediately_loaded_implant_needs_contact(kit) -> None:
    implant = kit.block("implant")
    assert implant.needs_physics(kit.raw("implant")) == {"bonded_elastic"}
    assert implant.needs_physics(kit.raw("implant", healing_state="immediately_loaded")) == {
        "bonded_elastic",
        "contact",
    }
    assert implant.needs_physics({}) == {"bonded_elastic"}


class _Repo:
    def __init__(self, *ready: str) -> None:
        self.rows = {
            c: CapabilityStatus(capability_id=c, status="ready", report_id="R1", cases=["textbook-x"]) for c in ready
        }

    def get(self, capability_id: str) -> CapabilityStatus | None:
        return self.rows.get(capability_id)

    def set(self, status: CapabilityStatus) -> None:
        self.rows[status.capability_id] = status

    def list_all(self) -> list[CapabilityStatus]:
        return list(self.rows.values())


def _single_crown(kit) -> Scenario:
    def block(instance_id: str, type_id: str, parent: tuple[str, str] | None) -> BlockInstance:
        attach = AttachTo(instance_id=parent[0], attach_point=parent[1]) if parent else None
        return BlockInstance(
            instance_id=instance_id, type_id=type_id, type_version="1.0.0", attach_to=attach, settings=kit.raw(type_id)
        )

    return Scenario(
        name="Single implant crown",
        conventions_version=get_conventions().version,
        blocks=[
            block("jaw", "jaw_segment", None),
            block("implant-1", "implant", ("jaw", "site")),
            block("abutment-1", "abutment", ("implant-1", "platform")),
            block("crown-1", "crown", ("abutment-1", "crown_seat")),
        ],
    )


def test_the_gate_reads_the_real_catalogue(kit, tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "textbook-x.yaml").write_text("id: textbook-x\ncapability: bonded_elastic\n", encoding="utf-8")
    scenario = _single_crown(kit)

    awaiting = check_gate(scenario, repo=_Repo(), cases_dir=cases)
    assert [m.capability_id for m in awaiting.missing] == ["bonded_elastic"]
    assert awaiting.missing[0].needed_by == ["jaw", "implant-1", "abutment-1", "crown-1"]

    ready = check_gate(scenario, repo=_Repo("bonded_elastic"), cases_dir=cases)
    assert not ready.refused
    locked = {(r.result_type, r.missing_capability) for r in ready.locked_results}
    assert ("screw_stress", "screw_preload") in locked
    assert ("micromotion", "contact") in locked
    assert not any(r.result_type in ("crown_stress", "implant_stress", "bone_strain") for r in ready.locked_results)
