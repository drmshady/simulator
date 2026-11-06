"""The worker process (research R9, FR-019, FR-056, FR-057): runs the queued jobs unattended, one per lane at a time.

`python -m dentalsim_studio.worker.main` and `dentalsim worker` start it alone; `dentalsim studio serve` starts it
next to the server. Its threads, each with its own database connection where it needs one:

- the main loop: start-up recovery (FR-056), then keeps Windows awake while jobs exist and restarts a lane loop
  that died
- one lane loop per lane: claims the lane's next job and runs its handler with the lane's solver thread count
- the job monitor: heartbeats for the running jobs, and ends the solver process tree of a cancelled job
- the memory watchdog, for the heavy lane's job

Stopping (Ctrl+C, or `stop` set) interrupts the running jobs: their solver processes are ended and their rows stay
`running`, so the next start requeues them at the front. Logs go to `<data>/logs/worker.log` and stderr.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Any

from dentalsim.config import ensure_data_folder
from dentalsim.errors import Conflict, DentalsimError, Refused
from dentalsim.persistence import JobLane, JobRecord, JobStatus, Repos
from dentalsim.threads import heavy_threads, preview_threads
from dentalsim_studio.db import open_repos
from dentalsim_studio.db.repos.jobs import LANES
from dentalsim_studio.jobs import queue
from dentalsim_studio.worker.handlers import (
    OUT_OF_MEMORY_REASON,
    JobContext,
    discover_handlers,
    get_handler,
    get_requeue_hook,
    registered_kinds,
)
from dentalsim_studio.worker.power import PowerGuard
from dentalsim_studio.worker.watchdog import MEMORY_LIMIT_PERCENT, MemoryWatchdog, end_process_tree

MONITOR_INTERVAL_S = 1.0
HEARTBEAT_INTERVAL_S = 5.0
SHUTDOWN_GRACE_S = 10.0
DB_RETRY_DELAY_S = 1.0
FINISH_ATTEMPTS = 5
LOG_FILE = "worker.log"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUPS = 5
LOGGED_PACKAGES = ("dentalsim_studio", "dentalsim")

log = logging.getLogger("dentalsim_studio.worker")


def lane_threads(lane: JobLane) -> int:
    """Solver threads per job (`dentalsim.threads`): 2 for previews, every physical core but 2 for heavy jobs."""
    return preview_threads() if lane == "preview" else heavy_threads()


def failure_reason(exc: BaseException) -> str:
    """The plain reason a job failed: the engine's own message, or the type and message of an unexpected error."""
    if isinstance(exc, Refused | DentalsimError):
        return str(exc) or type(exc).__name__
    return f"unexpected error: {type(exc).__name__}: {exc}"


def job_outcome(context: JobContext, error: BaseException | None) -> tuple[JobStatus | None, str | None]:
    """The final status and reason of a job whose handler has ended; None while the worker is shutting down."""
    if context.out_of_memory:
        return "failed", OUT_OF_MEMORY_REASON
    if context.shutting_down:
        return None, None
    if context.cancel_requested():
        return "cancelled", None
    if error is not None:
        return "failed", failure_reason(error)
    return "succeeded", None


def _finish(repos: Repos, job: JobRecord, status: JobStatus, reason: str | None, logger: logging.Logger) -> None:
    for attempt in range(1, FINISH_ATTEMPTS + 1):
        try:
            queue.finish(repos, job.id, status, failure_reason=reason)
        except Conflict as exc:
            logger.warning("job %s: not finished as %s: %s", job.id, status, exc)
            return
        except sqlite3.Error:
            logger.exception("job %s: could not record %s (attempt %d)", job.id, status, attempt)
            time.sleep(DB_RETRY_DELAY_S)
        else:
            logger.info("job %s (%s): %s%s", job.id, job.kind, status, f": {reason}" if reason else "")
            return
    logger.error("job %s: left running; it is requeued when the worker starts again", job.id)


class JobMonitor:
    """Heartbeats for the running jobs, and ends a cancelled job's solver processes, from one thread."""

    def __init__(self, data_dir: Path, stop: threading.Event) -> None:
        self.data_dir = data_dir
        self.stop = stop
        self.interval_s = MONITOR_INTERVAL_S
        self.heartbeat_interval_s = HEARTBEAT_INTERVAL_S
        self._lock = threading.Lock()
        self._contexts: dict[str, JobContext] = {}
        self._last_beat: dict[str, float] = {}
        self._thread: threading.Thread | None = None

    def watch(self, context: JobContext) -> None:
        with self._lock:
            self._contexts[context.job_id] = context
            self._last_beat[context.job_id] = time.monotonic()

    def unwatch(self, context: JobContext) -> None:
        with self._lock:
            self._contexts.pop(context.job_id, None)
            self._last_beat.pop(context.job_id, None)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="worker-monitor", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def check(self, repos: Repos, context: JobContext) -> None:
        job = repos.jobs.get(context.job_id)
        if job is None or job.cancel_requested:
            if context.note_cancel():
                context.logger.info("job %s: cancel requested; ending its solver processes", context.job_id)
            for process in context.processes:
                end_process_tree(process)
        now = time.monotonic()
        with self._lock:
            due = now - self._last_beat.get(context.job_id, now) >= self.heartbeat_interval_s
            if due:
                self._last_beat[context.job_id] = now
        if due:
            repos.jobs.heartbeat(context.job_id)

    def _run(self) -> None:
        repos = open_repos(self.data_dir)
        try:
            while not self.stop.wait(self.interval_s):
                with self._lock:
                    contexts = list(self._contexts.values())
                for context in contexts:
                    try:
                        self.check(repos, context)
                    except Exception:
                        log.exception("job monitor: check of job %s failed", context.job_id)
        finally:
            repos.close()


class LaneLoop:
    """Claims and runs the jobs of one lane, one at a time, on its own thread and database connection."""

    def __init__(
        self,
        lane: JobLane,
        data_dir: Path,
        stop: threading.Event,
        *,
        poll_interval_s: float,
        monitor: JobMonitor,
        watchdog: MemoryWatchdog | None,
    ) -> None:
        self.lane = lane
        self.data_dir = data_dir
        self.stop = stop
        self.poll_interval_s = poll_interval_s
        self.monitor = monitor
        self.watchdog = watchdog
        self.threads = lane_threads(lane)
        self.logger = log.getChild(lane)
        self._lock = threading.Lock()
        self._current: JobContext | None = None
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"worker-{self.lane}", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def interrupt(self) -> None:
        """For shutdown, after `stop` is set: the running job stops and stays `running` for start-up recovery."""
        with self._lock:
            context = self._current
            if context is not None:
                context.mark_shutting_down()
        if context is not None:
            for process in context.processes:
                end_process_tree(process)

    def _run(self) -> None:
        repos = open_repos(self.data_dir)
        try:
            while not self.stop.is_set():
                try:
                    job = queue.claim(repos, self.lane)
                except sqlite3.Error:
                    self.logger.exception("%s lane: could not claim the next job", self.lane)
                    self.stop.wait(DB_RETRY_DELAY_S)
                    continue
                if job is None:
                    self.stop.wait(self.poll_interval_s)
                    continue
                self.run_job(repos, job)
        finally:
            repos.close()

    def run_job(self, repos: Repos, job: JobRecord) -> None:
        context = JobContext(job, repos, threads=self.threads, logger=self.logger)
        handler = get_handler(job.kind)
        if handler is None:
            _finish(repos, job, "failed", f"no handler for job kind '{job.kind}' yet", self.logger)
            return
        with self._lock:
            if self.stop.is_set():
                self.logger.info("job %s: claimed while stopping; left for start-up recovery", job.id)
                return
            self._current = context
        self.logger.info(
            "job %s (%s, target %s): started, attempt %d, %d solver threads",
            job.id,
            job.kind,
            job.target_id,
            job.attempts,
            self.threads,
        )
        self.monitor.watch(context)
        if self.watchdog is not None:
            self.watchdog.watch(context)
        error: BaseException | None = None
        try:
            handler(context)
        except BaseException as exc:  # a handler must never take the lane down
            error = exc
        finally:
            if self.watchdog is not None:
                self.watchdog.unwatch(context)
            self.monitor.unwatch(context)
            with self._lock:
                self._current = None
        status, reason = job_outcome(context, error)
        if status is None:
            self.logger.info("job %s: interrupted by the worker stopping; requeued at the next start", job.id)
            return
        if error is not None and status == "failed" and not isinstance(error, Refused | DentalsimError):
            self.logger.error("job %s: handler raised", job.id, exc_info=error)
        _finish(repos, job, status, reason, self.logger)


def recover_interrupted(repos: Repos) -> list[JobRecord]:
    """Start-up recovery (FR-056): requeue the jobs left running, and let their handlers mirror it on the targets."""
    requeued = queue.requeue_interrupted(repos)
    for job in requeued:
        log.warning(
            "job %s (%s, target %s) was interrupted; requeued at the front of the %s lane",
            job.id,
            job.kind,
            job.target_id,
            job.lane,
        )
        hook = get_requeue_hook(job.kind)
        if hook is None:
            continue
        try:
            hook(job, repos)
        except Exception:
            log.exception("job %s: requeue hook for %s failed", job.id, job.kind)
    return requeued


def run_worker(
    data_dir: Path | None = None,
    *,
    stop: threading.Event | None = None,
    poll_interval_s: float = 0.5,
    memory_limit_percent: float = MEMORY_LIMIT_PERCENT,
) -> None:
    """Run the worker until `stop` is set or, on the main thread, Ctrl+C."""
    data_dir = ensure_data_folder() if data_dir is None else data_dir
    stop = stop or threading.Event()
    log_setup = _start_logging(data_dir)
    previous_signals = _install_signal_handlers(stop)
    try:
        _serve(data_dir, stop, poll_interval_s=poll_interval_s, memory_limit_percent=memory_limit_percent)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        _restore_signal_handlers(previous_signals)
        log.info("worker stopped")
        _stop_logging(log_setup)


def _serve(data_dir: Path, stop: threading.Event, *, poll_interval_s: float, memory_limit_percent: float) -> None:
    repos = open_repos(data_dir)
    power = PowerGuard()
    watchdog = MemoryWatchdog(limit_percent=memory_limit_percent)
    monitor = JobMonitor(data_dir, stop)
    lanes = [
        LaneLoop(
            lane,
            data_dir,
            stop,
            poll_interval_s=poll_interval_s,
            monitor=monitor,
            watchdog=watchdog if lane == "heavy" else None,
        )
        for lane in LANES
    ]
    try:
        log.info(
            "worker starting: data folder %s; solver threads %s",
            data_dir,
            ", ".join(f"{lane.lane} {lane.threads}" for lane in lanes),
        )
        discover_handlers()
        log.info("handlers for job kinds: %s", ", ".join(registered_kinds()) or "none yet")
        recover_interrupted(repos)
        monitor.start()
        watchdog.start()
        for lane in lanes:
            lane.start()
        while not stop.is_set():
            try:
                power.update(queue.has_work(repos))
            except sqlite3.Error:
                log.exception("could not read the queue")
            for lane in lanes:
                if not lane.alive and not stop.is_set():
                    log.error("%s lane loop stopped unexpectedly; restarting it", lane.lane)
                    lane.start()
            stop.wait(poll_interval_s)
    finally:
        log.info("worker stopping")
        stop.set()
        for lane in lanes:
            lane.interrupt()
        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        for lane in lanes:
            lane.join(max(0.0, deadline - time.monotonic()))
            if lane.alive:
                log.warning(
                    "%s lane: the running job did not stop in time; it is requeued at the next start", lane.lane
                )
        watchdog.stop()
        monitor.join(monitor.interval_s * 2)
        power.release()
        repos.close()


SignalHandler = Callable[[int, FrameType | None], Any] | int | None


def _install_signal_handlers(stop: threading.Event) -> dict[int, SignalHandler]:
    """Ctrl+C, Ctrl+Break and SIGTERM set `stop`, on the main thread only (signals reach no other thread)."""
    if threading.current_thread() is not threading.main_thread():
        return {}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        log.info("signal %d: stopping", signum)
        stop.set()

    previous: dict[int, SignalHandler] = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            previous[signum] = signal.signal(signum, request_stop)
    return previous


def _restore_signal_handlers(previous: dict[int, SignalHandler]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


LogSetup = list[tuple[logging.Logger, logging.Handler, int]]


def _start_logging(data_dir: Path) -> LogSetup:
    folder = data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        folder / LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler(sys.stderr)
    setup: LogSetup = []
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        for name in LOGGED_PACKAGES:
            logger = logging.getLogger(name)
            setup.append((logger, handler, logger.level))
            logger.addHandler(handler)
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
    return setup


def _stop_logging(setup: LogSetup) -> None:
    for logger, handler, level in reversed(setup):  # so each logger gets back the level it had first
        logger.removeHandler(handler)
        logger.setLevel(level)
    for handler in {id(handler): handler for _, handler, _ in setup}.values():
        handler.close()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):  # a second Ctrl+C while stopping
        run_worker()


if __name__ == "__main__":
    main()
