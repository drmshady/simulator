"""The literature library through the studio API (task T113; Story 2; FR-012 to FR-016, FR-053 to FR-055), on a
real database seeded from the approved snapshot, with Crossref and NCBI mocked."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.library import service as library_service
from dentalsim.library import source_check
from dentalsim.persistence import CapabilityStatus
from dentalsim.physics.registry import read_case_headers, required_cases_in
from dentalsim.testing import seed_library
from dentalsim_studio.api import runs
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.db import open_repos

PASSWORD = "correct horse battery"
NEW_DOI = "10.1000/owner.paper"
NEW_TITLE = "Cortical bone stiffness measured by nanoindentation"


class Crossref:
    """Answers DOIs from registered works; anything else is not found."""

    def __init__(self) -> None:
        self.works: dict[str, dict[str, Any]] = {}

    def add(self, doi: str, *, family: str, year: int, title: str) -> None:
        self.works[doi.lower()] = {
            "status": "ok",
            "message": {
                "DOI": doi.lower(),
                "title": [title],
                "author": [{"given": "A.", "family": family, "sequence": "first"}],
                "issued": {"date-parts": [[year]]},
            },
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        work = self.works.get(request.url.path.removeprefix("/works/").lower())
        return httpx.Response(200, json=work) if work else httpx.Response(404, text="Resource not found.")


@pytest.fixture
def crossref(monkeypatch: pytest.MonkeyPatch) -> Crossref:
    mock = Crossref()
    monkeypatch.setattr(source_check, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(mock)))
    return mock


@pytest.fixture
def app(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    repos = open_repos(data_dir)
    set_password(repos, PASSWORD)
    seed_library(repos)
    repos.close()
    monkeypatch.setattr(runs, "solver_build", lambda: SimpleNamespace(build_id="ccx-test-build", path=None))
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield test_client


def make_ready(data_dir: Path) -> None:
    repos = open_repos(data_dir)
    try:
        report = {
            "id": "R1",
            "engine_version": "x",
            "solver_build_id": "ccx-test-build",
            "conventions_version": "1.1.0",
            "overall": "pass",
            "created_at": "2026-11-06T00:00:00+00:00",
        }
        repos.verification.add_report(report)
        required = list(required_cases_in("bonded_elastic", read_case_headers()))
        repos.capabilities.set(
            CapabilityStatus(capability_id="bonded_elastic", status="ready", report_id="R1", cases=required)
        )
    finally:
        repos.close()


def new_scenario(client: TestClient) -> dict[str, Any]:
    project = client.post("/api/projects", json={"name": "Library checks"}).json()
    body = {"project_id": project["id"], "from": {"template_id": "single_crown_implant"}}
    response = client.post("/api/scenarios", json=body)
    assert response.status_code == 201, response.json()
    return dict(response.json()["scenario"])


def first_library_setting(scenario: dict[str, Any]) -> tuple[int, str, dict[str, Any]]:
    """The first block library setting: (block index, setting name, value)."""
    for index, block in enumerate(scenario["scenario"]["blocks"]):
        for name, value in block["settings"].items():
            if value.get("kind") == "library":
                return index, name, value
    raise AssertionError("the template has no library setting")


def succeeded_run(client: TestClient, data_dir: Path, scenario: dict[str, Any]) -> str:
    """A run of the scenario, marked succeeded without solving."""
    response = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "standard"})
    assert response.status_code == 202, response.json()
    run_id = str(response.json()["run_id"])
    repos = open_repos(data_dir)
    try:
        repos.runs.set_status(run_id, "succeeded")
    finally:
        repos.close()
    return run_id


def owner_entry(client: TestClient, entry_id: str, *, doi: str = NEW_DOI) -> dict[str, Any]:
    """The owner's entry for the same parameter as `entry_id`, citing one new paper."""
    detail = client.get(f"/api/library/entries/{entry_id}").json()
    current = next(v for v in detail["versions"] if v["status"] == "approved")
    return {
        "entry": {
            "parameter_key": detail["entry"]["parameter_key"],
            "category": detail["entry"]["category"],
            "label": detail["entry"]["label"],
            "units": current["units"],
            "typical": current["typical"],
            "range": {"min": current["range_min"], "max": current["range_max"]},
            "values_by_source": [{"source": "owner-1", "value": current["typical"], "note": "Table 2."}],
        },
        "sources": [
            {
                "id": "owner-1",
                "authors": ["Paper AB"],
                "year": 2015,
                "title": NEW_TITLE,
                "doi": doi,
                "study_type": "experimental",
            }
        ],
    }


# --- reading -----------------------------------------------------------------------------------------------------


def test_list_filters_and_entry_detail(client: TestClient) -> None:
    approved = client.get("/api/library/entries", params={"status": "approved"}).json()
    assert approved and all(e["current_version"] is not None for e in approved)
    assert client.get("/api/library/entries", params={"status": "proposed"}).json() == []
    category = approved[0]["category"]
    assert {e["category"] for e in client.get("/api/library/entries", params={"category": category}).json()} == {
        category
    }
    detail = client.get(f"/api/library/entries/{approved[0]['parameter_key']}").json()
    assert detail["entry"]["id"] == approved[0]["id"]
    assert detail["sources"] and all(s["doi"] or s["pmid"] for s in detail["sources"])
    assert [s["version"] for s in detail["states"]] == [v["version"] for v in detail["versions"]]
    assert client.get("/api/library/entries/no.such.key").status_code == 404


# --- adding and choosing -----------------------------------------------------------------------------------------


def test_a_proposed_entry_cannot_be_chosen(client: TestClient) -> None:
    scenario = new_scenario(client)
    block, name, value = first_library_setting(scenario)
    added = client.post("/api/library/entries", json=owner_entry(client, value["entry_id"]))
    assert added.status_code == 201, added.json()
    assert (added.json()["entry_id"], added.json()["version"]) == (value["entry_id"], 2)

    detail = client.get(f"/api/library/entries/{value['entry_id']}").json()
    v2 = next(v for v in detail["versions"] if v["version"] == 2)
    assert (v2["status"], v2["origin"]) == ("proposed", "owner")
    assert any(s["doi"] == NEW_DOI for s in detail["sources"])  # its source is shown

    definition = scenario["scenario"]
    definition["blocks"][block]["settings"][name] = {**value, "entry_version": 2}
    response = client.put(f"/api/scenarios/{scenario['id']}", json={"revision": scenario["revision"],
                                                                     "scenario": definition})  # fmt: skip
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert "version 2 is proposed; only approved versions can be chosen" in response.json()["message"]


def test_bulk_import_creates_proposed_versions(client: TestClient) -> None:
    scenario = new_scenario(client)
    _, _, value = first_library_setting(scenario)
    body = owner_entry(client, value["entry_id"])
    document = {
        "format": "dentalsim.library/1",
        "prepared_by": "claude",
        "prepared_at": "2026-11-07",
        "entries": [body["entry"]],
        "sources": body["sources"],
    }
    upload = yaml.safe_dump(document, sort_keys=False).encode()
    response = client.post("/api/library/import", files={"file": ("proposal.yaml", upload, "application/yaml")})
    assert response.status_code == 200, response.json()
    result = response.json()
    assert result["origin"] == "claude_proposal"
    assert [(i["entry_id"], i["version"]) for i in result["imported"]] == [(value["entry_id"], 2)]
    proposed = client.get("/api/library/entries", params={"status": "proposed"}).json()
    assert [e["id"] for e in proposed] == [value["entry_id"]]

    again = client.post("/api/library/import", files={"file": ("proposal.yaml", upload, "application/yaml")})
    assert again.json()["imported"] == [] and len(again.json()["unchanged"]) == 1

    bad = yaml.safe_dump({**document, "entries": [{**body["entry"], "units": "GPa"}]}).encode()
    refused = client.post("/api/library/import", files={"file": ("bad.yaml", bad, "application/yaml")})
    assert refused.status_code == 422
    assert refused.json()["problems"][0]["code"] == "units"


# --- source check and approval -----------------------------------------------------------------------------------


def test_approval_is_refused_on_a_mismatching_doi(client: TestClient, crossref: Crossref) -> None:
    crossref.add(NEW_DOI, family="Paper", year=1999, title="A different paper on dental cements")
    _, _, value = first_library_setting(new_scenario(client))
    entry_id = value["entry_id"]
    client.post("/api/library/entries", json=owner_entry(client, entry_id))

    state = client.get(f"/api/library/entries/{entry_id}").json()["states"]
    assert [(s["version"], s["approvable"]) for s in state] == [(1, False), (2, False)]
    assert state[1]["problems"][0]["status"] == "not_checked"

    checked = client.post(f"/api/library/entries/{entry_id}/versions/2/check-sources")
    assert checked.status_code == 200, checked.json()
    body = checked.json()
    assert body["approvable"] is False
    assert body["sources"][0]["check"]["status"] == "resolved_mismatch"
    match = body["sources"][0]["check"]["matched"]
    assert (match["first_author"], match["year"], match["title_similarity"] < 0.5) == (True, False, True)

    refused = client.post(f"/api/library/entries/{entry_id}/versions/2/approve")
    assert refused.status_code == 422
    assert refused.json()["code"] == "validation_failed"
    assert [p["code"] for p in refused.json()["problems"]] == ["source_not_matched"]
    assert "does not match the citation" in refused.json()["problems"][0]["message"]

    crossref.add(NEW_DOI, family="Paper", year=2015, title=NEW_TITLE)  # the citation is right after all
    assert client.post(f"/api/library/entries/{entry_id}/versions/2/check-sources").json()["approvable"] is True
    approved = client.post(f"/api/library/entries/{entry_id}/versions/2/approve")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["superseded"] == [1]


# --- editing and rejecting ---------------------------------------------------------------------------------------


def test_editing_an_approved_entry_creates_v2_and_an_old_run_shows_v1(client: TestClient, data_dir: Path) -> None:
    make_ready(data_dir)
    scenario = new_scenario(client)
    _, _, value = first_library_setting(scenario)
    entry_id = value["entry_id"]
    run_id = succeeded_run(client, data_dir, scenario)
    v1 = client.get(f"/api/library/entries/{entry_id}").json()["versions"][0]
    notes = "Edited: the value applies to the crestal 3 mm only."

    edited = client.put(f"/api/library/entries/{entry_id}", json={"changes": {"notes": notes}})
    assert edited.status_code == 201, edited.json()
    assert (edited.json()["version"], edited.json()["status"], edited.json()["notes"]) == (2, "proposed", notes)
    unchanged = client.put(f"/api/library/entries/{entry_id}", json={"changes": {"notes": notes}})
    assert unchanged.status_code == 422  # the edit leaves the newest version (v2) as it is
    assert "as it is" in unchanged.json()["message"]
    outside = client.put(f"/api/library/entries/{entry_id}", json={"changes": {"typical": v1["range_max"] * 10 + 1}})
    assert outside.status_code == 422 and outside.json()["problems"][0]["code"] == "range"

    assert client.post(f"/api/library/entries/{entry_id}/versions/2/approve").status_code == 200
    detail = client.get(f"/api/library/entries/{entry_id}").json()
    assert [(v["version"], v["status"]) for v in detail["versions"]][:2] == [(1, "superseded"), (2, "approved")]
    assert detail["versions"][0]["notes"] == v1["notes"]  # superseded versions stay readable

    run = client.get(f"/api/runs/{run_id}").json()
    use = next(u for u in run["library"] if u["entry_id"] == entry_id)
    assert (use["version"], use["status"], use["newer_version"]) == (1, "superseded", 2)
    assert use["values"] == [value["value"]] and use["sources"]
    assert use["used_in"] and "." in use["used_in"][0]
    setting_values = [s for b in run["run"]["snapshot"]["blocks"] for s in b["settings"].values()
                      if isinstance(s, dict) and s.get("entry_id") == entry_id]  # fmt: skip
    assert {s["entry_version"] for s in setting_values} == {1}


def test_rejecting_a_used_version_flags_its_runs(client: TestClient, data_dir: Path) -> None:
    make_ready(data_dir)
    scenario = new_scenario(client)
    _, _, value = first_library_setting(scenario)
    entry_id = value["entry_id"]
    run_id = succeeded_run(client, data_dir, scenario)
    assert client.get(f"/api/library/entries/{entry_id}").json()["states"][0]["runs_using"] == 1

    no_reason = client.post(f"/api/library/entries/{entry_id}/versions/1/reject", json={"reason": "  "})
    assert no_reason.status_code == 422

    rejected = client.post(f"/api/library/entries/{entry_id}/versions/1/reject",
                           json={"reason": "The paper reports wet bone; ours is dry."})  # fmt: skip
    assert rejected.status_code == 200, rejected.json()
    assert (rejected.json()["was_current"], rejected.json()["flagged_runs"]) == (True, [run_id])

    run = client.get(f"/api/runs/{run_id}").json()
    assert "uses_rejected_entry" in run["flags"]
    assert next(u for u in run["library"] if u["entry_id"] == entry_id)["status"] == "rejected"
    repos = open_repos(data_dir)
    try:
        assert repos.runs.require(run_id).is_publishable() is False
    finally:
        repos.close()

    again = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "standard"})
    assert again.status_code == 422  # nothing new runs on the rejected version
    assert "version 1 is rejected; only approved versions can be chosen" in again.json()["message"]


def test_export_writes_the_approved_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "snapshots" / "library-snapshot.yaml"
    monkeypatch.setattr(library_service, "default_snapshot_path", lambda: target)
    response = client.post("/api/library/export")
    assert response.status_code == 200
    count = len(client.get("/api/library/entries", params={"status": "approved"}).json())
    assert response.json() == {"path": str(target), "entries": count}
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["snapshot_of"] == "approved"
