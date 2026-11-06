"""A solved verification model, the same whichever solver solved it, and what the case builders measure on it.

`CaseSolution` holds the nodal displacements of one load case and the stress and strain at every integration
point, in element order then point order 1–4 (CalculiX's 4-point rule, `dentalsim.post.integration`). From
CalculiX they are read from its output; from the scikit-fem cross-check they are recovered from its displacements
by `recover_fields`, with the same shape functions and points.

Point values such as NAFEMS LE10's stress at point D are not reported anywhere else in the engine, which uses region
statistics only; here they are what the textbooks state, so `nodal_stress` extrapolates the integration-point
values of each element to the node (the linear field through its 4 points, as CalculiX does for its nodal output)
and averages over the elements that share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dentalsim.post.integration import GAUSS_POINTS, SHAPE_DERIVATIVES, IntegrationPoints, integration_points
from dentalsim.solvers.calculix.inp_writer import SolverModel

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]

_CORNERS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
EXTRAPOLATION = np.column_stack([np.ones(4), _CORNERS]) @ np.linalg.inv(np.column_stack([np.ones(4), GAUSS_POINTS]))
"""(4 corners, 4 points): the linear field through the 4 integration-point values, at the corners."""


@dataclass(frozen=True, eq=False)
class CaseSolution:
    model: SolverModel
    displacements: FloatArray
    """(n, 3) in the model's node order."""
    stress: FloatArray
    """(4m, 6): xx, yy, zz, xy, xz, yz, in MPa."""
    strain: FloatArray
    """(4m, 6), tensor shear components."""

    @cached_property
    def points(self) -> IntegrationPoints:
        m = self.model
        return integration_points(m.node_ids, m.coordinates, m.element_ids, m.connectivity)

    def rows(self, node_ids: ArrayLike) -> IntArray:
        ids = np.asarray(node_ids, dtype=np.int64)
        order = np.argsort(self.model.node_ids)
        rows = order[np.searchsorted(self.model.node_ids, ids, sorter=order)]
        if not np.array_equal(self.model.node_ids[rows], ids):
            raise KeyError("some nodes are not in the model")
        return np.asarray(rows, dtype=np.int64)

    def displacement(self, node_ids: ArrayLike) -> FloatArray:
        return np.asarray(self.displacements[self.rows(node_ids)], dtype=np.float64)

    def nodal_stress(self, node_id: int) -> FloatArray:
        """The stress (6,) at a corner node: each sharing element's extrapolation, averaged."""
        conn = self.model.connectivity
        hits = np.argwhere(conn[:, :4] == node_id)
        if not hits.size:
            raise KeyError(f"node {node_id} is not a corner of any element")
        values = [EXTRAPOLATION[corner] @ self.stress[4 * element : 4 * element + 4] for element, corner in hits]
        return np.asarray(np.mean(values, axis=0), dtype=np.float64)


def ccx_solution(
    model: SolverModel,
    displacements: FloatArray,
    stress: FloatArray,
    strain: FloatArray,
    ip_element_ids: IntArray,
    ip_numbers: IntArray,
) -> CaseSolution:
    """A solution from CalculiX output, its rows checked to be in element order then point order."""
    expected_elements = np.repeat(np.sort(model.element_ids), 4)
    expected_points = np.tile(np.arange(1, 5), model.element_ids.size)
    if not (np.array_equal(ip_element_ids, expected_elements) and np.array_equal(ip_numbers, expected_points)):
        raise ValueError("the solver's integration-point rows are not one per element and point")
    if not np.array_equal(model.element_ids, np.sort(model.element_ids)):
        raise ValueError("verification models number their elements in order")
    return CaseSolution(model, displacements, stress, strain)


def recover_fields(
    model: SolverModel, displacements: FloatArray, lam: FloatArray, mu: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Stress and strain at the integration points from nodal displacements (C3D10 shape functions).

    `lam` and `mu` are each element's Lamé parameters, shape (m,).
    """
    m = model
    order = np.argsort(m.node_ids)
    rows = order[np.searchsorted(m.node_ids, m.connectivity, sorter=order)]
    x = m.coordinates[rows]  # (m, 10, 3)
    u = displacements[rows]  # (m, 10, 3)
    jac = np.einsum("pka,mkb->mpab", SHAPE_DERIVATIVES, x)  # d x_b / d xi_a
    dn_dx = np.einsum("mpba,pka->mpkb", np.linalg.inv(jac), SHAPE_DERIVATIVES)  # (m, 4, 10, 3): dN_k / dx_b
    grad = np.einsum("mpkb,mkc->mpcb", dn_dx, u)  # du_c / dx_b
    eps = 0.5 * (grad + np.swapaxes(grad, 2, 3))
    trace = np.trace(eps, axis1=2, axis2=3)
    sigma = 2.0 * mu[:, None, None, None] * eps + (lam[:, None] * trace)[:, :, None, None] * np.eye(3)
    pick = ([0, 1, 2, 0, 0, 1], [0, 1, 2, 1, 2, 2])
    strain = eps[:, :, pick[0], pick[1]].reshape(-1, 6)
    stress = sigma[:, :, pick[0], pick[1]].reshape(-1, 6)
    return np.asarray(stress, dtype=np.float64), np.asarray(strain, dtype=np.float64)


def lame(e_mpa: float, nu: float) -> tuple[float, float]:
    return e_mpa * nu / ((1 + nu) * (1 - 2 * nu)), e_mpa / (2 * (1 + nu))
