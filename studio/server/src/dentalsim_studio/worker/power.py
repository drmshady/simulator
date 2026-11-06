"""Keeps Windows awake while jobs exist (research R9, FR-019), so overnight studies are not stopped by sleep.

`SetThreadExecutionState` applies to the calling thread and lapses when that thread ends, so `PowerGuard` must be
updated from one long-lived thread: the worker's main loop. On other systems it does nothing.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
KEEP_AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
RELEASE = ES_CONTINUOUS

IS_WINDOWS = sys.platform == "win32"

log = logging.getLogger(__name__)


def set_thread_execution_state(flags: int) -> int:
    """Call kernel32's `SetThreadExecutionState`; returns the previous state, or 0 when the call failed."""
    if sys.platform != "win32":
        return 0
    function = ctypes.windll.kernel32.SetThreadExecutionState
    function.argtypes = [ctypes.c_uint]
    function.restype = ctypes.c_uint
    return int(function(flags))


class PowerGuard:
    """`update(busy)` asks Windows not to sleep while busy, and allows sleep again when idle. Calls only on changes."""

    def __init__(self, set_state: Callable[[int], int] | None = None) -> None:
        if set_state is None and IS_WINDOWS:
            set_state = set_thread_execution_state
        self._set_state = set_state
        self._busy: bool | None = None

    @property
    def keeping_awake(self) -> bool:
        return bool(self._busy)

    def update(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        if self._set_state is None:
            return
        if self._set_state(KEEP_AWAKE if busy else RELEASE) == 0:
            log.warning("SetThreadExecutionState failed; Windows may sleep while jobs are queued")
        else:
            log.info("%s", "keeping the PC awake while jobs exist" if busy else "no jobs: the PC may sleep again")

    def release(self) -> None:
        """Allow sleep again (on worker exit)."""
        if self._busy:
            self.update(False)
