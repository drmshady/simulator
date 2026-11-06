"""Integration-point positions and volumes of 10-node tetrahedra (conventions `statistics.ip_volume`).

Each integration point weighs by the volume the solver itself gives it: det(J) at the point times the Gauss weight.
For a straight-edged element that is a quarter of the element volume; for a curved one (edges bent to follow the
implant, crest or crown surfaces) the four shares differ. Using the solver's own weights keeps the statistics and the
energy check consistent with the solve.

The rule is CalculiX's (ccx 2.23 `gauss.f`, `gauss3d5`; `shape10tet.f`): four points at (a, a, a), (b, a, a),
(a, b, a), (a, a, b) with a = 0.138196601125011 and b = 0.585410196624968, weight 1/24 each, in CalculiX node
order (corners 1–4, then the edge nodes of 1-2, 2-3, 3-1, 1-4, 2-4, 3-4). The positions computed here must match
the integration-point coordinates the solver prints (`COORD`); `check_positions` compares them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing

_A, _B = 0.138196601125011, 0.585410196624968
GAUSS_POINTS = np.array([[_A, _A, _A], [_B, _A, _A], [_A, _B, _A], [_A, _A, _B]])
GAUSS_WEIGHT = 1.0 / 24.0
SUPPORTED_IP_VOLUME_RULE = "jacobian_times_gauss_weight"


def _shape(point: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """C3D10 shape functions (10,) and their derivatives (10, 3) with respect to (xi, eta, zeta)."""
    xi, et, ze = point
    a = 1.0 - xi - et - ze
    n = np.array(
        [
            (2 * a - 1) * a, xi * (2 * xi - 1), et * (2 * et - 1), ze * (2 * ze - 1),
            4 * xi * a, 4 * xi * et, 4 * et * a, 4 * ze * a, 4 * xi * ze, 4 * et * ze,
        ]
    )  # fmt: skip
    da = np.array([-1.0, -1.0, -1.0])
    d = np.array(
        [
            (4 * a - 1) * da,
            [4 * xi - 1, 0, 0],
            [0, 4 * et - 1, 0],
            [0, 0, 4 * ze - 1],
            4 * (a * np.array([1, 0, 0]) + xi * da),
            [4 * et, 4 * xi, 0],
            4 * (a * np.array([0, 1, 0]) + et * da),
            4 * (a * np.array([0, 0, 1]) + ze * da),
            [4 * ze, 0, 4 * xi],
            [0, 4 * ze, 4 * et],
        ],
        dtype=np.float64,
    )
    return n, d


_SHAPES = [_shape(p) for p in GAUSS_POINTS]
SHAPE_VALUES = np.stack([n for n, _ in _SHAPES])  # (4, 10)
SHAPE_DERIVATIVES = np.stack([d for _, d in _SHAPES])  # (4, 10, 3)


@dataclass(frozen=True)
class IntegrationPoints:
    """Per integration point, in element order then point order 1–4."""

    element_ids: NDArray[np.int64]
    ip_numbers: NDArray[np.int64]
    positions: NDArray[np.float64]
    volumes: NDArray[np.float64]


def integration_points(
    node_ids: ArrayLike, coordinates: ArrayLike, element_ids: ArrayLike, connectivity: ArrayLike
) -> IntegrationPoints:
    """Positions and volumes (det J × 1/24) of the four integration points of every C3D10 element."""
    ids = np.asarray(node_ids, dtype=np.int64)
    coords = np.asarray(coordinates, dtype=np.float64)
    conn = np.asarray(connectivity, dtype=np.int64)
    elements = np.asarray(element_ids, dtype=np.int64)
    order = np.argsort(ids)
    where = order[np.searchsorted(ids, conn, sorter=order)]
    if not np.array_equal(ids[where], conn):
        raise ValueError("an element refers to a node that has no coordinates")
    x = coords[where]  # (m, 10, 3)
    jacobians = np.einsum("pka,mkb->mpab", SHAPE_DERIVATIVES, x)  # (m, 4, 3, 3): d x_b / d xi_a
    det = np.linalg.det(jacobians)
    if np.any(det <= 0):
        bad = elements[np.any(det <= 0, axis=1)]
        raise ValueError(f"elements with a non-positive Jacobian (inverted or degenerate): {bad[:10].tolist()}")
    positions = np.einsum("pk,mkb->mpb", SHAPE_VALUES, x)
    return IntegrationPoints(
        element_ids=np.repeat(elements, 4),
        ip_numbers=np.tile(np.arange(1, 5, dtype=np.int64), elements.size),
        positions=positions.reshape(-1, 3),
        volumes=(det * GAUSS_WEIGHT).reshape(-1),
    )


def volumes_for(
    points: IntegrationPoints,
    element_ids: ArrayLike,
    ip_numbers: ArrayLike,
    conventions: Conventions | None = None,
) -> NDArray[np.float64]:
    """The volumes of the integration points listed by (element id, point number), e.g. a `.dat` step's rows."""
    rule = (conventions or get_conventions()).statistics.ip_volume
    if rule != SUPPORTED_IP_VOLUME_RULE:
        raise ConventionsMissing(f"this engine cannot compute the integration-point volume rule {rule!r}")
    return points.volumes[_rows(points, element_ids, ip_numbers)]


def check_positions(
    points: IntegrationPoints,
    element_ids: ArrayLike,
    ip_numbers: ArrayLike,
    solver_positions: ArrayLike,
    *,
    tolerance_mm: float = 1e-4,
) -> float:
    """The largest distance between the computed and the solver's integration-point positions; raises above
    `tolerance_mm`, which would mean a node-order or point-order mismatch."""
    computed = points.positions[_rows(points, element_ids, ip_numbers)]
    gap = float(np.max(np.linalg.norm(computed - np.asarray(solver_positions, dtype=np.float64), axis=1)))
    if gap > tolerance_mm:
        raise ValueError(f"computed integration points differ from the solver's by up to {gap:.3g} mm")
    return gap


def _rows(points: IntegrationPoints, element_ids: ArrayLike, ip_numbers: ArrayLike) -> NDArray[np.intp]:
    keys = points.element_ids * 8 + points.ip_numbers
    wanted = np.asarray(element_ids, dtype=np.int64) * 8 + np.asarray(ip_numbers, dtype=np.int64)
    order = np.argsort(keys)
    found = np.minimum(np.searchsorted(keys, wanted, sorter=order), keys.size - 1)
    rows = order[found]
    if not np.array_equal(keys[rows], wanted):
        raise ValueError("some integration points are not in the mesh")
    return rows
