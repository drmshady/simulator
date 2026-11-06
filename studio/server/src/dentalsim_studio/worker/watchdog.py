"""The memory watchdog (research R8, FR-057): a heavy job's solver is stopped before the main PC runs out of memory.

A thread polls the system memory use. When it passes the limit (92% by default) while a heavy job has a solver
process registered, it ends that process tree and marks the job out of memory, so the job, and the run record
its handler keeps, fail with `OUT_OF_MEMORY_REASON`. Run estimates refuse runs above 85% before they start; this
is the backstop for estimates that were too low.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable

import psutil

from dentalsim_studio.worker.handlers import OUT_OF_MEMORY_REASON, JobContext

MEMORY_LIMIT_PERCENT = 92.0
CHECK_INTERVAL_S = 1.0
TERMINATE_TIMEOUT_S = 5.0

log = logging.getLogger(__name__)


def end_process_tree(process: psutil.Process | int, *, timeout_s: float = TERMINATE_TIMEOUT_S) -> None:
    """End a process and its descendants, children first: terminate, then kill what is left after `timeout_s`."""
    try:
        parent = process if isinstance(process, psutil.Process) else psutil.Process(process)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    tree = [*reversed(children), parent]  # the deepest descendants first
    for member in tree:
        with contextlib.suppress(psutil.NoSuchProcess):
            member.terminate()
    _, alive = psutil.wait_procs(tree, timeout=timeout_s)
    for member in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            member.kill()
    if alive:
        psutil.wait_procs(alive, timeout=timeout_s)


def memory_percent() -> float:
    return float(psutil.virtual_memory().percent)


class MemoryWatchdog:
    """Watches the system memory while one job (the heavy lane's) is registered with `watch`."""

    def __init__(
        self,
        *,
        limit_percent: float = MEMORY_LIMIT_PERCENT,
        interval_s: float = CHECK_INTERVAL_S,
        read_percent: Callable[[], float] = memory_percent,
    ) -> None:
        self.limit_percent = limit_percent
        self.interval_s = interval_s
        self._read_percent = read_percent
        self._lock = threading.Lock()
        self._context: JobContext | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, context: JobContext) -> None:
        with self._lock:
            self._context = context

    def unwatch(self, context: JobContext) -> None:
        with self._lock:
            if self._context is context:
                self._context = None

    def check_once(self) -> bool:
        """End the watched job's solver if memory is past the limit. True when it did."""
        with self._lock:
            context = self._context
        if context is None or context.out_of_memory:
            return False
        processes = context.processes
        if not processes:
            return False
        percent = self._read_percent()
        if percent < self.limit_percent:
            return False
        log.warning(
            "memory at %.0f%% (limit %.0f%%): ending the solver of job %s; %s",
            percent,
            self.limit_percent,
            context.job_id,
            OUT_OF_MEMORY_REASON,
        )
        context.mark_out_of_memory()
        for process in processes:
            end_process_tree(process)
        return True

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="worker-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + TERMINATE_TIMEOUT_S * 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.check_once()
            except Exception:
                log.exception("memory watchdog check failed")
