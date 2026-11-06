"""Runs CalculiX ccx as a child process in a per-job work folder (research R3, R8, R9).

- `ccx -i <jobname>` runs with the work folder as its current folder. `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
  `CCX_NPROC_EQUATION_SOLVER`, `CCX_NPROC_STIFFNESS` and `CCX_NPROC_RESULTS` are set to the requested thread count.
  (ccx 2.23 gives PARDISO `MKL_NUM_THREADS`, else `OMP_NUM_THREADS`, capped by `CCX_NPROC_EQUATION_SOLVER`.)
- Standard output and standard error are read line by line and written to `<jobname>.log`. A progress callback gets
  the stages ("STEP 2", "Using up to 4 cpu(s) ...", "factoring the system of equations", ...). The C part of ccx
  buffers its output when it writes to a pipe, so stage messages can arrive in bursts rather than one by one.
- Every `poll_interval_s` the memory of the whole process tree is read with psutil. On Windows the peak working set
  of each process is included, so short peaks between two polls still count.
- Cancel (an Event or a callable), a watchdog callback or a time limit ends the whole process tree: children first,
  then the parent; terminate, then kill after `kill_timeout_s`.
- A failure raises `SolverFailed` with a plain-language reason and the end of the log. ccx 2.23 never reports a
  singular stiffness matrix as an error: PARDISO and SPOOLES return a meaningless solution, and PaStiX does too, but
  its iterative refinement then stops far above its 1e-12 target (a cube held only along one edge stopped at 2e-6
  after 50 iterations, against about 1e-14 for a supported one). The per-step residuals are returned for the
  convergence checks after the solve (FR-023: force balance, energy balance and residual).
- PaStiX (the default equation solver, research R3) prints the error of each refinement iteration; the last one of
  a step is that step's relative residual. PARDISO and SPOOLES print none.

The ccx 2.23 messages matched here were checked against its source (linstatic.c, pardiso.c, spooles.c,
u_calloc.c, e_c3d.f) and a run of the Windows build from dhondt.de.
"""

from __future__ import annotations

import contextlib
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import psutil
from pydantic import BaseModel, ConfigDict

from dentalsim.config import get_settings
from dentalsim.errors import SolverFailed, SolverNotFound
from dentalsim.qos import unthrottle_process

POLL_INTERVAL_S = 0.25
KILL_TIMEOUT_S = 5.0
LOG_TAIL_LINES = 40
MAX_MESSAGES = 200
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "CCX_NPROC_EQUATION_SOLVER",
    "CCX_NPROC_STIFFNESS",
    "CCX_NPROC_RESULTS",
)
OUTPUT_SUFFIXES = (".frd", ".dat", ".sta", ".cvg", ".12d", ".log")
SINGULAR = "the solver stopped: the model is not fully supported (singular stiffness matrix)"

# Windows exit codes (NTSTATUS) that ccx itself never returns.
_EXIT_CODES = {
    0xC0000135: (
        "the solver could not start because a DLL it needs is missing (ccx_dynamic.exe needs Intel MKL's "
        "mkl_rt.2.dll; ccx_static.exe, the recommended build, needs none; see docs/setup/solver.md)"
    ),
    0xC000007B: "the solver could not start: one of its DLLs is for the wrong architecture (32-bit instead of 64-bit)",
    0xC0000005: "the solver crashed (access violation)",
    0xC00000FD: "the solver crashed (stack overflow)",
    0xC0000017: "the solver ran out of memory",
    0xC000013A: "the solver was interrupted",
}
_STEP = re.compile(r"^\s*STEP\s+(\d+)\s*$")
_NUMBER = r"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)"
_RESIDUAL = re.compile(r"relative\s+residual\D{0,20}?" + _NUMBER, re.IGNORECASE)
_PASTIX_ERROR = re.compile(r"^\s*error\s+" + _NUMBER + r"\s*$")
_STATIC_PIVOTS = re.compile(r"Number of static pivots\s+(\d+)")
_NOT_LINKED = re.compile(r"the\s+(\w+)\s+library\s+is\s+not\s+linked", re.IGNORECASE)
_STAGES = (
    (re.compile(r"Factoring the system of equations", re.IGNORECASE), "factoring the system of equations"),
    (re.compile(r"Solving the system of equations", re.IGNORECASE), "solving the system of equations"),
    (re.compile(r"Determining the structure of the matrix", re.IGNORECASE), "determining the structure of the matrix"),
    (re.compile(r"^\s*Factorization task:"), "factoring the system of equations"),
    (re.compile(r"^\s*- iteration 1 :"), "refining the solution"),
    (re.compile(r"Job finished", re.IGNORECASE), "job finished"),
)
_ECHO_STAGES = (re.compile(r"^\s*Using up to \d+ cpu", re.IGNORECASE), re.compile(r"^\s*number of threads\s*="))


class SolverCancelled(SolverFailed):
    """The run was cancelled by the caller; the process tree has been ended."""


@dataclass(frozen=True)
class SolverProgress:
    """One stage message from a running solver. Callbacks get these in the thread that called `run_ccx`."""

    stage: str
    step: int | None
    steps_total: int | None
    elapsed_s: float
    line: str


@dataclass(frozen=True)
class ResourceSample:
    """What a watchdog sees at each poll: memory of the whole process tree, in bytes."""

    elapsed_s: float
    rss_bytes: int
    peak_bytes: int


class SolverRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    jobname: str
    work_dir: Path
    command: tuple[str, ...]
    threads: int
    returncode: int
    wall_time_s: float
    peak_memory_bytes: int
    steps_total: int | None
    steps_seen: int
    messages: tuple[str, ...]
    relative_residual: float | None
    """The worst (largest) of the step residuals, or None when the equation solver prints none."""
    step_residuals: tuple[float | None, ...] = ()
    """The relative residual of each step (unit load case), in step order; None where none was printed."""
    frd_path: Path
    dat_path: Path
    log_path: Path
    disk_bytes: int


ProgressCallback = Callable[[SolverProgress], None]
Watchdog = Callable[[ResourceSample], str | None]
CancelSignal = threading.Event | Callable[[], bool]


def find_ccx(ccx: Path | None = None) -> Path:
    """The CalculiX binary: `ccx` if given, else `DENTALSIM_CCX`. Raises SolverNotFound when there is none."""
    path = ccx if ccx is not None else get_settings().ccx
    if path is None:
        raise SolverNotFound(
            "No CalculiX solver is configured. Set DENTALSIM_CCX to the full path of the ccx program "
            r"(for example D:\DentalSimTools\calculix\ccx_dynamic.exe), then open a new terminal. "
            "See docs/setup/solver.md."
        )
    path = Path(path).expanduser()
    if not path.is_file():
        where = f"The solver path {path}" if ccx is not None else f"DENTALSIM_CCX points to {path}, which"
        raise SolverNotFound(f"{where} is not an existing file. Correct the path (see docs/setup/solver.md).")
    return path.resolve()


def solver_env(threads: int, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child environment: `base` (default: this process's) with the thread counts set."""
    if threads < 1:
        raise ValueError("threads must be at least 1")
    env = dict(os.environ if base is None else base)
    for name in THREAD_VARIABLES:
        env[name] = str(threads)
    env["GFORTRAN_UNBUFFERED_PRECONNECTED"] = "y"  # Fortran messages are not held back in a buffer
    return env


def popen_flags() -> int:
    """Process creation flags: on Windows a new process group and no console window."""
    if sys.platform == "win32":
        return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return 0


def suppress_error_dialogs() -> None:
    """On Windows, stop the system from showing a dialog (for example "mkl_rt.2.dll was not found") that would
    block an unattended run. Child processes inherit this error mode."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        kernel32.SetErrorMode(kernel32.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)


def describe_exit_code(returncode: int) -> str | None:
    """Plain language for exit codes that mean the solver could not start or crashed, else None."""
    if returncode < 0 and sys.platform != "win32":
        return f"the solver was stopped by signal {-returncode}"
    return _EXIT_CODES.get(returncode & 0xFFFFFFFF)


def collect_messages(lines: Iterable[str]) -> list[str]:
    """ccx `*ERROR` and `*WARNING` messages, each joined with its indented continuation lines."""
    messages: list[str] = []
    collector = _MessageCollector(messages)
    for line in lines:
        collector.feed(line)
    return messages


class _MessageCollector:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.open = False
        self.dropped = 0

    def feed(self, line: str) -> None:
        if "*ERROR" in line or "*WARNING" in line:
            if len(self.messages) < MAX_MESSAGES:
                self.messages.append(line.strip())
                self.open = True
            else:
                self.dropped += 1
                self.open = False
        elif self.open and line.startswith("    ") and line.strip():
            self.messages[-1] = f"{self.messages[-1]} {line.strip()}"
        else:
            self.open = False

    def dropped_note(self, log_name: str) -> tuple[str, ...]:
        return (f"... and {self.dropped} more solver messages in {log_name}",) if self.dropped else ()


def parse_residual_line(line: str) -> float | None:
    """The residual on one log line: a PaStiX refinement `error` line or a "relative residual" line, else None."""
    match = _PASTIX_ERROR.match(line) or _RESIDUAL.search(line)
    if match:
        with contextlib.suppress(ValueError):
            return float(match.group(1))
    return None


def parse_relative_residual(lines: Iterable[str]) -> float | None:
    """The last relative residual the equation solver printed, or None. ccx 2.23 prints one for PaStiX (the error
    of each refinement iteration) but none for PARDISO or SPOOLES (pardiso.c runs with msglvl=0)."""
    value: float | None = None
    for line in lines:
        residual = parse_residual_line(line)
        if residual is not None:
            value = residual
    return value


def explain_failure(log: str, returncode: int | None = None) -> str:
    """A plain-language reason for a failed run, from the solver log and its exit code."""
    lowered = log.lower()
    not_linked = _NOT_LINKED.search(log)
    if not_linked:
        name = not_linked.group(1).upper()
        return f"the solver binary does not include the {name} equation solver (use another build or solver)"
    if "error allocating memory" in lowered or "out of memory" in lowered:
        return "the solver ran out of memory; choose a lower detail level"
    if "singular" in lowered or "zero pivot" in lowered:
        return SINGULAR
    if "nonpositive jacobian" in lowered:
        return "the solver stopped: an element of the mesh is inverted or badly distorted (nonpositive Jacobian)"
    errors = [m for m in collect_messages(log.splitlines()) if "*ERROR" in m]
    if errors:
        if errors[0].startswith("*ERROR reading"):
            return f"the solver could not read the input file: {errors[0]}"
        return f"the solver stopped with an error: {errors[0]}"
    if returncode is not None:
        described = describe_exit_code(returncode)
        if described:
            return described
        if returncode != 0:
            return f"the solver stopped with exit code {returncode}"
    return "the solver stopped without giving a reason"


def kill_process_tree(pid: int, timeout_s: float = KILL_TIMEOUT_S) -> None:
    """End a process and all its descendants: children first, then the parent; terminate, then kill."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        return
    _end(children, timeout_s)
    _end([parent], timeout_s)


def _end(processes: Sequence[psutil.Process], timeout_s: float) -> None:
    for process in processes:
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=timeout_s)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    if alive:
        psutil.wait_procs(alive, timeout=timeout_s)


def _tree_memory(root: psutil.Process) -> tuple[int, int]:
    """(resident bytes of the tree now, largest peak working set of one process so far, Windows only)."""
    processes = [root]
    with contextlib.suppress(psutil.Error):
        processes += root.children(recursive=True)
    total = peak = 0
    for process in processes:
        try:
            info = process.memory_info()
        except psutil.Error:
            continue
        total += int(info.rss)
        peak = max(peak, int(getattr(info, "peak_wset", 0)))
    return total, peak


class _PeakWorkingSet:
    """On Windows, a handle of our own on the solver process, so its peak working set can still be read after it
    has exited (psutil can no longer see an exited process). Elsewhere `peak()` is 0."""

    def __init__(self, pid: int) -> None:
        self.handle: int | None = None
        if sys.platform == "win32":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
            self.handle = int(handle) or None

    def peak(self) -> int:
        if sys.platform == "win32" and self.handle is not None:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [  # the layout of PROCESS_MEMORY_COUNTERS
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if ctypes.windll.kernel32.K32GetProcessMemoryInfo(
                wintypes.HANDLE(self.handle), ctypes.byref(counters), counters.cb
            ):
                return int(counters.PeakWorkingSetSize)
        return 0

    def close(self) -> None:
        if sys.platform == "win32" and self.handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self.handle)
        self.handle = None


def count_steps(inp_path: Path) -> int:
    """The number of `*STEP` cards in an input file (included files are not followed)."""
    pattern = re.compile(rb"^\s*\*STEP\b", re.IGNORECASE)
    with inp_path.open("rb") as stream:
        return sum(1 for line in stream if line[:1] in (b"*", b" ") and pattern.match(line))


def folder_bytes(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def _is_set(cancel: CancelSignal | None) -> bool:
    if cancel is None:
        return False
    if isinstance(cancel, threading.Event):
        return cancel.is_set()
    return bool(cancel())


def _pump(stream: IO[bytes], sink: queue.Queue[str | None]) -> None:
    try:
        for raw in iter(stream.readline, b""):
            sink.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    finally:
        sink.put(None)


def _frd_complete(path: Path) -> bool:
    """A complete ASCII .frd ends with the ` 9999` end-of-file line."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 64))
        return b"9999" in stream.read()


def run_ccx(
    work_dir: Path,
    jobname: str = "job",
    *,
    ccx: Path | None = None,
    command: Sequence[str] | None = None,
    threads: int = 1,
    progress: ProgressCallback | None = None,
    cancel: CancelSignal | None = None,
    watchdog: Watchdog | None = None,
    timeout_s: float | None = None,
    steps_total: int | None = None,
    poll_interval_s: float = POLL_INTERVAL_S,
    kill_timeout_s: float = KILL_TIMEOUT_S,
    unthrottled: bool = False,
) -> SolverRunResult:
    """Solve `<work_dir>/<jobname>.inp` and return the run record.

    `command` replaces the solver program (default: `find_ccx(ccx)`); `-i <jobname>` is appended. It serves tests
    and other ways of starting ccx, such as `["wsl", "ccx"]`. `watchdog` gets a `ResourceSample` at each poll and
    returns a reason to stop (for example "failed: out of memory"), or None to go on. `unthrottled` asks Windows
    never to power-throttle the solver (previews, which the owner waits for; see `dentalsim.qos`).
    """
    work_dir = Path(work_dir)
    inp_path = work_dir / f"{jobname}.inp"
    if not inp_path.is_file():
        raise SolverFailed(f"the solver input file is missing: {inp_path}")
    program = list(command) if command else [str(find_ccx(ccx))]
    argv = [*program, "-i", jobname]
    if steps_total is None:
        steps_total = count_steps(inp_path)
    for suffix in OUTPUT_SUFFIXES:
        (work_dir / f"{jobname}{suffix}").unlink(missing_ok=True)
    (work_dir / "spooles.out").unlink(missing_ok=True)  # SPOOLES appends to it
    log_path = work_dir / f"{jobname}.log"
    env = solver_env(threads)
    suppress_error_dialogs()

    start = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=work_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=popen_flags(),
        )
    except OSError as exc:
        raise SolverFailed(f"the solver could not be started: {exc}") from exc
    if unthrottled:
        unthrottle_process(process.pid)
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_pump, args=(process.stdout, lines), name="ccx-output", daemon=True)
    reader.start()

    state = _RunState(progress=progress, steps_total=steps_total, start=start)
    stop_reason: str | None = None
    cancelled = False
    peak = 0
    own_handle = _PeakWorkingSet(process.pid)
    try:
        handle: psutil.Process | None = psutil.Process(process.pid)
    except psutil.Error:
        handle = None
    try:
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            state.log = log
            while True:
                state.drain(lines)
                if process.poll() is not None:
                    break
                rss = 0
                if handle is not None:
                    rss, peak_wset = _tree_memory(handle)
                    peak = max(peak, rss, peak_wset)
                sample = ResourceSample(elapsed_s=time.monotonic() - start, rss_bytes=rss, peak_bytes=peak)
                if _is_set(cancel):
                    cancelled = True
                elif watchdog is not None and (reason := watchdog(sample)):
                    stop_reason = reason
                elif timeout_s is not None and sample.elapsed_s > timeout_s:
                    stop_reason = f"the solver did not finish within {timeout_s:g} s"
                if cancelled or stop_reason:
                    kill_process_tree(process.pid, kill_timeout_s)
                    break
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=poll_interval_s)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2 * kill_timeout_s)
            reader.join(timeout=kill_timeout_s)
            state.drain(lines)
        peak = max(peak, own_handle.peak())
    except BaseException:
        # A failing callback or Ctrl+C must not leave the solver running.
        kill_process_tree(process.pid, kill_timeout_s)
        raise
    finally:
        own_handle.close()
    wall_time = time.monotonic() - start
    returncode = process.returncode if process.returncode is not None else -1

    log_tail = "\n".join(state.tail)
    if cancelled:
        raise SolverCancelled("the run was cancelled", log_tail=log_tail)
    if stop_reason:
        raise SolverFailed(stop_reason, log_tail=log_tail)
    spooles_out = work_dir / "spooles.out"
    extra = spooles_out.read_text(errors="replace")[-4000:] if spooles_out.is_file() else ""
    full_log = log_path.read_text(encoding="utf-8", errors="replace")
    if returncode != 0 or any("*ERROR" in m for m in state.messages):
        raise SolverFailed(explain_failure(full_log + "\n" + extra, returncode), log_tail=log_tail)
    frd_path, dat_path = work_dir / f"{jobname}.frd", work_dir / f"{jobname}.dat"
    if not _frd_complete(frd_path):
        raise SolverFailed("the solver finished without writing complete results (.frd)", log_tail=log_tail)
    if not dat_path.is_file() or dat_path.stat().st_size == 0:
        raise SolverFailed("the solver finished without writing printed results (.dat)", log_tail=log_tail)
    return SolverRunResult(
        jobname=jobname,
        work_dir=work_dir,
        command=tuple(argv),
        threads=threads,
        returncode=returncode,
        wall_time_s=wall_time,
        peak_memory_bytes=peak,
        steps_total=steps_total,
        steps_seen=state.step or 0,
        messages=tuple(state.messages) + state.collector.dropped_note(log_path.name),
        relative_residual=max(found) if (found := [r for r in state.step_residuals() if r is not None]) else None,
        step_residuals=state.step_residuals(),
        frd_path=frd_path,
        dat_path=dat_path,
        log_path=log_path,
        disk_bytes=folder_bytes(work_dir),
    )


class _RunState:
    """Line handling for one run: the log file, progress stages, messages, the residual and the log tail."""

    def __init__(self, *, progress: ProgressCallback | None, steps_total: int | None, start: float) -> None:
        self.progress = progress
        self.steps_total = steps_total
        self.start = start
        self.log: IO[str] | None = None
        self.step: int | None = None
        self.messages: list[str] = []
        self.collector = _MessageCollector(self.messages)
        self.residuals: dict[int, float] = {}
        self.tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)

    def drain(self, lines: queue.Queue[str | None]) -> None:
        while True:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                return
            if line is not None:
                self.handle(line)

    def handle(self, line: str) -> None:
        if self.log is not None:
            self.log.write(line + "\n")
        if line.strip():
            self.tail.append(line)
        self.collector.feed(line)
        residual = parse_residual_line(line)
        if residual is not None:
            self.residuals[self.step or 1] = residual  # the last value of a step is its final residual
        pivots = _STATIC_PIVOTS.search(line)
        if pivots and int(pivots.group(1)) > 0:
            self.messages.append(
                f"step {self.step or 1}: PaStiX used {pivots.group(1)} static pivots; "
                "the stiffness matrix may be singular (is every part supported?)"
            )
        stage = self._stage(line)
        if stage is not None and self.progress is not None:
            self.progress(
                SolverProgress(
                    stage=stage,
                    step=self.step,
                    steps_total=self.steps_total,
                    elapsed_s=time.monotonic() - self.start,
                    line=line,
                )
            )

    def step_residuals(self) -> tuple[float | None, ...]:
        steps = max([self.step or 0, *self.residuals])
        return tuple(self.residuals.get(step) for step in range(1, steps + 1))

    def _stage(self, line: str) -> str | None:
        step = _STEP.match(line)
        if step:
            self.step = int(step.group(1))
            return f"STEP {self.step}"
        for pattern, stage in _STAGES:
            if pattern.search(line):
                return stage
        if any(pattern.search(line) for pattern in _ECHO_STAGES):
            return " ".join(line.split()).rstrip(".")
        return None
