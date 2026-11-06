"""Keeping previews on the fast cores (research R7; task T098).

Windows 11 power throttling (EcoQoS) runs the threads of a process without a focused window on the efficiency
cores of a hybrid CPU. On the main PC (i5-13400F, 6 performance and 4 efficiency cores, the Balanced plan) that
doubled a preview's single-threaded meshing, from 3.5 to 7.5 s, and slowed its solve. The owner waits for a
preview, so a preview's thread and its solver process ask not to be throttled; detailed runs keep the default,
so they still give way to the owner's other work. Elsewhere these calls do nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

_EXECUTION_SPEED = 0x1  # PROCESS_ and THREAD_POWER_THROTTLING_EXECUTION_SPEED
_THREAD_POWER_THROTTLING = 3  # THREAD_INFORMATION_CLASS.ThreadPowerThrottling
_PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
_PROCESS_SET_INFORMATION = 0x0200


def _throttling(control: int, state: int) -> bytes:
    """A PROCESS_ or THREAD_POWER_THROTTLING_STATE (the same layout): version 1, control mask, state mask."""
    return (1).to_bytes(4, "little") + control.to_bytes(4, "little") + state.to_bytes(4, "little")


def _set_thread(control: int, state: int) -> bool:
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThread.restype = wt.HANDLE
    kernel32.SetThreadInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel32.SetThreadInformation.restype = wt.BOOL
    data = ctypes.create_string_buffer(_throttling(control, state), 12)
    return bool(kernel32.SetThreadInformation(kernel32.GetCurrentThread(), _THREAD_POWER_THROTTLING, data, 12))


@contextmanager
def unthrottled_thread(enabled: bool = True) -> Iterator[bool]:
    """Within the block, the current thread is never power-throttled (when `enabled`); afterwards the system
    decides again. Yields whether the setting took effect (False off Windows or on an older Windows)."""
    if not enabled or sys.platform != "win32":
        yield False
        return
    done = _set_thread(_EXECUTION_SPEED, 0)
    try:
        yield done
    finally:
        if done:
            _set_thread(0, 0)


def unthrottle_process(pid: int) -> bool:
    """Asks that the process `pid` (a solver started for a preview) is never power-throttled. True when it took."""
    if sys.platform != "win32":
        return False
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel32.SetProcessInformation.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        data = ctypes.create_string_buffer(_throttling(_EXECUTION_SPEED, 0), 12)
        return bool(kernel32.SetProcessInformation(handle, _PROCESS_POWER_THROTTLING, data, 12))
    finally:
        kernel32.CloseHandle(handle)
