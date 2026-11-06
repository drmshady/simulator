"""Round trips through the `jobs` table: enqueue, positions, claim, heartbeat, finish rules and cancel (T031, T048)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from dentalsim.errors import Conflict, NotFound
from dentalsim.persistence import JobStatus
from dentalsim.testing import FixedClock
from dentalsim_studio.db import SqliteRepos, open_repos


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


def test_enqueue_round_trip(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    job = repos.jobs.enqueue("run", "heavy", {"detail_level": "standard", "n": [1, 2]}, target_id="run-1")
    assert repos.jobs.get(job.id) == job
    assert job.lane == "heavy"
    assert job.kind == "run"
    assert job.payload == {"detail_level": "standard", "n": [1, 2]}
    assert job.target_id == "run-1"
    assert job.position == 1
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.cancel_requested is False
    assert job.created_at == fixed_time.now
    assert job.started_at is None and job.finished_at is None and job.pid is None
    assert repos.jobs.get("missing") is None


def test_positions_increase_within_each_lane(repos: SqliteRepos) -> None:
    heavy = [repos.jobs.enqueue("run", "heavy", {}) for _ in range(3)]
    preview = repos.jobs.enqueue("preview", "preview", {})
    assert [job.position for job in heavy] == [1, 2, 3]
    assert preview.position == 1


def test_front_goes_before_every_queued_job_of_its_lane(repos: SqliteRepos) -> None:
    first = repos.jobs.enqueue("run", "heavy", {})
    second = repos.jobs.enqueue("run", "heavy", {})
    urgent = repos.jobs.enqueue("verification", "heavy", {}, front=True)
    assert urgent.position < first.position < second.position
    assert [job.id for job in repos.jobs.list_jobs(lane="heavy")] == [urgent.id, first.id, second.id]
    # With nothing queued, `front` is simply the back of the lane.
    alone = repos.jobs.enqueue("preview", "preview", {}, front=True)
    assert alone.position == 1


def test_list_jobs_filters_and_orders_by_lane_then_position(repos: SqliteRepos) -> None:
    preview = repos.jobs.enqueue("preview", "preview", {})
    heavy_1 = repos.jobs.enqueue("run", "heavy", {})
    heavy_2 = repos.jobs.enqueue("export", "heavy", {})
    repos.jobs.request_cancel(heavy_2.id)
    assert [job.id for job in repos.jobs.list_jobs()] == [heavy_1.id, heavy_2.id, preview.id]
    assert [job.id for job in repos.jobs.list_jobs(lane="preview")] == [preview.id]
    assert [job.id for job in repos.jobs.list_jobs(statuses=["queued"])] == [heavy_1.id, preview.id]
    assert [job.id for job in repos.jobs.list_jobs(lane="heavy", statuses=["cancelled"])] == [heavy_2.id]
    assert repos.jobs.list_jobs(statuses=[]) == []


def test_claim_marks_the_job_running(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    fixed_time.advance(seconds=30)
    claimed = repos.jobs.claim("heavy")
    assert claimed is not None and claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.started_at == fixed_time.now
    assert claimed.heartbeat_at == fixed_time.now
    assert repos.jobs.claim("preview") is None


def test_heartbeat_updates_time_and_pid_of_a_running_job(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    repos.jobs.claim("heavy")
    fixed_time.advance(seconds=5)
    repos.jobs.heartbeat(job.id, pid=4242)
    fixed_time.advance(seconds=5)
    repos.jobs.heartbeat(job.id)  # keeps the pid
    stored = repos.jobs.get(job.id)
    assert stored is not None
    assert stored.heartbeat_at == fixed_time.now
    assert stored.pid == 4242
    queued = repos.jobs.enqueue("run", "heavy", {})
    repos.jobs.heartbeat(queued.id, pid=1)  # not running: nothing changes
    assert repos.jobs.get(queued.id) == queued


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_finish_ends_a_running_job(repos: SqliteRepos, fixed_time: FixedClock, status: JobStatus) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    repos.jobs.claim("heavy")
    repos.jobs.heartbeat(job.id, pid=99)
    fixed_time.advance(minutes=2)
    reason = "the solver stopped" if status == "failed" else None
    repos.jobs.finish(job.id, status, failure_reason=reason)
    stored = repos.jobs.get(job.id)
    assert stored is not None
    assert stored.status == status
    assert stored.failure_reason == reason
    assert stored.finished_at == fixed_time.now
    assert stored.pid is None


def test_finish_rules(repos: SqliteRepos) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    with pytest.raises(ValueError, match="failure reason"):
        repos.jobs.finish(job.id, "failed")
    with pytest.raises(ValueError, match="no failure reason"):
        repos.jobs.finish(job.id, "succeeded", failure_reason="oops")
    for status in ("queued", "running", "interrupted"):
        with pytest.raises(ValueError, match="can only finish"):
            repos.jobs.finish(job.id, status)
    with pytest.raises(NotFound):
        repos.jobs.finish("missing", "succeeded")
    repos.jobs.finish(job.id, "failed", failure_reason="refused before it started")  # a queued job may end too
    with pytest.raises(Conflict, match="already ended"):
        repos.jobs.finish(job.id, "succeeded")
    stored = repos.jobs.get(job.id)
    assert stored is not None and stored.status == "failed"


def test_cancel_a_queued_job_at_once(repos: SqliteRepos, fixed_time: FixedClock) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    cancelled = repos.jobs.request_cancel(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    assert cancelled.finished_at == fixed_time.now
    assert repos.jobs.claim("heavy") is None


def test_cancel_a_running_job_flags_it_for_the_worker(repos: SqliteRepos) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    repos.jobs.claim("heavy")
    flagged = repos.jobs.request_cancel(job.id)
    assert flagged.status == "running"
    assert flagged.cancel_requested is True
    repos.jobs.finish(job.id, "cancelled")
    stored = repos.jobs.get(job.id)
    assert stored is not None and stored.status == "cancelled"


def test_cancel_an_ended_job_changes_nothing(repos: SqliteRepos) -> None:
    job = repos.jobs.enqueue("run", "heavy", {})
    repos.jobs.claim("heavy")
    repos.jobs.finish(job.id, "succeeded")
    before = repos.jobs.get(job.id)
    assert repos.jobs.request_cancel(job.id) == before
    with pytest.raises(NotFound):
        repos.jobs.request_cancel("missing")


def test_the_schema_refuses_unknown_kinds_and_lanes(repos: SqliteRepos) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repos.jobs.enqueue("backup", "heavy", {})  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        repos.jobs.enqueue("run", "fast", {})  # type: ignore[arg-type]
