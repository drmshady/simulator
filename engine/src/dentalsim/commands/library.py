"""`dentalsim library import|check|approve|reject|export` (contracts/cli.md, T038).

The commands apply the same rules as the studio through `LibraryService`, on the studio database in the data
folder (`DENTALSIM_DATA`). `<entry>` is an entry id or a parameter key. A refusal exits with code 1.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dentalsim.library.models import LiteratureEntry, LiteratureEntryVersion, Source, SourceCheck
from dentalsim.library.service import LibraryService, cite
from dentalsim.persistence import repos_session

if TYPE_CHECKING:
    from dentalsim.cli import Output


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("library", help="literature library: import, check, approve, reject, export")
    actions = parser.add_subparsers(dest="action", metavar="action", required=True)

    command = actions.add_parser("import", help="import a dentalsim.library/1 file as proposed versions")
    command.add_argument("file", type=Path)
    command.set_defaults(handler=import_command)

    command = actions.add_parser("check", help="check an entry's sources at Crossref and PubMed and show the result")
    command.add_argument("entry", help="entry id or parameter key")
    command.add_argument("--version", type=int, default=None, help="version to check (default: the newest)")
    command.set_defaults(handler=check_command)

    command = actions.add_parser("approve", help="approve a proposed version whose sources all match")
    command.add_argument("entry", help="entry id or parameter key")
    command.add_argument("version", type=int)
    command.set_defaults(handler=approve_command)

    command = actions.add_parser("reject", help="reject a proposed or approved version")
    command.add_argument("entry", help="entry id or parameter key")
    command.add_argument("version", type=int)
    command.add_argument("--reason", required=True, help="why the version is rejected")
    command.set_defaults(handler=reject_command)

    command = actions.add_parser("export", help="write the approved library to a snapshot file")
    command.add_argument(
        "--to", type=Path, default=None, help="default: content/library/snapshots/library-snapshot.yaml"
    )
    command.set_defaults(handler=export_command)


def _quantity(value: float, units: str) -> str:
    """690 MPa; a dimensionless value (units "1") is shown without units."""
    return f"{value:g}" if units == "1" else f"{value:g} {units}"


def _label(entry: LiteratureEntry, version: LiteratureEntryVersion) -> str:
    return f"{entry.parameter_key} v{version.version}"


def import_command(args: argparse.Namespace, out: Output) -> None:
    with repos_session() as repos:
        result = LibraryService(repos).import_file(args.file)
    lines = [f"imported {len(result.imported)} proposed version(s) from {args.file} (origin {result.origin})"]
    lines += [
        f"  {item.parameter_key} v{item.version}" + (" (new entry)" if item.new_entry else "")
        for item in result.imported
    ]
    if result.unchanged:
        lines.append(f"unchanged, equal to a stored version: {len(result.unchanged)}")
        lines += [f"  {item.parameter_key} v{item.version}" for item in result.unchanged]
    reused = len(result.source_ids) - len(result.new_sources)
    lines.append(f"sources: {len(result.new_sources)} new, {reused} already stored")
    lines += [f"warning: {text}" for text in result.warnings]
    lines.append("next: dentalsim library check <entry> for each entry, then approve or reject it")
    out.result(result.model_dump(mode="json"), "\n".join(lines))


def _check_lines(source: Source, check: SourceCheck) -> list[str]:
    lines = [f'  {check.status}: {", ".join(source.authors)} ({source.year}) "{source.title}"']
    ids = [f"doi {source.doi}" if source.doi else "", f"pmid {source.pmid}" if source.pmid else ""]
    lines.append(
        f"      {'; '.join(i for i in ids if i)}" + (f"; checked with {check.resolver}" if check.resolver else "")
    )
    match = check.matched
    if match is not None:
        author = "agrees" if match.first_author else "differs"
        year = "agrees" if match.year else "differs"
        lines.append(
            f"      first author {author} ({match.resolved_first_author}); year {year} ({match.resolved_year}); "
            f"title similarity {match.title_similarity:.2f}"
        )
        if match.resolved_title and match.title_similarity < 1:
            lines.append(f'      resolved title: "{match.resolved_title}"')
    if check.detail and check.status != "resolved_match":
        lines.append(f"      {check.detail}")
    return lines


def check_command(args: argparse.Namespace, out: Output) -> None:
    with repos_session() as repos:
        service = LibraryService(repos)
        entry = service.find_entry(args.entry)
        version = (
            service.get_version(entry.id, args.version)
            if args.version is not None
            else service.newest_version(entry.id)
        )
        checked = service.check_sources(entry.id, version.version)
        problems = service.approval_problems(entry.id, version.version)
        entry = service.get_entry(entry.id)
    units = version.units
    lines = [
        f"{entry.parameter_key} ({entry.id}): {entry.label}",
        f"category {entry.category}; version {version.version}, {version.status}, origin {version.origin}; "
        f"current approved version: {entry.current_version or 'none'}",
        f"typical {_quantity(version.typical, units)}; "
        f"range {version.range_min:g}–{_quantity(version.range_max, units)}",
        "values by source:",
    ]
    by_id = {source.id: source for source, _ in checked}
    for item in version.values_by_source:
        note = f"  ({item.note})" if item.note else ""
        lines.append(f"  {_quantity(item.value, units)}  {cite(by_id[item.source_id])}{note}")
    if version.notes:
        lines.append(f"notes: {version.notes}")
    lines.append("source checks:")
    for source, check in checked:
        lines += _check_lines(source, check)
    if problems:
        lines.append("cannot be approved:")
        lines += [f"  - {problem['message']}" for problem in problems]
    else:
        lines.append(f"can be approved: dentalsim library approve {entry.parameter_key} {version.version}")
    data = {
        "entry": entry.model_dump(mode="json"),
        "version": version.model_dump(mode="json"),
        "sources": [{"source": s.model_dump(mode="json"), "check": c.model_dump(mode="json")} for s, c in checked],
        "approvable": not problems,
        "problems": problems,
    }
    out.result(data, "\n".join(lines))


def approve_command(args: argparse.Namespace, out: Output) -> None:
    with repos_session() as repos:
        service = LibraryService(repos)
        entry = service.find_entry(args.entry)
        result = service.approve(entry.id, args.version)
    text = f"approved {_label(entry, result.version)}"
    if result.superseded:
        text += "; superseded " + ", ".join(f"v{n}" for n in result.superseded)
    out.result(result.model_dump(mode="json"), text)


def reject_command(args: argparse.Namespace, out: Output) -> None:
    with repos_session() as repos:
        service = LibraryService(repos)
        entry = service.find_entry(args.entry)
        result = service.reject(entry.id, args.version, args.reason)
    lines = [f"rejected {_label(entry, result.version)}: {result.version.rejected_reason}"]
    if result.was_current:
        lines.append(f"{entry.parameter_key} has no approved version now")
    if result.flagged_runs:
        lines.append(f"flagged {len(result.flagged_runs)} run(s) 'uses a rejected entry':")
        lines += [f"  {run_id}" for run_id in result.flagged_runs]
        lines.append("they cannot be published until they are re-run with approved versions")
    else:
        lines.append("no runs used this version")
    out.result(result.model_dump(mode="json"), "\n".join(lines))


def export_command(args: argparse.Namespace, out: Output) -> None:
    with repos_session() as repos:
        service = LibraryService(repos)
        count = len(service.snapshot_items())
        path = service.export_snapshot(args.to)
    out.result({"path": str(path), "entries": count}, f"wrote {count} approved entries to {path}")
