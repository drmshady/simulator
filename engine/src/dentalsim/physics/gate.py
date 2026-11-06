"""The physics gate: may a scenario be previewed or run, and which results stay locked (FR-011a, FR-059).

The gate reads only two things from the block catalogue: each block's `needs_physics(settings)` and each of its
result types' `needs_physics`. It never imports a block module (SC-012).

- A capability that a block needs and that is not ready refuses previews and runs, naming its benchmark. The
  scenario can still be built and saved.
- A result type whose own physics is not ready is listed in `locked_results`, for example screw stress as
  "not reported: needs screw preload", without refusing the rest of the run.
"""

from __future__ import annotations

import importlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from dentalsim.errors import DentalsimError, Refused
from dentalsim.model.results import LockedResult
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import SettingValue
from dentalsim.persistence import CapabilityRepo
from dentalsim.physics.registry import (
    CAPABILITIES,
    CapabilitySummary,
    get_capability,
    read_case_headers,
    summarise,
)

CATALOGUE_MODULE = "dentalsim.blocks.registry"


class ResultTypeInfo(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def needs_physics(self) -> Collection[str]:
        """Physics needed beyond the block's own, such as `screw_preload` for screw stress."""
        ...


class BlockTypeInfo(Protocol):
    @property
    def type_id(self) -> str: ...

    @property
    def result_types(self) -> Sequence[ResultTypeInfo]: ...

    def needs_physics(self, settings: Mapping[str, SettingValue]) -> Collection[str]:
        """The capabilities a block instance with these settings needs; it may depend on the settings."""
        ...


class BlockCatalogue(Protocol):
    def block_type(self, type_id: str, version: str) -> BlockTypeInfo: ...


def default_catalogue() -> BlockCatalogue:
    """The block registry's catalogue, imported only when it is needed."""
    try:
        module = importlib.import_module(CATALOGUE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != CATALOGUE_MODULE:
            raise
        raise DentalsimError(
            f"the block catalogue ({CATALOGUE_MODULE}, task T060) is not available yet, so the physics gate "
            "cannot read what the blocks need"
        ) from exc
    factory = getattr(module, "catalogue", None)
    if not callable(factory):
        raise DentalsimError(f"{CATALOGUE_MODULE} has no catalogue() for the physics gate")
    return cast(BlockCatalogue, factory())


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MissingCapability(_Model):
    capability_id: str
    label: str
    benchmark_cases: list[str]
    """The required case ids that gate it; empty when none has been written yet."""
    needed_by: list[str]
    """The block instances that need it, in scenario order."""
    message: str


class GateResult(_Model):
    missing: list[MissingCapability] = []
    locked_results: list[LockedResult] = []

    @property
    def refused(self) -> bool:
        return bool(self.missing)

    def refusal(self) -> Refused | None:
        """The `run_refused` error for previews and runs, naming every missing benchmark; None when open."""
        if not self.missing:
            return None
        return Refused(
            "This scenario cannot be previewed or run until the physics it needs passes its benchmark. "
            + " ".join(m.message for m in self.missing),
            code="run_refused",
            missing=[{"item": f"benchmark:{m.capability_id}", "message": m.message} for m in self.missing],
        )


def require_open(gate: GateResult) -> None:
    refusal = gate.refusal()
    if refusal is not None:
        raise refusal


def check_gate(
    scenario: Scenario,
    *,
    repo: CapabilityRepo,
    catalogue: BlockCatalogue | None = None,
    cases_dir: Path | None = None,
) -> GateResult:
    """Which capabilities the scenario's blocks need that are not ready, and which result types are locked.

    The case files are read once per call. A block or result type naming an unregistered capability is an error.
    """
    catalogue = catalogue if catalogue is not None else default_catalogue()
    cases = read_case_headers(cases_dir)
    known: dict[str, CapabilitySummary] = {}

    def summary(capability_id: str) -> CapabilitySummary:
        if capability_id not in known:
            known[capability_id] = summarise(capability_id, repo, cases=cases)
        return known[capability_id]

    needed_by: dict[str, list[str]] = {}
    locked: list[LockedResult] = []
    for block in scenario.blocks:
        info = catalogue.block_type(block.type_id, block.type_version)
        for capability_id in _ordered(info.needs_physics(block.settings)):
            needed_by.setdefault(capability_id, []).append(block.instance_id)
        for result_type in info.result_types:
            for capability_id in _ordered(result_type.needs_physics):
                state = summary(capability_id)
                if state.status != "ready":
                    locked.append(
                        LockedResult(
                            result_type=result_type.id,
                            block=block.instance_id,
                            missing_capability=capability_id,
                            message=f"not reported: needs {state.label.lower()}",
                        )
                    )
    missing = [
        MissingCapability(
            capability_id=capability_id,
            label=state.label,
            benchmark_cases=state.required_cases,
            needed_by=users,
            message=state.message or f"{state.label} is awaiting its benchmark.",
        )
        for capability_id, users in needed_by.items()
        if (state := summary(capability_id)).status != "ready"
    ]
    return GateResult(missing=missing, locked_results=locked)


_ORDER = {capability.id: index for index, capability in enumerate(CAPABILITIES)}


def _ordered(capability_ids: Iterable[str]) -> list[str]:
    """Each id once, in registry order, so a set from a block gives the same result every time."""
    unique = set(capability_ids)
    for capability_id in unique:
        get_capability(capability_id)
    return sorted(unique, key=_ORDER.__getitem__)
