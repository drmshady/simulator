"""On a curved 10-node mesh solved by CalculiX: the computed integration points match the solver's printed ones,
their volumes (det J × Gauss weight) add up to each element's printed volume, and the energy check passes."""

from pathlib import Path

import gmsh
import numpy as np
import pytest

from dentalsim.conventions.frame import force_direction
from dentalsim.mesh.load_patch import boundary_faces, patch_load
from dentalsim.post.convergence import energy_balance_error
from dentalsim.post.integration import check_positions, integration_points, volumes_for
from dentalsim.solvers.calculix.dat_reader import read_dat
from dentalsim.solvers.calculix.frd_reader import read_frd
from dentalsim.solvers.calculix.inp_writer import (
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    gmsh_to_ccx_tet10,
    write_inp,
)
from dentalsim.solvers.calculix.runner import run_ccx

RADIUS, HEIGHT = 3.0, 10.0


def _curved_cylinder(size: float = 1.5) -> SolverModel:
    gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        for name, value in (
            ("General.NumThreads", 1),
            ("Mesh.RandomSeed", 1),
            ("Mesh.MeshSizeMin", size),
            ("Mesh.MeshSizeMax", size),
            ("Mesh.ElementOrder", 2),
            ("Mesh.SecondOrderLinear", 0),  # edge nodes on the curved surface
        ):
            gmsh.option.setNumber(name, value)
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, HEIGHT, RADIUS)
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.generate(3)
        tags, flat, _ = gmsh.model.mesh.getNodes()
        _, nodes = gmsh.model.mesh.getElementsByType(11)
    finally:
        gmsh.finalize()
    ids = np.asarray(tags, dtype=np.int64)
    order = np.argsort(ids)
    ids, coords = ids[order], np.asarray(flat).reshape(-1, 3)[order]
    conn = gmsh_to_ccx_tet10(np.asarray(nodes, dtype=np.int64).reshape(-1, 10))
    elements = np.arange(1, conn.shape[0] + 1)
    patch = patch_load(boundary_faces(conn), ids, coords, [0.0, 0.0, HEIGHT], radius_mm=2.0)
    force = tuple(100.0 * force_direction(45.0, 90.0))
    return SolverModel(
        node_ids=ids,
        coordinates=coords,
        element_ids=elements,
        connectivity=conn,
        materials=(MaterialDef("BONE", 13700.0, 0.3),),
        parts=(PartSet("CYLINDER", "BONE", elements),),
        supports=(SupportSet("BASE", ids[np.abs(coords[:, 2]) < 1e-9]),),
        load_cases=(UnitLoadCase("F45", patch.as_patch(), force),),
    )


def test_integration_points_and_volumes_match_the_solver(tmp_path: Path) -> None:
    model = _curved_cylinder()
    write_inp(model, tmp_path / "job.inp")
    result = run_ccx(tmp_path, "job", threads=2)
    step = read_dat(result.dat_path)[0]
    points = integration_points(model.node_ids, model.coordinates, model.element_ids, model.connectivity)

    assert check_positions(points, step.element_ids, step.ip_numbers, step.ip_coordinates) < 1e-4
    volumes = volumes_for(points, step.element_ids, step.ip_numbers)
    per_element = np.bincount(np.searchsorted(step.volume_element_ids, step.element_ids), weights=volumes)
    assert per_element == pytest.approx(step.volumes, rel=1e-5)
    assert np.ptp(volumes.reshape(-1, 4) / volumes.reshape(-1, 4).mean(axis=1, keepdims=True)) > 1e-3  # curved

    case = model.load_cases[0]
    frd = read_frd(result.frd_path).steps[0]
    u = dict(zip(frd.node_ids.tolist(), frd.displacements, strict=True))
    loaded = [n for n, _ in case.patch]
    forces = np.outer([w for _, w in case.patch], case.force)
    displacements = np.array([u[n] for n in loaded])
    assert energy_balance_error(forces, displacements, step.stress, step.strain, volumes) < 1e-4
