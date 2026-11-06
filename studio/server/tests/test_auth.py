"""Sign-in: the owner password, session cookies, the idle timeout and the attempt limit (T043, FR-001)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.errors import Refused
from dentalsim.testing import FIXED_TIME, FixedClock
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import (
    PASSWORD_HASH_KEY,
    password_is_set,
    set_password,
    verify_password,
)
from dentalsim_studio.auth.sessions import SESSION_COOKIE, SessionStore
from dentalsim_studio.db import SqliteRepos, open_repos

PASSWORD = "correct horse battery"


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


@pytest.fixture
def app(data_dir: Path, fixed_time: FixedClock) -> FastAPI:
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def owner(repos: SqliteRepos) -> str:
    set_password(repos, PASSWORD)
    return PASSWORD


def login(client: TestClient, password: str = PASSWORD) -> int:
    return client.post("/api/auth/login", json={"password": password}).status_code


# Password ------------------------------------------------------------------------------------------------------------


def test_password_is_stored_as_an_argon2id_hash(repos: SqliteRepos) -> None:
    assert not password_is_set(repos)
    set_password(repos, PASSWORD)
    stored = repos.settings.get(PASSWORD_HASH_KEY)
    assert isinstance(stored, str) and stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    assert password_is_set(repos)
    assert verify_password(repos, PASSWORD)
    assert not verify_password(repos, PASSWORD + "x")


def test_short_password_is_refused_and_says_the_minimum(repos: SqliteRepos) -> None:
    with pytest.raises(Refused, match="at least 10 characters") as refused:
        set_password(repos, "123456789")
    assert refused.value.code == "validation_failed"
    assert not password_is_set(repos)


def test_hash_with_old_parameters_is_replaced_on_sign_in(repos: SqliteRepos) -> None:
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    repos.settings.set(PASSWORD_HASH_KEY, old)
    assert verify_password(repos, PASSWORD)
    renewed = repos.settings.get(PASSWORD_HASH_KEY)
    assert renewed != old
    assert not PasswordHasher().check_needs_rehash(renewed)


def test_invalid_stored_hash_never_verifies(repos: SqliteRepos) -> None:
    repos.settings.set(PASSWORD_HASH_KEY, "not a hash")
    assert not verify_password(repos, PASSWORD)


# Sign-in -------------------------------------------------------------------------------------------------------------


def test_sign_in_without_a_password_tells_the_owner_to_set_one(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "not_signed_in"
    assert "dentalsim studio set-password" in body["message"]
    assert SESSION_COOKIE not in response.cookies


def test_wrong_password_is_refused(client: TestClient, owner: str) -> None:
    response = client.post("/api/auth/login", json={"password": "wrong password"})
    assert response.status_code == 401
    assert response.json() == {"code": "not_signed_in", "message": "Wrong password.", "problems": [], "missing": []}
    assert "set-cookie" not in response.headers
    assert client.get("/api/auth/session").status_code == 401


def test_missing_password_field_is_a_validation_error(client: TestClient, owner: str) -> None:
    response = client.post("/api/auth/login", json={"pass": PASSWORD})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert {p["field"] for p in body["problems"]} == {"password", "pass"}
    assert PASSWORD not in response.text


def test_right_password_starts_a_session_with_a_strict_cookie(client: TestClient, owner: str) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"signed_in": True}
    cookie = response.headers["set-cookie"]
    assert re.match(rf"{SESSION_COOKIE}=[A-Za-z0-9_-]{{43}};", cookie)
    flags = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "secure", "samesite=strict", "path=/"} <= flags

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["signed_in"] is True
    assert datetime.fromisoformat(session.json()["expires_at"]) == FIXED_TIME + timedelta(hours=2)


def test_every_protected_route_needs_a_session(app: FastAPI, client: TestClient) -> None:
    checked = []
    for template, operations in app.openapi()["paths"].items():
        path = re.sub(r"\{[^}]+\}", "x", template)
        for method in operations:
            if (method, template) == ("post", "/api/auth/login"):
                continue
            response = client.request(method.upper(), path)
            assert response.status_code == 401, (method, path)
            assert response.json()["code"] == "not_signed_in"
            checked.append(f"{method.upper()} {template}")
    assert {"GET /api/system", "GET /api/events", "POST /api/auth/logout", "GET /api/auth/session"} <= set(checked)


def test_a_made_up_session_cookie_is_refused(client: TestClient, owner: str) -> None:
    client.cookies.set(SESSION_COOKIE, "made-up", domain="testserver.local")
    response = client.get("/api/auth/session")
    assert response.status_code == 401
    assert response.json()["message"] == "Your session has ended. Sign in again."


def test_session_ends_after_two_idle_hours(client: TestClient, owner: str, fixed_time: FixedClock) -> None:
    assert login(client) == 200
    fixed_time.advance(hours=1, minutes=59)
    assert client.get("/api/auth/session").status_code == 200  # every request moves the deadline on
    fixed_time.advance(hours=1, minutes=59)
    assert client.get("/api/auth/session").status_code == 200
    fixed_time.advance(hours=2, seconds=1)
    response = client.get("/api/auth/session")
    assert response.status_code == 401
    assert response.json()["code"] == "not_signed_in"


def test_sixth_attempt_in_a_minute_is_refused_even_with_the_right_password(
    client: TestClient, owner: str, fixed_time: FixedClock
) -> None:
    for _ in range(5):
        assert login(client, "wrong password") == 401
        fixed_time.advance(seconds=5)
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 429
    assert response.json()["code"] == "not_signed_in"
    assert "Wait a minute" in response.json()["message"]
    assert response.headers["retry-after"] == "35"  # the first attempt leaves the window 35 s from now
    assert client.get("/api/auth/session").status_code == 401

    fixed_time.advance(seconds=35)
    assert login(client) == 200


def test_successful_attempts_count_too(client: TestClient, owner: str) -> None:
    for _ in range(5):
        assert login(client) == 200
    assert login(client) == 429


def test_signing_in_again_ends_the_previous_session(client: TestClient, owner: str) -> None:
    assert login(client) == 200
    first = client.cookies[SESSION_COOKIE]
    assert login(client) == 200
    assert client.cookies[SESSION_COOKIE] != first
    client.cookies.set(SESSION_COOKIE, first, domain="testserver.local")
    assert client.get("/api/auth/session").status_code == 401


def test_logout_ends_the_session_and_clears_the_cookie(client: TestClient, owner: str) -> None:
    assert login(client) == 200
    session_id = client.cookies[SESSION_COOKIE]
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    cleared = response.headers["set-cookie"]
    assert cleared.startswith(f'{SESSION_COOKIE}="";') or cleared.startswith(f"{SESSION_COOKIE}=;")
    assert "max-age=0" in cleared.lower()
    assert SESSION_COOKIE not in client.cookies
    client.cookies.set(SESSION_COOKIE, session_id, domain="testserver.local")
    assert client.get("/api/auth/session").status_code == 401
    assert client.post("/api/auth/logout").status_code == 401


# The attempt limit on its own ------------------------------------------------------------------------------------


def test_limit_is_per_client_and_global(fixed_time: FixedClock) -> None:
    per_client = SessionStore(max_attempts=100, max_attempts_per_client=5)
    for _ in range(5):
        assert per_client.register_attempt("192.168.1.20") is None
    assert per_client.register_attempt("192.168.1.20") == 60.0
    assert per_client.register_attempt("192.168.1.21") is None

    everyone = SessionStore()
    for client in ("a", "a", "a", "b", "b"):
        assert everyone.register_attempt(client) is None
    assert everyone.register_attempt("c") == 60.0


def test_refused_attempts_do_not_extend_the_wait(fixed_time: FixedClock) -> None:
    store = SessionStore()
    for _ in range(5):
        assert store.register_attempt("a") is None
    fixed_time.advance(seconds=30)
    assert store.register_attempt("a") == 30.0
    fixed_time.advance(seconds=30)
    assert store.register_attempt("a") is None


def test_session_ids_are_random_and_long(fixed_time: FixedClock) -> None:
    store = SessionStore()
    ids = {store.create().id for _ in range(50)}
    assert len(ids) == 50
    assert all(len(session_id) == 43 for session_id in ids)  # token_urlsafe(32)
