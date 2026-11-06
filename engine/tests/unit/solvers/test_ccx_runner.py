"""The CalculiX process runner (T014), driven with a small Python script standing in for ccx.

The stand-in prints ccx-like lines, writes fake result files, and can wait, allocate memory or start a child process,
so streaming, peak memory, cancel and watchdog (ending the whole process tree) and the failure reasons can be tested
without the solver.
"""

import json
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from dentalsim.config import get_settings
from dentalsim.errors import SolverFailed, SolverNotFound
from dentalsim.solvers.calculix import runner
from dentalsim.solvers.calculix.runner import (
    SINGULAR,
    SolverCancelled,
    SolverProgress,
    collect_messages,
    count_steps,
    describe_exit_code,
    explain_failure,
    find_ccx,
    parse_relative_residual,
    run_ccx,
)

FAKE_CCX = r"""
import json, os, subprocess, sys, time
scenario = sys.argv[1]
job = sys.argv[sys.argv.index("-i") + 1]

def say(*lines):
    for line in lines:
        print(line, flush=True)

def finish():
    say(" STEP            2", " Factoring the system of equations using the symmetric pardiso solver", " Job finished")
    with open(job + ".frd", "w") as f:
        f.write("    1C\n 9999\n")
    with open(job + ".dat", "w") as f:
        f.write(" total force (fx,fy,fz) for set FIX and time  0.1000000E+01\n\n  -1.0  0.0  0.0\n")
    sys.exit(0)

say("", "CalculiX Version 2.23, Copyright(C) 1998-2025 Guido Dhondt", "")
if scenario == "ok":
    names = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS")
    with open("env.json", "w") as f:
        json.dump({name: os.environ.get(name) for name in names}, f)
    say(" STEP            1", " Using up to 3 cpu(s) for the stress calculation.",
        " *WARNING in e_c3d: element 5 is distorted", "        the results may be inaccurate",
        " relative residual = 1.5e-12")
    finish()
elif scenario == "wait_for_go":
    say(" STEP            1")
    deadline = time.time() + 20
    while not os.path.exists("go") and time.time() < deadline:
        time.sleep(0.02)
    finish()
elif scenario == "memory":
    block = bytearray(300 * 1024 * 1024)
    block[::4096] = b"x" * len(block[::4096])
    say(" STEP            1")
    time.sleep(1.5)
    finish()
elif scenario == "hang_with_child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open("pids.json", "w") as f:
        json.dump([os.getpid(), child.pid], f)
    say(" STEP            1")
    time.sleep(120)
elif scenario == "pastix":
    say(" STEP            1", "  Factorization task:", "    Number of static pivots                      0",
        "    - iteration 1 :", "         error                                  4.3693e-08",
        "         error                                  5.1302e-16", " STEP            2", "  Factorization task:",
        "    Number of static pivots                      3",
        "         error                                  2.0552e-06")
    finish()
elif scenario == "pardiso_missing":
    say(" STEP            1", "", " *ERROR in linstatic: the PARDISO library is not linked", "")
    sys.exit(201)
elif scenario == "singular":
    with open("spooles.out", "w") as f:
        f.write("\n\n matrix found to be singular\n")
    sys.exit(1)
elif scenario == "no_results":
    say(" Job finished")
elif scenario == "input_error":
    say(" *ERROR reading *MATERIAL. Card image:", "        *MATERIAL, NAME=")
    sys.exit(201)
"""


@pytest.fixture
def job(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "job.inp").write_text("*HEADING\nx\n*STEP\n*STATIC\n*END STEP\n*STEP\n*STATIC\n*END STEP\n")
    (tmp_path / "fake_ccx.py").write_text(FAKE_CCX)
    return work


def command(work: Path, scenario: str) -> list[str]:
    return [sys.executable, str(work.parent / "fake_ccx.py"), scenario]


def test_successful_run_streams_stages_and_records_the_run(job: Path) -> None:
    events: list[SolverProgress] = []
    result = run_ccx(job, "job", command=command(job, "ok"), threads=3, progress=events.append)

    assert result.returncode == 0
    assert result.threads == 3
    assert result.steps_total == 2 and result.steps_seen == 2
    assert result.frd_path == job / "job.frd" and result.dat_path == job / "job.dat"
    assert result.disk_bytes >= sum(p.stat().st_size for p in job.iterdir() if p.is_file()) > 0
    assert result.wall_time_s > 0
    assert result.messages == ("*WARNING in e_c3d: element 5 is distorted the results may be inaccurate",)
    assert result.relative_residual == pytest.approx(1.5e-12)
    stages = [event.stage for event in events]
    assert stages == [
        "STEP 1",
        "Using up to 3 cpu(s) for the stress calculation",
        "STEP 2",
        "factoring the system of equations",
        "job finished",
    ]
    assert events[1].step == 1 and events[3].step == 2 and events[0].steps_total == 2
    assert "CalculiX Version 2.23" in result.log_path.read_text()
    env = json.loads((job / "env.json").read_text())
    assert env == dict.fromkeys(env, "3")


def test_progress_arrives_while_the_solver_runs(job: Path) -> None:
    def on_progress(event: SolverProgress) -> None:
        if event.stage == "STEP 1":
            (job / "go").write_text("")  # the stand-in waits for this before it finishes

    started = time.monotonic()
    run_ccx(job, "job", command=command(job, "wait_for_go"), progress=on_progress, timeout_s=15)
    assert time.monotonic() - started < 10


def test_peak_memory_of_the_process_tree(job: Path) -> None:
    result = run_ccx(job, "job", command=command(job, "memory"), poll_interval_s=0.05)
    assert result.peak_memory_bytes > 250 * 1024 * 1024


def _pids(job: Path) -> list[int]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return list(json.loads((job / "pids.json").read_text()))
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("the stand-in did not write its process ids")


def _running(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _all_gone(pids: list[int]) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not any(_running(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return False


def test_cancel_ends_the_whole_process_tree(job: Path) -> None:
    cancel = threading.Event()
    seen: list[int] = []

    def on_progress(event: SolverProgress) -> None:
        if event.stage == "STEP 1":
            seen.extend(_pids(job))
            cancel.set()

    started = time.monotonic()
    with pytest.raises(SolverCancelled, match="cancelled"):
        run_ccx(job, "job", command=command(job, "hang_with_child"), progress=on_progress, cancel=cancel)
    assert time.monotonic() - started < 30
    assert len(seen) == 2 and _all_gone(seen)


def test_cancel_accepts_a_callable(job: Path) -> None:
    with pytest.raises(SolverCancelled):
        run_ccx(job, "job", command=command(job, "hang_with_child"), cancel=lambda: (job / "pids.json").exists())
    assert _all_gone(_pids(job))


def test_watchdog_stops_the_run_with_its_reason(job: Path) -> None:
    def watchdog(sample: runner.ResourceSample) -> str | None:
        return "failed: out of memory" if (job / "pids.json").exists() and sample.rss_bytes > 0 else None

    with pytest.raises(SolverFailed, match="failed: out of memory") as failure:
        run_ccx(job, "job", command=command(job, "hang_with_child"), watchdog=watchdog)
    assert not isinstance(failure.value, SolverCancelled)
    assert _all_gone(_pids(job))


def test_time_limit(job: Path) -> None:
    with pytest.raises(SolverFailed, match="did not finish within 1 s"):
        run_ccx(job, "job", command=command(job, "hang_with_child"), timeout_s=1)
    assert _all_gone(_pids(job))


def test_a_failing_callback_does_not_leave_the_solver_running(job: Path) -> None:
    def on_progress(event: SolverProgress) -> None:
        _pids(job)
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        run_ccx(job, "job", command=command(job, "hang_with_child"), progress=on_progress)
    assert _all_gone(_pids(job))


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("pardiso_missing", "does not include the PARDISO equation solver"),
        ("singular", "singular stiffness matrix"),
        ("no_results", "without writing complete results"),
        ("input_error", "could not read the input file"),
    ],
)
def test_failures_have_plain_reasons_and_the_log_tail(job: Path, scenario: str, reason: str) -> None:
    (job / "job.frd").write_text("stale results from an earlier run\n 9999\n")
    with pytest.raises(SolverFailed, match=reason) as failure:
        run_ccx(job, "job", command=command(job, scenario))
    assert "CalculiX Version 2.23" in failure.value.log_tail
    assert not (job / "job.frd").exists()  # stale results are removed before the run


def test_missing_input_file(tmp_path: Path) -> None:
    with pytest.raises(SolverFailed, match="input file is missing"):
        run_ccx(tmp_path, "job", command=[sys.executable, "-c", "pass"])


def test_a_program_that_cannot_start(job: Path) -> None:
    with pytest.raises(SolverFailed, match="could not be started"):
        run_ccx(job, "job", command=[str(job / "no-such-program.exe")])


def test_find_ccx_needs_a_configured_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DENTALSIM_CCX", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(SolverNotFound, match="DENTALSIM_CCX"):
            find_ccx()
        monkeypatch.setenv("DENTALSIM_CCX", str(tmp_path / "missing.exe"))
        get_settings.cache_clear()
        with pytest.raises(SolverNotFound, match="missing.exe"):
            find_ccx()
        binary = tmp_path / "ccx.exe"
        binary.write_bytes(b"MZ")
        monkeypatch.setenv("DENTALSIM_CCX", str(binary))
        get_settings.cache_clear()
        assert find_ccx() == binary.resolve()
        assert find_ccx(binary) == binary.resolve()
    finally:
        get_settings.cache_clear()


def test_explain_failure_messages() -> None:
    assert "PASTIX" in explain_failure(" *ERROR in linstatic: the PASTIX library is not linked")
    assert "out of memory" in explain_failure(" *ERROR in u_calloc: error allocating memory\nvariable=au")
    assert explain_failure("\n\n matrix found to be singular\n") == SINGULAR
    assert "nonpositive Jacobian" in explain_failure(" *ERROR in e_c3d: nonpositive jacobian")
    assert "exit code 7" in explain_failure("", 7)
    assert "DLL" in explain_failure("", 0xC0000135)
    assert explain_failure("") == "the solver stopped without giving a reason"


def test_describe_exit_code() -> None:
    assert describe_exit_code(0) is None
    assert describe_exit_code(201) is None
    assert "mkl_rt.2.dll" in (describe_exit_code(3221225781) or "")
    assert "crashed" in (describe_exit_code(0xC0000005) or "")


def test_collect_messages_and_residual() -> None:
    lines = [
        " *ERROR reading *BOUNDARY: nonexistent node set",
        "       FIX",
        " Using up to 1 cpu(s)",
        " *WARNING in x",
    ]
    assert collect_messages(lines) == ["*ERROR reading *BOUNDARY: nonexistent node set FIX", "*WARNING in x"]
    assert parse_relative_residual([" relative residual: 3.0e-10", "relative residual = 2.5E-11"]) == 2.5e-11
    assert parse_relative_residual([" Factoring the system of equations"]) is None


def test_count_steps(tmp_path: Path) -> None:
    inp = tmp_path / "a.inp"
    inp.write_text("*HEADING\n*STEP\n*STATIC\n*END STEP\n*step, nlgeom\n*END STEP\n** *STEP in a comment\n")
    assert count_steps(inp) == 2


def test_pastix_residuals_are_read_per_step(job: Path) -> None:
    stages: list[str] = []
    result = run_ccx(job, "job", command=command(job, "pastix"), progress=lambda p: stages.append(p.stage))
    assert result.step_residuals == (5.1302e-16, 2.0552e-06)
    assert result.relative_residual == 2.0552e-06  # the worst step
    assert "refining the solution" in stages
    assert any("3 static pivots" in m and "step 2" in m for m in result.messages)


def test_pastix_refinement_lines_parse_as_residuals() -> None:
    fixture = Path(__file__).parent / "fixtures" / "probe_pastix_log.txt"
    assert parse_relative_residual(fixture.read_text().splitlines()) == pytest.approx(1.114e-13)
