"""The solver build check (T012), with `ccx -v` and the probe run replaced by fakes.

The fake probe copies real CalculiX 2.23 output of the probe model into the work folder, so the result checks run on
genuine files: `fixtures/probe_frd.txt` and `probe_dat.txt`, `probe_pastix_log.txt` (ccx_static.exe with PaStiX) and
`probe_log.txt` (ccx_dynamic.exe with PARDISO, which stands in for the other equation solvers' logs).
"""

import hashlib
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dentalsim.config import get_settings
from dentalsim.errors import SolverFailed, SolverNotFound
from dentalsim.solvers.calculix import build
from dentalsim.solvers.calculix.inp_writer import write_inp
from dentalsim.solvers.calculix.runner import SolverRunResult, explain_failure

FIXTURES = Path(__file__).parent / "fixtures"


class FakeCcx:
    """Stands in for `subprocess.run` (`ccx -v`) and `run_ccx` (the probe)."""

    def __init__(self, linked: tuple[str, ...] = ("PASTIX", "SPOOLES"), version_output: str = "") -> None:
        self.linked = linked
        self.version_output = version_output or "\nThis is Version 2.23\n\n"
        self.version_code = 201
        self.probes: list[str] = []
        self.log_solver: str | None = None

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[1:] == ["-v"]
        return subprocess.CompletedProcess(argv, self.version_code, stdout=self.version_output, stderr="")

    def run_ccx(self, work: Path, jobname: str, **kwargs: Any) -> SolverRunResult:
        inp = (work / f"{jobname}.inp").read_text()
        solver = inp.split("*STATIC, SOLVER=")[1].split()[0]
        self.probes.append(solver)
        if solver not in self.linked:
            log = f" *ERROR in linstatic: the {solver} library is not linked"
            raise SolverFailed(explain_failure(log, 201), log_tail=log)
        for kind in ("frd", "dat"):
            shutil.copy(FIXTURES / f"probe_{kind}.txt", work / f"{jobname}.{kind}")
        used = (self.log_solver or solver).lower()
        if used == "pastix":
            log = (FIXTURES / "probe_pastix_log.txt").read_text()
        else:
            log = (FIXTURES / "probe_log.txt").read_text().replace("pardiso", used)
        (work / f"{jobname}.log").write_text(log)
        return SolverRunResult(
            jobname=jobname,
            work_dir=work,
            command=("ccx", "-i", jobname),
            threads=1,
            returncode=0,
            wall_time_s=0.1,
            peak_memory_bytes=1,
            steps_total=1,
            steps_seen=1,
            messages=(),
            relative_residual=None,
            frd_path=work / f"{jobname}.frd",
            dat_path=work / f"{jobname}.dat",
            log_path=work / f"{jobname}.log",
            disk_bytes=1,
        )


@pytest.fixture
def binary(tmp_path: Path) -> Path:
    path = tmp_path / "ccx_dynamic.exe"
    path.write_bytes(b"not really ccx")
    return path


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    build.clear_build_cache()
    yield
    build.clear_build_cache()


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeCcx) -> FakeCcx:
    monkeypatch.setattr(subprocess, "run", fake.run)  # the `ccx -v` call
    monkeypatch.setattr(build, "run_ccx", fake.run_ccx)
    return fake


def test_pastix_build(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = install(monkeypatch, FakeCcx())
    result = build.solver_build(binary)
    sha = hashlib.sha256(b"not really ccx").hexdigest()
    assert result.path == binary.resolve()
    assert result.version == "2.23"
    assert result.equation_solver == "pastix"
    assert result.preferred_solver_ok is True
    assert result.proprietary_libraries == ()
    assert result.sha256 == sha == build.file_sha256(binary)
    assert result.build_id == f"ccx-2.23-pastix-sha256:{sha}"
    assert fake.probes == ["PASTIX"]


def test_builds_are_cached_by_path_time_and_size(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = install(monkeypatch, FakeCcx())
    first = build.solver_build(binary)
    assert build.solver_build(binary) is first
    assert fake.probes == ["PASTIX"]
    binary.write_bytes(b"a different ccx build")
    changed = build.solver_build(binary)
    assert fake.probes == ["PASTIX", "PASTIX"]
    assert changed.sha256 != first.sha256


def test_falls_back_to_spooles_with_its_own_build_id(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = install(monkeypatch, FakeCcx(linked=("SPOOLES",)))
    result = build.solver_build(binary)
    assert fake.probes == ["PASTIX", "SPOOLES"]
    assert result.equation_solver == "spooles"
    assert result.preferred_solver_ok is False
    assert result.build_id.startswith("ccx-2.23-spooles-sha256:")
    assert any("PASTIX was not accepted" in note and "does not include" in note for note in result.notes)


def test_pardiso_is_never_chosen_on_its_own(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = install(monkeypatch, FakeCcx(linked=("PARDISO",)))
    with pytest.raises(SolverFailed, match="one-element test model"):
        build.solver_build(binary)
    assert "PARDISO" not in fake.probes


def test_a_build_that_loads_intel_mkl_is_reported(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    install(monkeypatch, FakeCcx())
    monkeypatch.setattr(build, "imported_libraries", lambda path: ("KERNEL32.dll", "mkl_rt.2.dll"))
    result = build.solver_build(binary)
    assert result.proprietary_libraries == ("mkl_rt.2.dll",)
    assert any("not open source" in note and "ccx_static.exe" in note for note in result.notes)


@pytest.mark.skipif(sys.platform != "win32", reason="reads a Windows program's import table")
def test_imported_libraries_reads_a_real_windows_program(tmp_path: Path) -> None:
    names = {name.lower() for name in build.imported_libraries(Path(sys.executable))}
    assert "kernel32.dll" in names
    not_a_program = tmp_path / "plain.exe"
    not_a_program.write_bytes(b"MZ" + b"\0" * 100)
    assert build.imported_libraries(not_a_program) == ()
    assert build.imported_libraries(tmp_path / "missing.exe") == ()


def test_no_working_equation_solver(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    install(monkeypatch, FakeCcx(linked=()))
    with pytest.raises(SolverFailed, match="one-element test model"):
        build.solver_build(binary)


def test_a_solver_that_silently_uses_another_equation_solver(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = FakeCcx()
    fake.log_solver = "spooles"
    install(monkeypatch, fake)
    result = build.solver_build(binary)
    assert result.equation_solver == "spooles"
    assert "instead of PASTIX" in result.notes[0]


def test_version_from_the_log_when_ccx_v_gives_none(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    install(monkeypatch, FakeCcx(version_output="Usage: CalculiX.exe -i jobname\n"))
    assert build.solver_build(binary).version == "2.23"  # "CalculiX Version 2.23, Copyright ..." in the probe log


def test_missing_dll(monkeypatch: pytest.MonkeyPatch, binary: Path) -> None:
    fake = FakeCcx(version_output=" ")
    fake.version_code = 0xC0000135
    install(monkeypatch, fake)
    with pytest.raises(SolverFailed, match=r"DLL .* missing"):
        build.solver_build(binary)
    assert fake.probes == []


def test_unconfigured_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DENTALSIM_CCX", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(SolverNotFound, match="Set DENTALSIM_CCX"):
            build.solver_build()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("\nThis is Version 2.23\n\n", "2.23"),
        ("CalculiX Version 2.23, Copyright(C) 1998-2025 Guido Dhondt", "2.23"),
        ("CalculiX Version 2.23 i8, Copyright(C) 1998-2025 Guido Dhondt", "2.23"),
        ("CalculiX Version 2.21.1, Copyright", "2.21.1"),
        ("no version here", None),
    ],
)
def test_parse_version(text: str, version: str | None) -> None:
    assert build.parse_version(text) == version


def test_probe_model_is_a_valid_one_element_model(tmp_path: Path) -> None:
    text = write_inp(build.probe_model(), tmp_path / "probe.inp").read_text()
    assert "*STATIC, SOLVER=PASTIX" in text
    assert text.count("*STEP") == 1
