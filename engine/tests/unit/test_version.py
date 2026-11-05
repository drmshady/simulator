"""The engine source hash covers only result-affecting source (research R14, Principle VI)."""

import re
import shutil
from pathlib import Path

import pytest

from dentalsim import version


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A copy of the engine package and a stand-in studio file, laid out as in the repository."""
    package = tmp_path / "engine" / "src" / "dentalsim"
    shutil.copytree(version.PACKAGE_ROOT, package, ignore=shutil.ignore_patterns("__pycache__"))
    studio_file = tmp_path / "studio" / "server" / "src" / "dentalsim_studio" / "app.py"
    studio_file.parent.mkdir(parents=True)
    studio_file.write_text("app = None\n")
    return tmp_path


def _package(repo: Path) -> Path:
    return repo / "engine" / "src" / "dentalsim"


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def test_hash_is_stable_and_independent_of_location(repo_copy: Path) -> None:
    first = version.engine_source_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert version.engine_source_hash() == first
    assert version.engine_source_hash(_package(repo_copy)) == first


def test_hash_changes_with_result_affecting_source(repo_copy: Path) -> None:
    before = version.engine_source_hash(_package(repo_copy))
    _append(_package(repo_copy) / "post" / "__init__.py", "# changed\n")
    assert version.engine_source_hash(_package(repo_copy)) != before


def test_hash_changes_when_a_result_affecting_file_is_added(repo_copy: Path) -> None:
    before = version.engine_source_hash(_package(repo_copy))
    (_package(repo_copy) / "post" / "statistics.py").write_text("WEIGHT = 0.25\n")
    assert version.engine_source_hash(_package(repo_copy)) != before


@pytest.mark.parametrize(
    "changed",
    [
        "engine/src/dentalsim/exports/__init__.py",
        "engine/src/dentalsim/commands/__init__.py",
        "studio/server/src/dentalsim_studio/app.py",
    ],
)
def test_hash_ignores_other_source(repo_copy: Path, changed: str) -> None:
    before = version.engine_source_hash(_package(repo_copy))
    _append(repo_copy / changed, "# changed\n")
    assert version.engine_source_hash(_package(repo_copy)) == before


def test_hash_ignores_line_endings(repo_copy: Path) -> None:
    before = version.engine_source_hash(_package(repo_copy))
    target = _package(repo_copy) / "model" / "__init__.py"
    target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert version.engine_source_hash(_package(repo_copy)) == before


def test_version_info() -> None:
    info = version.version_info()
    assert re.fullmatch(r"\d+\.\d+\.\d+(\+g[0-9a-f]+)?", info.engine_version)
    assert info.engine_source_hash == version.engine_source_hash()
    assert info.gmsh_version.startswith("4.15")
