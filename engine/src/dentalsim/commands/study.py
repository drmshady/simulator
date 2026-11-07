"""`dentalsim study <study.yaml>` (contracts/cli.md; task T125; FR-020, FR-021, FR-057).

Queues a sweep or sensitivity study in the studio database, exactly as `POST /api/studies` does
(`dentalsim.study.queue_study`); the studio's worker then runs its runs one at a time. The file is a
`dentalsim.study/1` document:

    format: dentalsim.study/1
    name: Implant length and bone density
    base_scenario_id: 01J...          # a studio scenario
    kind: sweep
    detail_level: standard
    axes:
      - {path: implant-1.length_mm, values: [8, 9, 10, 11.5, 13, 15]}
      - {path: jaw.density_class, values: [D1, D2, D3]}

or, for a sensitivity study, `kind: sensitivity` with `parameter: jaw.cancellous_E` instead of `axes`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from dentalsim.config import ensure_data_folder
from dentalsim.errors import Refused
from dentalsim.persistence import StudyRecord, repos_session
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.study import StudyDefinition, StudyEstimate, queue_study

if TYPE_CHECKING:
    from dentalsim.cli import Output


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("study", help="queue a sweep or sensitivity study (dentalsim.study/1)")
    parser.add_argument("study", type=Path, help="the study file (YAML)")
    parser.set_defaults(handler=study_command)


def read_study(path: Path) -> StudyDefinition:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return StudyDefinition.model_validate(data)
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f"{path} cannot be read: {exc}", code="validation_failed") from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'study'}: {e['msg']}" for e in exc.errors())
        raise Refused(f"{path} is not a valid study: {problems}", code="validation_failed") from exc


def _hours(seconds: float | None) -> str:
    return "unknown" if seconds is None else f"{seconds / 3600:.1f} h"


def study_text(study: StudyRecord) -> str:
    estimate = None if study.estimate is None else StudyEstimate.model_validate(study.estimate)
    lines = [f"study {study.id} queued: {study.kind}, {len(study.points)} runs at {study.detail_level} detail"]
    for index, point in enumerate(study.points, start=1):
        reuse = "  (reuses the unit load cases of an earlier run)" if point.reuse_of else ""
        lines.append(f"  {index:>3}. {point.label}  run {point.run_id}{reuse}")
    if estimate is not None:
        lines.append(
            f"expected: {_hours(estimate.time_s)} of solving for {estimate.solves} solve(s); "
            + ("" if estimate.memory_gb is None else f"at most {estimate.memory_gb:.1f} GB of memory; ")
            + ("" if estimate.disk_gb is None else f"{estimate.disk_gb:.1f} GB of disk")
        )
        lines += [f"warning: {warning}" for warning in estimate.warnings]
    lines.append("The studio's worker runs them one at a time; follow them on the study's page in the studio.")
    return "\n".join(lines)


def study_command(args: argparse.Namespace, out: Output) -> None:
    definition = read_study(args.study)
    data_dir = ensure_data_folder()
    build = solver_build()
    with repos_session(data_dir) as repos:
        study = queue_study(definition, repos, solver_build_id=build.build_id)
    out.result(study.model_dump(mode="json"), study_text(study))
