"""Backup and restore (task T121; FR-034; quickstart "Backup"), on a real data folder and studio database.

- A backup is one archive whose manifest lists every file with its SHA-256; restoring it brings back the projects,
  the library, stored results and published media, keeps the database as it was before, and cancels work that was
  in flight when the backup was made.
- A damaged or altered archive is refused before anything changes.
- A restore is refused while the worker is busy.
- `dentalsim backup --to` and `dentalsim restore` do the same from the command line.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dentalsim import cli
from dentalsim.backup import DATABASE, LIBRARY, MANIFEST, create_backup, read_manifest, restore_backup
from dentalsim.errors import Refused
from dentalsim.persistence import Repos, open_repos
from dentalsim.store.cas import ContentStore
from dentalsim.testing import seed_library

KEY = "ab" * 32


@pytest.fixture
def repos(data_dir: Path) -> Iterator[Repos]:
    opened = open_repos(data_dir)
    seed_library(opened)
    opened.scenarios.add_project("Implant length study", notes="10 against 8 mm", tags=["lengths"])
    store = ContentStore(data_dir / "store")
    store.put_bytes(KEY, "crown.glb", b"glb bytes")
    store.put_json(KEY, "result", {"ok": True})
    store.complete(KEY)
    (data_dir / "published-media").mkdir(exist_ok=True)
    (data_dir / "published-media" / "clip.mp4").write_bytes(b"film")
    yield opened
    opened.close()


def project_names(repos: Repos) -> list[str]:
    return sorted(p.name for p in repos.scenarios.list_projects())


def test_backup_then_restore_brings_everything_back(repos: Repos, data_dir: Path, tmp_path: Path) -> None:
    result = create_backup(repos, data_dir, tmp_path / "external")
    archive = Path(result.archive)
    assert archive.parent == tmp_path / "external" and archive.suffix == ".zip"
    assert not list(archive.parent.glob("*.partial")) and not list((data_dir / "work").iterdir())
    with zipfile.ZipFile(archive) as zf:
        manifest = read_manifest(zf)
        names = {f.path for f in manifest.files}
        assert {DATABASE, LIBRARY, "published-media/clip.mp4"} <= names
        assert any(n.startswith(f"store/{KEY[:2]}/{KEY}/crown.glb") for n in names)
        for item in manifest.files:
            assert hashlib.sha256(zf.read(item.path)).hexdigest() == item.sha256
        assert "parameter_key" in zf.read(LIBRARY).decode()

    # Change everything after the backup.
    project = repos.scenarios.list_projects()[0]
    repos.scenarios.update_project(project.id, name="Renamed")
    repos.scenarios.add_project("Made after the backup")
    ContentStore(data_dir / "store").delete(KEY)
    (data_dir / "published-media" / "clip.mp4").write_bytes(b"changed")
    job = repos.jobs.enqueue("run", "heavy", {"run_id": "R"})

    restored = restore_backup(repos, data_dir, archive)
    assert project_names(repos) == ["Implant length study"]
    assert repos.scenarios.list_projects()[0].tags == ["lengths"]
    assert ContentStore(data_dir / "store").get_bytes(KEY, "crown.glb") == b"glb bytes"
    assert (data_dir / "published-media" / "clip.mp4").read_bytes() == b"film"
    assert restored.files == len(manifest.files) and restored.replaced_files >= 3
    assert repos.jobs.get(job.id) is None  # made after the backup
    previous = open_repos_at(Path(restored.previous_database))
    assert "Made after the backup" in previous
    assert not list((data_dir / "work").iterdir())


def open_repos_at(database: Path) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(database)
    try:
        return sorted(row[0] for row in conn.execute("SELECT name FROM projects"))
    finally:
        conn.close()


def test_work_in_flight_at_backup_time_is_cancelled_and_the_restoring_job_kept(repos: Repos, data_dir: Path) -> None:
    queued = repos.jobs.enqueue("run", "heavy", {"run_id": "R"})
    archive = Path(create_backup(repos, data_dir).archive)
    assert archive.parent == data_dir / "backups"
    restoring = repos.jobs.enqueue("restore", "heavy", {"archive": str(archive)})
    claimed = repos.jobs.claim("heavy")
    assert claimed is not None and claimed.id == queued.id
    repos.jobs.finish(queued.id, "succeeded")
    claimed = repos.jobs.claim("heavy")
    assert claimed is not None and claimed.id == restoring.id

    restore_backup(repos, data_dir, archive, keep_job_id=restoring.id)
    back = repos.jobs.get(queued.id)
    assert back is not None and back.status == "cancelled" and "backup" in (back.failure_reason or "")
    kept = repos.jobs.get(restoring.id)
    assert kept is not None and kept.status == "running"


def test_the_backup_job_is_finished_in_its_own_backup(repos: Repos, data_dir: Path) -> None:
    job = repos.jobs.enqueue("backup", "heavy", {})
    repos.jobs.claim("heavy")
    archive = Path(create_backup(repos, data_dir, finished_job_id=job.id).archive)
    repos.jobs.finish(job.id, "succeeded")
    restore_backup(repos, data_dir, archive)
    restored = repos.jobs.get(job.id)
    assert restored is not None and restored.status == "succeeded"


def altered(archive: Path, name: str, data: bytes) -> Path:
    """A copy of the archive with one file's content replaced (the manifest left as it was)."""
    copy = archive.with_name("altered.zip")
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(copy, "w") as target:
        for info in source.infolist():
            target.writestr(info, data if info.filename == name else source.read(info.filename))
    return copy


def test_a_damaged_archive_is_refused_before_anything_changes(repos: Repos, data_dir: Path) -> None:
    archive = Path(create_backup(repos, data_dir).archive)
    project = repos.scenarios.list_projects()[0]
    repos.scenarios.update_project(project.id, name="Changed since")
    (data_dir / "published-media" / "clip.mp4").write_bytes(b"changed")

    with pytest.raises(Refused, match="SHA-256") as refused:
        restore_backup(repos, data_dir, altered(archive, "published-media/clip.mp4", b"tampered"))
    assert refused.value.code == "restore_refused"
    assert project_names(repos) == ["Changed since"]
    assert (data_dir / "published-media" / "clip.mp4").read_bytes() == b"changed"
    assert not list((data_dir / "work").iterdir())

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read(MANIFEST))
    manifest["files"] = manifest["files"][1:]
    extra = altered(archive, MANIFEST, json.dumps(manifest).encode())
    with pytest.raises(Refused, match="not in its manifest"):
        restore_backup(repos, data_dir, extra)
    with pytest.raises(Refused, match="not a readable zip"):
        restore_backup(repos, data_dir, _not_a_zip(data_dir))
    assert project_names(repos) == ["Changed since"]


def _not_a_zip(data_dir: Path) -> Path:
    path = data_dir / "backups" / "notes.zip"
    path.write_bytes(io.BytesIO(b"plain text").getvalue())
    return path


def test_a_restore_is_refused_while_the_worker_is_busy(repos: Repos, data_dir: Path) -> None:
    archive = Path(create_backup(repos, data_dir).archive)
    repos.jobs.enqueue("run", "heavy", {"run_id": "R"})
    repos.jobs.claim("heavy")
    with pytest.raises(Refused, match="wait until it finishes"):
        restore_backup(repos, data_dir, archive)


def test_the_command_line_backs_up_and_restores(
    repos: Repos, data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--json", "backup", "--to", str(tmp_path / "drive")]) == 0
    written = json.loads(capsys.readouterr().out)
    archives = list((tmp_path / "drive").glob("dentalsim-backup-*.zip"))
    assert [str(a) for a in archives] == [written["archive"]]
    project = repos.scenarios.list_projects()[0]
    repos.scenarios.update_project(project.id, name="Changed since")
    assert cli.main(["restore", str(archives[0])]) == 0
    assert "matched its recorded SHA-256" in capsys.readouterr().out
    assert project_names(repos) == ["Implant length study"]
    assert cli.main(["restore", str(tmp_path / "missing.zip")]) == 1
