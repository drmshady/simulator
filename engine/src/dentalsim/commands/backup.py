"""`dentalsim backup [--to PATH]` and `dentalsim restore <archive>` (contracts/cli.md; task T121; FR-034).

Both work on the data folder (`DENTALSIM_DATA`) and can run while the studio is running: the database is copied
with SQLite's online backup. A restore is refused while the worker is busy with a job, and when the archive does not
match its manifest; in both cases nothing is changed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dentalsim.backup import create_backup, restore_backup
from dentalsim.config import ensure_data_folder
from dentalsim.persistence import repos_session

if TYPE_CHECKING:
    from dentalsim.cli import Output

GB = 1024**3


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup", help="back up the database, results store, published media and library to one archive"
    )
    parser.add_argument(
        "--to", type=Path, default=None, help="the folder for the archive, such as an external drive (default: "
        "<data folder>/backups)"
    )  # fmt: skip
    parser.set_defaults(handler=backup_command)

    parser = subparsers.add_parser("restore", help="restore a backup archive, after checking every file's hash")
    parser.add_argument("archive", type=Path)
    parser.set_defaults(handler=restore_command)


def backup_command(args: argparse.Namespace, out: Output) -> None:
    data_dir = ensure_data_folder()
    with repos_session(data_dir) as repos:
        result = create_backup(repos, data_dir, args.to)
    out.result(
        result.model_dump(mode="json"),
        f"backup written: {result.archive}\n  {result.files} files, {result.size_bytes / GB:.2f} GB",
    )


def restore_command(args: argparse.Namespace, out: Output) -> None:
    data_dir = ensure_data_folder()
    with repos_session(data_dir) as repos:
        result = restore_backup(repos, data_dir, args.archive)
    out.result(
        result.model_dump(mode="json"),
        "\n".join(
            [
                f"restored {result.archive} (backup made {result.backup_created_at:%Y-%m-%d %H:%M} UTC)",
                f"  every one of its {result.files} files matched its recorded SHA-256",
                f"  stored and published files: {result.replaced_files} written, {result.unchanged_files} already "
                "present",
                f"  the database as it was before is kept at {result.previous_database}",
                "  work that was queued or running when the backup was made was cancelled; start it again",
            ]
        ),
    )
