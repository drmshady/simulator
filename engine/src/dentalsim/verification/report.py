"""Verification reports (data-model.md "VerificationReport"; FR-017, FR-036, FR-062)."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from dentalsim.model.scenario import DetailLevel
from dentalsim.verification.cases import CaseKind, Compare


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuantityResult(_Model):
    case_id: str
    case_file_hash: str
    level: DetailLevel
    quantity_id: str
    unit: str
    expected: float
    computed: float
    compare: Compare
    tolerance: float
    passed: bool
    """Judged at the case's first level only; other levels record their values for the preview accuracy."""
    judged: bool


class CrossCheck(_Model):
    """The scikit-fem value of a quantity against CalculiX's, on the same mesh."""

    case_id: str
    quantity_id: str
    calculix: float
    skfem: float
    difference: float
    allowed: float
    agrees: bool


class CaseAccuracy(_Model):
    case_id: str
    level: DetailLevel
    max_relative_difference: float
    """The largest |preview − standard| / |standard| over the case's quantities."""


class PreviewAccuracy(_Model):
    max_relative_difference: float | None
    per_case: list[CaseAccuracy]
    measured_on: list[CaseKind]
    """The kinds of case the accuracy was measured on (`textbook`, later `published_study`)."""


class CapabilityOutcome(_Model):
    capability_id: str
    required_cases: list[str]
    ran: list[str]
    passed: bool
    status: Literal["ready", "awaiting_benchmark", "unchanged"]


class VerificationReport(_Model):
    id: str
    created_at: AwareDatetime
    engine_version: str
    solver_build_id: str
    conventions_version: str
    case_ids: list[str]
    case_results: list[QuantityResult]
    crosscheck: list[CrossCheck]
    failures: list[str]
    """Plain-language reasons a case could not be solved or measured."""
    preview_accuracy: PreviewAccuracy
    capabilities: list[CapabilityOutcome]
    overall: Literal["pass", "fail"]

    def case_passed(self, case_id: str) -> bool:
        judged = [r for r in self.case_results if r.case_id == case_id and r.judged]
        checks = [c for c in self.crosscheck if c.case_id == case_id]
        failed = any(f.startswith(f"{case_id}:") for f in self.failures)
        return bool(judged) and all(r.passed for r in judged) and all(c.agrees for c in checks) and not failed

    def accuracy_note(self) -> str | None:
        """The note shown beside every preview (FR-017), or None when nothing was measured."""
        worst = self.preview_accuracy.max_relative_difference
        if worst is None:
            return None
        kinds = " and ".join("textbook" if k == "textbook" else "published" for k in self.preview_accuracy.measured_on)
        return f"Previews differ from detailed results by up to {100 * worst:.0f}% on the {kinds} verification cases."
