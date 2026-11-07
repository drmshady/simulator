"""Organising and comparing runs through the studio API (task T122; Story 3; FR-027, FR-031 to FR-034; SC-008).

The runs are finished without solving: each gets a small stored result (key numbers and one `.glb` per part, written
with the engine's glTF writer), so compare, reopen and search work on real stored data.

- A duplicate keeps its link, and the diff lists the one changed setting.
- Compare returns shared scales, the differing settings and the key-number differences, from stored results only.
- Search by tag, by note text and by `implant-1.length:=:8` finds the scenario.
- Reopening a run returns its results without creating a job.
- Notes and tags on projects, scenarios and runs; backup and restore are queued as jobs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.library.service import LibraryService
from dentalsim.model.results import KeyNumber, ResultSet, VisualAsset
from dentalsim.model.run import Actual, Convergence, ForceBalance
from dentalsim.pipeline import RESULT, MeshSummary, PipelineResult, Versions
from dentalsim.post.gltf import write_glb
from dentalsim.runs import library_inputs, new_run, succeeded_run
from dentalsim.store.cas import ContentStore
from dentalsim.testing import seed_library
from dentalsim_studio.api import runs
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.db import open_repos

from .test_api_library import make_ready

PASSWORD = "correct horse battery"
TRIANGLE = np.array([[0, 1, 2]])


@pytest.fixture
def app(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    repos = open_repos(data_dir)
    set_password(repos, PASSWORD)
    seed_library(repos)
    repos.close()
    make_ready(data_dir)
    monkeypatch.setattr(runs, "solver_build", lambda: SimpleNamespace(build_id="ccx-test-build", path=None))
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield test_client


def glb(strain: float, displacement: float, size: float = 10.0) -> bytes:
    positions = np.array([[0.0, 0.0, 0.0], [size, 0.0, 0.0], [0.0, size, 0.0]])
    values = np.array([0.0, strain / 2, strain])
    attributes = {
        "_STRAIN_UE": values,
        "_STRAIN_MAXP_UE": values,
        "_STRESS_VM": values / 100,
        "_PCT_STRENGTH": np.array([-1.0, -1.0, -1.0]),
        "_FROST_CLASS": np.array([0.0, 1.0, 1.0]),
    }
    return write_glb(positions, TRIANGLE, attributes, np.full((3, 3), displacement / np.sqrt(3)), {})


def store_result(data_dir: Path, run_id: str, *, crestal: float, displacement: float) -> PipelineResult:
    key = hashlib.sha256(run_id.encode()).hexdigest()
    store = ContentStore(data_dir / "store")
    assets = []
    for case in ("axial", "oblique"):
        name = f"bone.{case}.glb"
        store.put_bytes(key, name, glb(crestal, displacement))
        assets.append(VisualAsset(name=name, part="bone", load_case=case, store_key=key))
    numbers = [
        KeyNumber(
            id="crestal_mean",
            label="Crestal strain",
            load_case=case,
            block="implant-1",
            region="crestal",
            statistic="mean",
            value=crestal * (1 if case == "axial" else 2),
            unit="µε",
        )  # fmt: skip
        for case in ("axial", "oblique")
    ]
    result = PipelineResult(
        input_hash=key,
        purpose="run",
        detail_level="standard",
        result=ResultSet(
            input_hash=key, fields=[], combined_cases=["axial", "oblique"], key_numbers=numbers, visual_assets=assets
        ),  # fmt: skip
        convergence=Convergence(
            force_balance=[ForceBalance(load_case="U1", applied_n=(1, 0, 0), reaction_n=(-1, 0, 0), relative_error=0)]
        ),
        actual=Actual(dof=1000, time_s=1.0, peak_memory_gb=0.1, disk_gb=0.0),
        mesh=MeshSummary(dof=1000, elements=100, min_quality=0.5, straightened=0),
        locked_results=[],
        library_versions=[],
        versions=Versions(
            engine_version="0.1.0",
            engine_source_hash="f" * 64,
            conventions_version="1.1.0",
            solver_build_id="ccx-test-build",
            gmsh_version="4.15.2",
            dev_build=True,
        ),  # fmt: skip
        timings_s={"total": 1.0},
    )
    store.put_json(key, RESULT, result.model_dump(mode="json"))
    store.complete(key)
    return result


def queued_run(data_dir: Path, scenario_id: str) -> str:
    """A queued run of the scenario, made as the run route makes it but without its estimate and full checks (the
    run route has its own tests), so the fixture stays quick."""
    repos = open_repos(data_dir)
    try:
        record = repos.scenarios.require(scenario_id)
        inputs = library_inputs(record.scenario, LibraryService(repos))
        run = new_run(record.scenario, inputs, detail_level="standard", solver_build_id="ccx-test-build",
                      scenario_id=record.id, scenario_revision=record.revision)  # fmt: skip
        repos.runs.add(run)
        return run.id
    finally:
        repos.close()


def finished_run(data_dir: Path, scenario_id: str, *, crestal: float, displacement: float) -> str:
    run_id = queued_run(data_dir, scenario_id)
    repos = open_repos(data_dir)
    try:
        run = repos.runs.require(run_id)
        repos.runs.update(
            succeeded_run(run, store_result(data_dir, run_id, crestal=crestal, displacement=displacement))
        )
    finally:
        repos.close()
    return run_id


def implant_length(scenario: dict[str, Any]) -> float:
    block = next(b for b in scenario["scenario"]["blocks"] if b["instance_id"] == "implant-1")
    return float(block["settings"]["length_mm"]["value"])


def with_length(scenario: dict[str, Any], length: float) -> dict[str, Any]:
    definition = scenario["scenario"]
    for block in definition["blocks"]:
        if block["instance_id"] == "implant-1":
            block["settings"]["length_mm"] = {"kind": "design", "value": length}
    return definition


@pytest.fixture
def study(client: TestClient, data_dir: Path) -> dict[str, Any]:
    """Project "Implant length study": the 10 mm scenario and its duplicate changed to 8 mm, each run once."""
    project = client.post(
        "/api/projects", json={"name": "Implant length study", "notes": "Short against long", "tags": ["lengths"]}
    ).json()
    created = client.post("/api/scenarios", json={"project_id": project["id"], "name": "Long implant",
                                                  "from": {"template_id": "single_crown_implant"}})  # fmt: skip
    assert created.status_code == 201, created.json()
    original = created.json()["scenario"]
    assert implant_length(original) == 10.0
    client.patch(
        f"/api/scenarios/{original['id']}", json={"notes": "The control: a 10 mm implant", "tags": ["control"]}
    )

    copied = client.post("/api/scenarios", json={"project_id": project["id"], "name": "Short implant",
                                                 "from": {"scenario_id": original["id"]}})  # fmt: skip
    assert copied.status_code == 201, copied.json()
    duplicate = copied.json()["scenario"]
    saved = client.put(f"/api/scenarios/{duplicate['id']}",
                       json={"revision": duplicate["revision"], "scenario": with_length(duplicate, 8.0)})  # fmt: skip
    assert saved.status_code == 200, saved.json()
    long_run = finished_run(data_dir, original["id"], crestal=1000.0, displacement=0.002)
    short_run = finished_run(data_dir, duplicate["id"], crestal=1300.0, displacement=0.004)
    return {"project": project, "original": original, "duplicate": saved.json(), "runs": [long_run, short_run]}


def test_a_duplicate_keeps_its_link_and_the_diff_lists_the_one_changed_setting(
    client: TestClient, study: dict[str, Any]
) -> None:
    duplicate, original = study["duplicate"], study["original"]
    assert duplicate["derived_from"] == {"kind": "scenario", "id": original["id"]}
    assert duplicate["tags"] == ["control"]  # notes and tags come with the copy
    diff = client.get(f"/api/scenarios/{original['id']}/diff/{duplicate['id']}").json()
    assert [(d["path"], d["before"], d["after"], d["block"]) for d in diff["differences"]] == [
        ("implant-1.length_mm", 10.0, 8.0, "implant-1")
    ]
    assert client.get(f"/api/scenarios/{original['id']}/diff/missing").status_code == 404


def test_compare_returns_shared_scales_settings_and_key_number_differences(
    client: TestClient, study: dict[str, Any]
) -> None:
    jobs_before = len(client.get("/api/queue").json()["recent"])
    response = client.post("/api/compare", json={"run_ids": study["runs"]})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["run_ids"] == study["runs"] and body["load_cases"] == ["axial", "oblique"]
    strain = body["scales"]["axial"]["strain"]
    # Three vertices (0, half, all of the strain): the 98th-percentile rule picks the middle one, as the viewer does.
    assert [r["max"] for r in strain["by_run"]] == pytest.approx([500.0, 650.0])
    assert (strain["min"], strain["max"]) == pytest.approx((0.0, 650.0))
    assert body["scales"]["axial"]["frost"]["categorical"] is True
    assert body["frost"]["consistent"] is True and len(body["frost"]["edges"]) == 3
    deformation = body["deformation"]
    assert deformation["max_displacement_mm"] == pytest.approx(0.004, rel=1e-5)
    assert deformation["magnification"] == 100.0  # 0.05 x 14.14 mm / 0.004 mm = 177, rounded down
    rows = {(r["id"], r["load_case"]): r for r in body["key_numbers"]}
    axial = rows[("crestal_mean", "axial")]
    assert axial["values"] == [1000.0, 1300.0]
    assert axial["differences"][0]["absolute"] == pytest.approx(300.0)
    assert axial["differences"][0]["relative"] == pytest.approx(0.3)
    (settings,) = body["settings"]
    assert settings["run_id"] == study["runs"][1]
    assert [d["path"] for d in settings["differences"]] == ["implant-1.length_mm"]
    assert [len(r["assets"]) for r in body["runs"]] == [2, 2]
    assert body["teaching_notice"]
    assert len(client.get("/api/queue").json()["recent"]) == jobs_before  # nothing queued


def test_compare_refuses_unfinished_repeated_or_single_runs(
    client: TestClient, data_dir: Path, study: dict[str, Any]
) -> None:
    long_run, short_run = study["runs"]
    assert client.post("/api/compare", json={"run_ids": [long_run]}).status_code == 422
    assert client.post("/api/compare", json={"run_ids": [long_run, long_run]}).json()["code"] == "validation_failed"
    queued = queued_run(data_dir, study["original"]["id"])
    refused = client.post("/api/compare", json={"run_ids": [long_run, queued]})
    assert refused.status_code == 409 and refused.json()["code"] == "not_finished"
    unknown = client.post("/api/compare", json={"run_ids": [long_run, short_run], "fields": ["temperature"]})
    assert unknown.status_code == 422


def test_search_by_tag_note_text_and_setting_value(client: TestClient, study: dict[str, Any]) -> None:
    def names(params: dict[str, Any], kind: str = "scenarios") -> list[str]:
        response = client.get("/api/search", params=params)
        assert response.status_code == 200, response.json()
        return sorted(hit["name"] for hit in response.json()[kind])

    assert names({"tag": "LENGTHS"}, "projects") == ["Implant length study"]
    assert names({"tag": "control"}) == ["Long implant", "Short implant"]
    assert names({"q": "contr"}) == ["Long implant", "Short implant"]
    assert names({"q": "short against"}, "projects") == ["Implant length study"]
    assert names({"setting": "implant-1.length:=:8"}) == ["Short implant"]
    assert names({"setting": "implant-1.length_mm:>=:9"}) == ["Long implant"]
    assert names({"setting": "implant.length:<:12", "tag": "control"}) == ["Long implant", "Short implant"]
    assert names({"setting": "implant-1.length:=:8"}, "runs") == ["Short implant"]
    assert names({"setting": "implant-1.length:=:8"}, "projects") == []
    bad = client.get("/api/search", params={"setting": "implant-1.length:~:8"})
    assert bad.status_code == 422 and bad.json()["code"] == "validation_failed"
    assert client.get("/api/search").json() == {"projects": [], "scenarios": [], "runs": []}


def test_reopening_a_run_returns_its_results_without_creating_a_job(client: TestClient, study: dict[str, Any]) -> None:
    run_id = study["runs"][0]
    queue_before = client.get("/api/queue").json()
    results = client.get(f"/api/runs/{run_id}/results")
    assert results.status_code == 200, results.json()
    assert [n["value"] for n in results.json()["key_numbers"]] == [1000.0, 2000.0]
    asset = client.get(results.json()["assets"][0]["url"])
    assert asset.status_code == 200 and asset.headers["content-type"] == "model/gltf-binary"
    assert client.get("/api/queue").json() == queue_before


def test_notes_and_tags_on_projects_scenarios_and_runs(client: TestClient, study: dict[str, Any]) -> None:
    run_id = study["runs"][1]
    patched = client.patch(f"/api/runs/{run_id}", json={"notes": "Shown in the lecture", "tags": [" teaching ", ""]})
    assert patched.status_code == 200, patched.json()
    assert patched.json()["notes"] == "Shown in the lecture" and patched.json()["tags"] == ["teaching"]
    found = client.get("/api/search", params={"q": "lecture"}).json()
    assert [hit["id"] for hit in found["runs"]] == [run_id]

    project = client.get(f"/api/projects/{study['project']['id']}").json()
    assert [s["name"] for s in project["scenarios"]] == ["Short implant", "Long implant"]
    assert {r["id"] for r in project["runs"]} == set(study["runs"])
    assert next(r for r in project["runs"] if r["id"] == run_id)["tags"] == ["teaching"]
    assert project["studies"] == []

    scenario = study["original"]
    revision = client.get(f"/api/scenarios/{scenario['id']}").json()["revision"]
    renamed = client.patch(f"/api/scenarios/{scenario['id']}", json={"name": "Ten mm"}).json()
    assert renamed["name"] == "Ten mm" and renamed["revision"] == revision  # no new revision
    assert client.get("/api/search", params={"q": "ten"}).json()["scenarios"][0]["id"] == scenario["id"]
    assert client.patch(f"/api/projects/{study['project']['id']}", json={"tags": ["a", "A"]}).json()["tags"] == ["a"]


def test_backup_and_restore_are_queued_as_jobs(client: TestClient, tmp_path: Path) -> None:
    queued = client.post("/api/system/backup", json={"to": str(tmp_path / "drive")})
    assert queued.status_code == 202, queued.json()
    heavy = client.get("/api/queue").json()["heavy"]
    assert [(j["id"], j["kind"]) for j in heavy] == [(queued.json()["job_id"], "backup")]
    missing = client.post("/api/system/restore", json={"archive": str(tmp_path / "missing.zip")})
    assert missing.status_code == 422 and "no backup archive" in missing.json()["message"]
    assert client.post("/api/system/backup", json={"to": "relative/folder"}).status_code == 422
    assert client.get("/api/system").json()["last_backup"] is None
