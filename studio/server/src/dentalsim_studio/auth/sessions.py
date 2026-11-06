"""Owner sessions and the sign-in rate limit (FR-001, research R10).

There is one owner, so sessions live in the server's memory and end when it restarts; the owner then signs in
again.

- A session id is `secrets.token_urlsafe(32)`, sent in an HttpOnly, Secure, SameSite=Strict cookie on path `/`.
- A session ends after 2 hours without a request. Every request moves the deadline on.
- At most 5 sign-in attempts a minute, over a sliding window: for everyone together (there is one owner, so a
  guesser cannot spread attempts over several devices) and for each client address. Every attempt counts, right
  or wrong; an attempt refused by the limit does not.

Time comes from `dentalsim.clock.utc_now()`, so tests can move it on with the `fixed_time` fixture.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from dentalsim.clock import utc_now

SESSION_COOKIE = "dentalsim_session"
IDLE_TIMEOUT = timedelta(hours=2)
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(minutes=1)


@dataclass
class Session:
    id: str
    created_at: datetime
    last_seen_at: datetime
    idle_timeout: timedelta = IDLE_TIMEOUT

    @property
    def expires_at(self) -> datetime:
        return self.last_seen_at + self.idle_timeout

    def expired(self, now: datetime) -> bool:
        return now > self.expires_at


class SessionStore:
    """The live sessions and the recent sign-in attempts. Safe to use from several threads."""

    def __init__(
        self,
        *,
        idle_timeout: timedelta = IDLE_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        max_attempts_per_client: int = MAX_ATTEMPTS,
        attempt_window: timedelta = ATTEMPT_WINDOW,
    ) -> None:
        self.idle_timeout = idle_timeout
        self.max_attempts = max_attempts
        self.max_attempts_per_client = max_attempts_per_client
        self.attempt_window = attempt_window
        self._sessions: dict[str, Session] = {}
        self._attempts: deque[datetime] = deque()
        self._attempts_by_client: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        now = utc_now()
        session = Session(
            id=secrets.token_urlsafe(32), created_at=now, last_seen_at=now, idle_timeout=self.idle_timeout
        )
        with self._lock:
            self._drop_expired(now)
            self._sessions[session.id] = session
        return session

    def touch(self, session_id: str | None) -> Session | None:
        """The live session with this id, its idle deadline moved on; None when there is none or it has expired."""
        if not session_id:
            return None
        now = utc_now()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expired(now):
                del self._sessions[session_id]
                return None
            session.last_seen_at = now
            return session

    def is_live(self, session_id: str | None) -> bool:
        """True while the session exists and has not expired. Unlike `touch`, it does not move the deadline."""
        if not session_id:
            return False
        with self._lock:
            session = self._sessions.get(session_id)
            return session is not None and not session.expired(utc_now())

    def end(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)

    def register_attempt(self, client: str) -> float | None:
        """Record a sign-in attempt from `client` and return None, or return the seconds to wait when over the limit."""
        now = utc_now()
        cutoff = now - self.attempt_window
        with self._lock:
            for attempts in (self._attempts, *self._attempts_by_client.values()):
                while attempts and attempts[0] <= cutoff:
                    attempts.popleft()
            self._attempts_by_client = {key: value for key, value in self._attempts_by_client.items() if value}
            mine = self._attempts_by_client.setdefault(client, deque())
            waits = [
                (attempts[0] + self.attempt_window - now).total_seconds()
                for attempts, limit in ((self._attempts, self.max_attempts), (mine, self.max_attempts_per_client))
                if len(attempts) >= limit
            ]
            if waits:
                return max(max(waits), 0.0)
            self._attempts.append(now)
            mine.append(now)
            return None

    def _drop_expired(self, now: datetime) -> None:
        for session_id in [sid for sid, session in self._sessions.items() if session.expired(now)]:
            del self._sessions[session_id]
