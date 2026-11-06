"""`dentalsim solver check`: runs the configured CalculiX binary once and prints its build (contracts/cli.md).

Without a configured binary the command fails with exit code 2 and tells the owner to set `DENTALSIM_CCX`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dentalsim.solvers.calculix.build import solver_build

if TYPE_CHECKING:
    from dentalsim.cli import Output


def register(subparsers: Any) -> None:
    solver = subparsers.add_parser("solver", help="CalculiX solver commands")
    actions = solver.add_subparsers(dest="action", metavar="action", required=True)
    check = actions.add_parser(
        "check", help="run the configured CalculiX binary once and print its version, equation solver and SHA-256"
    )
    check.add_argument("--ccx", type=Path, default=None, help="path of the ccx binary (default: DENTALSIM_CCX)")
    check.set_defaults(handler=check_command)


def check_command(args: argparse.Namespace, out: Output) -> int | None:
    build = solver_build(args.ccx)
    data = {
        "path": str(build.path),
        "version": build.version,
        "equation_solver": build.equation_solver,
        "preferred_solver_ok": build.preferred_solver_ok,
        "proprietary_libraries": list(build.proprietary_libraries),
        "sha256": build.sha256,
        "build_id": build.build_id,
        "notes": list(build.notes),
    }
    solver = build.equation_solver.upper() + ("" if build.preferred_solver_ok else " (fallback: PaStiX not available)")
    lines = [
        f"solver:   {build.path}",
        f"version:  {build.version}",
        f"equation solver: {solver}",
        f"SHA-256:  {build.sha256}",
        f"build id: {build.build_id}",
        *(f"note:     {note}" for note in build.notes),
    ]
    out.result(data, "\n".join(lines))
    return None
