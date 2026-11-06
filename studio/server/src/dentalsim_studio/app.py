"""The studio's FastAPI app (research R10, contracts/studio-api.md).

`create_app()` loads the conventions and refuses to start without them (Principle IV: `ConventionsMissing`
propagates with its reason), makes sure the data folder exists, registers the routers and the error shape, and
serves the built interface (`studio/ui/dist`) for every path outside `/api`.

Routers: every router in `PROTECTED_ROUTERS` is included with the session dependency, so all its routes need a
signed-in owner; registering a new router is one more entry there. `PUBLIC_ROUTERS` is the opt-out and holds only
sign-in (`POST /api/auth/login`); a route that must be reachable without a session goes on a router listed there.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose

from dentalsim.config import Settings, ensure_data_folder, get_settings
from dentalsim.conventions.loader import get_loaded_conventions
from dentalsim.version import package_version
from dentalsim_studio.api import auth, catalogue, events, library, runs, scenarios, system, verification
from dentalsim_studio.api.deps import require_session
from dentalsim_studio.api.errors import error_responses, install_error_handlers
from dentalsim_studio.auth.sessions import SessionStore

PROTECTED_ROUTERS: tuple[APIRouter, ...] = (
    auth.router,
    events.router,
    system.router,
    catalogue.router,
    scenarios.router,
    runs.router,
    library.router,
    verification.router,
)
PUBLIC_ROUTERS: tuple[APIRouter, ...] = (auth.public_router,)

BUILD_COMMAND = "npm --prefix studio/ui run build"
NOT_BUILT_PAGE = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Simulation Studio</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; line-height: 1.5">
<h1>Simulation Studio</h1>
<p>The studio server is running, but its interface has not been built yet. On the main PC, in the repository
folder, run:</p>
<pre>npm --prefix studio/ui ci
{BUILD_COMMAND}</pre>
<p>Then reload this page.</p>
</body>
</html>
"""


def default_ui_dist(settings: Settings) -> Path:
    root = settings.repo_root or Path(__file__).resolve().parents[4]
    return root / "studio" / "ui" / "dist"


class SpaFallback:
    """The router's default handler: the built interface for every path no route matched.

    A file in `dist` is served as it is; any other path outside `/api` and `/assets` gets `index.html`, so the
    interface's own routes survive a reload. `/api` paths never fall back: they answer 404 in the error shape.
    """

    def __init__(self, dist: Path) -> None:
        self.dist = dist
        self.static = StaticFiles(directory=dist, check_dir=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await WebSocketClose()(scope, receive, send)
            return
        path: str = scope["path"]
        if path == "/api" or path.startswith("/api/") or scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=404)
        index = self.dist / "index.html"
        response: Response
        if not index.is_file():
            response = HTMLResponse(NOT_BUILT_PAGE)
        else:
            relative = path.lstrip("/")
            try:
                response = await self.static.get_response(relative, scope) if relative else _index(index)
            except HTTPException as exc:
                if exc.status_code != 404 or relative.startswith("assets/"):
                    raise
                response = _index(index)
        await response(scope, receive, send)


def _index(index: Path) -> Response:
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(*, data_dir: Path | None = None, ui_dist: Path | None = None) -> FastAPI:
    """The studio app over the data folder `data_dir` (default: `DENTALSIM_DATA`) and the interface in `ui_dist`."""
    settings = get_settings()
    conventions = get_loaded_conventions()
    if data_dir is not None:
        settings = settings.model_copy(update={"data_dir": data_dir})
    data_dir = ensure_data_folder(settings)

    app = FastAPI(
        title="Simulation Studio",
        version=package_version(),
        description="The studio's HTTP API (contracts/studio-api.md). Teaching use only.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.data_dir = data_dir
    app.state.conventions = conventions
    app.state.sessions = SessionStore()
    app.state.test_hooks = False

    install_error_handlers(app)
    for router in PUBLIC_ROUTERS:
        app.include_router(router, responses=error_responses(422, 500))
    for router in PROTECTED_ROUTERS:
        app.include_router(router, dependencies=[Depends(require_session)], responses=error_responses(401, 422, 500))
    app.router.default = SpaFallback(ui_dist or default_ui_dist(settings))
    return app
