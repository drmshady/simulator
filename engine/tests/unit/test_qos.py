"""Previews ask Windows not to power-throttle them (dentalsim.qos; task T098)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from dentalsim import qos

pytestmark = pytest.mark.unit

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="power throttling is a Windows setting")


def test_the_state_structure_is_version_control_and_state() -> None:
    assert qos._throttling(1, 0) == bytes([1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0])


def test_a_disabled_block_changes_nothing() -> None:
    with qos.unthrottled_thread(enabled=False) as done:
        assert done is False


@windows_only
def test_the_current_thread_is_unthrottled_within_the_block_and_released_after() -> None:
    with qos.unthrottled_thread() as done:
        assert done is True
    assert qos._set_thread(0, 0) is True  # releasing again is harmless


@windows_only
def test_a_child_process_can_be_unthrottled() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert qos.unthrottle_process(child.pid) is True
    finally:
        child.kill()
        child.wait()
