"""`dentalsim check`: the merge gate (constitution, Development Workflow; research R20).

The full gate runs lint, type checks, the `unit` and `integration` tests, the `benchmark` and `regression` tests and
the interface's type check and tests. The benchmark and regression tests need the solver, so without
`DENTALSIM_CCX` the result is "incomplete": exit code 1, and it does not count as a pass. Only a complete pass is
recorded, with the engine commit, in the studio's settings table, where merges and the publish checklist look for it.

`--quick` gives fast feedback on the laptop without the solver. It is never a merge gate and records nothing.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dentalsim.cli import Output
from dentalsim.clock import utc_now
from dentalsim.config import get_settings
from dentalsim.errors import DentalsimError, Refused
from dentalsim.persistence import repos_session
from dentalsim.version import version_info

LAST_PASS_KEY = "check.last_pass"
NO_TESTS_COLLECTED = 5  # pytest's exit code when a marker selects nothing

Outcome = Literal["pass", "fail", "incomplete", "quick_pass"]


@dataclass(frozen=True)
class Step:
    name: str
    argv: list[str]
    ok_codes: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class StepResult:
    name: str
    returncode: int
    ok: bool
    seconds: float
    output_tail: str


StepRunner = Callable[[Step, Path, bool], StepResult]


def _python(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def plan_steps(*, quick: bool, solver_available: bool, npm: str | None) -> tuple[list[Step], list[str]]:
    """The steps to run, and the parts of the full gate that are left out (which make the result incomplete)."""
    steps = [
        Step("ruff lint", _python("ruff", "check", ".")),
        Step("ruff format", _python("ruff", "format", "--check", ".")),
        Step("mypy", _python("mypy")),
        Step("unit and integration tests", _python("pytest", "-q", "-m", "unit or integration")),
    ]
    skipped: list[str] = []
    if quick:
        skipped.append("benchmark and regression tests (--quick)")
    elif solver_available:
        steps.append(
            Step(
                "benchmark and regression tests",
                _python("pytest", "-q", "-m", "benchmark or regression"),
                ok_codes=(0, NO_TESTS_COLLECTED),
            )
        )
    else:
        skipped.append("benchmark and regression tests (no solver: set DENTALSIM_CCX)")
    if npm is None:
        skipped.append("interface type check and tests (npm not found)")
    else:
        steps.append(Step("interface type check", [npm, "--prefix", "studio/ui", "run", "typecheck"]))
        steps.append(Step("interface tests", [npm, "--prefix", "studio/ui", "test"]))
    return steps, skipped


def run_step(step: Step, cwd: Path, capture: bool) -> StepResult:
    """Run one step. Output goes to the terminal unless `capture` (then the last lines are kept for the report)."""
    started = time.monotonic()
    try:
        done = subprocess.run(
            step.argv,
            cwd=cwd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True,
            check=False,
        )
    except OSError as exc:
        return StepResult(step.name, -1, False, time.monotonic() - started, f"could not start: {exc}")
    tail = "\n".join((done.stdout or "").splitlines()[-30:]) if capture else ""
    return StepResult(step.name, done.returncode, done.returncode in step.ok_codes, time.monotonic() - started, tail)


def outcome_of(results: Sequence[StepResult], skipped: Sequence[str], *, quick: bool) -> Outcome:
    if not all(r.ok for r in results):
        return "fail"
    if quick:
        return "quick_pass"
    return "incomplete" if skipped else "pass"


def record_pass(data_dir: Path | None = None) -> dict[str, Any]:
    info = version_info()
    record = {
        "engine_commit": info.engine_commit,
        "engine_version": info.engine_version,
        "engine_source_hash": info.engine_source_hash,
        "dev_build": info.dev_build,
        "passed_at": utc_now().isoformat(),
    }
    with repos_session(data_dir) as repos:
        repos.settings.set(LAST_PASS_KEY, record)
    return record


def check(
    *, quick: bool, out: Output, runner: StepRunner | None = None, data_dir: Path | None = None
) -> dict[str, Any]:
    run = runner or run_step
    settings = get_settings()
    if settings.repo_root is None:
        raise DentalsimError("cannot find the repository root (the folder with docs/conventions.md)")
    steps, skipped = plan_steps(quick=quick, solver_available=settings.ccx is not None, npm=shutil.which("npm"))
    results = []
    for step in steps:
        if not out.json_mode:
            print(f"== {step.name}", file=out.stdout, flush=True)
        results.append(run(step, settings.repo_root, out.json_mode))
    outcome = outcome_of(results, skipped, quick=quick)
    report: dict[str, Any] = {
        "outcome": outcome,
        "steps": [
            {"name": r.name, "ok": r.ok, "returncode": r.returncode, "seconds": round(r.seconds, 1)}
            | ({"output_tail": r.output_tail} if not r.ok and r.output_tail else {})
            for r in results
        ],
        "skipped": list(skipped),
        "recorded": None,
    }
    if outcome == "pass":
        report["recorded"] = record_pass(data_dir)
    return report


def _summary(report: dict[str, Any]) -> str:
    lines = [f"{'ok  ' if s['ok'] else 'FAIL'}  {s['name']} ({s['seconds']} s)" for s in report["steps"]]
    lines += [f"skip  {name}" for name in report["skipped"]]
    for step in report["steps"]:
        if not step["ok"] and step.get("output_tail"):
            lines += [f"--- {step['name']} (last lines)", step["output_tail"]]
    recorded = report["recorded"]
    if recorded:
        lines.append(f"pass recorded for engine commit {recorded['engine_commit']}")
    return "\n".join(lines)


def _handle(args: argparse.Namespace, out: Output) -> int | None:
    report = check(quick=args.quick, out=out)
    outcome = report["outcome"]
    if outcome == "fail":
        failed = [
            {"item": f"check:{s['name']}", "message": f"{s['name']} failed"} for s in report["steps"] if not s["ok"]
        ]
        if not out.json_mode:
            print(_summary(report), file=out.stdout)
        raise Refused("the merge gate failed", code="check_failed", missing=failed)
    if outcome == "incomplete":
        if not out.json_mode:
            print(_summary(report), file=out.stdout)
        raise Refused(
            "incomplete: the benchmark and regression tests did not run, so this is not a pass "
            "(run the full check on the main PC with DENTALSIM_CCX set)",
            code="check_incomplete",
            missing=[{"item": "check:skipped", "message": name} for name in report["skipped"]],
        )
    text = _summary(report)
    if outcome == "quick_pass":
        text += "\nquick check passed (not a merge gate; nothing recorded)"
    out.result(report, text)
    return None


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "check", help="run the merge gate: lint, types, all tests (benchmarks need DENTALSIM_CCX)"
    )
    parser.add_argument(
        "--quick", action="store_true", help="fast feedback without the benchmark and regression tests; never a gate"
    )
    parser.set_defaults(handler=_handle)
