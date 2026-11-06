"""Out-of-date detection (FR-058) and the re-verification flag (FR-051): stale items are never reused silently."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dentalsim import version
from dentalsim.conventions.loader import get_loaded_conventions
from dentalsim.errors import Refused
from dentalsim.library.models import LiteratureEntryVersion, ValueBySource
from dentalsim.model.run import LibraryVersionRef
from dentalsim.model.scenario import Scenario
from dentalsim.persistence import Repos
from dentalsim.store.hashing import InputRecord, input_record
from dentalsim.store.staleness import CurrentState, assert_reusable, check_staleness

ENGINE_HASH = "a" * 64
REFS = (
    LibraryVersionRef(entry_id="E1", parameter_key="bone.cortical.E", version=2, content_hash="h-e1-2"),
    LibraryVersionRef(entry_id="E2", parameter_key="load.bite_force.molar", version=1, content_hash="h-e2-1"),
)
LIBRARY = {("E1", 2): "h-e1-2", ("E2", 1): "h-e2-1"}


def _record() -> InputRecord:
    scenario = Scenario.model_validate(
        {
            "name": "Staleness test",
            "conventions_version": "1.0.0",
            "blocks": [
                {
                    "instance_id": "base",
                    "type_id": "test_base",
                    "type_version": "1.0.0",
                    "settings": {
                        "stiffness": {
                            "kind": "library",
                            "entry_id": "E1",
                            "entry_version": 2,
                            "choice": "typical",
                            "value": 13700,
                        }
                    },
                }
            ],
        }
    )
    return input_record(
        scenario,
        library_versions=REFS,
        detail_level="standard",
        solver_build_id="ccx-2.23-pardiso",
        conventions_version="1.0.0",
        engine_source_hash=ENGINE_HASH,
        gmsh_version="4.15.2",
    )


def _current(**overrides: Any) -> CurrentState:
    values: dict[str, Any] = {
        "engine_source_hash": ENGINE_HASH,
        "solver_build_id": "ccx-2.23-pardiso",
        "gmsh_version": "4.15.2",
        "conventions_version": "1.0.0",
        "library": LIBRARY,
    }
    values.update(overrides)
    return CurrentState(**values)


def test_matching_state_is_reusable() -> None:
    staleness = check_staleness(_record(), _current())
    assert staleness.reusable
    assert staleness.flags == set()
    assert staleness.reasons == []
    assert staleness.notes == []
    assert assert_reusable(_record(), _current()) == staleness


@pytest.mark.parametrize(
    ("override", "changed", "reason"),
    [
        ({"engine_source_hash": "b" * 64}, "engine_source_hash", "engine code"),
        ({"solver_build_id": "ccx-2.24-pardiso"}, "solver_build_id", "ccx-2.23-pardiso to ccx-2.24-pardiso"),
        ({"gmsh_version": "4.15.3"}, "gmsh_version", "4.15.2 to 4.15.3"),
    ],
)
def test_engine_changes_make_it_out_of_date(override: dict[str, Any], changed: str, reason: str) -> None:
    staleness = check_staleness(_record(), _current(**override))
    assert staleness.flags == {"out_of_date"}
    assert staleness.out_of_date
    assert not staleness.needs_reverification
    assert staleness.changed == [changed]
    assert reason in staleness.reasons[0]


def test_a_conventions_change_needs_reverification() -> None:
    staleness = check_staleness(_record(), _current(conventions_version="1.1.0"))
    assert staleness.flags == {"out_of_date", "needs_reverification"}
    assert staleness.changed == ["conventions_version"]
    assert "1.0.0 to 1.1.0" in staleness.reasons[0]


def test_changed_library_content_makes_it_out_of_date() -> None:
    staleness = check_staleness(_record(), _current(library={**LIBRARY, ("E1", 2): "h-e1-2-new"}))
    assert staleness.flags == {"out_of_date"}
    assert staleness.changed == ["library:E1/2"]
    assert "bone.cortical.E version 2" in staleness.reasons[0]


def test_a_library_version_that_no_longer_exists_makes_it_out_of_date() -> None:
    staleness = check_staleness(_record(), _current(library={("E1", 2): "h-e1-2"}))
    assert staleness.flags == {"out_of_date"}
    assert staleness.changed == ["library:E2/1"]
    assert "no longer exists" in staleness.reasons[0]


def test_the_library_can_be_a_lookup_function() -> None:
    asked: list[tuple[str, int]] = []

    def lookup(entry_id: str, version: int) -> str | None:
        asked.append((entry_id, version))
        return LIBRARY.get((entry_id, version))

    assert check_staleness(_record(), _current(library=lookup)).reusable
    assert asked == [("E1", 2), ("E2", 1)]


def test_without_a_solver_the_solver_build_is_not_compared() -> None:
    staleness = check_staleness(_record(), _current(solver_build_id=None))
    assert staleness.reusable
    assert staleness.notes == ["No solver is configured, so the solver build was not compared."]


def test_every_change_is_listed() -> None:
    current = _current(engine_source_hash="b" * 64, gmsh_version="4.16.0", conventions_version="2.0.0", library={})
    staleness = check_staleness(_record(), current)
    assert staleness.changed == [
        "engine_source_hash",
        "gmsh_version",
        "conventions_version",
        "library:E1/2",
        "library:E2/1",
    ]
    assert len(staleness.reasons) == 5


def test_a_stale_item_is_refused_with_its_reasons() -> None:
    with pytest.raises(Refused) as caught:
        assert_reusable(_record(), _current(gmsh_version="4.15.3", conventions_version="1.1.0"))
    refused = caught.value
    assert refused.code == "out_of_date"
    assert "out of date" in refused.message
    assert [item["item"] for item in refused.missing] == ["input:gmsh_version", "input:conventions_version"]
    assert all(item["message"] for item in refused.missing)


def _version(entry_id: str, number: int, content_hash: str) -> LiteratureEntryVersion:
    return LiteratureEntryVersion(
        entry_id=entry_id,
        version=number,
        units="MPa",
        typical=1.0,
        range_min=0.5,
        range_max=2.0,
        values_by_source=[ValueBySource(source_id="S1", value=1.0)],
        status="approved",
        origin="owner",
        proposed_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=content_hash,
    )


class _FakeLibrary:
    def __init__(self, versions: list[LiteratureEntryVersion]) -> None:
        self.versions = {(v.entry_id, v.version): v for v in versions}

    def get_version(self, entry_id: str, version: int) -> LiteratureEntryVersion | None:
        return self.versions.get((entry_id, version))


def test_from_repos_reads_library_versions_through_the_repository() -> None:
    library = _FakeLibrary([_version("E1", 2, "h-e1-2"), _version("E2", 1, "h-e2-1")])
    repos = cast(Repos, SimpleNamespace(library=library))
    current = CurrentState.from_repos(
        repos,
        solver_build_id="ccx-2.23-pardiso",
        engine_source_hash=ENGINE_HASH,
        gmsh_version="4.15.2",
        conventions_version="1.0.0",
    )
    assert check_staleness(_record(), current).reusable
    del library.versions[("E2", 1)]
    assert check_staleness(_record(), current).changed == ["library:E2/1"]


def test_from_repos_defaults_to_the_running_engine() -> None:
    current = CurrentState.from_repos(cast(Repos, SimpleNamespace(library=_FakeLibrary([]))), solver_build_id=None)
    assert current.engine_source_hash == version.engine_source_hash()
    assert current.gmsh_version == version.gmsh_version()
    assert current.conventions_version == get_loaded_conventions().version
