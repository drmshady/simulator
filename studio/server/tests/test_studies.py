"""Studies through the studio API and the worker's run handler (task T127; Story 4; FR-020, FR-021, FR-022, FR-056).

The engine pipeline is stood in for: each run "solves" at once and stores a result whose key number follows the run's
settings, so the queue order, failures, restart recovery and the spread summary are checked on real records.

- A 6-run sweep queues in order and runs one heavy run at a time.
- A failing run records its reason, and the rest continue.
- A worker restart mid-study requeues the interrupted run at the front.
- Sensitivity reports the spread; a load-only axis reuses the first solve; a study can be cancelled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.model.results import KeyNumber, ResultSet
from dentalsim.model.run import Actual, Convergence, ForceBalance
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import DesignValue, LibraryValue
from dentalsim.persistence import JobRecord
from dentalsim.pipeline import RESULT, MeshSummary, PipelineFailed, PipelineResult, Versions
from dentalsim.store.cas import ContentStore
from dentalsim.testing import seed_library
from dentalsim_studio.api import runs, studies
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.jobs import queue
from dentalsim_studio.worker.handlers import JobContext, solve
from dentalsim_studio.worker.main import job_outcome, recover_interrupted

from .test_api_library import make_ready

PASSWORD = "correct horse battery"
BUILD = "ccx-test-build"
FAILING_LENGTH = 9.0


@pytest.fixture
def app(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    repos = open_repos(data_dir)
    set_password(repos, PASSWORD)
    seed_library(repos)
    repos.close()
    make_ready(data_dir)
    build = SimpleNamespace(build_id=BUILD, path=None)
    for module in (runs, studies, solve):
        monkeypatch.setattr(module, "solver_build", lambda: build)
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield test_client


@pytest.fixture
def base(client: TestClient) -> dict[str, Any]:
    project = client.post("/api/projects", json={"name": "Overnight studies"}).json()
    created = client.post("/api/scenarios", json={"project_id": project["id"], "name": "Single crown",
                                                  "from": {"template_id": "single_crown_implant"}})  # fmt: skip
    assert created.status_code == 201, created.json()
    return dict(created.json()["scenario"])


class FakePipeline:
    """Stands in for `run_pipeline`: stores a result whose crestal strain follows the implant length, the cortical
    modulus and the oblique force, and fails the run of `FAILING_LENGTH` mm."""

    def __init__(self) -> None:
        self.unit_sources: list[str | None] = []

    def __call__(self, request: Any, **kwargs: Any) -> PipelineResult:
        scenario: Scenario = request.scenario
        store: ContentStore = kwargs["store"]
        self.unit_sources.append(kwargs.get("unit_source"))
        length = self._design(scenario, "implant-1", "length_mm")
        if length == FAILING_LENGTH:
            raise PipelineFailed("the mesh could not be made: a surface is too thin", stage="mesh")
        modulus = self._library(scenario, "jaw", "cortical_E")
        force = next(load for load in scenario.loads if load.id == "oblique").force.value
        strain = 1000 * (10 / length) * (13700 / modulus) * (force / 601.83)
        key = hashlib.sha256(json.dumps(scenario.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        result = PipelineResult(
            input_hash=key, purpose="run", detail_level=request.detail_level,
            result=ResultSet(input_hash=key, fields=[], combined_cases=["oblique"], visual_assets=[], key_numbers=[
                KeyNumber(id="crestal_mean", label="Crestal strain", load_case="oblique", block="implant-1",
                          region="crestal", statistic="mean", value=strain, unit="µε"),
            ]),
            convergence=Convergence(force_balance=[ForceBalance(load_case="U1", applied_n=(1, 0, 0),
                                                                reaction_n=(-1, 0, 0), relative_error=0)]),
            actual=Actual(dof=1000, time_s=1.0, peak_memory_gb=0.1, disk_gb=0.0),
            mesh=MeshSummary(dof=1000, elements=100, min_quality=0.5, straightened=0), locked_results=[],
            library_versions=[],
            versions=Versions(engine_version="0.1.0", engine_source_hash="f" * 64, conventions_version="1.1.0",
                              solver_build_id=BUILD, gmsh_version="4.15.2", dev_build=True),
            timings_s={"solve": 1.0, "total": 1.0}, unit_source=kwargs.get("unit_source"),
        )  # fmt: skip
        store.put_json(key, RESULT, result.model_dump(mode="json"))
        store.complete(key)
        return result

    @staticmethod
    def _design(scenario: Scenario, block: str, setting: str) -> float:
        value = scenario.block(block).settings[setting]
        assert isinstance(value, DesignValue) and isinstance(value.value, int | float)
        return float(value.value)

    @staticmethod
    def _library(scenario: Scenario, block: str, setting: str) -> float:
        value = scenario.block(block).settings[setting]
        assert isinstance(value, LibraryValue)
        return value.value


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> FakePipeline:
    fake = FakePipeline()
    monkeypatch.setattr(solve, "run_pipeline", fake)
    return fake


@pytest.fixture
def worker(data_dir: Path) -> Iterator[SqliteRepos]:
    """The worker's database connection; `work` runs jobs as its heavy lane does."""
    repos = open_repos(data_dir)
    yield repos
    repos.close()


def run_one(repos: SqliteRepos, job: JobRecord) -> None:
    """Run a claimed job through the run handler and finish it, as the worker's lane loop does."""
    context = JobContext(job, repos, threads=8)
    error: BaseException | None = None
    try:
        solve.run_job(context)
    except BaseException as exc:
        error = exc
    status, reason = job_outcome(context, error)
    assert status is not None
    queue.finish(repos, job.id, status, failure_reason=reason)


def work(repos: SqliteRepos, *, limit: int = 50) -> list[str]:
    """Run the heavy lane until it is empty; the targets in the order they ran. Never two at once."""
    order: list[str] = []
    for _ in range(limit):
        job = queue.claim(repos, "heavy")
        if job is None:
            break
        assert queue.claim(repos, "heavy") is None  # one heavy run at a time
        assert job.target_id is not None
        order.append(job.target_id)
        run_one(repos, job)
    return order


def sweep(client: TestClient, base: dict[str, Any], axes: list[dict[str, Any]]) -> dict[str, Any]:
    body = {"base_scenario_id": base["id"], "name": "Lengths", "kind": "sweep", "axes": axes}
    response = client.post("/api/studies", json=body)
    assert response.status_code == 202, response.json()
    return dict(response.json())


LENGTHS = [8.0, FAILING_LENGTH, 10.0, 11.5, 13.0, 15.0]


def test_a_six_run_sweep_queues_in_order_and_runs_one_at_a_time(
    client: TestClient, base: dict[str, Any], worker: SqliteRepos, pipeline: FakePipeline
) -> None:
    study = sweep(client, base, [{"path": "implant-1.length", "values": LENGTHS}])
    assert study["status"] == "queued" and study["progress"] == {"total": 6, "done": 0, "failed": 0, "running": 0}
    assert [r["values"]["implant-1.length_mm"] for r in study["runs"]] == LENGTHS
    assert study["estimate"]["runs"] == 6 and study["estimate"]["warnings"]  # no coefficients yet: said so
    run_ids = [r["run_id"] for r in study["runs"]]
    heavy = client.get("/api/queue").json()["heavy"]
    assert [j["target_id"] for j in heavy] == run_ids and {j["kind"] for j in heavy} == {"run"}

    assert work(worker) == run_ids
    assert pipeline.unit_sources == [None] * 6
    done = client.get(f"/api/studies/{study['id']}").json()
    assert done["status"] == "finished_with_failures"
    assert done["progress"] == {"total": 6, "done": 6, "failed": 1, "running": 0}
    project = client.get(f"/api/projects/{base['project_id']}").json()
    assert [(s["id"], s["runs"], s["status"]) for s in project["studies"]] == [
        (study["id"], 6, "finished_with_failures")
    ]
    progress = [e for e in worker.events.since(0) if e.type == "study.progress"]
    assert progress[0].payload["status"] == "queued" and progress[-1].payload["done"] == 6


def test_a_failing_run_records_its_reason_and_the_rest_continue(
    client: TestClient, base: dict[str, Any], worker: SqliteRepos, pipeline: FakePipeline
) -> None:
    study = sweep(client, base, [{"path": "implant-1.length", "values": LENGTHS}])
    work(worker)
    done = client.get(f"/api/studies/{study['id']}").json()
    statuses = [(r["values"]["implant-1.length_mm"], r["status"]) for r in done["runs"]]
    assert statuses == [(length, "failed" if length == FAILING_LENGTH else "succeeded") for length in LENGTHS]
    failed = done["runs"][1]
    assert failed["failure_reason"] == "the mesh could not be made: a surface is too thin"
    assert client.get(f"/api/runs/{failed['run_id']}/results").status_code == 404  # no partial result
    summary = done["summary"]
    assert (summary["runs_succeeded"], summary["runs_failed"], summary["runs_pending"]) == (5, 1, 0)
    (row,) = summary["axes"][0]["key_numbers"]
    assert [m["value"] for m in row["by_value"]] == [8.0, 10.0, 11.5, 13.0, 15.0]  # the failed length is left out
    assert row["reference"] == pytest.approx(1250.0)  # the first length, 8 mm
    assert (row["min"], row["max"]) == (pytest.approx(10000 / 15), pytest.approx(1250.0))


def test_a_worker_restart_mid_study_requeues_the_interrupted_run_at_the_front(
    client: TestClient, base: dict[str, Any], data_dir: Path, pipeline: FakePipeline
) -> None:
    study = sweep(client, base, [{"path": "implant-1.length", "values": [8.0, 9.5, 10.0, 11.5, 13.0, 14.0]}])
    run_ids = [r["run_id"] for r in study["runs"]]
    first = open_repos(data_dir)
    run_one(first, queue.claim(first, "heavy"))  # type: ignore[arg-type]
    interrupted = queue.claim(first, "heavy")
    assert interrupted is not None and interrupted.target_id == run_ids[1]
    first.runs.set_status(run_ids[1], "running", started=True)  # the solve is under way when the PC restarts
    first.close()

    restarted = open_repos(data_dir)
    try:
        recover_interrupted(restarted)
        assert restarted.runs.require(run_ids[1]).status == "queued"
        assert client.get(f"/api/studies/{study['id']}").json()["status"] == "running"
        assert work(restarted) == run_ids[1:]  # the interrupted run first, then the rest in order
    finally:
        restarted.close()
    done = client.get(f"/api/studies/{study['id']}").json()
    assert done["status"] == "finished" and {r["status"] for r in done["runs"]} == {"succeeded"}


def test_sensitivity_reports_the_spread(
    client: TestClient, base: dict[str, Any], worker: SqliteRepos, pipeline: FakePipeline
) -> None:
    body = {"base_scenario_id": base["id"], "kind": "sensitivity", "parameter": "load:oblique.force"}
    response = client.post("/api/studies", json=body)
    assert response.status_code == 202, response.json()
    study = response.json()
    (axis,) = study["axes"]
    low, typical, high = axis["values"]
    assert axis["path"] == "load:oblique.force" and low < typical == 601.83 < high
    assert [r["label"].split(":")[0] for r in study["runs"]] == ["minimum", "typical", "maximum"]
    work(worker)
    done = client.get(f"/api/studies/{study['id']}").json()
    assert done["status"] == "finished"
    (spread,) = done["summary"]["axes"]
    assert spread["reference_value"] == typical
    (row,) = spread["key_numbers"]
    assert row["reference"] == pytest.approx(1000.0)  # the typical run
    assert row["min"] == pytest.approx(1000 * low / typical) and row["max"] == pytest.approx(1000 * high / typical)
    assert row["percent_change"] == pytest.approx(100 * (high - low) / typical)


def test_a_load_only_axis_reuses_the_first_solve(
    client: TestClient, base: dict[str, Any], worker: SqliteRepos, pipeline: FakePipeline
) -> None:
    study = sweep(client, base, [{"path": "load:oblique.force", "values": [300, 600, 900]}])
    first = study["runs"][0]["run_id"]
    assert [r["reuse_of"] for r in study["runs"]] == [None, first, first]
    assert study["estimate"]["solves"] == 1
    work(worker)
    result_ref = worker.runs.require(first).result_ref
    assert pipeline.unit_sources == [None, result_ref, result_ref]
    (row,) = client.get(f"/api/studies/{study['id']}").json()["summary"]["axes"][0]["key_numbers"]
    assert [m["mean"] for m in row["by_value"]] == pytest.approx([1000 * 300 / 601.83, 1000 * 600 / 601.83,
                                                                  1000 * 900 / 601.83])  # fmt: skip


def test_a_study_can_be_cancelled_and_refuses_bad_settings(
    client: TestClient, base: dict[str, Any], worker: SqliteRepos, pipeline: FakePipeline
) -> None:
    study = sweep(client, base, [{"path": "implant-1.length", "values": [8.0, 10.0, 11.5]}])
    run_one(worker, queue.claim(worker, "heavy"))  # type: ignore[arg-type]
    cancelled = client.post(f"/api/studies/{study['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert [r["status"] for r in cancelled["runs"]] == ["succeeded", "cancelled", "cancelled"]
    assert client.get("/api/queue").json()["heavy"] == []

    unknown = client.post("/api/studies", json={"base_scenario_id": base["id"], "kind": "sweep",
                                                "axes": [{"path": "implant-1.colour", "values": [1]}]})  # fmt: skip
    assert unknown.status_code == 422 and unknown.json()["code"] == "validation_failed"
    design = client.post("/api/studies", json={"base_scenario_id": base["id"], "kind": "sensitivity",
                                               "parameter": "implant-1.length"})  # fmt: skip
    assert design.status_code == 422 and "not a library setting" in design.json()["message"]
    too_long = {"base_scenario_id": base["id"], "kind": "sweep",
                "axes": [{"path": "implant-1.length", "values": [10, 40]}]}  # fmt: skip
    refused = client.post("/api/studies", json=too_long)
    assert refused.status_code == 409 and refused.json()["code"] == "run_refused"
    assert client.get("/api/studies/missing").status_code == 404
