"""Validity problems of an assembly (FR-011, data-model.md "ValidityResult")."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from dentalsim.clock import utc_now

ProblemCode = Literal[
    "overlap",
    "unsupported_pontic",
    "in_nerve_canal",
    "in_sinus",
    "in_root",
    "exceeds_bone",
    "template_misfit",
    "design_limit",
]
Severity = Literal["error", "warning"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Problem(_Model):
    """One problem with a plain-language message and, where it has one, its location in the conventions frame."""

    code: ProblemCode
    message: str
    block: str | None = None
    location_mm: tuple[float, float, float] | None = None
    severity: Severity = "error"


class ValidityResult(_Model):
    ok: bool
    problems: list[Problem]
    checked_at: AwareDatetime
    scenario_revision: int | None = None

    @classmethod
    def from_problems(
        cls, problems: Iterable[Problem], *, scenario_revision: int | None = None, checked_at: datetime | None = None
    ) -> ValidityResult:
        """`ok` is true when no problem has severity `error`."""
        found = list(problems)
        return cls(
            ok=not any(p.severity == "error" for p in found),
            problems=found,
            checked_at=checked_at or utc_now(),
            scenario_revision=scenario_revision,
        )

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]
