"""Canonical JSON and input hashes (research R14, FR-058): equal inputs give one key, any change gives another."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dentalsim import version
from dentalsim.conventions.loader import get_loaded_conventions
from dentalsim.model.run import LibraryVersionRef
from dentalsim.model.scenario import Scenario
from dentalsim.store.hashing import InputRecord, canonical_json, input_hash, input_record

ENGINE_HASH = "a" * 64
REFS = (
    LibraryVersionRef(entry_id="E1", parameter_key="bone.cortical.E", version=2, content_hash="h-e1-2"),
    LibraryVersionRef(entry_id="E2", parameter_key="load.bite_force.molar", version=1, content_hash="h-e2-1"),
    LibraryVersionRef(entry_id="E3", parameter_key="material.titanium_g5.yield", version=1, content_hash="h-e3-1"),
)


def _scenario_data(*, width: Any = 10, settings_reversed: bool = False) -> dict[str, Any]:
    settings = {
        "width": {"kind": "design", "value": width},
        "stiffness": {"kind": "library", "entry_id": "E1", "entry_version": 2, "choice": "typical", "value": 13700},
    }
    if settings_reversed:
        settings = dict(reversed(settings.items()))
    return {
        "name": "Hashing test",
        "conventions_version": "1.0.0",
        "detail_level": "standard",
        "blocks": [
            {"instance_id": "base", "type_id": "test_base", "type_version": "1.0.0", "settings": settings},
            {
                "instance_id": "post",
                "type_id": "test_post",
                "type_version": "1.0.0",
                "attach_to": {"instance_id": "base", "attach_point": "site"},
                "placement": {"position_mm": [0, 0, 0]},
            },
        ],
        "loads": [
            {
                "id": "bite-1",
                "pattern": "single_bite",
                "force": {"kind": "library", "entry_id": "E2", "entry_version": 1, "choice": "typical", "value": 200},
                "direction": {"angle_deg": 30, "azimuth_deg": 90},
                "contact_points": [{"block": "post", "point": "top", "share": 1.0}],
            }
        ],
    }


def _scenario(**options: Any) -> Scenario:
    return Scenario.model_validate(_scenario_data(**options))


def _record(scenario: Scenario | None = None, **overrides: Any) -> InputRecord:
    arguments: dict[str, Any] = {
        "library_versions": REFS,
        "detail_level": "standard",
        "solver_build_id": "ccx-2.23-pardiso",
        "conventions_version": "1.0.0",
        "engine_source_hash": ENGINE_HASH,
        "gmsh_version": "4.15.2",
        "purpose": "run",
    }
    arguments.update(overrides)
    return input_record(scenario or _scenario(), **arguments)


# Canonical JSON


def test_keys_are_sorted_with_no_whitespace() -> None:
    assert canonical_json({"b": 1, "a": [1, 2], "c": {"z": None, "y": True}}) == (
        b'{"a":[1.0,2.0],"b":1.0,"c":{"y":true,"z":null}}'
    )


def test_equal_numbers_have_one_spelling() -> None:
    assert canonical_json(10) == canonical_json(10.0) == b"10.0"
    assert canonical_json(-0.0) == canonical_json(0) == b"0.0"
    assert canonical_json(0.1) == b"0.1"
    assert canonical_json(True) == b"true"
    assert canonical_json(True) != canonical_json(1)


def test_text_is_utf8_not_escaped() -> None:
    assert canonical_json({"unit": "µε"}) == '{"unit":"µε"}'.encode()


def test_tuples_are_lists_and_sets_are_sorted() -> None:
    assert canonical_json((1, 2)) == canonical_json([1, 2])
    assert canonical_json({"b", "a", "c"}) == canonical_json(frozenset({"c", "a", "b"})) == b'["a","b","c"]'


def test_models_and_dates_are_dumped_as_json() -> None:
    ref = REFS[0]
    assert canonical_json(ref) == canonical_json(
        {"entry_id": "E1", "parameter_key": "bone.cortical.E", "version": 2, "content_hash": "h-e1-2"}
    )
    assert canonical_json(datetime(2026, 1, 1, 9, 0, tzinfo=UTC)) == b'"2026-01-01T09:00:00Z"'


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (math.nan, ValueError),
        (math.inf, ValueError),
        (-math.inf, ValueError),
        (2**53 + 1, ValueError),
        ({1: "not a text key"}, TypeError),
        (b"raw", TypeError),
        (object(), TypeError),
    ],
)
def test_values_without_a_canonical_form_are_refused(value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        canonical_json(value)


# Input records and hashes


def test_hash_is_a_sha256_hex_digest_and_stable() -> None:
    first = input_hash(_record())
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)
    assert input_hash(_record()) == first


def test_key_order_and_number_spelling_do_not_change_the_hash() -> None:
    base = input_hash(_record())
    reordered = _record(_scenario(width=10.0, settings_reversed=True), library_versions=tuple(reversed(REFS)))
    assert input_hash(reordered) == base


def _change_width(record: dict[str, Any]) -> None:
    record["scenario"] = _scenario(width=11)


def _change_position(record: dict[str, Any]) -> None:
    data = _scenario_data()
    data["blocks"][1]["placement"]["position_mm"] = [0, 0, 0.5]
    record["scenario"] = Scenario.model_validate(data)


def _change_angle(record: dict[str, Any]) -> None:
    data = _scenario_data()
    data["loads"][0]["direction"]["angle_deg"] = 31
    record["scenario"] = Scenario.model_validate(data)


def _change_library_content(record: dict[str, Any]) -> None:
    record["library_versions"] = (REFS[0].model_copy(update={"content_hash": "h-e1-2-changed"}), *REFS[1:])


def _change_library_version(record: dict[str, Any]) -> None:
    record["library_versions"] = (*REFS[:2], REFS[2].model_copy(update={"version": 2}))


def _set(field: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(record: dict[str, Any]) -> None:
        record[field] = value

    return change


CHANGES: dict[str, Callable[[dict[str, Any]], None]] = {
    "design setting": _change_width,
    "placement": _change_position,
    "load direction": _change_angle,
    "library content hash": _change_library_content,
    "library version": _change_library_version,
    "detail level": _set("detail_level", "fine"),
    "conventions version": _set("conventions_version", "1.1.0"),
    "engine source hash": _set("engine_source_hash", "b" * 64),
    "solver build": _set("solver_build_id", "ccx-2.23-spooles"),
    "gmsh version": _set("gmsh_version", "4.15.3"),
    "purpose": _set("purpose", "preview"),
}


@pytest.mark.parametrize("change", CHANGES, ids=list(CHANGES))
def test_any_single_input_change_changes_the_hash(change: str) -> None:
    arguments: dict[str, Any] = {}
    CHANGES[change](arguments)
    scenario = arguments.pop("scenario", None)
    assert input_hash(_record(scenario, **arguments)) != input_hash(_record())


def test_every_change_gives_a_different_hash() -> None:
    hashes = {input_hash(_record())}
    for change in CHANGES.values():
        arguments: dict[str, Any] = {}
        change(arguments)
        scenario = arguments.pop("scenario", None)
        hashes.add(input_hash(_record(scenario, **arguments)))
    assert len(hashes) == len(CHANGES) + 1


def test_engine_commit_is_not_an_input(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not any("commit" in name or name == "engine_version" for name in InputRecord.model_fields)
    defaults = {"engine_source_hash": None, "gmsh_version": None}
    monkeypatch.setattr(version, "engine_commit", lambda: "1111111")
    first = input_hash(_record(**defaults))
    monkeypatch.setattr(version, "engine_commit", lambda: "2222222")
    assert input_hash(_record(**defaults)) == first
    monkeypatch.setattr(version, "engine_source_hash", lambda: "c" * 64)
    assert input_hash(_record(**defaults)) != first


def test_defaults_come_from_the_running_engine() -> None:
    record = _record(engine_source_hash=None, gmsh_version=None, conventions_version=None)
    assert record.engine_source_hash == version.engine_source_hash()
    assert record.gmsh_version == version.gmsh_version()
    assert record.conventions_version == get_loaded_conventions().version


def test_the_detail_level_argument_is_written_into_the_scenario() -> None:
    record = _record(detail_level="preview")
    assert record.detail_level == "preview"
    assert record.scenario["detail_level"] == "preview"
    assert input_hash(record) != input_hash(_record())
    mismatched = record.model_dump() | {"detail_level": "standard"}
    with pytest.raises(ValidationError, match="detail level"):
        InputRecord.model_validate(mismatched)


def test_library_versions_are_sorted_and_listed_once() -> None:
    record = _record(library_versions=(REFS[2], REFS[0], REFS[1], REFS[0]))
    assert [(r.entry_id, r.version) for r in record.library_versions] == [("E1", 2), ("E2", 1), ("E3", 1)]
    unsorted = record.model_dump() | {"library_versions": [r.model_dump() for r in reversed(REFS)]}
    with pytest.raises(ValidationError, match="sorted"):
        InputRecord.model_validate(unsorted)


def test_one_version_with_two_contents_is_refused() -> None:
    conflicting = REFS[0].model_copy(update={"content_hash": "other"})
    with pytest.raises(ValueError, match="two different contents"):
        _record(library_versions=(*REFS, conflicting))


def test_every_library_value_of_the_scenario_must_be_listed() -> None:
    with pytest.raises(ValueError, match="E2 version 1"):
        _record(library_versions=(REFS[0], REFS[2]))


def test_a_record_round_trips_through_json_with_the_same_hash() -> None:
    record = _record()
    again = InputRecord.model_validate_json(record.model_dump_json())
    assert again == record
    assert input_hash(again) == input_hash(record)
