"""`dentalsim studio serve`, `dentalsim studio set-password` and `dentalsim worker` (contracts/cli.md, FR-001).

- `studio serve [--host IP] [--port N]` starts the server and the worker on the main PC (research R10); it refuses
  to start while no owner password is set.
- `studio set-password` asks for the owner password twice and stores its Argon2id hash; `--password-stdin` reads
  one line from standard input instead, for scripts.
- `worker` runs the worker alone (normally `serve` starts it).

The studio package is imported inside the handlers, so the rest of the command line works without it.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from dentalsim.cli import Output
from dentalsim.config import ensure_data_folder
from dentalsim.errors import Refused


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    studio = subparsers.add_parser("studio", help="the studio server on the main PC")
    actions = studio.add_subparsers(dest="action", metavar="action", required=True)

    serve = actions.add_parser("serve", help="start the studio server and its worker")
    serve.add_argument("--host", help="address to listen on (default: 0.0.0.0 with home subnets, else 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port to listen on (default: DENTALSIM_PORT, 8443)")
    serve.set_defaults(handler=serve_studio)

    password = actions.add_parser("set-password", help="set or change the owner password")
    password.add_argument(
        "--password-stdin", action="store_true", help="read the password from the first line of standard input"
    )
    password.set_defaults(handler=set_owner_password)

    worker = subparsers.add_parser("worker", help="start the worker alone (normally started by `studio serve`)")
    worker.set_defaults(handler=start_worker)


def serve_studio(args: argparse.Namespace, out: Output) -> None:
    from dentalsim_studio.serve import serve

    serve(args.host, args.port, out=out.stdout)


def read_new_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New owner password (at least 10 characters): ")
    second = getpass.getpass("The same password again: ")
    if first != second:
        raise Refused("The two passwords differ. Nothing was changed.", code="validation_failed")
    return first


def set_owner_password(args: argparse.Namespace, out: Output) -> None:
    from dentalsim_studio.auth.password import check_new_password, set_password
    from dentalsim_studio.db import open_repos

    password = read_new_password(args)
    check_new_password(password)
    data_dir = ensure_data_folder()
    repos = open_repos(data_dir)
    try:
        set_password(repos, password)
    finally:
        repos.close()
    out.result(
        {"password_set": True, "data_dir": str(data_dir)},
        text=f"The owner password is set (stored as an Argon2id hash in {data_dir}).",
    )


def start_worker(args: argparse.Namespace, out: Output) -> None:
    from dentalsim_studio.worker.main import run_worker

    run_worker()
