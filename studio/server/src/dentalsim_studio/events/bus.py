"""Appending live-update events (research R10, contracts/studio-api.md "Auth and system").

The worker and the server both call `emit`; it writes to the `events` table, which `GET /api/events` streams to
the browser. Only the contract's event types are accepted, so a misspelt type fails where it is emitted instead of
never reaching the interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, get_args

from dentalsim.persistence import EventSink

EventType = Literal[
    "preview.ready",
    "run.progress",
    "run.finished",
    "study.progress",
    "job.failed",
    "validity.changed",
    "capability.changed",
]
EVENT_TYPES: frozenset[str] = frozenset(get_args(EventType))


class HasEvents(Protocol):
    @property
    def events(self) -> EventSink: ...


def check_event_type(type: str) -> None:
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}; the event types are {', '.join(sorted(EVENT_TYPES))}")


def emit(repos: HasEvents, type: str, payload: Mapping[str, Any]) -> int:
    """Append an event and return its id. `run.progress` carries `run_id`, `percent`, `stage` and `remaining_s`."""
    check_event_type(type)
    return repos.events.emit(type, dict(payload))
