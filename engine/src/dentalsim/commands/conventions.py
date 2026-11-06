"""`dentalsim conventions check`: parse and validate docs/conventions.md (contracts/cli.md, SC-014, FR-050).

Prints the version, the content hash and the path. Missing or invalid conventions are an error (exit 2, with the
plain reason); a generated colour-scale module that no longer matches them is a refusal (exit 1).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from dentalsim.cli import Output
from dentalsim.conventions.codegen import (
    GENERATED_TS_PATH,
    REGENERATE_COMMAND,
    generated_typescript_path,
    typescript_is_current,
)
from dentalsim.conventions.loader import load_conventions
from dentalsim.errors import Refused

_GENERATED_FROM = re.compile(r"^// Generated from \S+ version (\S+) \(hash ([0-9a-f]+)\)\.$", re.MULTILINE)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("conventions", help="the conventions document, docs/conventions.md")
    actions = parser.add_subparsers(dest="action", metavar="action", required=True)
    check = actions.add_parser(
        "check", help="validate the conventions block; print its version and hash; check the generated scales"
    )
    check.set_defaults(handler=check_conventions)


def _stale_reason(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"{path} is missing"
    except OSError as exc:
        return f"cannot read {path}: {exc.strerror or exc}"
    found = _GENERATED_FROM.search(text)
    if found:
        return f"{path} was generated from conventions {found[1]} (hash {found[2][:12]})"
    return f"{path} was not generated from the current conventions, or was edited by hand"


def check_conventions(args: argparse.Namespace, out: Output) -> None:
    loaded = load_conventions()  # raises ConventionsMissing (exit 2) with the reason
    ts_path = generated_typescript_path()
    if not typescript_is_current(loaded, ts_path):
        raise Refused(
            f"conventions {loaded.version} are valid, but {GENERATED_TS_PATH.as_posix()} is out of date: "
            f"run `{REGENERATE_COMMAND}`",
            code="conventions_codegen_stale",
            problems=[{"item": str(ts_path), "message": _stale_reason(ts_path)}],
        )
    data = {
        "version": loaded.version,
        "content_hash": loaded.content_hash,
        "path": str(loaded.path),
        "generated_typescript": {"path": str(ts_path), "up_to_date": True},
    }
    text = "\n".join(
        [
            f"conventions {loaded.version}",
            f"hash: {loaded.content_hash}",
            f"path: {loaded.path}",
            f"colour scales: {GENERATED_TS_PATH.as_posix()} is up to date",
        ]
    )
    out.result(data, text)
