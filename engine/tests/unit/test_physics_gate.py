"""The physics gate (FR-011a, FR-059): missing benchmarks refuse previews and runs; result types lock on their own."""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dentalsim.errors import DentalsimError, Refused
from dentalsim.model.results import LockedResult
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import DesignValue, SettingValue
from dentalsim.persistence import CapabilityStatus
from dentalsim.physics import gate
from dentalsim.physics.gate import check_gate, default_catalogue, require_open
from dentalsim.physics.registry import UnknownCapability


class MemoryCapabilityRepo:
    def __init__(self) -> None:
        self.rows: dict[str, CapabilityStatus] = {}

    def get(self, capability_id: str) -> CapabilityStatus | None:
        return self.rows.get(capability_id)

    def set(self, status: CapabilityStatus) -> None:
        self.rows[status.capability_id] = status

    def list_all(self) -> list[CapabilityStatus]:
        return list(self.rows.values())

    def ready(self, *capability_ids: str) -> MemoryCapabilityRepo:
        for capability_id in capability_ids:
            self.set(CapabilityStatus(capability_id=capability_id, status="ready", report_id="R1", cases=CASE_IDS))
        return self


@dataclass(frozen=True)
class FakeResultType:
    id: str
    needs_physics: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeBlockType:
    type_id: str
    result_types: tuple[FakeResultType, ...] = ()
    physics: Collection[str] = ("bonded_elastic",)
    by_settings: Callable[[Mapping[str, SettingValue]], Collection[str]] | None = None

    def needs_physics(self, settings: Mapping[str, SettingValue]) -> Collection[str]:
        extra = self.by_settings(settings) if self.by_settings else ()
        return [*self.physics, *extra]


def _healing_physics(settings: Mapping[str, SettingValue]) -> Collection[str]:
    healing = settings.get("healing_state")
    if isinstance(healing, DesignValue) and healing.value == "immediately_loaded":
        return ["contact"]
    return []


@dataclass
class FakeCatalogue:
    types: dict[str, FakeBlockType]
    asked: list[tuple[str, str]] = field(default_factory=list)

    def block_type(self, type_id: str, version: str) -> FakeBlockType:
        self.asked.append((type_id, version))
        return self.types[type_id]


def _catalogue(*extra: FakeBlockType) -> FakeCatalogue:
    types = [
        FakeBlockType("fake_bone", (FakeResultType("bone_strain"),)),
        FakeBlockType(
            "fake_fixture",
            (FakeResultType("fixture_stress"), FakeResultType("micromotion", ("contact",))),
            by_settings=_healing_physics,
        ),
        FakeBlockType("fake_screw", (FakeResultType("screw_stress", ("screw_preload",)),)),
        *extra,
    ]
    return FakeCatalogue({t.type_id: t for t in types})


def _scenario(healing: str = "osseointegrated", *extra_blocks: dict[str, Any]) -> Scenario:
    return Scenario.model_validate(
        {
            "name": "Gate test",
            "conventions_version": "1.0.0",
            "blocks": [
                {"instance_id": "base", "type_id": "fake_bone", "type_version": "1.0.0"},
                {
                    "instance_id": "fixture",
                    "type_id": "fake_fixture",
                    "type_version": "2.1.0",
                    "attach_to": {"instance_id": "base", "attach_point": "site"},
                    "settings": {"healing_state": {"kind": "design", "value": healing}},
                },
                {
                    "instance_id": "screw",
                    "type_id": "fake_screw",
                    "type_version": "1.0.0",
                    "attach_to": {"instance_id": "fixture", "attach_point": "screw_seat"},
                },
                *extra_blocks,
            ],
        }
    )


CASE_IDS = ["textbook-cantilever", "textbook-patch", "screw_preload-case", "contact-case"]


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    folder = tmp_path / "cases"
    folder.mkdir()
    (folder / "textbook-cantilever.yaml").write_text("id: textbook-cantilever\ncapability: bonded_elastic\n")
    (folder / "textbook-patch.yaml").write_text("id: textbook-patch\ncapability: bonded_elastic\nrequired: true\n")
    return folder


SCREW_LOCKED = LockedResult(
    result_type="screw_stress",
    block="screw",
    missing_capability="screw_preload",
    message="not reported: needs screw preload",
)


def test_everything_awaiting_refuses_naming_bonded_elastic(cases: Path) -> None:
    result = check_gate(_scenario(), repo=MemoryCapabilityRepo(), catalogue=_catalogue(), cases_dir=cases)
    assert result.refused
    [missing] = result.missing
    assert missing.capability_id == "bonded_elastic"
    assert missing.label == "Bonded elastic"
    assert missing.benchmark_cases == ["textbook-cantilever", "textbook-patch"]
    assert missing.needed_by == ["base", "fixture", "screw"]
    assert missing.message == "Bonded elastic is awaiting its benchmark: the textbook cases have not passed."
    refusal = result.refusal()
    assert refusal is not None
    assert refusal.code == "run_refused"
    assert refusal.missing == [{"item": "benchmark:bonded_elastic", "message": missing.message}]
    assert missing.message in refusal.message


def test_a_refused_block_still_lists_only_results_whose_own_physics_is_missing(cases: Path) -> None:
    result = check_gate(_scenario(), repo=MemoryCapabilityRepo(), catalogue=_catalogue(), cases_dir=cases)
    assert {(r.result_type, r.missing_capability) for r in result.locked_results} == {
        ("micromotion", "contact"),
        ("screw_stress", "screw_preload"),
    }


def test_bonded_ready_and_screw_preload_awaiting_locks_screw_stress(cases: Path) -> None:
    repo = MemoryCapabilityRepo().ready("bonded_elastic")
    result = check_gate(_scenario(), repo=repo, catalogue=_catalogue(), cases_dir=cases)
    assert not result.refused
    assert result.refusal() is None
    require_open(result)
    assert SCREW_LOCKED in result.locked_results
    assert [r.result_type for r in result.locked_results] == ["micromotion", "screw_stress"]


def test_a_setting_can_add_physics_that_refuses_the_run(cases: Path) -> None:
    repo = MemoryCapabilityRepo().ready("bonded_elastic")
    result = check_gate(_scenario("immediately_loaded"), repo=repo, catalogue=_catalogue(), cases_dir=cases)
    assert result.refused
    [missing] = result.missing
    assert missing.capability_id == "contact"
    assert missing.needed_by == ["fixture"]
    assert missing.benchmark_cases == []
    assert missing.message.startswith("Contact is awaiting its benchmark")
    refusal = result.refusal()
    assert refusal is not None
    assert [item["item"] for item in refusal.missing] == ["benchmark:contact"]


def test_ready_status_without_case_files_is_still_refused(tmp_path: Path) -> None:
    repo = MemoryCapabilityRepo().ready("bonded_elastic", "screw_preload")
    result = check_gate(_scenario(), repo=repo, catalogue=_catalogue(), cases_dir=tmp_path / "empty")
    assert [m.capability_id for m in result.missing] == ["bonded_elastic"]
    assert result.missing[0].message == (
        "Bonded elastic is awaiting its benchmark: the textbook cases have not been written yet."
    )
    assert SCREW_LOCKED in result.locked_results


def test_nothing_is_locked_when_every_capability_is_ready(cases: Path) -> None:
    for capability_id in ("screw_preload", "contact"):
        (cases / f"{capability_id}.yaml").write_text(f"id: {capability_id}-case\ncapability: {capability_id}\n")
    repo = MemoryCapabilityRepo().ready("bonded_elastic", "screw_preload", "contact")
    result = check_gate(_scenario("immediately_loaded"), repo=repo, catalogue=_catalogue(), cases_dir=cases)
    assert result.missing == []
    assert result.locked_results == []


def test_require_open_raises_the_refusal(cases: Path) -> None:
    result = check_gate(_scenario(), repo=MemoryCapabilityRepo(), catalogue=_catalogue(), cases_dir=cases)
    with pytest.raises(Refused) as caught:
        require_open(result)
    assert caught.value.code == "run_refused"


def test_block_types_are_looked_up_by_pinned_version(cases: Path) -> None:
    catalogue = _catalogue()
    check_gate(_scenario(), repo=MemoryCapabilityRepo(), catalogue=catalogue, cases_dir=cases)
    assert catalogue.asked == [("fake_bone", "1.0.0"), ("fake_fixture", "2.1.0"), ("fake_screw", "1.0.0")]


def test_missing_capabilities_follow_registry_order_whatever_the_block_returns(cases: Path) -> None:
    unordered = FakeBlockType("fake_mixed", physics=["contact", "bonded_elastic", "contact"])
    block = {"instance_id": "mixed", "type_id": "fake_mixed", "type_version": "1.0.0"}
    result = check_gate(
        _scenario("osseointegrated", block),
        repo=MemoryCapabilityRepo(),
        catalogue=_catalogue(unordered),
        cases_dir=cases,
    )
    assert [m.capability_id for m in result.missing] == ["bonded_elastic", "contact"]
    assert result.missing[0].needed_by == ["base", "fixture", "screw", "mixed"]


def test_an_unregistered_capability_is_an_error(cases: Path) -> None:
    odd = FakeBlockType("fake_odd", physics=("magnetism",))
    block = {"instance_id": "odd", "type_id": "fake_odd", "type_version": "1.0.0"}
    scenario = _scenario("osseointegrated", block)
    with pytest.raises(UnknownCapability, match="magnetism"):
        check_gate(scenario, repo=MemoryCapabilityRepo(), catalogue=_catalogue(odd), cases_dir=cases)


def test_default_catalogue_names_task_t060_when_the_registry_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "CATALOGUE_MODULE", "dentalsim.blocks._registry_not_written")
    with pytest.raises(DentalsimError, match="T060"):
        default_catalogue()


def test_default_catalogue_comes_from_the_block_registry(monkeypatch: pytest.MonkeyPatch, cases: Path) -> None:
    module = ModuleType("fake_block_registry")
    catalogue = _catalogue()
    module.catalogue = lambda: catalogue  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_block_registry", module)
    monkeypatch.setattr(gate, "CATALOGUE_MODULE", "fake_block_registry")
    assert default_catalogue() is catalogue
    result = check_gate(_scenario(), repo=MemoryCapabilityRepo(), cases_dir=cases)
    assert [m.capability_id for m in result.missing] == ["bonded_elastic"]
