"""Worker job handlers by job kind, and the `JobContext` they receive (research R9, FR-019, FR-056, FR-057).

Each module in this package registers the handler of one or more job kinds; the worker imports them all
(`discover_handlers`), as the command line does for its commands:

    from dentalsim_studio.worker.handlers import JobContext, register

    @register("run")
    def run_job(ctx: JobContext) -> None:
        ...

A handler returns when its work is done and raises when it fails. Long work polls `ctx.should_stop()`, or passes
it to the solver runner as its cancel check, and returns or raises soon after it turns true. The job then ends:

- `failed` with `OUT_OF_MEMORY_REASON` when the memory watchdog stopped it (`ctx.out_of_memory`)
- still `running` when the worker is shutting down (`ctx.shutting_down`): start-up recovery requeues it
- `cancelled` when the owner cancelled it (`ctx.cancel_requested()`), however the handler ended
- `failed` when the handler raised: the message of a `Refused` or `DentalsimError`, otherwise
  "unexpected error: <type>: <message>", with the traceback in the worker log
- `succeeded` otherwise

The worker records that status on the job; a handler never finishes its own job, but records the same outcome
on its target (run, preview, export) and emits the target's events (`run.finished`, ...). A module can also register an
`on_requeued` hook, called at worker start-up for each of its jobs that was put back in the queue, to mirror that
on the target (FR-056: running, then interrupted, then queued again).
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import psutil

from dentalsim.persistence import JobKind, JobRecord, Repos
from dentalsim_studio.events import bus

OUT_OF_MEMORY_REASON = "failed: out of memory, try a lower detail level"
PROGRESS_MIN_INTERVAL_S = 0.5
"""Progress reports closer together than this within one stage are dropped, so the event stream stays small."""

log = logging.getLogger(__name__)


class JobContext:
    """What a handler gets: its job, a database connection for its thread, its solver thread count and stop checks.

    `repos` belongs to the handler's thread. The worker's other threads call only `processes`, `note_cancel`,
    `mark_out_of_memory` and `mark_shutting_down`, which are thread-safe.
    """

    def __init__(self, job: JobRecord, repos: Repos, *, threads: int, logger: logging.Logger | None = None) -> None:
        self.job = job
        self.repos = repos
        self.threads = threads
        """Solver threads for this job: cores - 2 in the heavy lane, 2 in the preview lane."""
        self.logger = logger or logging.getLogger(f"dentalsim_studio.worker.{job.lane}")
        self._lock = threading.Lock()
        self._processes: list[psutil.Process] = []
        self._cancel = threading.Event()
        self._out_of_memory = threading.Event()
        self._shutting_down = threading.Event()
        self._last_progress: tuple[float, str] | None = None

    @property
    def job_id(self) -> str:
        return self.job.id

    @property
    def kind(self) -> JobKind:
        return self.job.kind

    @property
    def target_id(self) -> str | None:
        return self.job.target_id

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    def solver_env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """The environment for a solver process: `base` (default: this process's) with `OMP_NUM_THREADS` set."""
        env = dict(os.environ if base is None else base)
        env["OMP_NUM_THREADS"] = str(self.threads)
        return env

    # Stop checks.

    def cancel_requested(self) -> bool:
        """The owner asked to cancel this job (reads the job row)."""
        if self._cancel.is_set():
            return True
        with self._lock:
            job = self.repos.jobs.get(self.job.id)
        if job is None or job.cancel_requested:
            self._cancel.set()
        return self._cancel.is_set()

    def should_stop(self) -> bool:
        """Stop now: cancelled, stopped by the memory watchdog, or the worker is shutting down."""
        return self._out_of_memory.is_set() or self._shutting_down.is_set() or self.cancel_requested()

    @property
    def out_of_memory(self) -> bool:
        """The memory watchdog ended this job's solver; the job fails with `OUT_OF_MEMORY_REASON`."""
        return self._out_of_memory.is_set()

    @property
    def shutting_down(self) -> bool:
        """The worker is stopping; the job stays `running` and is requeued when the worker starts again."""
        return self._shutting_down.is_set()

    # Reporting.

    def heartbeat(self, pid: int | None = None) -> None:
        with self._lock:
            self.repos.jobs.heartbeat(self.job.id, pid=pid)

    def progress(self, percent: float, stage: str, remaining_s: float | None = None) -> None:
        """Report progress; for run jobs this emits `run.progress {run_id, percent, stage, remaining_s}`."""
        percent = min(100.0, max(0.0, float(percent)))
        now = time.monotonic()
        last = self._last_progress
        if last is not None and last[1] == stage and percent < 100 and now - last[0] < PROGRESS_MIN_INTERVAL_S:
            return
        self._last_progress = (now, stage)
        self.logger.debug("job %s: %s %.0f%%", self.job.id, stage, percent)
        if self.job.kind == "run":
            payload = {"run_id": self.job.target_id, "percent": percent, "stage": stage, "remaining_s": remaining_s}
            with self._lock:
                bus.emit(self.repos, "run.progress", payload)

    # Solver processes, which the watchdog and cancel end.

    def register_process(self, pid: int) -> None:
        """Tell the worker about a solver process, so that cancel and the memory watchdog can end its tree."""
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        with self._lock:
            self._processes.append(process)
        self.heartbeat(pid=pid)

    def unregister_process(self, pid: int) -> None:
        with self._lock:
            self._processes = [process for process in self._processes if process.pid != pid]

    @property
    def processes(self) -> list[psutil.Process]:
        with self._lock:
            return list(self._processes)

    # Called by the worker's threads.

    def note_cancel(self) -> bool:
        """Record a cancel request seen by another thread; True the first time."""
        first = not self._cancel.is_set()
        self._cancel.set()
        return first

    def mark_out_of_memory(self) -> None:
        self._out_of_memory.set()

    def mark_shutting_down(self) -> None:
        self._shutting_down.set()


Handler = Callable[[JobContext], None]
RequeueHook = Callable[[JobRecord, Repos], None]

_handlers: dict[str, Handler] = {}
_requeue_hooks: dict[str, RequeueHook] = {}
_discovered = False
_discovery_lock = threading.RLock()


def register(kind: JobKind) -> Callable[[Handler], Handler]:
    """`@register("run")` makes the decorated function the handler of that job kind."""

    def decorator(handler: Handler) -> Handler:
        existing = _handlers.get(kind)
        if existing is not None and existing is not handler:
            raise ValueError(f"job kind {kind!r} already has a handler ({existing.__module__}.{existing.__name__})")
        _handlers[kind] = handler
        return handler

    return decorator


def on_requeued(kind: JobKind) -> Callable[[RequeueHook], RequeueHook]:
    """`@on_requeued("run")`: called with the job (now queued again) and the worker's repos at start-up."""

    def decorator(hook: RequeueHook) -> RequeueHook:
        _requeue_hooks[kind] = hook
        return hook

    return decorator


def discover_handlers() -> list[ModuleType]:
    """Import every handler module of this package once. A module that fails to import is logged and skipped."""
    global _discovered
    modules: list[ModuleType] = []
    with _discovery_lock:
        if _discovered:
            return modules
        _discovered = True
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
            if info.name.startswith("_"):
                continue
            try:
                modules.append(importlib.import_module(f"{__name__}.{info.name}"))
            except Exception:
                log.exception("handler module %s could not be imported; its job kinds have no handler", info.name)
    return modules


def get_handler(kind: str) -> Handler | None:
    discover_handlers()
    return _handlers.get(kind)


def get_requeue_hook(kind: str) -> RequeueHook | None:
    discover_handlers()
    return _requeue_hooks.get(kind)


def registered_kinds() -> list[str]:
    discover_handlers()
    return sorted(_handlers)


@contextmanager
def override(kind: JobKind, handler: Handler | None, requeue_hook: RequeueHook | None = None) -> Iterator[None]:
    """For tests: the handler and requeue hook of `kind` are these (None: none) for the duration of the block."""
    discover_handlers()
    previous_handler, previous_hook = _handlers.pop(kind, None), _requeue_hooks.pop(kind, None)
    _set(_handlers, kind, handler)
    _set(_requeue_hooks, kind, requeue_hook)
    try:
        yield
    finally:
        _set(_handlers, kind, previous_handler)
        _set(_requeue_hooks, kind, previous_hook)


def _set[T](registry: dict[str, T], kind: str, value: T | None) -> None:
    registry.pop(kind, None)
    if value is not None:
        registry[kind] = value
