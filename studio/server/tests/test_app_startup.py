"""The app factory: conventions at start-up, the error shape, the interface and its fallback (T041)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from dentalsim.config import DATA_SUBFOLDERS, get_settings
from dentalsim.conventions.loader import get_loaded_conventions
from dentalsim.errors import Conflict, ConventionsMissing, NotFound, Refused, SolverFailed
from dentalsim_studio import openapi
from dentalsim_studio.api.errors import INTERNAL_ERROR_MESSAGE, ApiError
from dentalsim_studio.app import BUILD_COMMAND, create_app

INDEX = "<!doctype html><title>Simulation Studio</title><div id=root></div>"


@pytest.fixture
def clear_caches() -> Iterator[None]:
    get_settings.cache_clear()
    get_loaded_conventions.cache_clear()
    yield
    get_settings.cache_clear()
    get_loaded_conventions.cache_clear()


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    folder = tmp_path / "ui" / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX, encoding="utf-8")
    (folder / "assets" / "index-abc.js").write_text("console.log('studio');", encoding="utf-8")
    (tmp_path / "ui" / "secret.txt").write_text("not for the browser", encoding="utf-8")
    return folder


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


# Conventions and the data folder ------------------------------------------------------------------------------------


def test_refuses_to_start_without_the_conventions(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch, clear_caches: None
) -> None:
    monkeypatch.setenv("DENTALSIM_REPO_ROOT", str(tmp_path / "no-repo"))
    with pytest.raises(ConventionsMissing, match="conventions.md"):
        create_app(data_dir=data_dir)


def test_refuses_to_start_with_conventions_lacking_the_block(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch, clear_caches: None
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conventions.md").write_text("# Conventions\n\nNo block here.\n", encoding="utf-8")
    monkeypatch.setenv("DENTALSIM_REPO_ROOT", str(tmp_path))
    with pytest.raises(ConventionsMissing, match="no ```yaml conventions``` block"):
        create_app(data_dir=data_dir)


def test_makes_the_data_folder_and_keeps_it_on_the_app(tmp_path: Path, dist: Path) -> None:
    folder = tmp_path / "fresh-data"
    app = create_app(data_dir=folder, ui_dist=dist)
    assert sorted(p.name for p in folder.iterdir()) == sorted(DATA_SUBFOLDERS)
    assert app.state.data_dir == folder
    assert app.state.settings.data_dir == folder
    assert app.state.conventions.version == get_loaded_conventions().version


# The interface -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/builder", "/results/01J0000000/compare", "/library?status=approved"])
def test_interface_routes_fall_back_to_index(data_dir: Path, dist: Path, path: str) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=dist)) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


def test_built_files_are_served(data_dir: Path, dist: Path) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=dist)) as client:
        script = client.get("/assets/index-abc.js")
        assert script.status_code == 200
        assert script.text == "console.log('studio');"
        assert "javascript" in script.headers["content-type"]
        assert client.head("/builder").status_code == 200
        missing = client.get("/assets/index-missing.js")
        assert missing.status_code == 404
        assert missing.json()["code"] == "not_found"


def test_files_outside_dist_are_never_served(data_dir: Path, dist: Path) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=dist)) as client:
        response = client.get("/%2e%2e/secret.txt")
    assert "not for the browser" not in response.text


def test_without_a_build_the_page_says_how_to_build(data_dir: Path, tmp_path: Path) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=tmp_path / "missing-dist")) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert BUILD_COMMAND in page.text
        assert client.get("/builder").text == page.text
        assert client.get("/api/nothing").json()["code"] == "not_found"


@pytest.mark.parametrize(("method", "path"), [("GET", "/api/nothing"), ("POST", "/api/nothing/here"), ("GET", "/api")])
def test_unknown_api_paths_answer_404_in_the_error_shape(data_dir: Path, dist: Path, method: str, path: str) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=dist)) as client:
        response = client.request(method, path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body == {"code": "not_found", "message": f"There is nothing at {path}.", "problems": [], "missing": []}


def test_wrong_method_on_a_known_path(data_dir: Path, dist: Path) -> None:
    with _client(create_app(data_dir=data_dir, ui_dist=dist)) as client:
        response = client.put("/api/auth/login", json={})
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


# The error shape -----------------------------------------------------------------------------------------------------

PROBLEM = {"code": "in_nerve_canal", "block": "implant-1", "location_mm": [12.0, 0.4, -9.1], "message": "In the canal."}
MISSING = {"item": "benchmark:bonded_elastic", "message": "Bonded-elastic textbook benchmarks have not passed."}

failing = APIRouter(prefix="/api/test")


@failing.get("/refused/{code}")
def refused(code: str) -> None:
    raise Refused(f"Refused with {code}.", code=code, problems=[PROBLEM], missing=[MISSING])


@failing.get("/not-found")
def not_found() -> None:
    raise NotFound("There is no scenario s1.")


@failing.get("/conflict")
def conflict() -> None:
    raise Conflict("The scenario changed while it was being edited: reload it.")


@failing.get("/api-error")
def api_error() -> None:
    raise ApiError(429, "not_signed_in", "Too many.", headers={"Retry-After": "7"})


@failing.get("/solver-failed")
def solver_failed() -> None:
    raise SolverFailed("The solver ran out of memory.")


@failing.get("/crash")
def crash() -> None:
    raise RuntimeError("secret internal detail")


@failing.get("/number")
def number(n: int) -> int:
    return n


@pytest.fixture
def error_client(data_dir: Path, dist: Path) -> Iterator[TestClient]:
    app = create_app(data_dir=data_dir, ui_dist=dist)
    app.include_router(failing)
    with _client(app) as client:
        yield client


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("run_refused", 409),
        ("estimate_too_large", 409),
        ("publish_refused", 409),
        ("validation_failed", 422),
        ("not_found", 404),
        ("conflict", 409),
        ("out_of_date", 409),
        ("not_signed_in", 401),
        ("some_new_refusal", 409),
    ],
)
def test_refusals_keep_their_code(error_client: TestClient, code: str, status: int) -> None:
    response = error_client.get(f"/api/test/refused/{code}")
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": f"Refused with {code}.",
        "problems": [PROBLEM],
        "missing": [MISSING],
    }


def test_engine_errors_map_to_their_codes(error_client: TestClient) -> None:
    missing = error_client.get("/api/test/not-found")
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
    assert missing.json()["message"] == "There is no scenario s1."
    changed = error_client.get("/api/test/conflict")
    assert (changed.status_code, changed.json()["code"]) == (409, "conflict")
    solver = error_client.get("/api/test/solver-failed")
    assert (solver.status_code, solver.json()["code"]) == (500, "engine_error")
    assert solver.json()["message"] == "The solver ran out of memory."


def test_api_error_keeps_status_and_headers(error_client: TestClient) -> None:
    response = error_client.get("/api/test/api-error")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["code"] == "not_signed_in"


def test_unexpected_errors_answer_a_generic_500_and_log_the_traceback(
    error_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="dentalsim_studio.api.errors"):
        response = error_client.get("/api/test/crash")
    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": INTERNAL_ERROR_MESSAGE,
        "problems": [],
        "missing": [],
    }
    assert "secret internal detail" not in response.text
    logged = [r for r in caplog.records if r.exc_info]
    assert logged and "secret internal detail" in str(logged[0].exc_info[1])  # type: ignore[index]


def test_request_validation_answers_validation_failed(error_client: TestClient) -> None:
    response = error_client.get("/api/test/number?n=abc")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert body["problems"] == [
        {
            "code": "invalid_value",
            "field": "n",
            "message": "n: Input should be a valid integer, unable to parse string as an integer",
        }
    ]
    assert body["message"].startswith("The request is not valid: n: ")


# The OpenAPI document ------------------------------------------------------------------------------------------------


def test_openapi_document_has_the_response_models(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert openapi.main([str(out)]) == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    for name in ("SystemInfo", "LoginRequest", "LoginResult", "SessionInfo", "ErrorBody", "ConventionsInfo"):
        assert name in schemas
    assert "HTTPValidationError" not in schemas
    paths = document["paths"]
    assert set(paths) >= {"/api/auth/login", "/api/auth/logout", "/api/auth/session", "/api/system", "/api/events"}
    system = paths["/api/system"]["get"]["responses"]
    assert system["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/SystemInfo")
    assert system["401"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    assert "max_polls" not in json.dumps(paths["/api/events"])


def test_openapi_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert openapi.main([]) == 2
    assert "usage" in capsys.readouterr().err
