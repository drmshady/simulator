"""The CalculiX solver build: version, equation solver, SHA-256 and the solver build id (research R3, R14).

`solver_build()` finds the binary (`DENTALSIM_CCX`), asks it for its version (`ccx -v` prints
"This is Version 2.23" and exits with code 201), and solves a one-element C3D10 input with `*STATIC, SOLVER=PASTIX`.

- PaStiX is the preferred equation solver (research R3): open source (with Scotch, hwloc and OpenBLAS in the
  `ccx_static.exe` build), faster than PARDISO for detailed models on the main PC, and it prints its residual.
- It counts as accepted when the run succeeds, shows the PaStiX banner, writes displacements and balances the
  applied load. A build without it stops with "*ERROR in linstatic: the PASTIX library is not linked"; it is then
  tried with SPOOLES (open source, about 3 times slower), which gives a different build id and
  `preferred_solver_ok=False`. PARDISO is never chosen on its own: it needs Intel MKL, which is not open source
  (constitution, Scope).
- A Windows binary that loads Intel MKL is reported in `proprietary_libraries`, so the owner can see it.

The build id is `ccx-<version>-<solver>-sha256:<hash of the binary>`; it is part of every input hash (R14). Results
are cached by the binary's path, modification time and size, so repeated calls are cheap.
"""

from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict

from dentalsim.errors import SolverFailed
from dentalsim.solvers.calculix.dat_reader import read_dat
from dentalsim.solvers.calculix.frd_reader import read_frd
from dentalsim.solvers.calculix.inp_writer import (
    CCX_TET10_EDGES,
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    write_inp,
)
from dentalsim.solvers.calculix.runner import (
    describe_exit_code,
    find_ccx,
    popen_flags,
    run_ccx,
    solver_env,
    suppress_error_dialogs,
)

__all__ = [
    "SolverBuild",
    "clear_build_cache",
    "file_sha256",
    "find_ccx",
    "imported_libraries",
    "make_build_id",
    "solver_build",
]

PREFERRED_SOLVER = "pastix"
FALLBACK_SOLVERS = ("spooles",)
PROPRIETARY_LIBRARY = re.compile(r"^(mkl_|libiomp)", re.IGNORECASE)
PROBE_TIMEOUT_S = 120.0
_VERSION = re.compile(r"(?:CalculiX Version|This is Version)\s+(\d+(?:\.\d+)+)")
_FACTORING = re.compile(r"Factoring the system of equations using the \w+ (\w+) solver", re.IGNORECASE)


class SolverBuild(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    version: str
    equation_solver: str
    preferred_solver_ok: bool
    proprietary_libraries: tuple[str, ...] = ()
    sha256: str
    build_id: str
    notes: tuple[str, ...] = ()


_cache: dict[tuple[str, int, int], SolverBuild] = {}
_lock = threading.Lock()


def clear_build_cache() -> None:
    with _lock:
        _cache.clear()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_build_id(version: str, solver: str, sha256: str) -> str:
    return f"ccx-{version}-{solver.lower()}-sha256:{sha256}"


def parse_version(text: str) -> str | None:
    match = _VERSION.search(text)
    return match.group(1) if match else None


def solver_build(ccx: Path | None = None) -> SolverBuild:
    """The build record of `ccx` (default: `DENTALSIM_CCX`). Raises SolverNotFound or SolverFailed."""
    path = find_ccx(ccx)
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    build = _probe_build(path)
    with _lock:
        _cache[key] = build
    return build


def _version_of(path: Path) -> str | None:
    suppress_error_dialogs()
    try:
        done = subprocess.run(
            [str(path), "-v"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            env=solver_env(1),
            creationflags=popen_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SolverFailed("the solver did not answer `ccx -v` within 60 s") from exc
    except OSError as exc:
        raise SolverFailed(f"the solver at {path} could not be started: {exc}") from exc
    described = describe_exit_code(done.returncode)
    if described:
        raise SolverFailed(described, log_tail=(done.stdout + done.stderr)[-2000:])
    return parse_version(done.stdout + done.stderr)


def probe_model() -> SolverModel:
    """One unit C3D10 tetrahedron, its base face fixed, 1 N on the apex."""
    corners = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    coordinates = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in CCX_TET10_EDGES]])
    return SolverModel(
        node_ids=np.arange(1, 11),
        coordinates=coordinates,
        element_ids=np.array([1]),
        connectivity=np.arange(1, 11).reshape(1, 10),
        materials=(MaterialDef("PROBE", 210000.0, 0.3),),
        parts=(PartSet("PROBE_PART", "PROBE", np.array([1])),),
        supports=(SupportSet("FIX", np.array([1, 2, 3, 5, 6, 7])),),
        load_cases=(UnitLoadCase("apex_z", ((4, 1.0),), (0.0, 0.0, 1.0)),),
        heading="dentalsim solver probe",
    )


def _try_solver(path: Path, solver: str) -> tuple[str | None, str | None]:
    """Run the probe with one equation solver: (problem or None, version seen in the log or None)."""
    with tempfile.TemporaryDirectory(prefix="dentalsim-probe-", ignore_cleanup_errors=True) as folder:
        work = Path(folder)
        write_inp(probe_model(), work / "probe.inp", solver=solver)
        try:
            result = run_ccx(work, "probe", ccx=path, threads=1, timeout_s=PROBE_TIMEOUT_S)
        except SolverFailed as failed:
            return failed.reason, None
        log = result.log_path.read_text(encoding="utf-8", errors="replace")
        version = parse_version(log)
        factoring = _FACTORING.search(log)
        if factoring and factoring.group(1).lower() != solver.lower():
            return f"the solver used {factoring.group(1)} instead of {solver.upper()}", version
        if solver == "pastix" and "PaStiX" not in log:
            return "the solver log does not show PaStiX running", version
        problem = _check_probe_results(result.frd_path, result.dat_path)
        return problem, version


def _check_probe_results(frd_path: Path, dat_path: Path) -> str | None:
    try:
        frd = read_frd(frd_path)
        dat = read_dat(dat_path)
    except ValueError as exc:
        return f"the solver results could not be read: {exc}"
    if len(frd.steps) != 1 or frd.steps[0].node_ids.size != 10:
        return "the solver did not write the expected displacements"
    if not np.all(np.isfinite(frd.steps[0].displacements)):
        return "the solver wrote displacements that are not numbers"
    reaction = dat[0].reaction_totals.get("FIX") if dat else None
    if reaction is None or not np.allclose(reaction, [0.0, 0.0, -1.0], atol=1e-6):
        return f"the support reactions do not balance the 1 N probe load: {reaction}"
    return None


def imported_libraries(path: Path) -> tuple[str, ...]:
    """The DLLs a Windows program imports (from its PE import table), or () when it cannot be read."""
    try:
        data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            return ()
        sections = struct.unpack_from("<H", data, pe + 6)[0]
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        directories = optional + (112 if struct.unpack_from("<H", data, optional)[0] == 0x20B else 96)
        import_rva = struct.unpack_from("<I", data, directories + 8)[0]
        table = []
        for index in range(sections):
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", data, optional + optional_size + 40 * index + 8
            )
            table.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

        def offset(rva: int) -> int:
            for address, size, raw in table:
                if address <= rva < address + size:
                    return int(rva - address + raw)
            raise ValueError(f"address {rva:#x} is in no section")

        names = []
        entry = offset(import_rva)
        while True:
            name_rva = struct.unpack_from("<5I", data, entry)[3]
            if name_rva == 0:
                break
            start = offset(name_rva)
            names.append(data[start : data.index(b"\0", start)].decode("ascii", "replace"))
            entry += 20
        return tuple(names)
    except (OSError, ValueError, struct.error, IndexError):
        return ()


def _probe_build(path: Path) -> SolverBuild:
    version = _version_of(path)
    sha256 = file_sha256(path)
    proprietary = tuple(name for name in imported_libraries(path) if PROPRIETARY_LIBRARY.match(name))
    notes: list[str] = []
    if proprietary:
        notes.append(
            f"This program loads {', '.join(proprietary)} (Intel MKL), which is not open source; the constitution "
            "asks for open-source tools only, so use ccx_static.exe (see docs/setup/solver.md)."
        )
    for solver in (PREFERRED_SOLVER, *FALLBACK_SOLVERS):
        problem, logged_version = _try_solver(path, solver)
        version = version or logged_version
        if problem is None:
            if solver != PREFERRED_SOLVER:
                notes.append(f"Using {solver.upper()}; about 3 times slower than PaStiX for detailed models.")
            resolved = version or "unknown"
            return SolverBuild(
                path=path,
                version=resolved,
                equation_solver=solver,
                preferred_solver_ok=solver == PREFERRED_SOLVER,
                proprietary_libraries=proprietary,
                sha256=sha256,
                build_id=make_build_id(resolved, solver, sha256),
                notes=tuple(notes),
            )
        notes.append(f"{solver.upper()} was not accepted: {problem}")
    raise SolverFailed("the solver could not solve a one-element test model. " + " ".join(notes))
