"""The queue's guarantees (T048, T051; research R9, FR-019, FR-056).

- two heavy jobs never run at once, even when two worker connections race for them
- a job left `running` at start-up becomes `interrupted`, then queued at the front, and is the next one claimed
- queued jobs keep their order, and `front=True` jumps the queue
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from dentalsim.errors import NotFound
from dentalsim.persistence import JobRecord
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.jobs import queue


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


def restart(data_dir: Path, old: SqliteRepos) -> SqliteRepos:
    """The worker died without finishing its jobs; a new one opens the database."""
    old.close()
    return open_repos(data_dir)


def record_status_changes(repos: SqliteRepos) -> None:
    """Log every status change made on this connection into the temporary table `transitions`."""
    repos.db.execute("CREATE TEMP TABLE transitions (job_id TEXT, old TEXT, new TEXT)")
    repos.db.execute(
        """CREATE TEMP TRIGGER record_transition AFTER UPDATE OF status ON main.jobs
           BEGIN INSERT INTO transitions VALUES (old.id, old.status, new.status); END"""
    )


# One heavy job at a time.


def test_two_heavy_jobs_never_run_at_once(repos: SqliteRepos) -> None:
    first = queue.enqueue(repos, "run", {})
    second = queue.enqueue(repos, "run", {})
    claimed = queue.claim(repos, "heavy")
    assert claimed is not None and claimed.id == first.id
    assert queue.claim(repos, "heavy") is None  # the lane is busy
    preview = queue.enqueue(repos, "preview", {})
    assert queue.claim(repos, "preview") == repos.jobs.get(preview.id)  # the preview lane is separate
    queue.finish(repos, first.id, "succeeded")
    after = queue.claim(repos, "heavy")
    assert after is not None and after.id == second.id


def test_racing_connections_claim_only_one_heavy_job(data_dir: Path, repos: SqliteRepos) -> None:
    for _ in range(5):
        queue.enqueue(repos, "run", {})
    racers = 4
    barrier = threading.Barrier(racers, timeout=10)
    claims: list[JobRecord] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def race() -> None:
        own = open_repos(data_dir)
        try:
            barrier.wait()
            for _ in range(10):
                job = own.jobs.claim("heavy")
                if job is not None:
                    with lock:
                        claims.append(job)
        except BaseException as exc:
            errors.append(exc)
        finally:
            own.close()

    threads = [threading.Thread(target=race) for _ in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert errors == []
    assert len(claims) == 1
    assert len(repos.jobs.list_jobs(statuses=["running"])) == 1


def test_racing_connections_take_turns_as_jobs_finish(data_dir: Path, repos: SqliteRepos) -> None:
    rounds = 6
    jobs = [queue.enqueue(repos, "run", {}) for _ in range(rounds)]
    start = threading.Barrier(3, timeout=10)
    done = threading.Barrier(3, timeout=10)
    claims: list[list[JobRecord | None]] = [[] for _ in range(rounds)]

    def race() -> None:
        own = open_repos(data_dir)
        try:
            for index in range(rounds):
                start.wait()
                claims[index].append(own.jobs.claim("heavy"))
                done.wait()
        finally:
            own.close()

    threads = [threading.Thread(target=race) for _ in range(2)]
    for thread in threads:
        thread.start()
    for index in range(rounds):
        start.wait()
        done.wait()
        winners = [job for job in claims[index] if job is not None]
        assert len(winners) == 1, claims[index]
        assert winners[0].id == jobs[index].id  # still in queue order
        queue.finish(repos, winners[0].id, "succeeded")
    for thread in threads:
        thread.join(timeout=10)


# Start-up recovery (FR-056).


def test_a_job_left_running_is_interrupted_then_requeued_at_the_front(data_dir: Path, repos: SqliteRepos) -> None:
    first = queue.enqueue(repos, "run", {}, target_id="run-1")
    second = queue.enqueue(repos, "run", {}, target_id="run-2")
    third = queue.enqueue(repos, "run", {}, target_id="run-3")
    queue.claim(repos, "heavy")
    repos.jobs.heartbeat(first.id, pid=1234)

    repos = restart(data_dir, repos)
    try:
        record_status_changes(repos)
        requeued = queue.requeue_interrupted(repos)
        transitions = [tuple(row) for row in repos.db.all("SELECT job_id, old, new FROM transitions")]
        assert transitions == [(first.id, "running", "interrupted"), (first.id, "interrupted", "queued")]
        assert [job.id for job in requeued] == [first.id]
        job = requeued[0]
        assert job.status == "queued"
        assert job.pid is None and job.started_at is None
        assert job.attempts == 1
        queued = repos.jobs.list_jobs(lane="heavy", statuses=["queued"])
        assert [j.id for j in queued] == [first.id, second.id, third.id]
        assert queued[0].position < queued[1].position

        again = queue.claim(repos, "heavy")
        assert again is not None and again.id == first.id
        assert again.attempts == 2
    finally:
        repos.close()


def test_the_interrupted_job_goes_before_jobs_queued_at_the_front_earlier(data_dir: Path, repos: SqliteRepos) -> None:
    running = queue.enqueue(repos, "run", {})
    queued = queue.enqueue(repos, "run", {})
    queue.claim(repos, "heavy")
    urgent = queue.enqueue(repos, "verification", {}, front=True)
    repos = restart(data_dir, repos)
    try:
        queue.requeue_interrupted(repos)
        order = [job.id for job in repos.jobs.list_jobs(lane="heavy", statuses=["queued"])]
        assert order == [running.id, urgent.id, queued.id]
    finally:
        repos.close()


def test_several_interrupted_jobs_keep_their_relative_order(data_dir: Path, repos: SqliteRepos) -> None:
    jobs = [queue.enqueue(repos, "run", {}) for _ in range(4)]
    preview = queue.enqueue(repos, "preview", {})
    preview_waiting = queue.enqueue(repos, "preview", {})
    queue.claim(repos, "preview")
    # A state only a crash mid-transition could leave: two running jobs in one lane.
    repos.db.execute("UPDATE jobs SET status = 'running' WHERE id IN (?, ?)", (jobs[1].id, jobs[2].id))
    repos = restart(data_dir, repos)
    try:
        requeued = queue.requeue_interrupted(repos)
        assert [job.id for job in requeued] == [jobs[1].id, jobs[2].id, preview.id]
        heavy = [job.id for job in repos.jobs.list_jobs(lane="heavy", statuses=["queued"])]
        assert heavy == [jobs[1].id, jobs[2].id, jobs[0].id, jobs[3].id]
        previews = [job.id for job in repos.jobs.list_jobs(lane="preview", statuses=["queued"])]
        assert previews == [preview.id, preview_waiting.id]
    finally:
        repos.close()


def test_a_pending_cancel_survives_the_restart(data_dir: Path, repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "run", {})
    queue.claim(repos, "heavy")
    queue.cancel(repos, job.id)
    repos = restart(data_dir, repos)
    try:
        (requeued,) = queue.requeue_interrupted(repos)
        assert requeued.status == "queued"
        assert requeued.cancel_requested is True  # its handler stops it as soon as it is claimed again
    finally:
        repos.close()


# Queue order.


def test_queued_jobs_keep_their_order_across_a_restart(data_dir: Path, repos: SqliteRepos) -> None:
    jobs = [queue.enqueue(repos, "run", {"index": index}) for index in range(4)]
    repos = restart(data_dir, repos)
    try:
        assert queue.requeue_interrupted(repos) == []
        order = []
        while (job := queue.claim(repos, "heavy")) is not None:
            order.append(job.id)
            queue.finish(repos, job.id, "succeeded")
        assert order == [job.id for job in jobs]
    finally:
        repos.close()


def test_front_jumps_the_queue(repos: SqliteRepos) -> None:
    back = [queue.enqueue(repos, "run", {}) for _ in range(2)]
    front = queue.enqueue(repos, "mesh_check", {}, front=True)
    order = []
    while (job := queue.claim(repos, "heavy")) is not None:
        order.append(job.id)
        queue.finish(repos, job.id, "succeeded")
    assert order == [front.id, back[0].id, back[1].id]


# The queue service.


def test_the_lane_follows_the_job_kind(repos: SqliteRepos) -> None:
    assert queue.lane_for("preview") == "preview"
    for kind in ("run", "mesh_check", "verification", "export", "promo", "interactive_data"):
        assert queue.lane_for(kind) == "heavy"
    assert queue.enqueue(repos, "preview", {}).lane == "preview"
    assert queue.enqueue(repos, "export", {}).lane == "heavy"
    assert queue.enqueue(repos, "export", {}, lane="preview").lane == "preview"


def test_queue_summary_counts_queued_and_running_jobs(repos: SqliteRepos) -> None:
    empty = {"heavy": {"queued": 0, "running": 0}, "preview": {"queued": 0, "running": 0}}
    assert queue.queue_summary(repos) == empty
    assert queue.has_work(repos) is False
    for _ in range(3):
        queue.enqueue(repos, "run", {})
    queue.enqueue(repos, "preview", {})
    done = queue.enqueue(repos, "export", {})
    queue.cancel(repos, done.id)
    queue.claim(repos, "heavy")
    assert queue.queue_summary(repos) == {"heavy": {"queued": 2, "running": 1}, "preview": {"queued": 1, "running": 0}}
    assert queue.has_work(repos) is True
    assert [job.status for job in queue.active_jobs(repos, "heavy")] == ["running", "queued", "queued"]


def test_a_failure_emits_job_failed_in_the_same_transaction(repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "run", {}, target_id="run-7")
    queue.claim(repos, "heavy")
    before = repos.events.latest_id()
    finished = queue.finish(repos, job.id, "failed", failure_reason="the mesh could not be made")
    assert finished.status == "failed"
    events = repos.events.since(before)
    assert [(event.type, event.payload) for event in events] == [
        ("job.failed", {"job_id": job.id, "kind": "run", "target_id": "run-7", "reason": "the mesh could not be made"})
    ]
    other = queue.enqueue(repos, "run", {})
    queue.claim(repos, "heavy")
    queue.finish(repos, other.id, "succeeded")
    assert repos.events.since(events[-1].id) == []


def test_cancel_through_the_service(repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "run", {})
    assert queue.cancel(repos, job.id).status == "cancelled"
    with pytest.raises(NotFound):
        queue.cancel(repos, "missing")
