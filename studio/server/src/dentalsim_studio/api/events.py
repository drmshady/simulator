"""`GET /api/events`: live updates as Server-Sent Events (research R10, contracts/studio-api.md).

The stream polls the `events` table every 250 ms and sends each new event as
`id: <n>` / `event: <type>` / `data: <payload JSON>`. A reconnecting browser sends `Last-Event-ID` and resumes after
it (`?after=` does the same for tests and tools); a new stream starts after the latest event. A comment line every
15 s keeps idle connections open. The stream ends when the client disconnects, and when the owner's session ends
(sign-out, or 2 hours without another request: an open stream is not activity), so the browser's reconnect then
gets 401 and the interface asks for the password.

The stream holds its own database connection, not the request's, and reads it in a worker thread so the event loop
never blocks. Tests set `app.state.test_hooks = True` to make `max_polls` end the stream after that many polls.
"""

from __future__ import annotations

import functools
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated

import anyio
import anyio.to_thread
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from dentalsim.persistence import EventRecord
from dentalsim_studio.api.deps import data_dir_of, get_sessions
from dentalsim_studio.auth.sessions import SESSION_COOKIE
from dentalsim_studio.db import SqliteRepos, open_repos

router = APIRouter(prefix="/api", tags=["events"])

POLL_INTERVAL_S = 0.25
HEARTBEAT_INTERVAL_S = 15.0
RECONNECT_DELAY_MS = 3000
BATCH_SIZE = 100


def format_event(event: EventRecord) -> str:
    data = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


def parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


async def event_stream(
    data_dir: Path,
    after: int | None,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    signed_in: Callable[[], bool] | None = None,
    poll_interval_s: float = POLL_INTERVAL_S,
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
    max_polls: int | None = None,
) -> AsyncIterator[str]:
    """The stream's text chunks: events after `after` (None: after the latest one), and heartbeat comments."""
    repos: SqliteRepos = await anyio.to_thread.run_sync(open_repos, data_dir)
    try:
        last_id = after if after is not None else await anyio.to_thread.run_sync(repos.events.latest_id)
        yield f"retry: {RECONNECT_DELAY_MS}\n\n"
        last_sent = time.monotonic()
        polls = 0
        while (signed_in is None or signed_in()) and not await is_disconnected():
            events = await anyio.to_thread.run_sync(functools.partial(repos.events.since, last_id, limit=BATCH_SIZE))
            for event in events:
                yield format_event(event)
                last_id = event.id
            if events:
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= heartbeat_interval_s:
                yield ": heartbeat\n\n"
                last_sent = time.monotonic()
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            if len(events) < BATCH_SIZE:
                await anyio.sleep(poll_interval_s)
    finally:
        repos.close()


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events"}},
)
async def stream_events(
    request: Request,
    after: Annotated[int | None, Query(ge=0, description="Resume after this event id")] = None,
    max_polls: Annotated[int | None, Query(ge=1, include_in_schema=False)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    state = request.app.state
    resume = parse_last_event_id(last_event_id)
    session_id = request.cookies.get(SESSION_COOKIE)
    stream = event_stream(
        data_dir_of(request),
        resume if resume is not None else after,
        is_disconnected=request.is_disconnected,
        signed_in=functools.partial(get_sessions(request).is_live, session_id),
        poll_interval_s=getattr(state, "events_poll_interval_s", POLL_INTERVAL_S),
        heartbeat_interval_s=getattr(state, "events_heartbeat_s", HEARTBEAT_INTERVAL_S),
        max_polls=max_polls if getattr(state, "test_hooks", False) else None,
    )
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
