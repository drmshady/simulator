"""The current time and new ids. Tests replace the sources to get fixed values (see dentalsim.testing)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ulid import ULID


def _system_now() -> datetime:
    return datetime.now(UTC)


_now: Callable[[], datetime] = _system_now
_new_ulid: Callable[[], ULID] = ULID


def utc_now() -> datetime:
    return _now()


def new_ulid() -> str:
    return str(_new_ulid())
