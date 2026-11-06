"""Round trips through the literature library tables (T037): sources, entries, versions and their source links."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dentalsim.errors import Conflict, NotFound
from dentalsim.library.models import (
    LiteratureEntry,
    LiteratureEntryVersion,
    Source,
    SourceCheck,
    SourceMatch,
    ValueBySource,
)
from dentalsim_studio.db import SqliteRepos
from dentalsim_studio.db import open_repos as studio_open_repos
from dentalsim_studio.db.repos.library import SqliteLibraryRepo

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def repos(data_dir: Path) -> Iterator[SqliteRepos]:
    opened = studio_open_repos(data_dir)
    yield opened
    opened.close()


@pytest.fixture
def library(repos: SqliteRepos) -> SqliteLibraryRepo:
    return repos.library


def _source(source_id: str = "S1", *, doi: str | None = "10.1016/J.Example.2000.01", pmid: str | None = None) -> Source:
    return Source(
        id=source_id,
        authors=["Müller AB", "Smith CD"],
        year=2000,
        title="Elastic modulus of cancellous bone",
        journal="Journal of Biomechanics",
        doi=doi,
        pmid=pmid,
        study_type="experimental",
    )


def _entry(
    entry_id: str = "E1", key: str = "bone.cancellous.E.D4", label: str = "Cancellous bone E, D4"
) -> LiteratureEntry:
    return LiteratureEntry(id=entry_id, parameter_key=key, category="bone", label=label, created_at=T0)


def _version(entry_id: str = "E1", number: int = 1, sources: tuple[str, ...] = ("S1",)) -> LiteratureEntryVersion:
    return LiteratureEntryVersion(
        entry_id=entry_id,
        version=number,
        units="MPa",
        typical=690.0,
        range_min=200.0,
        range_max=1400.0,
        values_by_source=[
            ValueBySource(source_id=sid, value=690.0 + 10 * i, note="mean" if i == 0 else None)
            for i, sid in enumerate(sources)
        ],
        notes="Linear isotropic.",
        origin="claude_proposal",
        proposed_at=T0,
        content_hash=f"hash-{entry_id}-{number}",
    )


def test_source_round_trip_and_case_insensitive_doi_lookup(library: SqliteLibraryRepo) -> None:
    source = _source(pmid="12345678")
    library.add_source(source)
    assert library.get_source("S1") == source
    assert library.find_source(doi="10.1016/j.example.2000.01") == source
    assert library.find_source(pmid="12345678") == source
    assert library.find_source(doi="10.9999/other", pmid="12345678") == source
    assert library.find_source(doi="10.9999/other") is None
    assert library.find_source() is None
    assert library.get_source("missing") is None


def test_duplicate_doi_is_a_conflict(library: SqliteLibraryRepo) -> None:
    library.add_source(_source("S1"))
    with pytest.raises(Conflict):
        library.add_source(_source("S2", doi="10.1016/j.example.2000.01"))


def test_source_check_is_stored(library: SqliteLibraryRepo) -> None:
    library.add_source(_source())
    check = SourceCheck(
        status="resolved_match",
        checked_at=T1,
        resolver="crossref",
        matched=SourceMatch(
            first_author=True, year=True, title_similarity=0.97, resolved_first_author="Müller", resolved_year=2000
        ),
        detail="Crossref: first author, year and title agree",
    )
    library.update_source_check("S1", check)
    stored = library.get_source("S1")
    assert stored is not None
    assert stored.check == check
    with pytest.raises(NotFound):
        library.update_source_check("missing", check)


def test_entry_round_trip_current_version_and_duplicate_key(library: SqliteLibraryRepo) -> None:
    entry = _entry()
    library.add_entry(entry)
    assert library.get_entry("E1") == entry
    assert library.get_entry_by_key("bone.cancellous.E.D4") == entry
    assert library.get_entry_by_key("bone.cancellous.E.D1") is None
    library.add_source(_source())
    library.add_version(_version())
    library.set_current_version("E1", 1)
    assert library.get_entry("E1") == entry.model_copy(update={"current_version": 1})
    library.set_current_version("E1", None)
    assert library.get_entry("E1") == entry
    with pytest.raises(Conflict):
        library.add_entry(_entry("E2"))
    with pytest.raises(NotFound):
        library.set_current_version("missing", 1)


def test_version_round_trip_keeps_source_order(library: SqliteLibraryRepo) -> None:
    library.add_entry(_entry())
    for sid in ("S2", "S1"):
        library.add_source(_source(sid, doi=f"10.1000/{sid}"))
    version = _version(sources=("S2", "S1"))
    library.add_version(version)
    assert library.get_version("E1", 1) == version
    assert [v.source_id for v in library.get_version("E1", 1).values_by_source] == ["S2", "S1"]  # type: ignore[union-attr]
    assert library.get_version("E1", 2) is None
    library.add_version(_version(number=2, sources=("S1",)))
    assert [v.version for v in library.list_versions("E1")] == [1, 2]
    assert library.list_versions("other") == []


def test_add_version_is_all_or_nothing(repos: SqliteRepos, library: SqliteLibraryRepo) -> None:
    library.add_entry(_entry())
    library.add_source(_source())
    with pytest.raises(Conflict):
        library.add_version(_version(sources=("S1", "not-stored")))
    assert library.get_version("E1", 1) is None
    assert repos.db.all("SELECT * FROM version_sources") == []
    library.add_version(_version())
    with pytest.raises(Conflict):
        library.add_version(_version())


def test_add_version_joins_an_outer_transaction(repos: SqliteRepos, library: SqliteLibraryRepo) -> None:
    library.add_source(_source())
    with pytest.raises(RuntimeError), repos.transaction():
        library.add_entry(_entry())
        library.add_version(_version())
        raise RuntimeError("roll back")
    assert library.get_entry("E1") is None
    assert library.get_version("E1", 1) is None


def test_update_version_status_changes_only_status_columns(library: SqliteLibraryRepo) -> None:
    library.add_entry(_entry())
    library.add_source(_source())
    version = _version()
    library.add_version(version)
    tampered = version.model_copy(
        update={
            "status": "rejected",
            "rejected_at": T1,
            "rejected_reason": "Wrong density class.",
            "typical": 700.0,
            "notes": "changed",
            "content_hash": "other",
        }
    )
    library.update_version_status(tampered)
    stored = library.get_version("E1", 1)
    assert stored == version.model_copy(
        update={"status": "rejected", "rejected_at": T1, "rejected_reason": "Wrong density class."}
    )
    approved = version.model_copy(update={"status": "superseded", "approved_at": T0, "superseded_at": T1})
    library.update_version_status(approved)
    assert library.get_version("E1", 1) == approved
    with pytest.raises(NotFound):
        library.update_version_status(version.model_copy(update={"version": 9}))


def test_list_entries_filters(library: SqliteLibraryRepo) -> None:
    library.add_source(_source())
    library.add_entry(_entry("E1", "bone.cancellous.E.D4", "Cancellous bone E, D4"))
    library.add_entry(_entry("E2", "bone.cortical.nu", "Cortical bone Poisson's ratio"))
    library.add_entry(
        LiteratureEntry(
            id="E3", parameter_key="material.zirconia.E", category="material", label="Zirconia E", created_at=T0
        )
    )
    library.add_version(_version("E1"))
    library.add_version(_version("E2"))
    library.update_version_status(_version("E2").model_copy(update={"status": "approved", "approved_at": T1}))

    def keys(**filters: str) -> list[str]:
        return [e.parameter_key for e in library.list_entries(**filters)]  # type: ignore[arg-type]

    assert keys() == ["bone.cancellous.E.D4", "bone.cortical.nu", "material.zirconia.E"]
    assert keys(status="approved") == ["bone.cortical.nu"]
    assert keys(status="proposed") == ["bone.cancellous.E.D4"]
    assert keys(status="rejected") == []
    assert keys(category="material") == ["material.zirconia.E"]
    assert keys(text="CORTICAL") == ["bone.cortical.nu"]
    assert keys(text="poisson") == ["bone.cortical.nu"]
    assert keys(text="E", category="bone", status="proposed") == ["bone.cancellous.E.D4"]
