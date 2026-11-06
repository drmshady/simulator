"""`dentalsim solver check` (T016) with the solver build mocked, and its error without a configured solver."""

import json
from pathlib import Path

import pytest

from dentalsim import cli
from dentalsim.commands import solver as solver_command
from dentalsim.config import get_settings
from dentalsim.solvers.calculix import build
from dentalsim.solvers.calculix.build import SolverBuild

SHA = "ab" * 32
FAKE = SolverBuild(
    path=Path(r"D:\DentalSimTools\calculix\ccx_static.exe"),
    version="2.23",
    equation_solver="pastix",
    preferred_solver_ok=True,
    sha256=SHA,
    build_id=f"ccx-2.23-pastix-sha256:{SHA}",
)


def run(*argv: str) -> int:
    return cli.main(list(argv), modules=[solver_command])


def test_check_prints_version_equation_solver_and_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Path | None] = []

    def fake_build(ccx: Path | None = None) -> SolverBuild:
        calls.append(ccx)
        return FAKE

    monkeypatch.setattr(solver_command, "solver_build", fake_build)
    assert run("solver", "check") == 0
    out = capsys.readouterr().out
    assert "version:  2.23" in out
    assert "equation solver: PASTIX" in out.splitlines()
    assert f"SHA-256:  {SHA}" in out
    assert f"build id: ccx-2.23-pastix-sha256:{SHA}" in out
    assert calls == [None]


def test_fallback_is_named(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fallback = FAKE.model_copy(update={"equation_solver": "spooles", "preferred_solver_ok": False})
    monkeypatch.setattr(solver_command, "solver_build", lambda ccx=None: fallback)
    assert run("solver", "check") == 0
    assert "equation solver: SPOOLES (fallback: PaStiX not available)" in capsys.readouterr().out


def test_check_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fallback = FAKE.model_copy(
        update={
            "equation_solver": "spooles",
            "preferred_solver_ok": False,
            "proprietary_libraries": ("mkl_rt.2.dll",),
            "notes": ("PASTIX was not accepted",),
        }
    )
    monkeypatch.setattr(solver_command, "solver_build", lambda ccx=None: fallback)
    assert run("solver", "check", "--json", "--ccx", "other.exe") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["version"] == "2.23"
    assert body["preferred_solver_ok"] is False
    assert body["proprietary_libraries"] == ["mkl_rt.2.dll"]
    assert body["equation_solver"] == "spooles"
    assert body["sha256"] == SHA
    assert body["notes"] == ["PASTIX was not accepted"]


def test_check_without_a_solver_exits_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DENTALSIM_CCX", raising=False)
    get_settings.cache_clear()
    build.clear_build_cache()
    try:
        assert run("solver", "check") == 2
        assert "Set DENTALSIM_CCX" in capsys.readouterr().err
        assert run("solver", "check", "--json") == 2
        assert json.loads(capsys.readouterr().out)["code"] == "error"
    finally:
        get_settings.cache_clear()


def test_solver_needs_an_action() -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("solver")
    assert exit_info.value.code == 2
