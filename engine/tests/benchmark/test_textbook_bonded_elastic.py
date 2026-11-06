"""Every textbook case passes on CalculiX and agrees with the scikit-fem cross-check (task T075; FR-011a, FR-035).

The case files must be committed: the runner refuses them otherwise, so this test fails until the owner has
approved and committed them (task T074). Nothing is written to the studio database; `dentalsim verify` does that.
"""

from __future__ import annotations

from pathlib import Path

from dentalsim.commands.verify import summary_lines
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.solvers.skfem.crosscheck import solve_skfem
from dentalsim.threads import heavy_threads
from dentalsim.verification.cases import load_cases, select_cases
from dentalsim.verification.runner import calculix_solver, run_verification


def test_every_textbook_case_passes_and_agrees_with_the_cross_check(tmp_path: Path) -> None:
    cases = select_cases(load_cases(), physics="bonded_elastic")
    report = run_verification(
        cases,
        solver=calculix_solver(threads=heavy_threads()),
        solver_build_id=solver_build().build_id,
        work_dir=tmp_path / "work",
        crosscheck=solve_skfem,
    )
    assert report.overall == "pass", "\n".join(summary_lines(report))
    assert {c.case.id for c in cases} <= set(report.case_ids)
    assert report.preview_accuracy.max_relative_difference is not None
    (bonded,) = report.capabilities
    assert bonded.status == "ready"
