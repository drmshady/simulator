"""The `dentalsim` command line (contracts/cli.md).

Each subcommand lives in its own module in `dentalsim.commands`. A command module defines
`register(subparsers)`, which adds its parsers and sets a handler on each with `set_defaults(handler=...)`.
A handler takes the parsed arguments and an `Output`, and returns an exit code, or None for success.

Exit codes: 0 success, 1 refused (`Refused`), 2 error. `--json` gives machine-readable output; it is accepted
before or after the subcommand.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import ModuleType
from typing import Any, TextIO

from dentalsim import commands
from dentalsim.errors import Refused

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

Handler = Callable[[argparse.Namespace, "Output"], int | None]


class Output:
    """Writes results and errors as text, or as JSON in `--json` mode."""

    def __init__(self, json_mode: bool, stdout: TextIO | None = None, stderr: TextIO | None = None) -> None:
        self.json_mode = json_mode
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def result(self, data: Mapping[str, Any], text: str | None = None) -> None:
        """Print a command's result: `data` as JSON, or `text` (default: `data` as indented key-value lines)."""
        if self.json_mode:
            print(json.dumps(data, indent=2, default=str), file=self.stdout)
        else:
            print(text if text is not None else _as_text(data), file=self.stdout)

    def refused(self, refused: Refused) -> None:
        if self.json_mode:
            print(json.dumps(refused.to_dict(), indent=2, default=str), file=self.stdout)
            return
        lines = [f"refused: {refused.message}"]
        lines += [f"  - {item.get('message', item)}" for item in (*refused.problems, *refused.missing)]
        print("\n".join(lines), file=self.stderr)

    def failed(self, exc: BaseException, *, debug: bool) -> None:
        if self.json_mode:
            print(json.dumps({"code": "error", "message": str(exc) or type(exc).__name__}), file=self.stdout)
        else:
            print(f"error: {exc or type(exc).__name__}", file=self.stderr)
        if debug:
            traceback.print_exception(exc, file=self.stderr)


def _as_text(data: Mapping[str, Any], indent: int = 0) -> str:
    lines = []
    for key, value in data.items():
        if isinstance(value, Mapping):
            lines.append(f"{' ' * indent}{key}:")
            lines.append(_as_text(value, indent + 2))
        else:
            lines.append(f"{' ' * indent}{key}: {value}")
    return "\n".join(lines)


def discover_command_modules() -> list[ModuleType]:
    names = sorted(info.name for info in pkgutil.iter_modules(commands.__path__) if not info.name.startswith("_"))
    return [importlib.import_module(f"{commands.__name__}.{name}") for name in names]


def _add_common_flags(parser: argparse.ArgumentParser, *, top: bool, seen: set[int]) -> None:
    # Subparsers default to SUPPRESS so that leaving a flag out there keeps the value parsed before the subcommand.
    default: Any = False if top else argparse.SUPPRESS
    parser.add_argument("--json", action="store_true", default=default, help="machine-readable output")
    parser.add_argument("--debug", action="store_true", default=default, help="print the traceback of an error")
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                if id(sub) not in seen:  # aliases map to the same parser
                    seen.add(id(sub))
                    _add_common_flags(sub, top=False, seen=seen)


def build_parser(modules: Iterable[ModuleType] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dentalsim", description="Simulation Studio engine and command line (teaching use only)."
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)
    for module in discover_command_modules() if modules is None else modules:
        module.register(subparsers)
    _add_common_flags(parser, top=True, seen=set())
    return parser


def main(argv: Sequence[str] | None = None, modules: Iterable[ModuleType] | None = None) -> int:
    parser = build_parser(modules)
    args = parser.parse_args(argv)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.error(f"'{args.command}' needs a subcommand")  # exits with code 2
    out = Output(json_mode=args.json)
    try:
        code = handler(args, out)
    except Refused as refused:
        out.refused(refused)
        return EXIT_REFUSED
    except Exception as exc:
        out.failed(exc, debug=args.debug)
        return EXIT_ERROR
    return EXIT_OK if code is None else code
