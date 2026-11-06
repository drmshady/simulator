"""An independent second solver for the textbook cases: scikit-fem (task T073; research R3, R15).

The same `SolverModel` CalculiX solves is solved here with scikit-fem's own assembly: quadratic tetrahedra
(`ElementTetP2`), isotropic linear elasticity, on the corner nodes of the mesh (the textbook meshes are
straight-sided, so the edge nodes are the edge midpoints scikit-fem assumes). Supports, prescribed displacements,
patch forces and face pressures are applied as CalculiX applies them, the pressures as consistent nodal forces.

Stress and strain at the integration points are then recovered from the displacements with the same C3D10 shape
functions and points as CalculiX uses (`dentalsim.verification.solution.recover_fields`), so the two solutions are
compared quantity by quantity. Two unrelated codes agreeing on the same discrete problem checks the CalculiX
solve, its input and the reading of its output.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from skfem import Basis, ElementTetP2, ElementVector, MeshTet, asm, condense, solve
from skfem.models.elasticity import linear_elasticity

from dentalsim.mesh.load_patch import TET10_FACES, face_area_vectors
from dentalsim.solvers.calculix.inp_writer import CCX_TET10_EDGES, SolverModel, validate_model
from dentalsim.verification.solution import CaseSolution, lame, recover_fields

IntArray = NDArray[np.int64]
FloatArray = NDArray[np.float64]


def _dof_map(model: SolverModel, mesh: MeshTet, basis: Basis, vertex_of: dict[int, int]) -> IntArray:
    """(n, 3): the scikit-fem degrees of freedom of each node of the model, in the model's node order."""
    edge_index = {(int(a), int(b)): k for k, (a, b) in enumerate(np.sort(mesh.edges, axis=0).T)}
    dofs = np.full((model.node_ids.size, 3), -1, dtype=np.int64)
    row_of = {int(n): i for i, n in enumerate(model.node_ids)}
    for node, vertex in vertex_of.items():
        dofs[row_of[node]] = basis.nodal_dofs[:, vertex]
    for element in model.connectivity:
        for k, (a, b) in enumerate(CCX_TET10_EDGES, start=4):
            va, vb = vertex_of[int(element[a])], vertex_of[int(element[b])]
            dofs[row_of[int(element[k])]] = basis.edge_dofs[:, edge_index[(min(va, vb), max(va, vb))]]
    if np.any(dofs < 0):
        raise ValueError("some nodes are neither corners nor edge midpoints of the mesh")
    return dofs


def solve_skfem(model: SolverModel) -> CaseSolution:
    """Solve a one-load-case model with scikit-fem."""
    validate_model(model)
    if len(model.load_cases) != 1:
        raise ValueError("the cross-check solves models of one load case")
    case = model.load_cases[0]
    corners = np.unique(model.connectivity[:, :4])
    vertex_of = {int(node): i for i, node in enumerate(corners)}
    order = np.argsort(model.node_ids)
    corner_rows = order[np.searchsorted(model.node_ids, corners, sorter=order)]
    t = np.vectorize(vertex_of.__getitem__)(model.connectivity[:, :4]).T
    mesh = MeshTet(model.coordinates[corner_rows].T.copy(), np.ascontiguousarray(t))
    element = ElementVector(ElementTetP2())
    basis = Basis(mesh, element)

    element_row = {int(e): i for i, e in enumerate(model.element_ids)}
    lam = np.zeros(model.element_ids.size)
    mu = np.zeros(model.element_ids.size)
    materials = {m.name: m for m in model.materials}
    stiffness = None
    for part in model.parts:
        rows = np.array([element_row[int(e)] for e in part.element_ids], dtype=np.int64)
        material = materials[part.material]
        lam[rows], mu[rows] = lame(material.youngs_modulus, material.poissons_ratio)
        part_basis = Basis(mesh, element, elements=rows)
        term = asm(linear_elasticity(*lame(material.youngs_modulus, material.poissons_ratio)), part_basis)
        stiffness = term if stiffness is None else stiffness + term
    assert stiffness is not None

    dofs = _dof_map(model, mesh, basis, vertex_of)
    row_of = {int(n): i for i, n in enumerate(model.node_ids)}
    load = np.zeros(basis.N)
    for node, weight in case.patch:
        load[dofs[row_of[int(node)]]] += weight * np.asarray(case.force, dtype=np.float64)
    for element_id, face, pressure in case.pressures:
        nodes = model.connectivity[element_row[int(element_id)]][list(TET10_FACES[int(face) - 1])]
        rows = np.array([row_of[int(n)] for n in nodes])
        # TET10_FACES list a face's corners so that its normal points into the element, the way a pressure pushes.
        forces = pressure * face_area_vectors(model.coordinates[rows])
        for row, force in zip(rows, forces, strict=True):
            load[dofs[row]] += force

    fixed: dict[int, float] = {}
    for support in model.supports:
        for node in support.node_ids:
            for dof in support.dofs:
                fixed[int(dofs[row_of[int(node)], dof - 1])] = 0.0
    for node, dof, value in case.prescribed:
        fixed[int(dofs[row_of[int(node)], dof - 1])] = float(value)
    known = np.zeros(basis.N)
    held = np.array(sorted(fixed), dtype=np.int64)
    known[held] = [fixed[int(d)] for d in held]
    solution = solve(*condense(stiffness, load, x=known, D=held))

    displacements = np.asarray(solution[dofs], dtype=np.float64)
    stress, strain = recover_fields(model, displacements, lam, mu)
    return CaseSolution(model, displacements, stress, strain)
