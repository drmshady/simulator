"""Backup and restore of everything the studio keeps (task T121; FR-034; research R12; quickstart "Backup").

**The archive** is one zip file, `dentalsim-backup-<UTC time>.zip`, holding:

- `studio.db`: an online snapshot of the database (projects, scenarios, runs, studies, the literature library with
  every version, verification reports, publication records)
- `store/...`: the content-addressed results store
- `published-media/...`: the large published media
- `library-snapshot.yaml`: the approved library as a readable export (the database already holds the library)
- `manifest.json`, written last: the SHA-256 and size of every other file

The archive is written under a `.partial` name and renamed once complete, so a backup cut short never looks whole.
The store's files are already zstd-compressed, so they are stored as they are; the rest is deflated.

**Restore** checks every file against the manifest (no file missing, none extra, every hash equal) by extracting to
a staging folder in the data folder, before anything is replaced. Only then:

1. the current database is copied to `backups/studio-before-restore-<time>.db`, so a restore can be undone
2. the database is replaced through the repositories (checked, then copied in while the studio keeps running)
3. each stored and published file is moved into place, unless an identical file is already there

Store and media files are named by their content, so a file that is in the data folder but not in the backup is
left where it is: it does no harm, and nothing is ever deleted by a restore.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from dentalsim.clock import new_ulid, utc_now
from dentalsim.errors import Refused
from dentalsim.library.service import LibraryService
from dentalsim.persistence import Repos
from dentalsim.store.cas import TEMP_PREFIX
from dentalsim.version import engine_version

FORMAT: Literal["dentalsim.backup/1"] = "dentalsim.backup/1"
MANIFEST = "manifest.json"
DATABASE = "studio.db"
LIBRARY = "library-snapshot.yaml"
FOLDERS = ("store", "published-media")
PARTIAL = ".partial"
CHUNK = 1024 * 1024
STORED_SUFFIXES = (".zst",)
"""Files already compressed, stored in the archive as they are."""
LAST_BACKUP = "last_backup"
LAST_RESTORE = "last_restore"
"""The settings keys where the studio keeps the outcome of its last backup and restore."""

Progress = Callable[[float, str], None]
StopCheck = Callable[[], bool]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackupFile(_Model):
    path: str
    """The path inside the archive, with forward slashes."""
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class BackupManifest(_Model):
    format: Literal["dentalsim.backup/1"] = FORMAT
    created_at: AwareDatetime
    engine_version: str
    files: list[BackupFile]


class BackupResult(_Model):
    archive: str
    created_at: AwareDatetime
    files: int
    size_bytes: int
    """The archive's size."""


class RestoreResult(_Model):
    archive: str
    backup_created_at: AwareDatetime
    restored_at: AwareDatetime
    files: int
    replaced_files: int
    """Stored and published files written from the backup."""
    unchanged_files: int
    """Stored and published files already present with the same content."""
    previous_database: str
    """Where the database as it was before the restore was kept."""


def _refuse(message: str) -> Refused:
    return Refused(message, code="restore_refused")


def _stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _content(data_dir: Path) -> Iterator[tuple[str, Path]]:
    """(archive path, file) for every stored and published file, skipping files still being written."""
    for folder in FOLDERS:
        root = data_dir / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.startswith(TEMP_PREFIX):
                yield path.relative_to(data_dir).as_posix(), path


def _archive_path(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    base = f"dentalsim-backup-{_stamp()}"
    path, n = folder / f"{base}.zip", 1
    while path.exists() or path.with_name(path.name + PARTIAL).exists():
        n += 1
        path = folder / f"{base}-{n}.zip"
    return path


def _add(archive: zipfile.ZipFile, name: str, path: Path) -> BackupFile:
    compression = zipfile.ZIP_STORED if path.suffix in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            target.write(chunk)
    return BackupFile(path=name, sha256=digest.hexdigest(), size_bytes=size)


def create_backup(
    repos: Repos,
    data_dir: Path,
    to: Path | None = None,
    *,
    finished_job_id: str | None = None,
    progress: Progress | None = None,
    should_stop: StopCheck | None = None,
) -> BackupResult:
    """Write one archive of the database, the store, published media and a library export to the folder `to`
    (default: `<data>/backups`). Stops early, leaving nothing behind, when `should_stop` turns true."""
    created = utc_now()
    folder = to or data_dir / "backups"
    final = _archive_path(folder)
    partial = final.with_name(final.name + PARTIAL)
    work = data_dir / "work" / f"backup-{new_ulid()}"
    work.mkdir(parents=True)
    try:
        repos.snapshot_database(work / DATABASE, finished_job_id=finished_job_id)
        LibraryService(repos).export_snapshot(work / LIBRARY)
        entries = [(DATABASE, work / DATABASE), (LIBRARY, work / LIBRARY), *_content(data_dir)]
        total = sum(path.stat().st_size for _, path in entries) or 1
        done = 0
        files = []
        with zipfile.ZipFile(partial, "w", allowZip64=True) as archive:
            for name, path in entries:
                if should_stop is not None and should_stop():
                    raise InterruptedError("the backup was stopped")
                files.append(_add(archive, name, path))
                done += files[-1].size_bytes
                if progress is not None:
                    progress(100.0 * done / total, "backup")
            manifest = BackupManifest(created_at=created, engine_version=engine_version(), files=files)
            archive.writestr(MANIFEST, json.dumps(manifest.model_dump(mode="json"), indent=1), zipfile.ZIP_DEFLATED)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return BackupResult(archive=str(final), created_at=created, files=len(files), size_bytes=final.stat().st_size)


# --- restore -----------------------------------------------------------------------------------------------------


def read_manifest(archive: zipfile.ZipFile) -> BackupManifest:
    try:
        data = json.loads(archive.read(MANIFEST))
    except KeyError as exc:
        raise _refuse("This is not a studio backup: it has no manifest.json.") from exc
    except ValueError as exc:
        raise _refuse(f"The backup's manifest cannot be read ({exc}).") from exc
    try:
        return BackupManifest.model_validate(data)
    except ValidationError as exc:
        raise _refuse(f"The backup's manifest is not a {FORMAT} manifest: {exc.errors()[0]['msg']}.") from exc


def _safe(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    allowed_root = name in (DATABASE, LIBRARY) or (len(path.parts) > 1 and path.parts[0] in FOLDERS)
    if path.is_absolute() or "\\" in name or ".." in path.parts or not allowed_root:
        raise _refuse(f"The backup holds a file the studio does not restore: {name!r}.")
    return path


def _check_names(archive: zipfile.ZipFile, manifest: BackupManifest) -> None:
    listed = {f.path for f in manifest.files}
    present = {i.filename for i in archive.infolist() if not i.is_dir()} - {MANIFEST}
    missing, extra = sorted(listed - present), sorted(present - listed)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing from the archive: {', '.join(missing[:5])}")
        if extra:
            parts.append(f"not in its manifest: {', '.join(extra[:5])}")
        raise _refuse(f"The backup is incomplete or altered ({'; '.join(parts)}). Nothing was restored.")
    if DATABASE not in listed:
        raise _refuse("The backup has no studio.db. Nothing was restored.")
    for name in listed:
        _safe(name)


def _extract(archive: zipfile.ZipFile, item: BackupFile, staging: Path) -> Path:
    target = staging / PurePosixPath(item.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with archive.open(item.path) as source, target.open("wb") as out:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != item.sha256:
        raise _refuse(f"{item.path} in the backup does not match its recorded SHA-256, so the backup is damaged. "
                      "Nothing was restored.")  # fmt: skip
    return target


def restore_backup(
    repos: Repos,
    data_dir: Path,
    archive_path: Path,
    *,
    keep_job_id: str | None = None,
    progress: Progress | None = None,
) -> RestoreResult:
    """Restore `archive_path` into the data folder `data_dir` (see the module notes)."""
    if not archive_path.is_file():
        raise _refuse(f"There is no backup at {archive_path}.")
    running = [j for j in repos.jobs.list_jobs(statuses=["running"]) if j.id != keep_job_id]
    if running:
        raise _refuse(
            "The studio is working on " + ", ".join(f"a {j.kind} job ({j.id})" for j in running)
            + "; wait until it finishes, then restore."
        )  # fmt: skip
    staging = data_dir / "work" / f"restore-{new_ulid()}"
    try:
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile as exc:
            raise _refuse(f"{archive_path} is not a readable zip archive ({exc}).") from exc
        with archive:
            manifest = read_manifest(archive)
            _check_names(archive, manifest)
            total = sum(f.size_bytes for f in manifest.files) or 1
            done = 0
            for item in manifest.files:
                _extract(archive, item, staging)
                done += item.size_bytes
                if progress is not None:
                    progress(80.0 * done / total, "checking")

        previous = data_dir / "backups" / f"studio-before-restore-{_stamp()}-{new_ulid()[-6:]}.db"
        repos.snapshot_database(previous)
        repos.restore_database(staging / DATABASE, keep_job_id=keep_job_id)

        replaced = unchanged = 0
        content = [f for f in manifest.files if f.path not in (DATABASE, LIBRARY)]
        for n, item in enumerate(content, start=1):
            source = staging / PurePosixPath(item.path)
            target = data_dir / PurePosixPath(item.path)
            if target.is_file() and sha256_of(target) == item.sha256:
                unchanged += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                replaced += 1
            if progress is not None:
                progress(80.0 + 20.0 * n / len(content), "restoring")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return RestoreResult(
        archive=str(archive_path),
        backup_created_at=manifest.created_at,
        restored_at=utc_now(),
        files=len(manifest.files),
        replaced_files=replaced,
        unchanged_files=unchanged,
        previous_database=str(previous),
    )
