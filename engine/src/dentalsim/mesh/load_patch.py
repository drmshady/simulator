"""The load patch of a contact point (conventions `load_patch`; research R4).

A force acts as a uniform traction vector, in the load direction, over the outer-surface faces whose centroids lie
within `radius_mm` of the contact point. Its nodal forces are the consistent ones: for each face node,
fᵢ = t ∫ Nᵢ dA, with t = F / A over the selected faces, so the nodal forces add up to F exactly. Splitting the force
equally between nodes is not allowed (it misplaces the load on quadratic faces).

Because the traction is one constant vector, each node's force is F times a scalar weight, wᵢ = ∫ Nᵢ dA / A. The
weights add up to 1 and fit `solvers.calculix.inp_writer.UnitLoadCase.patch`. They are 0 at the corners of a flat
quadratic face and can be slightly negative there on a curved one.

Faces are 6-node triangles in CalculiX order: corners 0, 1, 2, then the edge nodes of (0, 1), (1, 2), (2, 0).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing

# The four faces of a CalculiX C3D10 element (0-based), as 6-node triangles.
TET10_FACES = ((0, 1, 2, 4, 5, 6), (0, 3, 1, 7, 8, 4), (1, 3, 2, 8, 9, 5), (2, 3, 0, 9, 7, 6))

# Degree-4 rule on the reference triangle (Dunavant, 6 points); weights add up to 1/2 (the reference area).
_A, _B = 0.445948490915965, 0.091576213509771
_QUAD_POINTS = np.array([[_A, _A], [1 - 2 * _A, _A], [_A, 1 - 2 * _A], [_B, _B], [1 - 2 * _B, _B], [_B, 1 - 2 * _B]])
_QUAD_WEIGHTS = 0.5 * np.array([0.223381589678011] * 3 + [0.109951743655322] * 3)


@dataclass(frozen=True)
class PatchLoad:
    """Selected faces (k, 6) node ids, the patch area (mm²) and the weight of each loaded node (sums to 1)."""

    faces: NDArray[np.int64]
    area_mm2: float
    node_ids: NDArray[np.int64]
    weights: NDArray[np.float64]

    def as_patch(self) -> tuple[tuple[int, float], ...]:
        return tuple((int(n), float(w)) for n, w in zip(self.node_ids, self.weights, strict=True))

    def nodal_forces(self, force: ArrayLike) -> NDArray[np.float64]:
        """(n, 3) consistent nodal forces of a total force `force`, in `node_ids` order."""
        return np.outer(self.weights, np.asarray(force, dtype=np.float64))


def boundary_faces(connectivity: ArrayLike) -> NDArray[np.int64]:
    """The faces (k, 6) that belong to exactly one element of a C3D10 mesh (CalculiX node order)."""
    conn = np.asarray(connectivity, dtype=np.int64)
    faces = conn[:, np.array(TET10_FACES)].reshape(-1, 6)
    keys = np.sort(faces[:, :3], axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    return np.asarray(faces[counts[inverse.ravel()] == 1], dtype=np.int64)


def _shape(xi: float, eta: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Quadratic triangle shape functions (6,) and their derivatives (6, 2) at (xi, eta)."""
    l0, l1, l2 = 1.0 - xi - eta, xi, eta
    n = np.array([l0 * (2 * l0 - 1), l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), 4 * l0 * l1, 4 * l1 * l2, 4 * l2 * l0])
    d = np.array(
        [
            [-(4 * l0 - 1), -(4 * l0 - 1)],
            [4 * l1 - 1, 0.0],
            [0.0, 4 * l2 - 1],
            [4 * (l0 - l1), -4 * l1],
            [4 * l2, 4 * l1],
            [-4 * l2, 4 * (l0 - l2)],
        ]
    )
    return n, d


def _integrals(points: NDArray[np.float64]) -> tuple[NDArray[np.float64], float]:
    """∫ Nᵢ dA (6,) and the area of one quadratic face with node coordinates (6, 3)."""
    integral = np.zeros(6)
    for (xi, eta), weight in zip(_QUAD_POINTS, _QUAD_WEIGHTS, strict=True):
        n, d = _shape(float(xi), float(eta))
        tangents = d.T @ points  # (2, 3)
        jacobian = float(np.linalg.norm(np.cross(tangents[0], tangents[1])))
        integral += weight * jacobian * n
    return integral, float(integral.sum())


def face_area_vectors(points: ArrayLike) -> NDArray[np.float64]:
    """∫ Nᵢ n dA (6, 3) of one quadratic face with node coordinates (6, 3): each node's share of the face's area
    vector, the normal by the right-hand rule on the corner order. A uniform pressure p on the face gives the
    consistent nodal forces p × these (directed along the normal)."""
    xyz = np.asarray(points, dtype=np.float64)
    vectors = np.zeros((6, 3))
    for (xi, eta), weight in zip(_QUAD_POINTS, _QUAD_WEIGHTS, strict=True):
        n, d = _shape(float(xi), float(eta))
        tangents = d.T @ xyz
        vectors += weight * np.outer(n, np.cross(tangents[0], tangents[1]))
    return vectors


def select_faces(
    faces: ArrayLike, node_ids: ArrayLike, coordinates: ArrayLike, contact_point: ArrayLike, radius_mm: float
) -> NDArray[np.int64]:
    """The faces whose centroids (mean of the corner nodes) lie within `radius_mm` of `contact_point`."""
    faces_ = np.asarray(faces, dtype=np.int64)
    xyz = _coordinates_of(faces_, node_ids, coordinates)
    centroids = xyz[:, :3].mean(axis=1)
    distance = np.linalg.norm(centroids - np.asarray(contact_point, dtype=np.float64), axis=1)
    return faces_[distance <= radius_mm]


def _coordinates_of(faces: NDArray[np.int64], node_ids: ArrayLike, coordinates: ArrayLike) -> NDArray[np.float64]:
    ids = np.asarray(node_ids, dtype=np.int64)
    coords = np.asarray(coordinates, dtype=np.float64)
    order = np.argsort(ids)
    where = order[np.searchsorted(ids, faces, sorter=order)]
    if not np.array_equal(ids[where], faces):
        raise ValueError("a face refers to a node that has no coordinates")
    return np.asarray(coords[where], dtype=np.float64)


def patch_load(
    faces: ArrayLike,
    node_ids: ArrayLike,
    coordinates: ArrayLike,
    contact_point: ArrayLike,
    *,
    radius_mm: float | None = None,
    conventions: Conventions | None = None,
) -> PatchLoad:
    """Select the patch faces around `contact_point` and compute the consistent node weights of a uniform traction."""
    patch_rule = (conventions or get_conventions()).load_patch
    if (patch_rule.selection, patch_rule.application) != ("face_centroid_within_radius", "uniform_traction"):
        raise ConventionsMissing(f"this engine cannot apply the load-patch rule {patch_rule.model_dump()}")
    radius = patch_rule.radius_mm if radius_mm is None else radius_mm
    selected = select_faces(faces, node_ids, coordinates, contact_point, radius)
    if not selected.size:
        raise ValueError(
            f"no surface face has its centroid within {radius:g} mm of the contact point "
            f"{np.asarray(contact_point, dtype=np.float64).tolist()}"
        )
    xyz = _coordinates_of(selected, node_ids, coordinates)
    totals: dict[int, float] = {}
    area = 0.0
    for face, points in zip(selected, xyz, strict=True):
        integral, face_area = _integrals(points)
        area += face_area
        for node, value in zip(face, integral, strict=True):
            totals[int(node)] = totals.get(int(node), 0.0) + float(value)
    ids = np.array(sorted(totals), dtype=np.int64)
    weights = np.array([totals[int(n)] for n in ids]) / area
    return PatchLoad(faces=selected, area_mm2=area, node_ids=ids, weights=weights)
