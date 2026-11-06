"""`GET /api/system` and `GET /api/conventions` (T046)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.conventions.loader import get_loaded_conventions
from dentalsim.errors import SolverNotFound
from dentalsim.persistence import JobLane, JobRecord, JobStatus
from dentalsim.testing import FIXED_TIME
from dentalsim.version import VersionInfo
from dentalsim_studio.api import system
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.db import open_repos
from dentalsim_studio.db.repos.jobs import SqliteJobRepo

PASSWORD = "correct horse battery"
VERSIONS = VersionInfo(
    engine_version="0.1.0+gabc1234",
    engine_commit="abc1234",
    engine_source_hash="f" * 64,
    dev_build=True,
    gmsh_version="4.15.2",
)


def _job(n: int, lane: JobLane, status: JobStatus) -> JobRecord:
    kind = "run" if lane == "heavy" else "preview"
    return JobRecord(id=f"j{n}", lane=lane, kind=kind, payload={}, position=n, status=status, created_at=FIXED_TIME)


JOBS = [
    _job(1, "heavy", "running"),
    _job(2, "heavy", "queued"),
    _job(3, "heavy", "queued"),
    _job(4, "preview", "queued"),
]


@pytest.fixture
def app(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    repos = open_repos(data_dir)
    set_password(repos, PASSWORD)
    repos.close()
    monkeypatch.setattr(system, "version_info", lambda: VERSIONS)
    monkeypatch.setattr(system, "lookup_solver_build_id", lambda: "ccx-2.23-pardiso-sha256:" + "a" * 64)

    def list_jobs(
        self: SqliteJobRepo, *, lane: JobLane | None = None, statuses: Sequence[JobStatus] | None = None
    ) -> list[JobRecord]:
        return [j for j in JOBS if (lane is None or j.lane == lane) and (statuses is None or j.status in statuses)]

    monkeypatch.setattr(SqliteJobRepo, "list_jobs", list_jobs)
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield test_client


def test_system_has_exactly_the_contract_shape(client: TestClient, data_dir: Path) -> None:
    response = client.get("/api/system")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "engine_version",
        "dev_build",
        "engine_source_hash",
        "gmsh_version",
        "solver_build_id",
        "solver_error",
        "conventions_version",
        "conventions_hash",
        "memory",
        "disk",
        "queue",
    }
    loaded = get_loaded_conventions()
    assert body["engine_version"] == "0.1.0+gabc1234"
    assert body["dev_build"] is True
    assert body["engine_source_hash"] == "f" * 64
    assert body["gmsh_version"] == "4.15.2"
    assert body["solver_build_id"] == "ccx-2.23-pardiso-sha256:" + "a" * 64
    assert body["solver_error"] is None
    assert body["conventions_version"] == loaded.version
    assert body["conventions_hash"] == loaded.content_hash
    assert set(body["memory"]) == {"total_gb", "available_gb", "used_percent"}
    assert body["memory"]["total_gb"] > 0
    assert 0 <= body["memory"]["available_gb"] <= body["memory"]["total_gb"]
    assert 0 <= body["memory"]["used_percent"] <= 100
    assert body["disk"]["path"] == str(data_dir)
    assert set(body["disk"]) == {"path", "total_gb", "free_gb"}
    assert 0 <= body["disk"]["free_gb"] <= body["disk"]["total_gb"]
    assert body["queue"] == {"heavy": {"queued": 2, "running": 1}, "preview": {"queued": 1, "running": 0}}


def test_missing_solver_does_not_fail_the_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> str:
        raise SolverNotFound("No CalculiX binary is configured: set DENTALSIM_CCX.")

    monkeypatch.setattr(system, "lookup_solver_build_id", missing)
    body = client.get("/api/system").json()
    assert body["solver_build_id"] is None
    assert body["solver_error"] == "No CalculiX binary is configured: set DENTALSIM_CCX."


def test_any_solver_check_error_is_reported_in_plain_words(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        raise RuntimeError("ccx.exe exited with code 3")

    monkeypatch.setattr(system, "lookup_solver_build_id", broken)
    response = client.get("/api/system")
    assert response.status_code == 200
    assert response.json()["solver_build_id"] is None
    assert response.json()["solver_error"] == "The solver could not be checked: ccx.exe exited with code 3"


def test_solver_lookup_uses_the_build_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = SimpleNamespace(solver_build=lambda: SimpleNamespace(build_id="ccx-2.23-pardiso-sha256:abc"))
    monkeypatch.setattr(system.importlib, "import_module", lambda name: fake_module)
    assert system.solver_status() == ("ccx-2.23-pardiso-sha256:abc", None)


def test_queue_summary_counts_queued_and_running_by_lane() -> None:
    finished = _job(5, "heavy", "succeeded")

    class FakeJobs:
        def list_jobs(self, *, lane: Any = None, statuses: Any = None) -> list[JobRecord]:
            return [j for j in [*JOBS, finished] if statuses is None or j.status in statuses]

    summary = system.queue_summary(SimpleNamespace(jobs=FakeJobs()))  # type: ignore[arg-type]
    assert summary.model_dump() == {"heavy": {"queued": 2, "running": 1}, "preview": {"queued": 1, "running": 0}}


def test_conventions_endpoint_returns_the_parsed_block(client: TestClient) -> None:
    loaded = get_loaded_conventions()
    response = client.get("/api/conventions")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == loaded.version
    assert body["content_hash"] == loaded.content_hash
    expected = loaded.conventions.model_dump(mode="json", by_alias=True)
    assert {key: value for key, value in body.items() if key != "content_hash"} == expected
    assert body["teaching_notice"] == loaded.conventions.teaching_notice
    assert "crestal_ring" in body["regions"]


def test_system_routes_need_a_session(app: FastAPI) -> None:
    with TestClient(app, base_url="https://testserver") as anonymous:
        assert anonymous.get("/api/system").status_code == 401
        assert anonymous.get("/api/conventions").status_code == 401
