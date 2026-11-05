"""Exit codes and output modes of the command line (contracts/cli.md)."""

import argparse
import json
from types import SimpleNamespace
from typing import Any

import pytest

from dentalsim import cli
from dentalsim.errors import Refused


def _ok(args: argparse.Namespace, out: cli.Output) -> None:
    out.result({"status": "ok", "count": 3})


def _refuse(args: argparse.Namespace, out: cli.Output) -> None:
    raise Refused(
        "Bonded elastic is awaiting its benchmark.",
        code="run_refused",
        missing=[{"item": "benchmark:bonded_elastic", "message": "Textbook benchmarks have not passed."}],
    )


def _fail(args: argparse.Namespace, out: cli.Output) -> None:
    raise RuntimeError("solver binary not found")


def _register(subparsers: Any) -> None:
    for name, handler in {"ok": _ok, "refuse": _refuse, "fail": _fail}.items():
        subparsers.add_parser(name).set_defaults(handler=handler)
    group = subparsers.add_parser("group").add_subparsers(dest="action", required=True)
    group.add_parser("ok").set_defaults(handler=_ok)


FAKE_COMMANDS = [SimpleNamespace(register=_register)]


def run(*argv: str) -> int:
    return cli.main(list(argv), modules=FAKE_COMMANDS)  # type: ignore[arg-type]


def test_success_exits_0_with_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ok") == 0
    assert capsys.readouterr().out == "status: ok\ncount: 3\n"


@pytest.mark.parametrize("argv", [["--json", "ok"], ["ok", "--json"], ["group", "ok", "--json"]])
def test_json_flag_before_or_after_the_command(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(*argv) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "count": 3}


def test_refused_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("refuse") == 1
    err = capsys.readouterr().err
    assert "refused: Bonded elastic is awaiting its benchmark." in err
    assert "Textbook benchmarks have not passed." in err


def test_refused_json_uses_the_error_shape(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("refuse", "--json") == 1
    body = json.loads(capsys.readouterr().out)
    assert body["code"] == "run_refused"
    assert body["missing"][0]["item"] == "benchmark:bonded_elastic"
    assert body["problems"] == []


def test_other_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("fail") == 2
    assert "error: solver binary not found" in capsys.readouterr().err


def test_usage_errors_exit_2() -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("no-such-command")
    assert exit_info.value.code == 2


def test_command_modules_are_discovered() -> None:
    parser = cli.build_parser()
    assert parser.prog == "dentalsim"
