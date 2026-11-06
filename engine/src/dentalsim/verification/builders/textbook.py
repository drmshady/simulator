"""Textbook verification models for bonded elastic (task T072; research R15).

Each builder takes a case file's `model.parameters` and a detail level, and returns the solver models and how to
measure the case's quantities on their solutions. The expected values live in the case files, never here; a
builder only states how a quantity is measured. Units are those of the conventions: mm, N, MPa.

- `cantilever`: a square beam clamped at x = 0 with a uniform shear traction on its free end; `tip_deflection` is
  the end face's area-weighted mean deflection.
- `lame_cylinder`: a quarter of a thick cylinder in plane strain under internal pressure;
  `radial_displacement_inner`, `hoop_stress_mid`, `radial_stress_mid`, `axial_stress`.
- `nafems_le10`: NAFEMS LE10, a quarter of a thick elliptic plate with an elliptic hole, pressure on top;
  `sigma_yy_at_d` is σyy at point D, extrapolated to the node.
- `patch_test`: a cube with a linear displacement prescribed on its whole boundary; `max_strain_error` and
  `max_stress_error`, relative to the largest exact component.
- `bonded_bars`: two bars bonded end to end and pulled along their axis, with the same ν/E so that they contract
  alike and the field is exactly uniaxial; `end_displacement`, `stress_bar_1`, `stress_bar_2`.
- `cylinder_convergence`: `lame_cylinder` at each element size of `mesh_sizes_mm`; `finest_relative_error` and
  `errors_decrease`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import gmsh
import numpy as np
from numpy.typing import NDArray

from dentalsim.errors import DentalsimError
from dentalsim.model.scenario import DetailLevel
from dentalsim.solvers.calculix.inp_writer import MaterialDef, PartSet, SolverModel, SupportSet, UnitLoadCase
from dentalsim.verification.builders.meshing import FloatArray, SimpleMesh, mesh_shapes
from dentalsim.verification.solution import CaseSolution, lame

Evaluate = Callable[[Sequence[CaseSolution]], dict[str, float]]


@dataclass(frozen=True)
class BuiltCase:
    models: tuple[SolverModel, ...]
    evaluate: Evaluate


Builder = Callable[[Mapping[str, Any], DetailLevel], BuiltCase]


def _number(params: Mapping[str, Any], key: str) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DentalsimError(f"the case parameter {key!r} must be a number")
    return float(value)


def _level_value(params: Mapping[str, Any], key: str, level: DetailLevel) -> Any:
    table = params.get(key)
    if not isinstance(table, Mapping) or level not in table:
        raise DentalsimError(f"the case parameter {key!r} needs a value for the {level} level")
    return table[level]


def _model(
    mesh: SimpleMesh,
    materials: Sequence[tuple[str, float, float]],
    supports: Sequence[SupportSet],
    case: UnitLoadCase,
    heading: str,
) -> SolverModel:
    """One part per material, in the order of the meshed volumes."""
    return SolverModel(
        node_ids=mesh.node_ids,
        coordinates=mesh.coordinates,
        element_ids=mesh.element_ids,
        connectivity=mesh.connectivity,
        materials=tuple(MaterialDef(name, e, nu) for name, e, nu in materials),
        parts=tuple(
            PartSet(f"PART{index + 1}", name, mesh.element_ids[mesh.element_volume == index])
            for index, (name, _, _) in enumerate(materials)
        ),
        supports=tuple(supports),
        load_cases=(case,),
        heading=heading,
    )


def _plane(axis: int, value: float, tolerance: float) -> Callable[[FloatArray], NDArray[np.bool_]]:
    return lambda x: np.asarray(np.abs(x[:, axis] - value) <= tolerance)


def _weights(patch: tuple[tuple[int, float], ...]) -> tuple[NDArray[np.int64], FloatArray]:
    return np.array([n for n, _ in patch], dtype=np.int64), np.array([w for _, w in patch], dtype=np.float64)


def _weighted_mean(values: FloatArray, weights: FloatArray) -> float:
    return float(np.sum(values * weights) / np.sum(weights))


# --- cantilever --------------------------------------------------------------------------------------------------


def cantilever(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    length, width, height = (_number(params, k) for k in ("length_mm", "width_mm", "height_mm"))
    e, nu, load = _number(params, "E_mpa"), _number(params, "nu"), _number(params, "load_n")
    mesh = mesh_shapes(
        lambda: [gmsh.model.occ.addBox(0, 0, 0, length, width, height)],
        float(_level_value(params, "mesh_size_mm", level)),
    )
    tolerance = 1e-6 * length
    root = mesh.nodes_where(_plane(0, 0.0, tolerance))
    patch = mesh.traction_weights(_plane(0, length, tolerance))
    model = _model(
        mesh,
        [("MAT1", e, nu)],
        [SupportSet("ROOT", root)],
        UnitLoadCase("TIP", patch, (0.0, 0.0, -load)),
        "Textbook: cantilever with an end load",
    )
    nodes, weights = _weights(patch)

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        uz = solutions[0].displacement(nodes)[:, 2]
        return {"tip_deflection": -_weighted_mean(uz, weights)}

    return BuiltCase((model,), evaluate)


# --- thick-walled cylinder (Lamé) --------------------------------------------------------------------------------


def _cylinder_model(params: Mapping[str, Any], size: float) -> tuple[SolverModel, NDArray[np.int64]]:
    a, b, t = (_number(params, k) for k in ("inner_radius_mm", "outer_radius_mm", "length_mm"))
    e, nu, p = _number(params, "E_mpa"), _number(params, "nu"), _number(params, "pressure_mpa")

    def build() -> list[int]:
        kernel = gmsh.model.occ
        outer = kernel.addCylinder(0, 0, 0, 0, 0, t, b, angle=math.pi / 2)
        inner = kernel.addCylinder(0, 0, 0, 0, 0, t, a, angle=math.pi / 2)
        made, _ = kernel.cut([(3, outer)], [(3, inner)])
        return [tag for dim, tag in made if dim == 3]

    mesh = mesh_shapes(build, size)
    tolerance = 1e-6 * b
    on_inner = lambda x: np.asarray(np.abs(np.hypot(x[:, 0], x[:, 1]) - a) <= tolerance)  # noqa: E731
    faces, elements, numbers = mesh.faces_on(on_inner)
    supports = [
        SupportSet("XSYM", mesh.nodes_where(_plane(0, 0.0, tolerance)), (1,)),
        SupportSet("YSYM", mesh.nodes_where(_plane(1, 0.0, tolerance)), (2,)),
        SupportSet(
            "ENDS",
            np.union1d(mesh.nodes_where(_plane(2, 0.0, tolerance)), mesh.nodes_where(_plane(2, t, tolerance))),
            (3,),
        ),
    ]
    case = UnitLoadCase(
        "PRESSURE",
        (),
        (0.0, 0.0, 0.0),
        pressures=tuple((int(el), int(f), p) for el, f in zip(elements, numbers, strict=True)),
    )
    model = _model(mesh, [("MAT1", e, nu)], supports, case, "Textbook: thick-walled cylinder under internal pressure")
    return model, np.unique(faces[:, :3])


def _polar(solution: CaseSolution) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Radius, σrr, σθθ and σzz at every integration point."""
    xyz, s = solution.points.positions, solution.stress
    theta = np.arctan2(xyz[:, 1], xyz[:, 0])
    c, n = np.cos(theta), np.sin(theta)
    radial = s[:, 0] * c * c + s[:, 1] * n * n + 2 * s[:, 3] * c * n
    hoop = s[:, 0] * n * n + s[:, 1] * c * c - 2 * s[:, 3] * c * n
    return np.hypot(xyz[:, 0], xyz[:, 1]), radial, hoop, s[:, 2]


def _inner_radial_displacement(solution: CaseSolution, corners: NDArray[np.int64]) -> float:
    xyz = solution.model.coordinates[solution.rows(corners)]
    u = solution.displacement(corners)
    radial = (u[:, 0] * xyz[:, 0] + u[:, 1] * xyz[:, 1]) / np.hypot(xyz[:, 0], xyz[:, 1])
    return float(radial.mean())


def lame_cylinder(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    model, corners = _cylinder_model(params, float(_level_value(params, "mesh_size_mm", level)))
    middle, half_band = _number(params, "probe_radius_mm"), _number(params, "probe_half_width_mm")

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        solution = solutions[0]
        r, radial, hoop, axial = _polar(solution)
        weights = solution.points.volumes
        band = np.abs(r - middle) <= half_band
        return {
            "radial_displacement_inner": _inner_radial_displacement(solution, corners),
            "hoop_stress_mid": _weighted_mean(hoop[band], weights[band]),
            "radial_stress_mid": _weighted_mean(radial[band], weights[band]),
            "axial_stress": _weighted_mean(axial, weights),
        }

    return BuiltCase((model,), evaluate)


def cylinder_convergence(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    sizes = params.get("mesh_sizes_mm")
    if not isinstance(sizes, list) or len(sizes) < 2:
        raise DentalsimError("the case parameter 'mesh_sizes_mm' must list at least two element sizes")
    reference = _number(params, "reference_radial_displacement_mm")
    built = [_cylinder_model(params, float(size)) for size in sorted(sizes, reverse=True)]

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        errors = [
            abs(_inner_radial_displacement(solution, corners) / reference - 1.0)
            for solution, (_, corners) in zip(solutions, built, strict=True)
        ]
        decreasing = all(later < earlier for earlier, later in zip(errors, errors[1:], strict=False))
        return {"finest_relative_error": errors[-1], "errors_decrease": 1.0 if decreasing else 0.0}

    return BuiltCase(tuple(model for model, _ in built), evaluate)


# --- NAFEMS LE10 -------------------------------------------------------------------------------------------------


def nafems_le10(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    """NAFEMS LE10: inner ellipse x²/2000² + y²/1000² = 1, outer x²/3250² + y²/2750² = 1, 600 mm thick (z from −300
    to 300); faces y = 0 held in y, x = 0 held in x, the outer face held in x and y, its mid-plane line (z = 0)
    held in z; 1 MPa on the top face. Point D is (2000, 0, 300)."""
    ai, bi = _number(params, "inner_semi_axes_mm_x"), _number(params, "inner_semi_axes_mm_y")
    ao, bo = _number(params, "outer_semi_axes_mm_x"), _number(params, "outer_semi_axes_mm_y")
    half = _number(params, "thickness_mm") / 2
    e, nu, p = _number(params, "E_mpa"), _number(params, "nu"), _number(params, "pressure_mpa")
    sizes = _level_value(params, "mesh", level)
    near, far, reach = (float(sizes[k]) for k in ("near_d_mm", "far_mm", "reach_mm"))
    point_d = np.array([ai, 0.0, half])

    def build() -> list[int]:
        kernel = gmsh.model.occ
        volumes = []
        for z0 in (-half, 0.0):
            outer = kernel.addDisk(0, 0, z0, ao, bo)
            inner = kernel.addDisk(0, 0, z0, ai, bi)
            ring, _ = kernel.cut([(2, outer)], [(2, inner)])
            box = kernel.addRectangle(0, 0, z0, ao + 1, bo + 1)
            quarter, _ = kernel.intersect(ring, [(2, box)])
            made = kernel.extrude(quarter, 0, 0, half)
            volumes += [tag for dim, tag in made if dim == 3]
        return volumes

    def size_field() -> None:
        gmsh.model.occ.synchronize()
        vertices = gmsh.model.getEntitiesInBoundingBox(*(point_d - 1e-3), *(point_d + 1e-3), dim=0)
        field = gmsh.model.mesh.field
        distance = field.add("Distance")
        field.setNumbers(distance, "PointsList", [tag for _, tag in vertices])
        threshold = field.add("Threshold")
        field.setNumber(threshold, "InField", distance)
        for key, value in (("SizeMin", near), ("SizeMax", far), ("DistMin", 0.0), ("DistMax", reach)):
            field.setNumber(threshold, key, value)
        field.setAsBackgroundMesh(threshold)

    mesh = mesh_shapes(build, size_field)
    tolerance = 1e-6 * ao
    on_outer = lambda x: np.asarray(np.abs((x[:, 0] / ao) ** 2 + (x[:, 1] / bo) ** 2 - 1.0) <= 1e-8)  # noqa: E731
    outer_nodes = mesh.surface_nodes(on_outer)
    mid_line = outer_nodes[np.abs(mesh.xyz(outer_nodes)[:, 2]) <= tolerance]
    _, elements, numbers = mesh.faces_on(_plane(2, half, tolerance))
    supports = [
        SupportSet("YSYM", mesh.nodes_where(_plane(1, 0.0, tolerance)), (2,)),
        SupportSet("XSYM", mesh.nodes_where(_plane(0, 0.0, tolerance)), (1,)),
        SupportSet("OUTER", outer_nodes, (1, 2)),
        SupportSet("MIDLINE", mid_line, (3,)),
    ]
    case = UnitLoadCase(
        "PRESSURE",
        (),
        (0.0, 0.0, 0.0),
        pressures=tuple((int(el), int(f), p) for el, f in zip(elements, numbers, strict=True)),
    )
    materials = [("MAT1", e, nu), ("MAT2", e, nu)]
    model = _model(mesh, materials, supports, case, "NAFEMS LE10: thick plate with an elliptic hole, pressure on top")
    node_d = int(mesh.node_ids[np.argmin(np.linalg.norm(mesh.coordinates - point_d, axis=1))])
    if np.linalg.norm(mesh.xyz(np.array([node_d]))[0] - point_d) > tolerance:
        raise DentalsimError("the LE10 mesh has no node at point D")

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        return {"sigma_yy_at_d": float(solutions[0].nodal_stress(node_d)[1])}

    return BuiltCase((model,), evaluate)


# --- constant-strain patch test ----------------------------------------------------------------------------------


def patch_test(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    side, e, nu = _number(params, "side_mm"), _number(params, "E_mpa"), _number(params, "nu")
    components = params.get("strain")
    if not isinstance(components, list) or len(components) != 6:
        raise DentalsimError("the case parameter 'strain' lists 6 tensor components: xx, yy, zz, xy, xz, yz")
    exx, eyy, ezz, exy, exz, eyz = (float(c) for c in components)
    strain = np.array([[exx, exy, exz], [exy, eyy, eyz], [exz, eyz, ezz]])
    lam, mu = lame(e, nu)
    stress = lam * np.trace(strain) * np.eye(3) + 2 * mu * strain
    pick = ([0, 1, 2, 0, 0, 1], [0, 1, 2, 1, 2, 2])
    exact_strain, exact_stress = strain[pick], stress[pick]

    mesh = mesh_shapes(
        lambda: [gmsh.model.occ.addBox(0, 0, 0, side, side, side)], float(_level_value(params, "mesh_size_mm", level))
    )
    faces, _, _ = mesh.boundary_faces()
    boundary = np.unique(faces)
    displacement = mesh.xyz(boundary) @ strain.T
    prescribed = tuple(
        (int(node), dof + 1, float(displacement[row, dof])) for row, node in enumerate(boundary) for dof in range(3)
    )
    case = UnitLoadCase("PATCH", (), (0.0, 0.0, 0.0), prescribed=prescribed)
    model = _model(mesh, [("MAT1", e, nu)], [], case, "Constant-strain patch test")

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        solution = solutions[0]
        strain_error = np.abs(solution.strain - exact_strain).max() / np.abs(exact_strain).max()
        stress_error = np.abs(solution.stress - exact_stress).max() / np.abs(exact_stress).max()
        return {"max_strain_error": float(strain_error), "max_stress_error": float(stress_error)}

    return BuiltCase((model,), evaluate)


# --- two bonded bars ---------------------------------------------------------------------------------------------


def bonded_bars(params: Mapping[str, Any], level: DetailLevel) -> BuiltCase:
    l1, l2, side = _number(params, "length_1_mm"), _number(params, "length_2_mm"), _number(params, "side_mm")
    e1, e2, force = _number(params, "E1_mpa"), _number(params, "E2_mpa"), _number(params, "load_n")
    nu1, nu2 = _number(params, "nu1"), _number(params, "nu2")
    if abs(nu1 / e1 - nu2 / e2) > 1e-12 * abs(nu1 / e1):
        raise DentalsimError("the two bars need the same ν/E, so that they contract alike and the field is uniaxial")
    size = float(_level_value(params, "mesh_size_mm", level))

    def build() -> list[int]:
        kernel = gmsh.model.occ
        return [kernel.addBox(0, 0, 0, l1, side, side), kernel.addBox(l1, 0, 0, l2, side, side)]

    mesh = mesh_shapes(build, size)
    tolerance = 1e-6 * (l1 + l2)
    patch = mesh.traction_weights(_plane(0, l1 + l2, tolerance))
    supports = [
        SupportSet("XFIX", mesh.nodes_where(_plane(0, 0.0, tolerance)), (1,)),
        SupportSet("YFIX", mesh.nodes_where(_plane(1, 0.0, tolerance)), (2,)),
        SupportSet("ZFIX", mesh.nodes_where(_plane(2, 0.0, tolerance)), (3,)),
    ]
    model = _model(
        mesh,
        [("MAT1", e1, nu1), ("MAT2", e2, nu2)],
        supports,
        UnitLoadCase("PULL", patch, (force, 0.0, 0.0)),
        "Two bonded bars in series under an axial load",
    )
    nodes, weights = _weights(patch)
    first = np.repeat(mesh.element_volume == 0, 4)

    def evaluate(solutions: Sequence[CaseSolution]) -> dict[str, float]:
        solution = solutions[0]
        ux = solution.displacement(nodes)[:, 0]
        sxx, volumes = solution.stress[:, 0], solution.points.volumes
        return {
            "end_displacement": _weighted_mean(ux, weights),
            "stress_bar_1": _weighted_mean(sxx[first], volumes[first]),
            "stress_bar_2": _weighted_mean(sxx[~first], volumes[~first]),
        }

    return BuiltCase((model,), evaluate)


BUILDERS: dict[str, Builder] = {
    "cantilever": cantilever,
    "lame_cylinder": lame_cylinder,
    "cylinder_convergence": cylinder_convergence,
    "nafems_le10": nafems_le10,
    "patch_test": patch_test,
    "bonded_bars": bonded_bars,
}
