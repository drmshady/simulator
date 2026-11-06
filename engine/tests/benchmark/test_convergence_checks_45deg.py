"""A 45° patch load on a fixed cube, solved by CalculiX: the force, moment and energy checks pass (FR-023)."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from dentalsim.conventions.frame import force_direction
from dentalsim.estimate.calibrate import BOX_MM, box_model
from dentalsim.mesh.load_patch import boundary_faces, patch_load
from dentalsim.post.convergence import check_unit_case
from dentalsim.post.integration import integration_points, volumes_for
from dentalsim.solvers.calculix.dat_reader import read_dat
from dentalsim.solvers.calculix.frd_reader import read_frd
from dentalsim.solvers.calculix.inp_writer import UnitLoadCase, write_inp
from dentalsim.solvers.calculix.runner import run_ccx


def test_45_degree_patch_load_passes_every_convergence_check(tmp_path: Path) -> None:
    model = box_model(1.6)
    top_centre = np.array([BOX_MM[0] / 2, BOX_MM[1] / 2, BOX_MM[2]])
    patch = patch_load(boundary_faces(model.connectivity), model.node_ids, model.coordinates, top_centre, radius_mm=2.0)
    force = 100.0 * force_direction(45.0, 90.0)
    model = replace(model, load_cases=(UnitLoadCase("F45", patch.as_patch(), tuple(force)),))
    write_inp(model, tmp_path / "job.inp")
    result = run_ccx(tmp_path, "job", threads=2)

    step = read_dat(result.dat_path)[0]
    points = integration_points(model.node_ids, model.coordinates, model.element_ids, model.connectivity)
    displacement = read_frd(result.frd_path).steps[0]
    xyz = dict(zip(model.node_ids.tolist(), model.coordinates, strict=True))
    u = dict(zip(displacement.node_ids.tolist(), displacement.displacements, strict=True))
    reactions = step.reactions["BOTTOM"]
    checks = check_unit_case(
        applied_total=force,
        reaction_total=step.reaction_totals["BOTTOM"],
        load_positions=np.array([xyz[int(n)] for n in patch.node_ids]),
        load_forces=patch.nodal_forces(force),
        reaction_positions=np.array([xyz[int(n)] for n in reactions.node_ids]),
        reaction_forces=reactions.values,
        load_displacements=np.array([u[int(n)] for n in patch.node_ids]),
        stress=step.stress,
        strain=step.strain,
        ip_volumes=volumes_for(points, step.element_ids, step.ip_numbers),
        residual=result.relative_residual,
    )
    assert [c.name for c in checks] == ["force balance", "moment balance", "energy", "solver residual"]
    failed = [c.reason() for c in checks if not c.ok]
    assert not failed, failed
