"""A run: one solve of a frozen scenario snapshot (data-model.md "Run")."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from dentalsim.model.results import LockedResult
from dentalsim.model.scenario import DetailLevel, Scenario

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
RunFlag = Literal["out_of_date", "uses_rejected_entry", "outside_published_range", "dev_build", "needs_reverification"]
BLOCKING_FLAGS: frozenset[RunFlag] = frozenset(
    {"out_of_date", "uses_rejected_entry", "outside_published_range", "dev_build", "needs_reverification"}
)
FINISHED: frozenset[RunStatus] = frozenset({"succeeded", "failed", "cancelled"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LibraryVersionRef(_Model):
    entry_id: str
    parameter_key: str
    version: int = Field(ge=1)
    content_hash: str


class Estimate(_Model):
    dof: int = Field(ge=0)
    time_s: float = Field(ge=0)
    memory_gb: float = Field(ge=0)
    disk_gb: float = Field(ge=0)


class Actual(_Model):
    dof: int = Field(ge=0)
    time_s: float = Field(ge=0)
    peak_memory_gb: float = Field(ge=0)
    disk_gb: float = Field(ge=0)


class ForceBalance(_Model):
    """The applied load and the sum of the support reactions for one unit load case (FR-023)."""

    load_case: str
    applied_n: tuple[float, float, float]
    reaction_n: tuple[float, float, float]
    relative_error: float = Field(ge=0)


class EnergyBalance(_Model):
    """The work of the applied load against twice the strain energy for one unit load case (FR-023).

    They are equal for a correct linear solution; a model that is not fully supported breaks it.
    """

    load_case: str
    external_work: float
    twice_strain_energy: float
    relative_error: float = Field(ge=0)


class CaseResidual(_Model):
    load_case: str
    relative_residual: float | None
    """None when the equation solver prints no residual."""


class Convergence(_Model):
    force_balance: list[ForceBalance]
    energy_balance: list[EnergyBalance] = []
    residuals: list[CaseResidual] = []
    solver_relative_residual: float | None = None
    """The worst of the case residuals."""
    solver_messages: list[str] = []


class Run(_Model):
    id: str
    scenario_id: str | None
    """None for a run of a scenario file from the command line."""
    scenario_revision: int | None
    snapshot: Scenario
    """The fully resolved scenario, including every library value."""
    detail_level: DetailLevel
    input_hash: str
    library_versions: list[LibraryVersionRef]
    engine_version: str
    engine_source_hash: str
    conventions_version: str
    solver_build_id: str
    gmsh_version: str
    solver_host: str | None = None
    """The rented solver machine that solved the run (FR-064); None for the main PC."""
    estimate: Estimate | None = None
    actual: Actual | None = None
    status: RunStatus = "queued"
    failure_reason: str | None = None
    convergence: Convergence | None = None
    result_ref: str | None = None
    locked_results: list[LockedResult] = []
    flags: set[RunFlag] = set()
    study_id: str | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _results_only_when_succeeded(self) -> Run:
        if self.result_ref is not None and self.status != "succeeded":
            raise ValueError("only a succeeded run has results (FR-022)")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("a failed run must record its reason (FR-022)")
        return self

    def is_publishable(self) -> bool:
        """Succeeded, and none of the blocking flags is set (data-model.md "Publishable")."""
        return self.status == "succeeded" and not (self.flags & BLOCKING_FLAGS)
