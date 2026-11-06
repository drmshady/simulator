"""The worker guards (T050): sleep prevention, the memory watchdog, and ending solver process trees.

The Windows API is replaced by fakes; the process tests start real Python child processes and end them.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import pytest

from dentalsim.persistence import JobRecord, Repos
from dentalsim_studio.db import SqliteRepos, open_repos
from dentalsim_studio.jobs import queue
from dentalsim_studio.worker import main as worker_main
from dentalsim_studio.worker import power
from dentalsim_studio.worker.handlers import OUT_OF_MEMORY_REASON, JobContext, override
from dentalsim_studio.worker.main import run_worker
from dentalsim_studio.worker.power import KEEP_AWAKE, RELEASE, PowerGuard
from dentalsim_studio.worker.watchdog import MemoryWatchdog, end_process_tree

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
PARENT_OF_SLEEPER = [
    sys.executable,
    "-c",
    "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(61)']); "
    "time.sleep(60)",
]


@dataclass
class FakeMemory:
    percent: float


def fake_memory(monkeypatch: pytest.MonkeyPatch, percent: float) -> None:
    monkeypatch.setattr(psutil, "virtual_memory", lambda: FakeMemory(percent))


def recorder(calls: list[int]) -> Callable[[int], int]:
    """A stand-in for `SetThreadExecutionState` that records its calls and succeeds."""

    def set_state(flags: int) -> int:
        calls.append(flags)
        return 1

    return set_state


def wait_for(check: Callable[[], Any], *, timeout: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting")


def wait_ended(repos: Repos, job_id: str) -> JobRecord:
    def ended() -> JobRecord | None:
        job = repos.jobs.get(job_id)
        return job if job is not None and job.status in ("succeeded", "failed", "cancelled") else None

    result: JobRecord = wait_for(ended, timeout=8)
    return result


@contextmanager
def child(command: list[str]) -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(command)
    try:
        yield process
    finally:
        if process.poll() is None:
            end_process_tree(process.pid, timeout_s=2)


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
        thread.join(timeout=20)
        assert not thread.is_alive(), "the worker did not stop"


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


def sleeper_handler(seen: dict[str, Any]) -> Callable[[JobContext], None]:
    """A handler that runs a sleeping solver stand-in, registered with the worker, and waits for it."""

    def handler(ctx: JobContext) -> None:
        with child(SLEEPER) as process:
            ctx.register_process(process.pid)
            seen["pid"] = process.pid
            seen["row_pid"] = ctx.repos.jobs.get(ctx.job_id).pid  # type: ignore[union-attr]
            seen["exit"] = process.wait(timeout=20)
            seen["out_of_memory"] = ctx.out_of_memory
        if ctx.out_of_memory:
            raise RuntimeError("the solver exited early")

    return handler


# Sleep prevention.


def test_power_guard_calls_only_on_changes() -> None:
    calls: list[int] = []
    guard = PowerGuard(set_state=recorder(calls))
    guard.update(True)
    guard.update(True)
    assert calls == [KEEP_AWAKE] and guard.keeping_awake
    guard.update(False)
    guard.update(False)
    guard.release()
    assert calls == [KEEP_AWAKE, RELEASE] and not guard.keeping_awake
    guard.update(True)
    guard.release()
    assert calls == [KEEP_AWAKE, RELEASE, KEEP_AWAKE, RELEASE]
    assert KEEP_AWAKE == 0x80000001 and RELEASE == 0x80000000


def test_power_guard_logs_a_failed_call(caplog: pytest.LogCaptureFixture) -> None:
    guard = PowerGuard(set_state=lambda flags: 0)
    guard.update(True)
    assert "SetThreadExecutionState failed" in caplog.text


class FakeKernel32:
    def __init__(self) -> None:
        self.calls: list[int] = []
        outer = self

        class Function:
            argtypes: Any = None
            restype: Any = None

            def __call__(self, flags: int) -> int:
                outer.calls.append(flags)
                return 0x80000000

        self.SetThreadExecutionState = Function()


class FakeWindll:
    def __init__(self) -> None:
        self.kernel32 = FakeKernel32()


@pytest.mark.skipif(sys.platform != "win32", reason="the default guard calls kernel32 only on Windows")
def test_power_guard_calls_kernel32_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    windll = FakeWindll()
    monkeypatch.setattr(ctypes, "windll", windll)
    guard = PowerGuard()
    guard.update(True)
    guard.update(False)
    assert windll.kernel32.calls == [KEEP_AWAKE, RELEASE]
    assert windll.kernel32.SetThreadExecutionState.argtypes == [ctypes.c_uint]


def test_power_guard_does_nothing_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    windll = FakeWindll()
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    monkeypatch.setattr(power, "IS_WINDOWS", False)
    guard = PowerGuard()
    guard.update(True)
    guard.update(False)
    assert windll.kernel32.calls == []


def test_the_worker_keeps_the_pc_awake_while_jobs_exist(
    data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(worker_main, "PowerGuard", lambda: PowerGuard(set_state=recorder(calls)))
    release = threading.Event()

    def handler(ctx: JobContext) -> None:
        release.wait(5)

    with override("export", handler), worker(data_dir):
        wait_for(lambda: calls == [RELEASE])  # idle at start: sleep allowed
        job = queue.enqueue(repos, "export", {})
        wait_for(lambda: calls == [RELEASE, KEEP_AWAKE])
        release.set()
        assert wait_ended(repos, job.id).status == "succeeded"
        wait_for(lambda: calls == [RELEASE, KEEP_AWAKE, RELEASE])


# Ending process trees.


def descendants(pid: int) -> list[psutil.Process]:
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def runs(process: psutil.Process, text: str) -> bool:
    try:
        return any(text in part for part in process.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def test_end_process_tree_ends_children_and_the_parent() -> None:
    with child(PARENT_OF_SLEEPER) as process:
        wait_for(lambda: any(runs(p, "time.sleep(61)") for p in descendants(process.pid)), timeout=8)
        time.sleep(0.2)  # let a launcher's own child appear
        tree = [psutil.Process(process.pid), *descendants(process.pid)]
        end_process_tree(process.pid, timeout_s=3)
        assert process.wait(timeout=5) is not None
        _, alive = psutil.wait_procs(tree, timeout=5)
        assert alive == []
    end_process_tree(process.pid)  # already gone: nothing to do


# The memory watchdog.


def test_watchdog_ends_the_solver_above_the_limit(
    data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = queue.enqueue(repos, "run", {}, target_id="run-1")
    ctx = JobContext(job, repos, threads=1)
    watchdog = MemoryWatchdog(limit_percent=92)
    fake_memory(monkeypatch, 99)
    assert watchdog.check_once() is False  # nothing watched
    watchdog.watch(ctx)
    assert watchdog.check_once() is False  # no solver process registered
    with child(SLEEPER) as process:
        ctx.register_process(process.pid)
        fake_memory(monkeypatch, 91.5)
        assert watchdog.check_once() is False
        assert process.poll() is None
        fake_memory(monkeypatch, 92.5)
        assert watchdog.check_once() is True
        assert process.wait(timeout=5) is not None
        assert ctx.out_of_memory and ctx.should_stop()
        assert watchdog.check_once() is False  # once per job
    watchdog.unwatch(ctx)
    assert watchdog.check_once() is False


def test_the_worker_fails_a_job_that_runs_out_of_memory(
    data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_memory(monkeypatch, 97)
    seen: dict[str, Any] = {}
    job = queue.enqueue(repos, "run", {}, target_id="run-2")
    with override("run", sleeper_handler(seen)), worker(data_dir):
        ended = wait_ended(repos, job.id)
    assert ended.status == "failed"
    assert ended.failure_reason == OUT_OF_MEMORY_REASON == "failed: out of memory, try a lower detail level"
    assert seen["out_of_memory"] is True
    assert seen["row_pid"] == seen["pid"]
    assert seen["exit"] is not None  # the solver stand-in was ended, long before its 60 s
    failed = [event.payload for event in repos.events.since(0) if event.type == "job.failed"]
    assert failed == [{"job_id": job.id, "kind": "run", "target_id": "run-2", "reason": OUT_OF_MEMORY_REASON}]


def test_previews_are_not_watched(data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_memory(monkeypatch, 97)

    def handler(ctx: JobContext) -> None:
        with child(SLEEPER) as process:
            ctx.register_process(process.pid)
            time.sleep(1.5)  # longer than a watchdog check interval
            assert process.poll() is None

    job = queue.enqueue(repos, "preview", {})
    with override("preview", handler), worker(data_dir):
        assert wait_ended(repos, job.id).status == "succeeded"


# Cancel and shutdown end the solver too.


def test_cancel_ends_the_registered_solver(data_dir: Path, repos: SqliteRepos, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_main, "MONITOR_INTERVAL_S", 0.05)
    seen: dict[str, Any] = {}
    job = queue.enqueue(repos, "run", {}, target_id="run-3")
    with override("run", sleeper_handler(seen)), worker(data_dir):
        wait_for(lambda: "pid" in seen)
        queue.cancel(repos, job.id)
        ended = wait_ended(repos, job.id)
    assert ended.status == "cancelled"
    assert "exit" in seen and seen["out_of_memory"] is False


def test_stopping_the_worker_ends_the_solver_and_leaves_the_job_for_recovery(
    data_dir: Path, repos: SqliteRepos
) -> None:
    seen: dict[str, Any] = {}
    job = queue.enqueue(repos, "run", {}, target_id="run-4")
    with override("run", sleeper_handler(seen)), worker(data_dir):
        wait_for(lambda: "pid" in seen)
    assert "exit" in seen  # the solver was ended, so the handler returned
    left = repos.jobs.get(job.id)
    assert left is not None and left.status == "running"
