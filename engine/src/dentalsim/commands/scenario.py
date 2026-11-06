"""`dentalsim validate|estimate|run|preview <scenario.json>` (contracts/cli.md; task T083).

A scenario comes from a file in the scenario format, or from the studio database with `--id`. Every command calls
the same engine functions as the studio, so both give identical results (Principle VI).

- `validate`: the validity checks and the problem list (exit 1 when the scenario is not valid).
- `estimate --detail LEVEL`: expected DOF, time, memory and disk.
- `run --detail LEVEL [--out DIR]`: checks validity, the physics gate and the estimate, then solves; the run is
  stored through the run repository, and the key numbers and run id are printed.
- `preview [--load ID ...]`: a preview solve (never a run, never publishable), of the named load cases or all.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dentalsim.assembly.validity import check_validity
from dentalsim.clock import utc_now
from dentalsim.config import ensure_data_folder
from dentalsim.errors import DentalsimError, NotFound, Refused
from dentalsim.estimate.model import EstimateSample, refit
from dentalsim.estimate.predict import RunEstimate, estimate_run, require_fits
from dentalsim.library.service import LibraryService
from dentalsim.model.results import KeyNumber
from dentalsim.model.run import Estimate
from dentalsim.model.scenario import DETAIL_LEVELS, Scenario
from dentalsim.persistence import Repos, repos_session
from dentalsim.physics.gate import check_gate, require_open
from dentalsim.pipeline import PipelineFailed, PipelineResult, run_pipeline
from dentalsim.runs import failed_run, library_inputs, new_run, succeeded_run
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.store.cas import ContentStore
from dentalsim.threads import heavy_threads, preview_threads

if TYPE_CHECKING:
    from dentalsim.cli import Output


def register(subparsers: Any) -> None:
    for name, help_text in (
        ("validate", "check a scenario's validity and list its problems"),
        ("estimate", "expected DOF, time, memory and disk of a run"),
        ("run", "solve a scenario and store the run"),
        ("preview", "a preview solve (never publishable)"),
    ):
        parser = subparsers.add_parser(name, help=help_text)
        parser.add_argument("scenario", nargs="?", type=Path, help="a scenario file (dentalsim.scenario/1)")
        parser.add_argument("--id", dest="scenario_id", default=None, help="a studio scenario id instead of a file")
        if name in ("estimate", "run"):
            parser.add_argument("--detail", choices=DETAIL_LEVELS, default=None, help="default: the scenario's own")
        if name == "validate":
            parser.add_argument("--fast", action="store_true", help="the fast checks only (no overlap checks)")
        if name in ("run", "preview"):
            parser.add_argument("--out", type=Path, default=None, help="also write the result and surfaces here")
            parser.add_argument("--threads", type=int, default=None, help="solver threads")
            parser.add_argument("--no-cache", action="store_true", help="solve again even if a result is stored")
        if name == "preview":
            parser.add_argument("--load", action="append", dest="loads", metavar="ID", help="an active load case")
        parser.set_defaults(handler=globals()[f"{name}_command"])


def load_scenario(args: argparse.Namespace, repos: Repos | None = None) -> tuple[Scenario, str | None, int | None]:
    """The scenario, its studio id and revision (None for a file)."""
    if args.scenario_id:
        if repos is None:
            raise DentalsimError("reading a studio scenario needs the studio database")
        record = repos.scenarios.get(args.scenario_id)
        if record is None:
            raise NotFound(f"there is no scenario {args.scenario_id}")
        return record.scenario, record.id, record.revision
    if args.scenario is None:
        raise Refused("name a scenario file or give --id", code="validation_failed")
    try:
        return Scenario.model_validate(json.loads(args.scenario.read_text(encoding="utf-8"))), None, None
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise Refused(f"{args.scenario} is not a valid scenario file: {exc}", code="validation_failed") from None


def _key_number_lines(numbers: list[KeyNumber]) -> list[str]:
    lines = []
    for n in numbers:
        side = f", {n.side} side" if n.side and f"{n.side} side" not in n.label else ""
        reference = f"  ({n.percent_of_reference:.1f}% of {n.reference.parameter_key})" if n.reference else ""
        frost = f"  [{n.frost_category}]" if n.frost_category else ""
        instant = f" @{n.instant}" if n.instant else ""
        lines.append(
            f"  {n.load_case}{instant:<10} {n.label}{side} ({n.block}), {n.statistic}: {n.value:.4g} {n.unit}"
            f"{reference}{frost}"
        )
    return lines


def _result_lines(result: PipelineResult) -> list[str]:
    lines = [
        f"{result.purpose} at the {result.detail_level} level: {result.mesh.dof} DOF, "
        f"{result.timings_s.get('total', 0):.1f} s{' (stored result reused)' if result.cached else ''}",
        "key numbers:",
        *_key_number_lines(result.result.key_numbers),
    ]
    lines += [f"  locked: {r.result_type} ({r.block}): {r.message}" for r in result.locked_results]
    worst = max((f.relative_error for f in result.convergence.force_balance), default=0.0)
    energy = max((e.relative_error for e in result.convergence.energy_balance), default=0.0)
    lines.append(f"convergence: force balance {worst:.1e}, energy {energy:.1e}, all checks passed")
    return lines


def _write_out(store: ContentStore, result: PipelineResult, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    for asset in result.result.visual_assets:
        (out / asset.name).write_bytes(store.get_bytes(result.input_hash, asset.name))


def validate_command(args: argparse.Namespace, out: Output) -> int | None:
    data_dir = ensure_data_folder() if args.scenario_id else None
    if data_dir is not None:
        with repos_session(data_dir) as repos:
            scenario, _, revision = load_scenario(args, repos)
    else:
        scenario, _, revision = load_scenario(args)
    result = check_validity(scenario, mode="fast" if args.fast else "full", scenario_revision=revision)
    lines = ["valid" if result.ok else "NOT valid"]
    lines += [
        f"  {p.severity} {p.code} ({p.block}): {p.message}" + (f" at {p.location_mm}" if p.location_mm else "")
        for p in result.problems
    ]
    out.result(result.model_dump(mode="json"), "\n".join(lines))
    return None if result.ok else 1


def _estimate(scenario: Scenario, detail: str, build_id: str, per_axis: bool = True) -> RunEstimate:
    return estimate_run(scenario, detail, solver_build_id=build_id, per_axis=per_axis)  # type: ignore[arg-type]


def estimate_command(args: argparse.Namespace, out: Output) -> int | None:
    data_dir = ensure_data_folder()
    with repos_session(data_dir) as repos:
        scenario, _, _ = load_scenario(args, repos)
    detail = args.detail or scenario.detail_level
    estimate = _estimate(scenario, detail, solver_build().build_id)
    lines = [
        f"{estimate.detail_level}: {estimate.dof} DOF, {estimate.unit_cases} unit load case(s)",
        f"time about {estimate.time_s / 60:.1f} min, memory {estimate.memory_gb:.1f} GB, "
        f"disk {estimate.disk_gb:.2f} GB",
        "fits in memory" if estimate.fits else "does NOT fit in memory",
        *(f"warning: {w}" for w in estimate.warnings),
    ]
    out.result(estimate.model_dump(mode="json"), "\n".join(lines))
    return None


def _prechecks(scenario: Scenario, repos: Repos) -> None:
    validity = check_validity(scenario, mode="full")
    if not validity.ok:
        raise Refused(
            "The scenario is not valid: " + " ".join(p.message for p in validity.errors),
            code="run_refused",
            problems=[p.model_dump(mode="json") for p in validity.errors],
        )
    require_open(check_gate(scenario, repo=repos.capabilities))


def run_command(args: argparse.Namespace, out: Output) -> int | None:
    data_dir = ensure_data_folder()
    build = solver_build()
    store = ContentStore()
    with repos_session(data_dir) as repos:
        scenario, scenario_id, revision = load_scenario(args, repos)
        detail = args.detail or scenario.detail_level
        inputs = library_inputs(scenario, LibraryService(repos))
        _prechecks(scenario, repos)
        expected: Estimate | None = None
        try:
            estimate = _estimate(scenario, detail, build.build_id)
            require_fits(estimate)
            expected = Estimate(
                dof=estimate.dof, time_s=estimate.time_s, memory_gb=estimate.memory_gb, disk_gb=estimate.disk_gb
            )
        except NotFound as exc:
            out.stderr.write(f"warning: no estimate: {exc}\n")
        run = new_run(
            scenario,
            inputs,
            detail_level=detail,
            solver_build_id=build.build_id,
            scenario_id=scenario_id,
            scenario_revision=revision,
            estimate=expected,
        )
        repos.runs.add(run.model_copy(update={"status": "running", "started_at": utc_now()}))
        try:
            result = run_pipeline(
                inputs.request(scenario, detail, "run"),
                capabilities=repos.capabilities,
                store=store,
                work_root=data_dir / "work",
                solver_build_id=build.build_id,
                ccx=build.path,
                threads=args.threads or heavy_threads(),
                use_cache=not args.no_cache,
                progress=None if out.json_mode else _progress(out),
            )
        except (PipelineFailed, Refused) as exc:
            reason = exc.reason if isinstance(exc, PipelineFailed) else exc.message
            repos.runs.update(failed_run(run, reason))
            raise
        repos.runs.update(succeeded_run(run, result))
        if not result.cached:
            refit(
                EstimateSample(
                    dof=result.actual.dof,
                    cases=len({f.load_case for f in result.convergence.force_balance}),
                    time_s=result.timings_s.get("solve", result.actual.time_s),
                    memory_bytes=int(result.actual.peak_memory_gb * 1e9),
                    disk_bytes=max(1, int(result.actual.disk_gb * 1e9)),
                    source="run",
                ),
                build.build_id,
            )
    if args.out:
        _write_out(store, result, args.out)
    data = {"run_id": run.id, **result.model_dump(mode="json")}
    out.result(data, "\n".join([f"run {run.id}", *_result_lines(result)]))
    return None


def preview_command(args: argparse.Namespace, out: Output) -> int | None:
    data_dir = ensure_data_folder()
    build = solver_build()
    store = ContentStore()
    with repos_session(data_dir) as repos:
        scenario, _, _ = load_scenario(args, repos)
        inputs = library_inputs(scenario, LibraryService(repos))
        result = run_pipeline(
            inputs.request(scenario, "preview", "preview", args.loads),
            capabilities=repos.capabilities,
            store=store,
            work_root=data_dir / "work",
            solver_build_id=build.build_id,
            ccx=build.path,
            threads=args.threads or preview_threads(),
            use_cache=not args.no_cache,
            progress=None if out.json_mode else _progress(out),
        )
    if args.out:
        _write_out(store, result, args.out)
    out.result(result.model_dump(mode="json"), "\n".join(["PREVIEW (not publishable)", *_result_lines(result)]))
    return None


def _progress(out: Output) -> Any:
    def report(event: Any) -> None:
        remaining = f", about {event.remaining_s:.0f} s left" if event.remaining_s is not None else ""
        out.stderr.write(f"{event.percent:5.1f}% {event.message}{remaining}\n")
        out.stderr.flush()

    return report
