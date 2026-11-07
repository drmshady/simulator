"""The studio API refuses what it must (task T089; FR-011, FR-011a, FR-014, FR-057), and its catalogue and scenario
routes work over a real database seeded from the approved library snapshot."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.estimate.predict import RunEstimate
from dentalsim.persistence import CapabilityStatus
from dentalsim.physics.registry import read_case_headers, required_cases_in
from dentalsim.testing import seed_library
from dentalsim_studio.api import runs
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.db import open_repos

PASSWORD = "correct horse battery"


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
        repos.verification.add_report(
            {
                "id": "R1",
                "engine_version": "x",
                "solver_build_id": "ccx-test-build",
                "conventions_version": "1.1.0",
                "overall": "pass",
                "created_at": "2026-11-06T00:00:00+00:00",
            }  # fmt: skip
        )
        required = list(required_cases_in("bonded_elastic", read_case_headers()))
        repos.capabilities.set(
            CapabilityStatus(capability_id="bonded_elastic", status="ready", report_id="R1", cases=required)
        )
    finally:
        repos.close()


def new_scenario(client: TestClient, **jaw: Any) -> dict[str, Any]:
    project = client.post("/api/projects", json={"name": "Teaching set"}).json()
    body: dict[str, Any] = {"project_id": project["id"], "from": {"template_id": "single_crown_implant"}}
    if jaw:
        body["from"]["jaw_settings"] = {k: {"kind": "design", "value": v} for k, v in jaw.items()}
    response = client.post("/api/scenarios", json=body)
    assert response.status_code == 201, response.json()
    return dict(response.json())


def moved_implant(scenario: dict[str, Any], z: float) -> dict[str, Any]:
    definition = scenario["scenario"]
    for block in definition["blocks"]:
        if block["instance_id"] == "implant-1":
            block["placement"] = {"position_mm": [0, 0, z], "axis_angles_deg": {"mesiodistal": 0, "buccolingual": 0},
                                  "rotation_deg": 0}  # fmt: skip
    return definition


# --- refusals ----------------------------------------------------------------------------------------------------


def test_a_preview_is_refused_naming_the_missing_benchmark(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    response = client.post(f"/api/scenarios/{scenario['id']}/preview", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "run_refused"
    assert body["missing"][0]["item"] == "benchmark:bonded_elastic"
    assert "Bonded elastic is awaiting its benchmark" in body["message"]
    run = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "standard"})
    assert run.status_code == 409 and run.json()["missing"][0]["item"] == "benchmark:bonded_elastic"


def test_a_run_is_refused_for_the_nerve_canal_with_its_location(client: TestClient, data_dir: Path) -> None:
    make_ready(data_dir)
    scenario = new_scenario(client)["scenario"]
    saved = client.put(
        f"/api/scenarios/{scenario['id']}",
        json={"revision": scenario["revision"], "scenario": moved_implant(scenario, -6.0)},
    )
    assert saved.status_code == 200, saved.json()
    response = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "standard"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "run_refused"
    canal = [p for p in body["problems"] if p["code"] == "in_nerve_canal"]
    assert canal and canal[0]["block"] == "implant-1" and len(canal[0]["location_mm"]) == 3


def test_an_unapproved_library_version_is_refused(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    definition = scenario["scenario"]
    definition["blocks"][1]["settings"]["E"]["entry_version"] = 99
    response = client.put(
        f"/api/scenarios/{scenario['id']}", json={"revision": scenario["revision"], "scenario": definition}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert "has no version 99" in response.json()["message"]


def test_a_run_too_large_for_memory_is_refused(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_ready(data_dir)
    scenario = new_scenario(client)["scenario"]
    too_big = RunEstimate(
        detail_level="extra_fine", dof=3_000_000, unit_cases=6, time_s=9000.0, memory_gb=60.0, disk_gb=5.0,
        fits=False, warnings=["The run needs about 60.0 GB of memory, more than 85% of this machine's 32.0 GB."],
        memory_limit_gb=27.2, solver_build_id="ccx-test-build",
    )  # fmt: skip
    monkeypatch.setattr(runs, "_estimate", lambda *args: too_big)
    response = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "extra_fine"})
    assert response.status_code == 409
    assert response.json()["code"] == "estimate_too_large"
    assert "60.0 GB" in response.json()["message"]


def test_a_queued_run_lists_the_locked_screw_stress(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_ready(data_dir)
    scenario = new_scenario(client)["scenario"]
    fits = RunEstimate(
        detail_level="standard", dof=200_000, unit_cases=6, time_s=120.0, memory_gb=2.0, disk_gb=0.2, fits=True,
        warnings=[], memory_limit_gb=27.2, solver_build_id="ccx-test-build",
    )  # fmt: skip
    monkeypatch.setattr(runs, "_estimate", lambda *args: fits)
    response = client.post(f"/api/scenarios/{scenario['id']}/runs", json={"detail_level": "standard"})
    assert response.status_code == 202, response.json()
    queued = response.json()
    locked = {(r["block"], r["result_type"]): r["message"] for r in queued["locked_results"]}
    assert locked[("abutment-1", "screw_stress")] == "not reported: needs screw preload"
    run = client.get(f"/api/runs/{queued['run_id']}").json()
    assert run["run"]["status"] == "queued" and run["run"]["estimate"]["dof"] == 200_000
    lanes = client.get("/api/queue").json()
    assert [j["target_id"] for j in lanes["heavy"]] == [queued["run_id"]]
    assert lanes["heavy"][0]["run"]["detail_level"] == "standard"
    cancelled = client.post(f"/api/runs/{queued['run_id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    lanes = client.get("/api/queue").json()
    assert lanes["heavy"] == []
    assert [(j["target_id"], j["status"]) for j in lanes["recent"]] == [(queued["run_id"], "cancelled")]
    assert lanes["recent"][0]["run"]["name"] == scenario["scenario"]["name"] and lanes["recent"][0]["finished_at"]


# --- catalogue and scenarios -------------------------------------------------------------------------------------


def test_the_catalogue_lists_blocks_physics_and_templates(client: TestClient) -> None:
    blocks = {b["type_id"]: b for b in client.get("/api/blocks").json()}
    assert {
        "jaw_segment",
        "implant",
        "abutment",
        "crown",
        "pontic",
        "splinting_connector",
        "cantilever_extension",
    } <= set(blocks)
    implant = blocks["implant"]
    assert implant["status"] == "awaiting_benchmark" and implant["awaiting"] == ["Bonded elastic"]
    assert implant["settings_schema"]["properties"]["diameter_mm"]["group"] == "implant"
    screw = next(r for r in blocks["abutment"]["result_types"] if r["id"] == "screw_stress")
    assert screw["locked_message"] == "not reported: needs screw preload"
    physics = {p["id"]: p for p in client.get("/api/physics").json()}
    assert physics["bonded_elastic"]["status"] == "awaiting_benchmark"
    templates = {t["id"]: t for t in client.get("/api/templates").json()}
    assert templates["single_crown_implant"]["approval"] == "approved"
    assert {"splinted_crowns", "bridge_implant", "cantilever_bridge", "full_arch_fixed"} <= set(templates)
    site = blocks["jaw_segment"]["attach_points"][0]
    assert (site["name"], site["keys_from"], site["default_key_from"]) == ("site", "region", "site")


def test_a_scenario_from_a_template_that_does_not_fit_lists_the_misfits(client: TestClient) -> None:
    created = new_scenario(client, region=["35", "36", "37"], neighbours=["34"])
    assert created["misfits"] and created["misfits"][0]["code"] == "template_misfit"
    assert "spanning 1 tooth" in created["misfits"][0]["message"]
    scenario = created["scenario"]
    assert scenario["validity"]["ok"] is False
    library = {v["path"]: v for v in scenario["library"]}
    assert library["jaw.cortical_thickness"]["parameter_key"] == "bone.cortical.thickness.D2"
    assert library["load:axial.force"]["parameter_key"] == "load.bite_force.molar"
    assert not any(v["outside_range"] for v in scenario["library"])


def test_a_save_must_carry_the_current_revision(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    first = client.put(f"/api/scenarios/{scenario['id']}", json={"revision": 1, "scenario": scenario["scenario"]})
    assert first.status_code == 200 and first.json()["revision"] == 2
    stale = client.put(f"/api/scenarios/{scenario['id']}", json={"revision": 1, "scenario": scenario["scenario"]})
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"


def test_validation_and_builder_geometry(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    result = client.post(f"/api/scenarios/{scenario['id']}/validate", params={"mode": "full"}).json()
    assert result["ok"] is True and result["scenario_revision"] == 1
    glb = client.post(f"/api/scenarios/{scenario['id']}/geometry")
    assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary"
    assert glb.content[:4] == b"glTF"


def test_saving_a_scenario_as_an_owner_template(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    response = client.post(
        "/api/templates",
        json={"scenario_id": scenario["id"], "name": "My molar crown", "template_id": "my-molar", "branch": "implant"},
    )
    assert response.status_code == 201, response.json()
    assert response.json()["origin"] == "owner"
    ids = [t["id"] for t in client.get("/api/templates").json()]
    assert ids[-1] == "my-molar" and "single_crown_implant" in ids  # the built-in templates first


def test_a_changed_material_takes_the_new_materials_values(client: TestClient) -> None:
    scenario = new_scenario(client)["scenario"]
    definition = scenario["scenario"]
    crown = next(b for b in definition["blocks"] if b["instance_id"] == "crown-1")
    crown["settings"]["material"] = {"kind": "design", "value": "lithium_disilicate"}
    saved = client.put(f"/api/scenarios/{scenario['id']}", json={"revision": 1, "scenario": definition}).json()
    library = {v["path"]: v for v in saved["library"]}
    assert library["crown-1.E"]["parameter_key"] == "material.lithium_disilicate.E"
    assert library["crown-1.strength"]["parameter_key"] == "material.lithium_disilicate.flexural_strength"
    points = {(p["block"], p["point"]) for p in saved["occlusal_points"]}
    assert ("crown-1", "buccal_cusp") in points and ("crown-1", "central_fossa") in points


def test_verification_cases_and_reports_can_be_read(client: TestClient, data_dir: Path) -> None:
    cases = client.get("/api/verification/cases").json()
    assert {c["id"] for c in cases} >= {"textbook-cantilever", "textbook-nafems-le10"}
    assert all(len(c["sha256"]) == 64 for c in cases)
    assert client.get("/api/verification/reports/R1").status_code == 404
    make_ready(data_dir)
    assert client.get("/api/verification/reports/R1").json()["overall"] == "pass"
    physics = {p["id"]: p for p in client.get("/api/physics").json()}
    assert physics["bonded_elastic"]["status"] == "ready"
    assert physics["bonded_elastic"]["report_url"] == "/api/verification/reports/R1"


def test_a_crown_added_in_the_builder_is_sized_to_its_site(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Restore"}).json()
    body = {"project_id": project["id"], "from": {"template_id": "cantilever_bridge"}}
    scenario = client.post("/api/scenarios", json=body).json()["scenario"]
    definition = scenario["scenario"]
    # Replace the premolar crown at 35 as the builder does: a new block with no settings yet.
    definition["blocks"] = [b for b in definition["blocks"] if b["instance_id"] != "crown-2"]
    definition["blocks"].append(
        {
            "instance_id": "crown-3",
            "type_id": "crown",
            "type_version": next(b for b in definition["blocks"] if b["type_id"] == "crown")["type_version"],
            "attach_to": {"instance_id": "abutment-2", "attach_point": "crown_seat"},
            "settings": {},
        }
    )
    saved = client.put(f"/api/scenarios/{scenario['id']}", json={"revision": 1, "scenario": definition})
    assert saved.status_code == 200, saved.json()
    blocks = {b["instance_id"]: b for b in saved.json()["scenario"]["blocks"]}
    assert blocks["crown-3"]["settings"]["mesiodistal_mm"] == {"kind": "design", "value": 6.6}  # tooth 35 less 0.4
    assert blocks["crown-1"]["settings"]["mesiodistal_mm"]["value"] == 10.5  # a saved block keeps its width
