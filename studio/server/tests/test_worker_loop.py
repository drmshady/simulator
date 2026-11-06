"""The worker loop (T049; research R9, FR-019, FR-056): `run_worker` in a thread with fake handlers.

Covers success with progress events, handler failures, cancel mid-run, a kind with no handler, the lanes' thread
counts and independence, heartbeats, and stopping with a job running, which start-up recovery then requeues.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest

from dentalsim.errors import Refused, SolverFailed
from dentalsim.persistence import JobRecord, Repos
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.jobs import queue
from dentalsim_studio.worker import main as worker_main
from dentalsim_studio.worker.handlers import JobContext, override
from dentalsim_studio.worker.main import lane_threads, run_worker

ENDED = ("succeeded", "failed", "cancelled")


def wait_for[T](check: Callable[[], T | None], *, timeout: float = 5.0) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def wait_ended(repos: Repos, job_id: str) -> JobRecord:
    def ended() -> JobRecord | None:
        job = repos.jobs.get(job_id)
        return job if job is not None and job.status in ENDED else None

    return wait_for(ended)


@contextmanager
def worker(data_dir: Path) -> Iterator[None]:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker, args=(data_dir,), kwargs={"stop": stop, "poll_interval_s": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=15)
        assert not thread.is_alive(), "the worker did not stop"


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


def test_a_job_succeeds_and_reports_progress(data_dir: Path, repos: SqliteRepos) -> None:
    seen: dict[str, Any] = {}

    def handler(ctx: JobContext) -> None:
        seen.update(threads=ctx.threads, omp=ctx.solver_env()["OMP_NUM_THREADS"], payload=ctx.payload)
        ctx.progress(10, "meshing", 120)
        ctx.progress(60, "solving", 30)
        ctx.progress(100, "solving", 0)

    job = queue.enqueue(repos, "run", {"detail_level": "standard"}, target_id="run-1")
    with override("run", handler), worker(data_dir):
        ended = wait_ended(repos, job.id)
    assert ended.status == "succeeded"
    assert ended.failure_reason is None
    assert ended.attempts == 1
    assert seen == {"threads": lane_threads("heavy"), "omp": str(lane_threads("heavy")), "payload": job.payload}
    progress = [event.payload for event in repos.events.since(0) if event.type == "run.progress"]
    assert progress == [
        {"run_id": "run-1", "percent": 10.0, "stage": "meshing", "remaining_s": 120},
        {"run_id": "run-1", "percent": 60.0, "stage": "solving", "remaining_s": 30},
        {"run_id": "run-1", "percent": 100.0, "stage": "solving", "remaining_s": 0},
    ]
    assert "worker starting" in (data_dir / "logs" / "worker.log").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (RuntimeError("boom"), "unexpected error: RuntimeError: boom"),
        (Refused("bonded_elastic is awaiting its benchmark"), "bonded_elastic is awaiting its benchmark"),
        (
            SolverFailed("the solver stopped: singular stiffness matrix"),
            "the solver stopped: singular stiffness matrix",
        ),
    ],
)
def test_a_handler_error_fails_the_job_with_a_plain_reason(
    data_dir: Path, repos: SqliteRepos, error: Exception, reason: str
) -> None:
    def handler(ctx: JobContext) -> None:
        raise error

    job = queue.enqueue(repos, "export", {}, target_id="export-1")
    with override("export", handler), worker(data_dir):
        ended = wait_ended(repos, job.id)
    assert ended.status == "failed"
    assert ended.failure_reason == reason
    failed = [event.payload for event in repos.events.since(0) if event.type == "job.failed"]
    assert failed == [{"job_id": job.id, "kind": "export", "target_id": "export-1", "reason": reason}]


def test_the_lane_keeps_going_after_a_failure(data_dir: Path, repos: SqliteRepos) -> None:
    def handler(ctx: JobContext) -> None:
        if ctx.payload["fail"]:
            raise ValueError("bad payload")

    failing = queue.enqueue(repos, "export", {"fail": True})
    passing = queue.enqueue(repos, "export", {"fail": False})
    with override("export", handler), worker(data_dir):
        assert wait_ended(repos, failing.id).status == "failed"
        assert wait_ended(repos, passing.id).status == "succeeded"


def test_a_job_cancelled_mid_run_ends_cancelled(data_dir: Path, repos: SqliteRepos) -> None:
    started = threading.Event()
    seen: dict[str, bool] = {}

    def handler(ctx: JobContext) -> None:
        started.set()
        deadline = time.monotonic() + 5
        while not ctx.should_stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        seen["cancel_requested"] = ctx.cancel_requested()
        raise RuntimeError("stopped part way")  # how it ends does not matter once cancel was asked for

    job = queue.enqueue(repos, "run", {}, target_id="run-2")
    with override("run", handler), worker(data_dir):
        assert started.wait(5)
        assert queue.cancel(repos, job.id).status == "running"
        ended = wait_ended(repos, job.id)
    assert ended.status == "cancelled"
    assert ended.failure_reason is None
    assert seen == {"cancel_requested": True}
    assert [event for event in repos.events.since(0) if event.type == "job.failed"] == []


def test_a_kind_without_a_handler_fails(data_dir: Path, repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "promo", {}, target_id="promo-1")
    with override("promo", None), worker(data_dir):
        ended = wait_ended(repos, job.id)
    assert ended.status == "failed"
    assert ended.failure_reason == "no handler for job kind 'promo' yet"
    assert [event.type for event in repos.events.since(0)] == ["job.failed"]


def test_heavy_jobs_run_one_at_a_time_in_order(data_dir: Path, repos: SqliteRepos) -> None:
    order: list[int] = []
    running = 0
    most = 0
    lock = threading.Lock()

    def handler(ctx: JobContext) -> None:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
            order.append(ctx.payload["index"])
        time.sleep(0.05)
        with lock:
            running -= 1

    jobs = [queue.enqueue(repos, "export", {"index": index}) for index in range(4)]
    with override("export", handler), worker(data_dir):
        for job in jobs:
            assert wait_ended(repos, job.id).status == "succeeded"
    assert most == 1
    assert order == [0, 1, 2, 3]


def test_the_preview_lane_runs_beside_a_heavy_job_with_two_threads(data_dir: Path, repos: SqliteRepos) -> None:
    preview_done = threading.Event()
    threads: dict[str, int] = {}

    def heavy(ctx: JobContext) -> None:
        threads["heavy"] = ctx.threads
        if not preview_done.wait(5):
            raise RuntimeError("the preview never ran beside the heavy job")

    def preview(ctx: JobContext) -> None:
        threads["preview"] = ctx.threads
        preview_done.set()

    heavy_job = queue.enqueue(repos, "export", {})
    preview_job = queue.enqueue(repos, "preview", {})
    with override("export", heavy), override("preview", preview), worker(data_dir):
        assert wait_ended(repos, heavy_job.id).status == "succeeded"
        assert wait_ended(repos, preview_job.id).status == "succeeded"
    assert threads == {"heavy": lane_threads("heavy"), "preview": 2}
    assert lane_threads("heavy") >= 1


def test_running_jobs_get_heartbeats(data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_main, "MONITOR_INTERVAL_S", 0.02)
    monkeypatch.setattr(worker_main, "HEARTBEAT_INTERVAL_S", 0.05)
    beats: list[Any] = []

    def handler(ctx: JobContext) -> None:
        deadline = time.monotonic() + 5
        while len(beats) < 2 and time.monotonic() < deadline:
            job = ctx.repos.jobs.get(ctx.job_id)
            assert job is not None
            if job.heartbeat_at not in beats:
                beats.append(job.heartbeat_at)
            time.sleep(0.01)

    job = queue.enqueue(repos, "export", {})
    with override("export", handler), worker(data_dir):
        assert wait_ended(repos, job.id).status == "succeeded"
    assert len(beats) == 2 and beats[1] > beats[0]


def test_stopping_leaves_the_running_job_for_recovery_at_the_next_start(data_dir: Path, repos: SqliteRepos) -> None:
    started = threading.Event()
    seen: dict[str, bool] = {}

    def interrupted(ctx: JobContext) -> None:
        started.set()
        deadline = time.monotonic() + 5
        while not ctx.should_stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        seen.update(shutting_down=ctx.shutting_down, cancel_requested=ctx.cancel_requested())

    requeued: list[JobRecord] = []

    def hook(job: JobRecord, hook_repos: Repos) -> None:
        requeued.append(job)

    first = queue.enqueue(repos, "run", {}, target_id="run-3")
    second = queue.enqueue(repos, "run", {}, target_id="run-4")
    with override("run", interrupted), worker(data_dir):
        assert started.wait(5)
    assert seen == {"shutting_down": True, "cancel_requested": False}
    left = repos.jobs.get(first.id)
    assert left is not None and left.status == "running"

    ran: list[str] = []
    with ExitStack() as stack:
        stack.enter_context(override("run", lambda ctx: ran.append(ctx.job_id), requeue_hook=hook))
        stack.enter_context(worker(data_dir))
        recovered = wait_ended(repos, first.id)
        wait_ended(repos, second.id)
    assert [job.id for job in requeued] == [first.id]
    assert requeued[0].status == "queued"
    assert recovered.status == "succeeded"
    assert recovered.attempts == 2
    assert ran == [first.id, second.id]  # the interrupted job went back to the front


def test_progress_reports_within_a_stage_are_thinned(repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "run", {}, target_id="run-5")
    ctx = JobContext(job, repos, threads=1)
    for percent in range(0, 100, 10):
        ctx.progress(percent, "solving")
    ctx.progress(100, "solving")
    ctx.progress(150, "writing results")
    percents = [event.payload["percent"] for event in repos.events.since(0) if event.type == "run.progress"]
    assert percents == [0.0, 100.0, 100.0]


def test_progress_of_other_kinds_emits_no_run_events(repos: SqliteRepos) -> None:
    job = queue.enqueue(repos, "export", {})
    JobContext(job, repos, threads=1).progress(50, "rendering")
    assert repos.events.since(0) == []
