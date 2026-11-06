"""Solver smoke tests (T016): needs the solver (`DENTALSIM_CCX`), so they are skipped without it.

- A C3D10 cube under uniaxial tension on roller supports (three symmetry planes), loaded with consistent nodal
  forces: for a 6-node triangle under uniform traction the corner nodes get 0 and each midside node a third of the
  face force. The linear displacement field is exact for quadratic tetrahedra, so the top face must move by sigma·L/E
  within 1e-6 (read from the .dat, which prints 7 significant digits; the .frd prints 6).
- A cube in simple shear by prescribed boundary displacements confirms that the printed shear strain is the tensor
  component (gamma / 2), which the post-processing relies on (dat_reader docstring).
"""

import itertools
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from dentalsim.solvers.calculix.build import file_sha256, make_build_id, solver_build
from dentalsim.solvers.calculix.dat_reader import read_dat
from dentalsim.solvers.calculix.frd_reader import read_frd
from dentalsim.solvers.calculix.inp_writer import (
    CCX_TET10_EDGES,
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    write_inp,
)
from dentalsim.solvers.calculix.runner import run_ccx

E = 210000.0  # MPa
NU = 0.3
LENGTH = 2.0  # mm
SIGMA = 100.0  # MPa
CELLS = 2
FACES = ((0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3))  # corner triples of a tetrahedron


class Cube:
    """A structured cube of 10-node tetrahedra: each cell is split into 6 (Kuhn), which is conforming."""

    def __init__(self, cells: int, length: float) -> None:
        h = length / cells
        self.length = length
        index: dict[tuple[int, ...], int] = {}
        points: list[NDArray[np.float64]] = []
        self.mid: dict[tuple[int, int], int] = {}

        def node(p: NDArray[np.float64]) -> int:
            key = tuple(int(round(2 * c / h)) for c in p)
            if key not in index:
                index[key] = len(points) + 1
                points.append(p)
            return index[key]

        connectivity = []
        for cell in itertools.product(range(cells), repeat=3):
            origin = np.array(cell, dtype=np.float64) * h
            for order in itertools.permutations(range(3)):
                corners = [origin.copy()]
                for axis in order:
                    step = corners[-1].copy()
                    step[axis] += h
                    corners.append(step)
                a, b, c, d = corners
                if np.dot(np.cross(b - a, c - a), d - a) < 0:
                    b, c = c, b
                ids = [node(p) for p in (a, b, c, d)]
                for i, j in CCX_TET10_EDGES:
                    mid = node((points[ids[i] - 1] + points[ids[j] - 1]) / 2)
                    self.mid[tuple(sorted((ids[i], ids[j])))] = mid  # type: ignore[index]
                    ids.append(mid)
                connectivity.append(ids)
        self.node_ids = np.arange(1, len(points) + 1)
        self.coordinates = np.array(points)
        self.connectivity = np.array(connectivity)
        self.element_ids = np.arange(1, len(connectivity) + 1)

    def nodes_where(self, axis: int, value: float) -> NDArray[np.int64]:
        return self.node_ids[np.isclose(self.coordinates[:, axis], value)]

    def top_face_forces(self, traction: float) -> dict[int, float]:
        """Consistent nodal forces of a uniform traction on the face z = L: a third of each triangle's force on each
        of its midside nodes, nothing on its corners."""
        forces: dict[int, float] = {}
        for element in self.connectivity:
            for face in FACES:
                corners = [int(element[k]) for k in face]
                xyz = self.coordinates[[c - 1 for c in corners]]
                if not np.allclose(xyz[:, 2], self.length):
                    continue
                area = 0.5 * np.linalg.norm(np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0]))
                for p, q in ((0, 1), (1, 2), (0, 2)):
                    mid = self.mid[tuple(sorted((corners[p], corners[q])))]  # type: ignore[index]
                    forces[mid] = forces.get(mid, 0.0) + traction * area / 3
        return forces


class PrintNodes:
    """Extension: node sets and `*NODE PRINT` U for them, so the .dat carries 7-digit displacements."""

    def __init__(self, sets: dict[str, NDArray[np.int64]]) -> None:
        self.sets = sets

    def model_cards(self, model: SolverModel) -> Iterable[str]:
        for name, ids in self.sets.items():
            yield f"*NSET, NSET={name}"
            values = sorted(ids.tolist())
            for start in range(0, len(values), 16):
                yield ", ".join(str(v) for v in values[start : start + 16])

    def step_cards(self, model: SolverModel, step: int, case: UnitLoadCase) -> Iterable[str]:
        for name in self.sets:
            yield f"*NODE PRINT, NSET={name}"
            yield "U"


def _model(
    cube: Cube, supports: tuple[SupportSet, ...], case: UnitLoadCase, cards: tuple[str, ...] = ()
) -> SolverModel:
    return SolverModel(
        node_ids=cube.node_ids,
        coordinates=cube.coordinates,
        element_ids=cube.element_ids,
        connectivity=cube.connectivity,
        materials=(MaterialDef("STEEL", E, NU),),
        parts=(PartSet("CUBE", "STEEL", cube.element_ids),),
        supports=supports,
        load_cases=(case,),
        extra_model_cards=cards,
    )


def test_solver_check_identifies_the_build() -> None:
    build = solver_build()
    assert build.sha256 == file_sha256(build.path)
    assert build.build_id == make_build_id(build.version, build.equation_solver, build.sha256)
    assert build.version != "unknown"


def test_uniaxial_tension_cube(tmp_path: Path) -> None:
    build = solver_build()
    cube = Cube(CELLS, LENGTH)
    forces = cube.top_face_forces(SIGMA)
    total = sum(forces.values())
    assert total == pytest.approx(SIGMA * LENGTH**2, rel=1e-12)
    case = UnitLoadCase("tension", tuple((n, f / total) for n, f in sorted(forces.items())), (0.0, 0.0, total))
    supports = (
        SupportSet("X0", cube.nodes_where(0, 0.0), dofs=(1,)),
        SupportSet("Y0", cube.nodes_where(1, 0.0), dofs=(2,)),
        SupportSet("Z0", cube.nodes_where(2, 0.0), dofs=(3,)),
    )
    top, side = cube.nodes_where(2, LENGTH), cube.nodes_where(0, LENGTH)
    write_inp(
        _model(cube, supports, case),
        tmp_path / "tension.inp",
        solver=build.equation_solver,
        extensions=[PrintNodes({"TOP": top, "XL": side})],
    )
    result = run_ccx(tmp_path, "tension", ccx=build.path, threads=1)

    strain = SIGMA / E
    (step,) = read_dat(result.dat_path)
    np.testing.assert_allclose(step.displacements["TOP"].values[:, 2], strain * LENGTH, rtol=1e-6)
    np.testing.assert_allclose(step.displacements["XL"].values[:, 0], -NU * strain * LENGTH, rtol=1e-6)

    (frd_step,) = read_frd(result.frd_path).steps
    on_top = np.isin(frd_step.node_ids, top)
    np.testing.assert_allclose(frd_step.displacements[on_top, 2], strain * LENGTH, rtol=1e-5)

    assert step.stress.shape == (4 * cube.element_ids.size, 6)
    np.testing.assert_allclose(step.stress[:, 2], SIGMA, rtol=1e-6)
    np.testing.assert_allclose(step.stress[:, [0, 1, 3, 4, 5]], 0.0, atol=1e-6 * SIGMA)
    np.testing.assert_allclose(step.strain[:, 2], strain, rtol=1e-6)
    np.testing.assert_allclose(step.strain[:, :2], -NU * strain, rtol=1e-6)
    assert step.volumes.sum() == pytest.approx(LENGTH**3, rel=1e-6)
    assert step.ip_volumes().sum() == pytest.approx(LENGTH**3, rel=1e-6)

    np.testing.assert_allclose(step.reaction_totals["Z0"], [0.0, 0.0, -total], rtol=1e-6, atol=1e-6 * total)
    np.testing.assert_allclose(step.reaction_totals["X0"], 0.0, atol=1e-6 * total)
    np.testing.assert_allclose(step.reaction_totals["Y0"], 0.0, atol=1e-6 * total)


def test_printed_shear_strain_is_the_tensor_component(tmp_path: Path) -> None:
    build = solver_build()
    cube = Cube(1, LENGTH)
    gamma = 1e-3
    surface = np.isclose(cube.coordinates, 0.0).any(axis=1) | np.isclose(cube.coordinates, LENGTH).any(axis=1)
    cards = ["*BOUNDARY"]
    for node, (_, y, _) in zip(cube.node_ids[surface].tolist(), cube.coordinates[surface].tolist(), strict=True):
        cards += [f"{node}, 1, 1, {gamma * y!r}", f"{node}, 2, 3"]
    model = _model(cube, (), UnitLoadCase("shear", (), (0.0, 0.0, 0.0)), tuple(cards))
    write_inp(model, tmp_path / "shear.inp", solver=build.equation_solver)
    result = run_ccx(tmp_path, "shear", ccx=build.path, threads=1)

    (step,) = read_dat(result.dat_path)
    np.testing.assert_allclose(step.strain[:, 3], gamma / 2, rtol=1e-6)  # exy = gamma / 2
    np.testing.assert_allclose(step.strain[:, [0, 1, 2, 4, 5]], 0.0, atol=1e-6 * gamma)
    shear_modulus = E / (2 * (1 + NU))
    np.testing.assert_allclose(step.stress[:, 3], shear_modulus * gamma, rtol=1e-6)
