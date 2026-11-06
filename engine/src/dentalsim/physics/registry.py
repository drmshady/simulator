"""The physics capability registry (FR-011a, FR-062; contracts/scenario-format.md "Physics capability contract").

Every kind of physics is a `Capability`. Bonded elastic is linear, so unit load cases may be superposed (research R4).
Screw preload, the periodontal ligament, contact and fatigue are non-linear and arrive as Story 9 increments (R21).

- **Required cases are data, not code.** They are the verification case files in `content/verification/cases/`
  whose top-level `capability` names the capability and whose `required` is true (a missing `required` counts as
  true). Only `id`, `capability` and `required` are read here; the full case schema belongs to the verification
  module.
- **Status** comes from the `CapabilityRepo`, and is `awaiting_benchmark` when there is no row. The verification
  runner sets it to `ready` when every required case passes in one report, and back when one fails (FR-062).
- **Ready** means the stored status is ready *and* at least one required case exists, so a capability without a
  benchmark can never be ready.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from dentalsim.config import get_settings
from dentalsim.errors import DentalsimError
from dentalsim.persistence import CapabilityRepo, CapabilityState, CapabilityStatus

CASES_DIR = Path("content") / "verification" / "cases"

SolverInputHook = Callable[[object, object], None]
"""`write_solver_input(model, solver)`: adds the capability's cards to the solver input. The solver model and
solver adapter types arrive with the solver tasks, so the hook takes plain objects for now."""
PostHook = Callable[[object], None]
"""`post(results)`: adds any result fields of the capability, such as micromotion."""


class UnknownCapability(DentalsimError, KeyError):
    """A capability id that is not registered."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "unknown capability"


def _bonded_solver_input(model: object, solver: object) -> None:
    """Bonded parts share nodes, which is the solver input's default, so bonded elastic adds no cards."""


def _bonded_post(results: object) -> None:
    """Bonded elastic adds no result fields of its own."""


def _awaiting_increment(capability_id: str) -> tuple[SolverInputHook, PostHook]:
    """Hooks for physics planned in a later increment. They cannot run: the capability has no benchmark yet."""
    reason = f"{capability_id} arrives in its own Story 9 increment; it has no solver input or results yet"

    def write_solver_input(model: object, solver: object) -> None:
        raise NotImplementedError(reason)

    def post(results: object) -> None:
        raise NotImplementedError(reason)

    return write_solver_input, post


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    """Plain English, starting with a capital: "Screw preload"."""
    linear: bool
    """Whether superposition of unit load cases is allowed (research R4)."""
    benchmark: str
    """What gates it, as a plural noun phrase for messages: "the textbook cases"."""
    write_solver_input: SolverInputHook = _bonded_solver_input
    post: PostHook = _bonded_post

    def required_cases(self, cases_dir: Path | None = None) -> tuple[str, ...]:
        """The ids of the required case files that name this capability, read now from `cases_dir`."""
        return required_cases_in(self.id, read_case_headers(cases_dir))

    def awaiting_message(self, required_cases: Sequence[str]) -> str:
        if not required_cases:
            return f"{self.label} is awaiting its benchmark: {self.benchmark} have not been written yet."
        return f"{self.label} is awaiting its benchmark: {self.benchmark} have not passed."


def _later(capability_id: str, label: str, benchmark: str) -> Capability:
    write_solver_input, post = _awaiting_increment(capability_id)
    return Capability(
        id=capability_id,
        label=label,
        linear=False,
        benchmark=benchmark,
        write_solver_input=write_solver_input,
        post=post,
    )


CAPABILITIES: tuple[Capability, ...] = (
    Capability(id="bonded_elastic", label="Bonded elastic", linear=True, benchmark="the textbook cases"),
    _later("screw_preload", "Screw preload", "the bolt-preload cases"),
    _later("periodontal_ligament", "Periodontal ligament", "the thin-layer and tooth-mobility cases"),
    _later("contact", "Contact", "the Hertz contact and sliding-friction cases"),
    _later("fatigue", "Fatigue", "the Basquin, Goodman and S-N cases"),
)
_BY_ID = {capability.id: capability for capability in CAPABILITIES}


def all_capabilities() -> tuple[Capability, ...]:
    return CAPABILITIES


def get_capability(capability_id: str) -> Capability:
    try:
        return _BY_ID[capability_id]
    except KeyError:
        known = ", ".join(_BY_ID)
        raise UnknownCapability(f"unknown physics capability {capability_id!r}; the known ones are {known}") from None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseHeader(_Model):
    """The three fields of a verification case file that the registry reads."""

    id: str
    capability: str
    required: bool
    path: Path


def default_cases_dir() -> Path:
    root = get_settings().repo_root
    if root is None:
        raise DentalsimError(f"cannot find {CASES_DIR.as_posix()}: no repository root was found")
    return root / CASES_DIR


def read_case_headers(cases_dir: Path | None = None) -> list[CaseHeader]:
    """Every case file (`*.yaml`, `*.yml`) in `cases_dir`, default `content/verification/cases/`, sorted by file name.

    A missing folder holds no cases. A file that cannot be read, lacks an `id` or `capability`, names an unknown
    capability or repeats another file's id is an error, so a broken case file can never quietly drop a benchmark.
    """
    folder = cases_dir if cases_dir is not None else default_cases_dir()
    if not folder.is_dir():
        return []
    paths = sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")], key=lambda p: p.name)
    headers = [_read_header(path) for path in paths if path.is_file()]
    first_file: dict[str, Path] = {}
    for header in headers:
        earlier = first_file.setdefault(header.id, header.path)
        if earlier != header.path:
            raise DentalsimError(
                f"verification case id {header.id!r} is used by both {earlier.name} and {header.path.name}"
            )
    return headers


def _read_header(path: Path) -> CaseHeader:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DentalsimError(f"cannot read verification case {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise DentalsimError(f"verification case {path.name} must be a YAML mapping")
    case_id, capability, required = data.get("id"), data.get("capability"), data.get("required", True)
    if not isinstance(case_id, str) or not case_id:
        raise DentalsimError(f"verification case {path.name} needs an `id`")
    if not isinstance(capability, str) or not capability:
        raise DentalsimError(f"verification case {path.name} needs a `capability`")
    if capability not in _BY_ID:
        raise DentalsimError(f"verification case {path.name} names the unknown capability {capability!r}")
    if not isinstance(required, bool):
        raise DentalsimError(f"verification case {path.name}: `required` must be true or false")
    return CaseHeader(id=case_id, capability=capability, required=required, path=path)


def required_cases_in(capability_id: str, cases: Sequence[CaseHeader]) -> tuple[str, ...]:
    return tuple(sorted(case.id for case in cases if case.capability == capability_id and case.required))


def stored_status(capability_id: str, repo: CapabilityRepo) -> CapabilityStatus:
    """The stored status row, or `awaiting_benchmark` when there is none."""
    get_capability(capability_id)
    return repo.get(capability_id) or CapabilityStatus(capability_id=capability_id)


class CapabilitySummary(_Model):
    """A capability as the catalogue and the gate see it.

    `status` is the effective state: `ready` only when the stored status is ready and a required case exists.
    `report_id` is the verification report that made it ready, and `message` says why it is not ready.
    """

    id: str
    label: str
    linear: bool
    status: CapabilityState
    required_cases: list[str]
    report_id: str | None = None
    message: str | None = None


def summarise(
    capability_id: str,
    repo: CapabilityRepo,
    cases_dir: Path | None = None,
    *,
    cases: Sequence[CaseHeader] | None = None,
) -> CapabilitySummary:
    """`cases` are already-read case headers, to read the case files once for many capabilities."""
    capability = get_capability(capability_id)
    required = required_cases_in(capability_id, cases if cases is not None else read_case_headers(cases_dir))
    stored = stored_status(capability_id, repo)
    ready = stored.status == "ready" and bool(required)
    return CapabilitySummary(
        id=capability.id,
        label=capability.label,
        linear=capability.linear,
        status="ready" if ready else "awaiting_benchmark",
        required_cases=list(required),
        report_id=stored.report_id if ready else None,
        message=None if ready else capability.awaiting_message(required),
    )


def summaries(repo: CapabilityRepo, cases_dir: Path | None = None) -> list[CapabilitySummary]:
    """Every registered capability, in registry order."""
    cases = read_case_headers(cases_dir)
    return [summarise(capability.id, repo, cases=cases) for capability in CAPABILITIES]


def capability_state(capability_id: str, repo: CapabilityRepo, cases_dir: Path | None = None) -> CapabilityState:
    return summarise(capability_id, repo, cases_dir).status
