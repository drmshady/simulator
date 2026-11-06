"""Events: the bus checks the types, and `GET /api/events` streams them as Server-Sent Events (T045, research R10)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dentalsim.testing import FixedClock
from dentalsim_studio.api import events as events_api
from dentalsim_studio.api.events import event_stream, format_event, parse_last_event_id
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.password import set_password
from dentalsim_studio.auth.sessions import SessionStore
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.events.bus import EVENT_TYPES, emit

PASSWORD = "correct horse battery"


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


@pytest.fixture
def app(data_dir: Path, repos: SqliteRepos) -> FastAPI:
    set_password(repos, PASSWORD)
    studio = create_app(data_dir=data_dir)
    studio.state.test_hooks = True
    studio.state.events_poll_interval_s = 0.01
    return studio


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as test_client:
        assert test_client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        yield test_client


def parse_stream(text: str) -> list[dict[str, Any]]:
    """The events of an SSE body, as {"id", "event", "data"}; comments and `retry:` lines are left out."""
    found = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if "event" in fields:
            found.append({"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])})
    return found


def read(client: TestClient, url: str, **headers: str) -> str:
    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        return response.read().decode()


# The bus -------------------------------------------------------------------------------------------------------------


def test_contract_event_types() -> None:
    assert {
        "preview.ready",
        "run.progress",
        "run.finished",
        "study.progress",
        "job.failed",
        "validity.changed",
        "capability.changed",
    } == EVENT_TYPES


def test_emit_appends_to_the_events_table(repos: SqliteRepos) -> None:
    payload = {"run_id": "r1", "percent": 40, "stage": "solve", "remaining_s": 120}
    first = emit(repos, "run.progress", payload)
    second = emit(repos, "run.finished", {"run_id": "r1"})
    assert second == first + 1
    stored = repos.events.since(0)
    assert [(e.type, e.payload) for e in stored] == [("run.progress", payload), ("run.finished", {"run_id": "r1"})]


def test_unknown_event_types_are_refused(repos: SqliteRepos) -> None:
    with pytest.raises(ValueError, match="unknown event type 'run.progres'"):
        emit(repos, "run.progres", {})
    assert repos.events.latest_id() == 0


def test_event_format() -> None:
    from datetime import UTC, datetime

    from dentalsim.persistence import EventRecord

    record = EventRecord(
        id=7, type="job.failed", payload={"reason": "out of memory\nline 2"}, created_at=datetime.now(UTC)
    )
    assert format_event(record) == 'id: 7\nevent: job.failed\ndata: {"reason":"out of memory\\nline 2"}\n\n'


@pytest.mark.parametrize(
    ("value", "parsed"), [("12", 12), (" 3 ", 3), ("0", 0), ("-1", None), ("x", None), (None, None)]
)
def test_last_event_id_parsing(value: str | None, parsed: int | None) -> None:
    assert parse_last_event_id(value) == parsed


# The stream ----------------------------------------------------------------------------------------------------------


def test_stream_sends_events_in_order(client: TestClient, repos: SqliteRepos) -> None:
    emit(repos, "preview.ready", {"preview_id": "p1"})
    emit(repos, "run.progress", {"run_id": "r1", "percent": 10, "stage": "mesh", "remaining_s": 600})
    emit(repos, "validity.changed", {"scenario_id": "s1"})
    text = read(client, "/api/events?after=0&max_polls=2")
    assert text.startswith("retry: 3000\n\n")
    assert parse_stream(text) == [
        {"id": 1, "event": "preview.ready", "data": {"preview_id": "p1"}},
        {
            "id": 2,
            "event": "run.progress",
            "data": {"run_id": "r1", "percent": 10, "stage": "mesh", "remaining_s": 600},
        },
        {"id": 3, "event": "validity.changed", "data": {"scenario_id": "s1"}},
    ]
    assert 'id: 1\nevent: preview.ready\ndata: {"preview_id":"p1"}\n\n' in text


def test_stream_resumes_after_last_event_id(client: TestClient, repos: SqliteRepos) -> None:
    for n in range(4):
        emit(repos, "run.progress", {"run_id": "r1", "percent": n * 25})
    text = read(client, "/api/events?max_polls=2", **{"Last-Event-ID": "2"})
    assert [e["id"] for e in parse_stream(text)] == [3, 4]
    # The header wins over ?after=, since a reconnecting browser sends the newer id.
    text = read(client, "/api/events?after=0&max_polls=2", **{"Last-Event-ID": "3"})
    assert [e["id"] for e in parse_stream(text)] == [4]


def test_new_stream_starts_after_the_latest_event(client: TestClient, repos: SqliteRepos) -> None:
    emit(repos, "run.finished", {"run_id": "old"})
    assert parse_stream(read(client, "/api/events?max_polls=2")) == []


def test_heartbeat_comment_on_an_idle_stream(app: FastAPI, client: TestClient) -> None:
    app.state.events_heartbeat_s = 0.0
    text = read(client, "/api/events?max_polls=3")
    assert text.count(": heartbeat\n\n") == 3
    assert parse_stream(text) == []


def test_max_polls_is_ignored_without_the_test_flag(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_stream(data_dir: Path, after: int | None, **options: Any) -> Any:
        seen.update(options, after=after)
        yield "retry: 3000\n\n"

    monkeypatch.setattr(events_api, "event_stream", fake_stream)
    app.state.test_hooks = False
    read(client, "/api/events?after=5&max_polls=1")
    assert seen["max_polls"] is None and seen["after"] == 5
    app.state.test_hooks = True
    read(client, "/api/events?max_polls=1")
    assert seen["max_polls"] == 1 and seen["after"] is None


def test_stream_picks_up_events_emitted_while_it_runs(data_dir: Path, repos: SqliteRepos) -> None:
    checks = 0

    async def is_disconnected() -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:
            emit(repos, "capability.changed", {"capability_id": "bonded_elastic", "status": "ready"})
        return False

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in event_stream(
                data_dir, None, is_disconnected=is_disconnected, poll_interval_s=0.0, max_polls=3
            )
        ]

    chunks = asyncio.run(collect())
    assert parse_stream("".join(chunks)) == [
        {"id": 1, "event": "capability.changed", "data": {"capability_id": "bonded_elastic", "status": "ready"}}
    ]


def test_stream_stops_and_closes_its_connection_when_the_client_leaves(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[SqliteRepos] = []
    closed: list[SqliteRepos] = []

    def tracking_open(path: Path) -> SqliteRepos:
        repos = open_repos(path)
        opened.append(repos)
        original = repos.close

        def close() -> None:
            closed.append(repos)
            original()

        repos.close = close  # type: ignore[method-assign]
        return repos

    monkeypatch.setattr(events_api, "open_repos", tracking_open)
    answers = iter([False, True])

    async def is_disconnected() -> bool:
        return next(answers)

    async def collect() -> list[str]:
        return [chunk async for chunk in event_stream(data_dir, 0, is_disconnected=is_disconnected, poll_interval_s=0)]

    assert asyncio.run(collect()) == ["retry: 3000\n\n"]
    assert len(opened) == 1 and closed == opened


def test_stream_ends_when_the_session_ends(data_dir: Path) -> None:
    answers = iter([True, True, False])

    async def is_disconnected() -> bool:
        return False

    async def collect() -> list[str]:
        stream = event_stream(
            data_dir, 0, is_disconnected=is_disconnected, signed_in=lambda: next(answers), poll_interval_s=0
        )
        return [chunk async for chunk in stream]

    assert asyncio.run(collect()) == ["retry: 3000\n\n"]


def test_session_liveness_check_does_not_extend_the_session(fixed_time: FixedClock) -> None:
    store = SessionStore()
    session = store.create()
    fixed_time.advance(hours=1)
    assert store.is_live(session.id)
    fixed_time.advance(hours=1, seconds=1)
    assert not store.is_live(session.id)  # the check above did not move the deadline
    assert not store.is_live(None)
