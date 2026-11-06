"""Integration-point positions and volumes, det J × Gauss weight (conventions `statistics.ip_volume`)."""

import numpy as np
import pytest

from dentalsim.post.integration import GAUSS_POINTS, integration_points, volumes_for

CORNERS = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]])
EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))  # CalculiX edge-node order


def _tet10(corners: np.ndarray, bend: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mids = np.array([(corners[a] + corners[b]) / 2 for a, b in EDGES])
    if bend is not None:
        mids = mids + bend
    coords = np.vstack([corners, mids])
    return np.arange(1, 11), coords, np.arange(1, 11).reshape(1, 10)


def test_a_straight_element_gives_a_quarter_of_its_volume_to_each_point() -> None:
    ids, coords, conn = _tet10(CORNERS)
    points = integration_points(ids, coords, [7], conn)
    volume = abs(np.linalg.det(CORNERS[1:] - CORNERS[0])) / 6  # = 4 mm³
    assert points.volumes == pytest.approx([volume / 4] * 4, rel=1e-12)
    assert points.element_ids.tolist() == [7, 7, 7, 7] and points.ip_numbers.tolist() == [1, 2, 3, 4]
    expected = [CORNERS[0] + p @ (CORNERS[1:] - CORNERS[0]) for p in GAUSS_POINTS]
    assert points.positions == pytest.approx(np.array(expected), abs=1e-12)


def test_a_curved_element_splits_its_volume_unequally() -> None:
    bend = np.zeros((6, 3))
    bend[0] = [0.0, -0.3, 0.0]  # bow the 1-2 edge outward
    ids, coords, conn = _tet10(CORNERS, bend)
    points = integration_points(ids, coords, [1], conn)
    assert np.ptp(points.volumes) > 1e-3 * points.volumes.mean()  # unequal shares
    assert np.all(points.volumes > 0)
    # Bowing an edge outward adds volume. The solver's own 4-point rule is what statistics use; that its four
    # shares add up to the solver's printed element volume is checked against a real run (benchmark test).
    assert points.volumes.sum() > abs(np.linalg.det(CORNERS[1:] - CORNERS[0])) / 6


def test_inverted_elements_are_refused() -> None:
    ids, coords, conn = _tet10(CORNERS)
    swapped = conn[:, [1, 0, 2, 3, 4, 6, 5, 7, 9, 8]]  # swap corners 1 and 2 (and their edges)
    with pytest.raises(ValueError, match="non-positive Jacobian"):
        integration_points(ids, coords, [1], swapped)


def test_volumes_follow_the_requested_row_order() -> None:
    ids, coords, conn = _tet10(CORNERS)
    points = integration_points(ids, coords, [3], conn)
    picked = volumes_for(points, [3, 3], [4, 1])
    assert picked.tolist() == [points.volumes[3], points.volumes[0]]
    with pytest.raises(ValueError, match="not in the mesh"):
        volumes_for(points, [9], [1])
