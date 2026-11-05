"""Engine version, result-affecting source hash and development-build flag (research R14).

- The engine version is the package version plus the git commit. It is kept for provenance only.
- The engine source hash covers only the result-affecting source, so a commit that touches only the interface,
  the server, the command line, exports or the documentation never marks stored runs out of date (Principle VI).
- A development build has uncommitted changes under `engine/`. Its runs can never be published.
"""

from __future__ import annotations

import hashlib
import subprocess
from importlib import metadata
from pathlib import Path

from pydantic import BaseModel, ConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = PACKAGE_ROOT.parents[1]

RESULT_AFFECTING = (
    "conventions",
    "model",
    "blocks",
    "templates",
    "assembly",
    "geometry",
    "mesh",
    "physics",
    "solvers",
    "post",
    "pipeline.py",
)


class VersionInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    engine_version: str
    engine_commit: str | None
    engine_source_hash: str
    dev_build: bool
    gmsh_version: str


def _git(*args: str, cwd: Path) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def package_version() -> str:
    return metadata.version("dentalsim")


def engine_commit() -> str | None:
    return _git("rev-parse", "--short", "HEAD", cwd=PACKAGE_ROOT) or None


def engine_version() -> str:
    commit = engine_commit()
    return f"{package_version()}+g{commit}" if commit else package_version()


def gmsh_version() -> str:
    return metadata.version("gmsh")


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in RESULT_AFFECTING:
        path = root / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            )
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def engine_source_hash(root: Path = PACKAGE_ROOT) -> str:
    """SHA-256 over the sorted relative paths and contents of the result-affecting source under `root`.

    Line endings are normalised to LF, so a Windows checkout with CRLF endings gives the same hash.
    """
    digest = hashlib.sha256()
    for path in _source_files(root):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def is_dev_build() -> bool:
    """True when `engine/` has uncommitted changes, or when git cannot tell (then the run is never publishable)."""
    status = _git("status", "--porcelain", "--", ".", cwd=ENGINE_ROOT)
    return status is None or status != ""


def version_info() -> VersionInfo:
    return VersionInfo(
        engine_version=engine_version(),
        engine_commit=engine_commit(),
        engine_source_hash=engine_source_hash(),
        dev_build=is_dev_build(),
        gmsh_version=gmsh_version(),
    )
