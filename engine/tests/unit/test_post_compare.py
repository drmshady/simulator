"""Comparing stored result sets (task T116; FR-027; Principle VII): shared scales and key-number differences."""

from __future__ import annotations

import numpy as np
import pytest

from dentalsim.model.results import KeyNumber
from dentalsim.post.compare import (
    ComparedRun,
    FrostEdge,
    SurfaceValues,
    compare,
    display_range,
    nice_magnification,
)

EDGES = (FrostEdge("frost.a", 1, 200.0), FrostEdge("frost.b", 1, 1500.0), FrostEdge("frost.c", 1, 3000.0))


def number(id: str, value: float, *, case: str = "axial", pct: float | None = None) -> KeyNumber:
    return KeyNumber(id=id, label=id, load_case=case, block="b-1", region="r", statistic="mean", value=value,
                     unit="µε", percent_of_reference=pct)  # fmt: skip


def surface(case: str, strain: list[float], *, displacement: float, size: float = 10.0) -> SurfaceValues:
    return SurfaceValues(
        load_case=case,
        values={"strain": np.array(strain), "stress": np.array([1.0, 2.0]), "pct_strength": np.array([-1.0, 50.0])},
        max_displacement_mm=displacement,
        bounds_mm=((0.0, 0.0, 0.0), (size, 0.0, 0.0)),
    )


def run(run_id: str, numbers: list[KeyNumber], surfaces: list[SurfaceValues], edges=EDGES) -> ComparedRun:  # type: ignore[no-untyped-def]
    return ComparedRun(run_id=run_id, key_numbers=numbers, surfaces=surfaces, frost_edges=edges)


def test_display_range_matches_the_viewer_rule() -> None:
    values = np.array([*range(100), -1.0, np.nan])
    assert display_range(values) == (0.0, 97.0)
    assert display_range(np.array([-1.0, -1.0])) == (0.0, 1.0)
    assert display_range(np.zeros(3)) == (0.0, 1.0)


def test_nice_magnification_rounds_down_to_1_2_or_5() -> None:
    assert [nice_magnification(x) for x in (0.3, 1.0, 3.7, 49.0, 120.0, 999.0)] == [1.0, 1.0, 2.0, 20.0, 100.0, 500.0]


def test_runs_share_one_scale_per_field_the_union_of_their_ranges() -> None:
    first = run("A", [], [surface("axial", list(range(101)), displacement=0.01)])
    second = run("B", [], [surface("axial", [2 * v for v in range(101)], displacement=0.02)])
    result = compare([first, second], ["strain", "frost"])
    strain = result.scales["axial"]["strain"]
    assert [(r.run_id, r.max) for r in strain.by_run] == [("A", 98.0), ("B", 196.0)]
    assert (strain.min, strain.max) == (0.0, 196.0)
    frost = result.scales["axial"]["frost"]
    assert frost.categorical and frost.max == 3
    assert result.frost is not None and result.frost.consistent


def test_one_deformation_scale_from_the_largest_displacement() -> None:
    first = run("A", [], [surface("axial", [1.0], displacement=0.001, size=20.0)])
    second = run("B", [], [surface("axial", [1.0], displacement=0.004, size=20.0)])
    deformation = compare([first, second]).deformation
    assert deformation.max_displacement_mm == 0.004 and deformation.model_size_mm == 20.0
    assert deformation.magnification == 200.0  # 0.05 * 20 / 0.004 = 250, rounded down


def test_key_number_differences_are_matched_by_id_absolute_and_relative() -> None:
    first = run(
        "A", [number("crest", 1000.0, pct=10.0), number("only_a", 5.0)], [surface("axial", [1], displacement=1)]
    )
    second = run(
        "B", [number("crest", 1200.0, pct=12.0), number("only_b", 7.0)], [surface("axial", [1], displacement=1)]
    )
    rows = {row.id: row for row in compare([first, second]).key_numbers}
    crest = rows["crest"]
    assert crest.values == [1000.0, 1200.0] and crest.percent_of_reference == [10.0, 12.0]
    (delta,) = crest.differences
    assert delta is not None and delta.run_id == "B"
    assert delta.absolute == pytest.approx(200.0) and delta.relative == pytest.approx(0.2)
    assert rows["only_a"].values == [5.0, None] and rows["only_a"].differences == [None]
    assert rows["only_b"].values == [None, 7.0]


def test_a_zero_base_has_no_relative_difference() -> None:
    first = run("A", [number("x", 0.0)], [surface("axial", [1], displacement=1)])
    second = run("B", [number("x", 3.0)], [surface("axial", [1], displacement=1)])
    (delta,) = compare([first, second]).key_numbers[0].differences
    assert delta is not None and delta.absolute == 3.0 and delta.relative is None


def test_different_frost_edges_and_missing_load_cases_are_warned_about() -> None:
    other_edges = (EDGES[0], FrostEdge("frost.b", 2, 1600.0), EDGES[2])
    first = run("A", [], [surface("axial", [1], displacement=1), surface("oblique", [1], displacement=1)])
    second = run("B", [], [surface("axial", [1], displacement=1)], edges=other_edges)
    result = compare([first, second])
    assert result.load_cases == ["axial"]
    assert result.frost is not None and not result.frost.consistent
    assert len(result.warnings) == 2


def test_a_comparison_needs_two_runs_and_known_fields() -> None:
    one = run("A", [], [surface("axial", [1], displacement=1)])
    with pytest.raises(ValueError, match="at least two"):
        compare([one])
    with pytest.raises(ValueError, match="unknown fields"):
        compare([one, one], ["temperature"])
