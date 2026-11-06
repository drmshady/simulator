"""The `run` and `preview` job handlers (task T088), with the engine pipeline stubbed: records, events and refits."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dentalsim.clock import utc_now
from dentalsim.estimate.model import load_coefficients
from dentalsim.model.results import ResultSet
from dentalsim.model.run import Actual, Convergence, ForceBalance
from dentalsim.pipeline import MeshSummary, PipelineFailed, PipelineResult, Versions
from dentalsim.runs import library_inputs, new_run
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import seed_library
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.worker.handlers import JobContext, solve


def fake_result(key: str = "a" * 64) -> PipelineResult:
    return PipelineResult(
        input_hash=key,
        purpose="run",
        detail_level="standard",
        result=ResultSet(input_hash=key, fields=[], combined_cases=["axial"], key_numbers=[], visual_assets=[]),
        convergence=Convergence(
            force_balance=[ForceBalance(load_case="U1", applied_n=(1, 0, 0), reaction_n=(-1, 0, 0), relative_error=0)]
        ),
        actual=Actual(dof=200_000, time_s=100.0, peak_memory_gb=2.0, disk_gb=0.1),
        mesh=MeshSummary(dof=200_000, elements=50_000, min_quality=0.1, straightened=0),
        locked_results=[],
        library_versions=[],
        versions=Versions(
            engine_version="0.1.0",
            engine_source_hash="f" * 64,
            conventions_version="1.1.0",
            solver_build_id="ccx-test",
            gmsh_version="4.15.2",
            dev_build=True,
        ),  # fmt: skip
        timings_s={"solve": 80.0, "total": 100.0},
    )


@pytest.fixture
def repos(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    seed_library(opened)
    monkeypatch.setattr(solve, "solver_build", lambda: SimpleNamespace(build_id="ccx-test", path=None))
    yield opened
    opened.close()


def queued_run(repos: SqliteRepos) -> tuple[str, Any]:
    from dentalsim.library.service import LibraryService

    scenario = open_template(builtin_template("single_crown_implant"), LibraryService(repos)).scenario
    inputs = library_inputs(scenario, LibraryService(repos))
    run = new_run(scenario, inputs, detail_level="standard", solver_build_id="ccx-test")
    repos.runs.add(run)
    job = repos.jobs.enqueue("run", "heavy", {"run_id": run.id}, target_id=run.id)
    return run.id, job


def test_a_succeeded_run_is_recorded_announced_and_refits_the_estimates(
    repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, job = queued_run(repos)
    seen: dict[str, Any] = {}

    def pipeline(request: Any, **kwargs: Any) -> PipelineResult:
        seen["purpose"], seen["threads"] = request.purpose, kwargs["threads"]
        assert callable(kwargs["cancel"]) and callable(kwargs["watchdog"])
        return fake_result()

    monkeypatch.setattr(solve, "run_pipeline", pipeline)
    solve.run_job(JobContext(job, repos, threads=8))
    run = repos.runs.require(run_id)
    assert run.status == "succeeded" and run.result_ref == "a" * 64 and run.started_at is not None
    assert seen == {"purpose": "run", "threads": 8}
    assert [e.payload for e in repos.events.since(0) if e.type == "run.finished"] == [
        {"run_id": run_id, "status": "succeeded"}
    ]
    samples = load_coefficients().builds["ccx-test"].samples
    assert samples[-1].dof == 200_000 and samples[-1].source == "run"


def test_a_failed_run_keeps_its_reason_and_no_result(repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, job = queued_run(repos)

    def pipeline(request: Any, **kwargs: Any) -> PipelineResult:
        raise PipelineFailed("the solution failed its convergence checks", stage="checks")

    monkeypatch.setattr(solve, "run_pipeline", pipeline)
    with pytest.raises(PipelineFailed):
        solve.run_job(JobContext(job, repos, threads=8))
    run = repos.runs.require(run_id)
    assert run.status == "failed" and run.result_ref is None
    assert run.failure_reason == "the solution failed its convergence checks"
    finished = [e.payload for e in repos.events.since(0) if e.type == "run.finished"]
    assert finished[-1]["status"] == "failed"


def test_an_interrupted_run_is_queued_again(repos: SqliteRepos) -> None:
    run_id, job = queued_run(repos)
    repos.runs.set_status(run_id, "running", started=True)
    solve.run_requeued(job, repos)
    run = repos.runs.require(run_id)
    assert run.status == "queued" and run.started_at is None


def test_a_preview_ends_with_preview_ready(repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch) -> None:
    from dentalsim.library.service import LibraryService

    scenario = open_template(builtin_template("single_crown_implant"), LibraryService(repos)).scenario
    preview = repos.previews.add(input_hash="b" * 64, scenario_id=None, scenario_revision=None, accuracy_note=None)
    job = repos.jobs.enqueue(
        "preview", "preview", {"scenario": scenario.model_dump(mode="json"), "load_ids": ["oblique"]},
        target_id=preview.id,
    )  # fmt: skip
    seen: dict[str, Any] = {}

    def pipeline(request: Any, **kwargs: Any) -> PipelineResult:
        seen["load_ids"], seen["purpose"] = request.load_ids, request.purpose
        return fake_result("b" * 64)

    monkeypatch.setattr(solve, "run_pipeline", pipeline)
    solve.preview_job(JobContext(job, repos, threads=2))
    stored = repos.previews.get(preview.id)
    assert stored is not None and stored.status == "succeeded" and stored.result_ref == "b" * 64
    assert seen == {"load_ids": ["oblique"], "purpose": "preview"}
    ready = [e.payload for e in repos.events.since(0) if e.type == "preview.ready"]
    assert ready == [{"preview_id": preview.id, "scenario_id": None, "status": "succeeded"}]
    assert utc_now() >= stored.created_at
