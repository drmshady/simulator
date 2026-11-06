"""The merge gate: without the solver the result is "incomplete" and no pass is recorded (T056)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dentalsim import cli
from dentalsim.commands import check
from dentalsim.config import get_settings
from dentalsim.persistence import open_repos


class FakeRunner:
    """Stands in for the subprocesses; `failing` names the steps that fail."""

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing
        self.ran: list[str] = []

    def __call__(self, step: check.Step, cwd: Path, capture: bool) -> check.StepResult:
        self.ran.append(step.name)
        failed = step.name in self.failing
        return check.StepResult(step.name, 1 if failed else 0, not failed, 0.1, "boom" if failed else "")


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    runner = FakeRunner()
    monkeypatch.setattr(check, "run_step", runner)
    monkeypatch.setattr(check.shutil, "which", lambda name: "npm")
    return runner


def _without_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DENTALSIM_CCX", raising=False)
    get_settings.cache_clear()


def _with_solver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DENTALSIM_CCX", str(tmp_path / "ccx.exe"))
    get_settings.cache_clear()


def _last_pass(data_dir: Path) -> object:
    repos = open_repos(data_dir)
    try:
        return repos.settings.get(check.LAST_PASS_KEY)
    finally:
        repos.close()


def run(*argv: str) -> int:
    return cli.main(list(argv), modules=[check])


def test_without_the_solver_check_is_incomplete_and_records_nothing(
    data_dir: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _without_solver(monkeypatch)
    assert run("check") == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "incomplete" in captured.err
    assert "benchmark and regression tests" not in fake_runner.ran
    assert _last_pass(data_dir) is None


def test_incomplete_in_json_mode(
    data_dir: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _without_solver(monkeypatch)
    assert run("check", "--json") == cli.EXIT_REFUSED
    body = json.loads(capsys.readouterr().out)
    assert body["code"] == "check_incomplete"
    assert any("DENTALSIM_CCX" in m["message"] for m in body["missing"])


def test_complete_pass_is_recorded_with_the_engine_commit(
    data_dir: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _with_solver(monkeypatch, tmp_path)
    assert run("check") == cli.EXIT_OK
    assert "benchmark and regression tests" in fake_runner.ran
    assert "interface tests" in fake_runner.ran
    recorded = _last_pass(data_dir)
    assert isinstance(recorded, dict)
    assert set(recorded) >= {"engine_commit", "engine_source_hash", "dev_build", "passed_at"}


def test_a_failing_step_fails_the_gate_and_records_nothing(
    data_dir: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _with_solver(monkeypatch, tmp_path)
    fake_runner.failing = ("mypy",)
    assert run("check") == cli.EXIT_REFUSED
    assert _last_pass(data_dir) is None


def test_quick_check_is_never_a_gate(
    data_dir: Path,
    fake_runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_solver(monkeypatch, tmp_path)
    assert run("check", "--quick") == cli.EXIT_OK
    assert "benchmark and regression tests" not in fake_runner.ran
    assert "not a merge gate" in capsys.readouterr().out
    assert _last_pass(data_dir) is None


def test_plan_without_npm_is_incomplete() -> None:
    steps, skipped = check.plan_steps(quick=False, solver_available=True, npm=None)
    assert all("interface" not in s.name for s in steps)
    assert any("npm" in name for name in skipped)
    results = [check.StepResult(s.name, 0, True, 0.0, "") for s in steps]
    assert check.outcome_of(results, skipped, quick=False) == "incomplete"
