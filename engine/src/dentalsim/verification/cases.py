"""Verification case files (data-model.md "VerificationCase"; research R15; FR-035, FR-036).

A case is a YAML file in `content/verification/cases/`, committed before it is first run so that its expected
values and tolerances are fixed in git before any result is seen. It names:

- the capability it gates and whether it is required;
- the model: a builder (`dentalsim.verification.builders`) and its parameters, the inputs its source states;
- the quantities: each with its expected value, where that value comes from, how it is compared (`absolute`,
  `relative` or `ratio`) and the tolerance;
- the source it reproduces, and every later change of a tolerance with its date and reason.

Comparisons: `absolute` passes when |computed − expected| ≤ tolerance; `relative` when |computed − expected| ≤
tolerance × |expected|; `ratio` when computed / expected lies within [1 / (1 + tolerance), 1 + tolerance].
"""

from __future__ import annotations

import hashlib
import math
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dentalsim.errors import DentalsimError, Refused
from dentalsim.model.scenario import DetailLevel
from dentalsim.physics.registry import default_cases_dir, get_capability

Compare = Literal["absolute", "relative", "ratio"]
CaseKind = Literal["textbook", "published_study"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quantity(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    unit: str
    expected: float
    expected_source: str = Field(min_length=1)
    compare: Compare
    tolerance: float = Field(gt=0)

    def band(self) -> float:
        """The allowed difference from the expected value, in the quantity's unit (approximate for `ratio`)."""
        return self.tolerance if self.compare == "absolute" else self.tolerance * abs(self.expected)

    def passes(self, computed: float) -> bool:
        if not math.isfinite(computed):
            return False
        if self.compare == "absolute":
            return abs(computed - self.expected) <= self.tolerance
        if self.compare == "relative":
            return abs(computed - self.expected) <= self.tolerance * abs(self.expected)
        if self.expected == 0 or computed / self.expected <= 0:
            return False
        ratio = computed / self.expected
        return 1.0 / (1.0 + self.tolerance) <= ratio <= 1.0 + self.tolerance


class ModelSpec(_Model):
    builder: str
    parameters: dict[str, Any] = {}


class CaseSource(_Model):
    citation: str = Field(min_length=1)
    doi: str | None = None
    pmid: str | None = None
    isbn: str | None = None


class ToleranceChange(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    date: date
    quantity: str
    from_: float = Field(alias="from")
    to: float
    reason: str = Field(min_length=1)


class VerificationCase(_Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    kind: CaseKind
    capability: str
    required: bool = True
    model: ModelSpec
    levels: list[DetailLevel] = ["standard", "preview"]
    """The first level decides pass or fail; the others measure how far they differ (the preview accuracy)."""
    quantities: list[Quantity] = Field(min_length=1)
    source: CaseSource
    tolerance_changes: list[ToleranceChange] = []

    @model_validator(mode="after")
    def _consistent(self) -> VerificationCase:
        get_capability(self.capability)
        ids = [q.id for q in self.quantities]
        if len(ids) != len(set(ids)):
            raise ValueError("quantity ids must be unique")
        if not self.levels or len(set(self.levels)) != len(self.levels):
            raise ValueError("levels must list distinct detail levels")
        for change in self.tolerance_changes:
            if change.quantity not in ids:
                raise ValueError(f"a tolerance change names the unknown quantity {change.quantity!r}")
        return self


class LoadedCase(_Model):
    case: VerificationCase
    path: Path
    sha256: str


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_case(path: Path) -> LoadedCase:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        case = VerificationCase.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise DentalsimError(f"verification case {path.name} is not valid: {exc}") from exc
    if case.id != path.stem:
        raise DentalsimError(f"verification case {path.name}: its id {case.id!r} must match the file name")
    return LoadedCase(case=case, path=path, sha256=file_sha256(path))


def load_cases(cases_dir: Path | None = None) -> list[LoadedCase]:
    """Every case file, sorted by id."""
    folder = cases_dir if cases_dir is not None else default_cases_dir()
    if not folder.is_dir():
        return []
    paths = sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")], key=lambda p: p.name)
    return [load_case(path) for path in paths]


def select_cases(
    cases: list[LoadedCase], *, case_ids: list[str] | None = None, physics: str | None = None
) -> list[LoadedCase]:
    """The cases named by id, or those gating `physics`, or every case."""
    chosen = cases
    if case_ids:
        known = {c.case.id for c in cases}
        unknown = sorted(set(case_ids) - known)
        if unknown:
            raise Refused(f"there is no verification case {', '.join(unknown)}", code="validation_failed")
        chosen = [c for c in chosen if c.case.id in case_ids]
    if physics is not None:
        get_capability(physics)
        chosen = [c for c in chosen if c.case.capability == physics]
    if not chosen:
        raise Refused("no verification case matches; write and commit the case files first", code="validation_failed")
    return chosen


def uncommitted(paths: list[Path]) -> list[Path]:
    """The files that git does not hold as committed: changed, staged but not committed, or not tracked.

    Refused when git cannot answer, since a case's tolerances must be in git before it runs (research R15).
    """
    if not paths:
        return []
    root = paths[0].parent
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *[str(p) for p in paths]],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 128 and "not a git repository" in (exc.stderr or ""):
            return list(paths)  # outside any repository nothing is committed
        raise Refused(
            f"cannot check with git that the case files are committed: {exc.stderr or exc}", code="validation_failed"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(
            f"cannot check with git that the case files are committed: {exc}", code="validation_failed"
        ) from exc
    changed = {line[3:].strip().strip('"').replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}
    return [p for p in paths if any(p.as_posix().endswith(c) for c in changed)]
