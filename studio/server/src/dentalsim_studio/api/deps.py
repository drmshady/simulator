"""Dependencies the routers share: the request's database connection and the owner's session.

- `ReposDep`: the studio database for one request, opened before the route and closed after the response.
- `SessionDep`: the signed-in session. The app applies `require_session` to every router it includes as
  protected (see `dentalsim_studio.app`), so a route declares `SessionDep` only when it needs the session itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from dentalsim_studio.api.errors import ApiError
from dentalsim_studio.auth.sessions import SESSION_COOKIE, Session, SessionStore
from dentalsim_studio.db import SqliteRepos, open_repos


def data_dir_of(request: Request) -> Path:
    path: Path = request.app.state.data_dir
    return path


def get_repos(request: Request) -> Iterator[SqliteRepos]:
    repos = open_repos(data_dir_of(request))
    try:
        yield repos
    finally:
        repos.close()


def get_sessions(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.sessions
    return store


def require_session(request: Request) -> Session:
    """The live session named by the cookie, its idle deadline moved on; otherwise 401 `not_signed_in`."""
    session_id = request.cookies.get(SESSION_COOKIE)
    session = get_sessions(request).touch(session_id)
    if session is None:
        message = "Your session has ended. Sign in again." if session_id else "Sign in to use the studio."
        raise ApiError(401, "not_signed_in", message)
    return session


ReposDep = Annotated[SqliteRepos, Depends(get_repos)]
SessionDep = Annotated[Session, Depends(require_session)]
