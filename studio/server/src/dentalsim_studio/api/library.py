"""The literature library, read routes (contracts/studio-api.md "Literature library"; FR-015).

The builder's library-value field (task T091) needs an entry's versions and sources: its approved range, typical
value and every source's value. The routes that change the library (import, check, approve, reject, edit, export)
arrive with Story 2 (task T108), in this module.
"""

from __future__ import annotations

from fastapi import APIRouter

from dentalsim.library.models import EntryStatus, LiteratureEntry
from dentalsim.library.service import EntryDetail, LibraryService
from dentalsim_studio.api.deps import ReposDep

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/entries", response_model=list[LiteratureEntry])
def list_entries(
    repos: ReposDep, status: EntryStatus | None = None, category: str | None = None, q: str | None = None
) -> list[LiteratureEntry]:
    return LibraryService(repos).list_entries(status=status, category=category, text=q)


@router.get("/entries/{entry_ref}", response_model=EntryDetail)
def get_entry(entry_ref: str, repos: ReposDep) -> EntryDetail:
    """The entry with every version and source; `entry_ref` is an entry id or a parameter key."""
    service = LibraryService(repos)
    return service.describe(service.find_entry(entry_ref).id)
