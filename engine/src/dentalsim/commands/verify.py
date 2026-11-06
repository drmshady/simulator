"""`dentalsim verify [--case ID ...] [--physics ID]`: runs the verification suite (contracts/cli.md; task T075).

Every chosen case file must be committed first. The cases are solved with the configured CalculiX build at their
levels and cross-checked with scikit-fem; the report is stored in the studio database and the capability status is
updated (ready only when every required case passes in one report, FR-062). Exit code 1 when any case fails.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from dentalsim.clock import new_ulid
from dentalsim.config import ensure_data_folder
from dentalsim.errors import Refused
from dentalsim.persistence import repos_session
from dentalsim.physics.registry import summarise
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.solvers.skfem.crosscheck import solve_skfem
from dentalsim.threads import heavy_threads
from dentalsim.verification.cases import load_cases, select_cases
from dentalsim.verification.report import VerificationReport
from dentalsim.verification.runner import calculix_solver, run_verification

if TYPE_CHECKING:
    from dentalsim.cli import Output


def register(subparsers: Any) -> None:
    verify = subparsers.add_parser("verify", help="run the verification cases and update the physics status")
    verify.add_argument("--case", action="append", dest="cases", metavar="ID", help="run only this case (repeatable)")
    verify.add_argument("--physics", default=None, help="run only the cases that gate this capability")
    verify.add_argument("--host", default=None, help="a rented solver machine (task T175; not available yet)")
    verify.add_argument("--threads", type=int, default=None, help="solver threads (default: the heavy lane's)")
    verify.add_argument("--no-crosscheck", action="store_true", help="skip the scikit-fem cross-check")
    verify.add_argument("--keep-work", action="store_true", help="keep the solver work folders")
    verify.set_defaults(handler=verify_command)


def summary_lines(report: VerificationReport) -> list[str]:
    lines = [f"report {report.id}: {report.overall.upper()} (solver {report.solver_build_id[:40]}...)"]
    for case_id in report.case_ids:
        lines.append(f"  {case_id}: {'pass' if report.case_passed(case_id) else 'FAIL'}")
        for result in (r for r in report.case_results if r.case_id == case_id):
            mark = "" if not result.judged else (" ok" if result.passed else " FAIL")
            lines.append(
                f"    {result.level:<10} {result.quantity_id:<28} {result.computed:+.6g} {result.unit} "
                f"(expected {result.expected:+.6g}, {result.compare} {result.tolerance:g}){mark}"
            )
        for check in (c for c in report.crosscheck if c.case_id == case_id):
            verdict = "agrees" if check.agrees else "DISAGREES"
            lines.append(f"    scikit-fem {check.quantity_id:<23} {check.skfem:+.6g} ({verdict})")
    lines += [f"  failed: {failure}" for failure in report.failures]
    note = report.accuracy_note()
    if note:
        lines.append(f"preview accuracy: {note}")
    for outcome in report.capabilities:
        lines.append(f"capability {outcome.capability_id}: {outcome.status.replace('_', ' ')}")
    return lines


def verify_command(args: argparse.Namespace, out: Output) -> int | None:
    if args.host:
        raise Refused("verifying on a rented solver machine arrives with task T175", code="validation_failed")
    chosen = select_cases(load_cases(), case_ids=args.cases, physics=args.physics)
    build = solver_build()
    data_dir = ensure_data_folder()
    threads = args.threads or heavy_threads()
    with repos_session(data_dir) as repos:
        report = run_verification(
            chosen,
            solver=calculix_solver(threads=threads),
            solver_build_id=build.build_id,
            work_dir=data_dir / "work" / f"verification-{new_ulid()}",
            repos=repos,
            crosscheck=None if args.no_crosscheck else solve_skfem,
            keep_work=args.keep_work,
            progress=None if out.json_mode else (lambda message: print(message, file=out.stderr, flush=True)),
        )
        states = {o.capability_id: summarise(o.capability_id, repos.capabilities).status for o in report.capabilities}
    data = report.model_dump(mode="json") | {"capability_status": states}
    out.result(data, "\n".join(summary_lines(report)))
    return None if report.overall == "pass" else 1
