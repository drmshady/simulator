"""`GET /api/search?q=&tag=&setting=path:op:value` (task T117; contracts/studio-api.md; FR-031).

Projects, scenarios and runs that pass every filter given, grouped by kind. `tag` and `setting` may be repeated; the
setting syntax is described in `dentalsim_studio.db.search`. A setting written wrongly answers 422
`validation_failed` with the reason.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from dentalsim_studio.api.deps import ReposDep
from dentalsim_studio.db.search import SearchResults, search

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=SearchResults)
def search_route(
    repos: ReposDep,
    q: Annotated[str | None, Query(max_length=500)] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    setting: Annotated[list[str] | None, Query()] = None,
) -> SearchResults:
    return search(repos.db, text=q, tags=tag or [], settings=setting or [])
