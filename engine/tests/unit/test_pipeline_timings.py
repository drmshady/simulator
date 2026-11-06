"""The pipeline's stage timings: every stage is kept, including the last one (`store`), and they add up to the total."""

from __future__ import annotations

import pytest

from dentalsim import pipeline

pytestmark = pytest.mark.unit


def test_every_stage_is_timed_and_the_stages_add_up_to_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 0.0, 1.0, 3.0, 6.5, 6.5])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))
    report = pipeline._Reporter(None)  # starts at 0.0
    report.stage("geometry")  # 0.0
    report.stage("solve")  # 1.0
    report.stage("store")  # 3.0
    timings = report.finish()  # 6.5
    assert timings == {"geometry": 1.0, "solve": 2.0, "store": 3.5, "total": 6.5}
