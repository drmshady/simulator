"""The verification runner (task T071; research R15; FR-017, FR-035, FR-036, FR-062).

For each case:

1. The case file must be committed: its tolerances are fixed in git before it is run. Its SHA-256 is recorded.
2. Its model is built and solved at each of its levels. The first level (standard) decides pass or fail against the
   expected values; the others (preview) are compared with it, which gives the preview accuracy (FR-017).
3. At the first level the same meshes are solved again with the scikit-fem cross-check; each quantity must agree
   with CalculiX's within a tenth of the case's tolerance band.

The report is stored, and each capability the cases gate is updated: **ready** when every one of its current
required cases ran in this report and passed; **awaiting benchmark** as soon as any required case fails (FR-062);
otherwise unchanged. A change emits `capability.changed`.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from dentalsim.clock import new_ulid, utc_now
from dentalsim.conventions.loader import get_conventions
from dentalsim.errors import DentalsimError, Refused
from dentalsim.persistence import CapabilityStatus, Repos
from dentalsim.physics.registry import CaseHeader, get_capability, read_case_headers, required_cases_in
from dentalsim.solvers.calculix.inp_writer import SolverModel
from dentalsim.solvers.calculix.solve import solve_model
from dentalsim.verification.builders import builder_for
from dentalsim.verification.cases import LoadedCase, uncommitted
from dentalsim.verification.report import (
    CapabilityOutcome,
    CaseAccuracy,
    CrossCheck,
    PreviewAccuracy,
    QuantityResult,
    VerificationReport,
)
from dentalsim.verification.solution import CaseSolution, ccx_solution
from dentalsim.version import engine_version

CROSSCHECK_FRACTION = 0.1
"""The cross-check must agree within this fraction of a quantity's tolerance band."""

CaseSolver = Callable[[SolverModel, Path], CaseSolution]
CrossSolver = Callable[[SolverModel], CaseSolution]
Progress = Callable[[str], None]


def calculix_solver(*, threads: int = 1, ccx: Path | None = None) -> CaseSolver:
    def solve(model: SolverModel, work_dir: Path) -> CaseSolution:
        step = solve_model(model, work_dir, threads=threads, ccx=ccx).steps[0]
        return ccx_solution(model, step.displacements, step.stress, step.strain, step.ip_element_ids, step.ip_numbers)

    return solve


def _relative(value: float, reference: float) -> float | None:
    if reference == 0 or not math.isfinite(value) or not math.isfinite(reference):
        return None
    return abs(value - reference) / abs(reference)


def _run_case(
    loaded: LoadedCase,
    solver: CaseSolver,
    crosscheck: CrossSolver | None,
    work_dir: Path,
    progress: Progress | None,
) -> tuple[list[QuantityResult], list[CrossCheck], list[CaseAccuracy]]:
    case = loaded.case
    build = builder_for(case.model.builder)
    results: list[QuantityResult] = []
    checks: list[CrossCheck] = []
    accuracy: list[CaseAccuracy] = []
    first: dict[str, float] = {}
    for index, level in enumerate(case.levels):
        if progress:
            progress(f"{case.id}: solving at the {level} level")
        built = build(case.model.parameters, level)
        solutions = [solver(model, work_dir / case.id / level / f"model{i}") for i, model in enumerate(built.models)]
        values = built.evaluate(solutions)
        missing = [q.id for q in case.quantities if q.id not in values]
        if missing:
            raise DentalsimError(f"the {case.model.builder} builder does not measure {', '.join(missing)}")
        for quantity in case.quantities:
            computed = float(values[quantity.id])
            results.append(
                QuantityResult(
                    case_id=case.id,
                    case_file_hash=loaded.sha256,
                    level=level,
                    quantity_id=quantity.id,
                    unit=quantity.unit,
                    expected=quantity.expected,
                    computed=computed,
                    compare=quantity.compare,
                    tolerance=quantity.tolerance,
                    passed=quantity.passes(computed),
                    judged=index == 0,
                )
            )
        if index == 0:
            first = {q.id: float(values[q.id]) for q in case.quantities}
            if crosscheck is not None:
                if progress:
                    progress(f"{case.id}: cross-checking with scikit-fem")
                other = built.evaluate([crosscheck(model) for model in built.models])
                for quantity in case.quantities:
                    a, b = first[quantity.id], float(other[quantity.id])
                    allowed = CROSSCHECK_FRACTION * quantity.band()
                    checks.append(
                        CrossCheck(
                            case_id=case.id,
                            quantity_id=quantity.id,
                            calculix=a,
                            skfem=b,
                            difference=abs(a - b),
                            allowed=allowed,
                            agrees=abs(a - b) <= allowed,
                        )
                    )
        else:
            differences = [
                d
                for q in case.quantities
                if q.expected != 0 and (d := _relative(float(values[q.id]), first[q.id])) is not None
            ]
            if differences:
                accuracy.append(CaseAccuracy(case_id=case.id, level=level, max_relative_difference=max(differences)))
    return results, checks, accuracy


def _capability_outcomes(
    report_cases: Sequence[LoadedCase], passed: dict[str, bool], headers: Sequence[CaseHeader]
) -> list[CapabilityOutcome]:
    outcomes = []
    for capability_id in sorted({c.case.capability for c in report_cases}):
        required = list(required_cases_in(capability_id, headers))
        ran = sorted(c.case.id for c in report_cases if c.case.capability == capability_id)
        failed_required = any(not passed[case_id] for case_id in ran if case_id in required)
        complete = bool(required) and set(required) <= {case_id for case_id in ran if passed[case_id]}
        status = "awaiting_benchmark" if failed_required else "ready" if complete else "unchanged"
        outcomes.append(
            CapabilityOutcome(
                capability_id=capability_id,
                required_cases=required,
                ran=ran,
                passed=all(passed[case_id] for case_id in ran),
                status=status,
            )
        )
    return outcomes


def run_verification(
    cases: Sequence[LoadedCase],
    *,
    solver: CaseSolver,
    solver_build_id: str,
    work_dir: Path,
    repos: Repos | None = None,
    crosscheck: CrossSolver | None = None,
    require_committed: bool = True,
    cases_dir: Path | None = None,
    keep_work: bool = False,
    progress: Progress | None = None,
) -> VerificationReport:
    """Run the cases, write the report, and update the capability status (when `repos` is given)."""
    if not cases:
        raise Refused("no verification case to run", code="validation_failed")
    if require_committed:
        dirty = uncommitted([c.path for c in cases])
        if dirty:
            raise Refused(
                "Commit the verification case files before running them, so their tolerances are fixed in git: "
                + ", ".join(p.name for p in dirty)
                + ".",
                code="validation_failed",
                missing=[{"item": f"commit:{p.name}", "message": f"{p.name} has uncommitted changes"} for p in dirty],
            )
    headers = read_case_headers(cases_dir if cases_dir is not None else cases[0].path.parent)
    results: list[QuantityResult] = []
    checks: list[CrossCheck] = []
    accuracy: list[CaseAccuracy] = []
    failures: list[str] = []
    for loaded in cases:
        get_capability(loaded.case.capability)
        try:
            found, crossed, accurate = _run_case(loaded, solver, crosscheck, work_dir, progress)
        except (DentalsimError, ValueError, KeyError) as exc:
            failures.append(f"{loaded.case.id}: {exc}")
            continue
        results += found
        checks += crossed
        accuracy += accurate
    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    kinds = sorted({c.case.kind for c in cases if any(a.case_id == c.case.id for a in accuracy)})
    draft = VerificationReport(
        id=new_ulid(),
        created_at=utc_now(),
        engine_version=engine_version(),
        solver_build_id=solver_build_id,
        conventions_version=get_conventions().version,
        case_ids=[c.case.id for c in cases],
        case_results=results,
        crosscheck=checks,
        failures=failures,
        preview_accuracy=PreviewAccuracy(
            max_relative_difference=max((a.max_relative_difference for a in accuracy), default=None),
            per_case=accuracy,
            measured_on=kinds,
        ),
        capabilities=[],
        overall="fail",
    )
    passed = {c.case.id: draft.case_passed(c.case.id) for c in cases}
    report = draft.model_copy(
        update={
            "capabilities": _capability_outcomes(cases, passed, headers),
            "overall": "pass" if all(passed.values()) else "fail",
        }
    )
    if repos is not None:
        record(report, repos)
    return report


def record(report: VerificationReport, repos: Repos) -> None:
    """Store the report and apply its capability outcomes, emitting `capability.changed` for each change."""
    with repos.transaction():
        repos.verification.add_report(report.model_dump(mode="json"))
        for outcome in report.capabilities:
            if outcome.status == "unchanged":
                continue
            before = repos.capabilities.get(outcome.capability_id)
            status = CapabilityStatus(
                capability_id=outcome.capability_id,
                status=outcome.status,
                report_id=report.id,
                cases=outcome.required_cases if outcome.status == "ready" else [],
                updated_at=report.created_at,
            )
            repos.capabilities.set(status)
            if before is None or before.status != status.status:
                repos.events.emit(
                    "capability.changed",
                    {"capability_id": outcome.capability_id, "status": outcome.status, "report_id": report.id},
                )
