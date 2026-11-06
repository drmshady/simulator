"""gmsh keeps one global state per process: sessions on several threads take turns (dentalsim.geometry.occ;
task T098, where the studio server built scenes and checked validity on two threads at once and both failed)."""

from __future__ import annotations

import threading

import gmsh
import pytest

from dentalsim.geometry import occ as occ_tools

pytestmark = pytest.mark.unit


def _box_volume(size: float) -> float:
    with occ_tools.session(f"box-{size}"):
        tag = gmsh.model.occ.addBox(0.0, 0.0, 0.0, size, size, size)
        gmsh.model.occ.synchronize()
        return float(gmsh.model.occ.getMass(3, tag))


def test_sessions_on_several_threads_take_turns() -> None:
    results: dict[int, float] = {}
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            for _ in range(5):
                results[index] = _box_volume(1.0 + index)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert results == pytest.approx({i: (1.0 + i) ** 3 for i in range(4)})
    assert not gmsh.isInitialized()


def test_a_session_may_open_inside_another_on_the_same_thread() -> None:
    with occ_tools.session("outer"):
        assert _box_volume(2.0) == pytest.approx(8.0)
        assert gmsh.model.getCurrent() == "outer"
