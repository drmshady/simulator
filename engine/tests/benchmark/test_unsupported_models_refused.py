"""Models the solver would "solve" without complaint are refused, never reported (task T075; FR-023; research R3).

- A cube held only along one edge: CalculiX returns huge displacements without an error; the convergence checks
  catch it (force balance, energy and residual).
- An assembly with a part that touches nothing: it carries no load, so no check after the solve would notice; the
  connectivity check fails the run before the solve, naming the part.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from dentalsim.estimate.calibrate import BOX_MM, box_model
from dentalsim.mesh.load_patch import boundary_faces, patch_load
from dentalsim.model.scenario import Placement
from dentalsim.pipeline import PipelineFailed, PipelineRequest, run_pipeline
from dentalsim.post.convergence import check_unit_case
from dentalsim.post.integration import integration_points, volumes_for
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.solvers.calculix.inp_writer import SupportSet, UnitLoadCase
from dentalsim.solvers.calculix.solve import solve_model
from dentalsim.store.cas import ContentStore
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import SnapshotLibrary


def test_a_cube_held_along_one_edge_fails_the_convergence_checks(tmp_path: Path) -> None:
    model = box_model(2.0)
    xyz = model.coordinates
    edge = model.node_ids[(np.abs(xyz[:, 0]) < 1e-9) & (np.abs(xyz[:, 2]) < 1e-9)]
    top = np.array([BOX_MM[0] / 2, BOX_MM[1] / 2, BOX_MM[2]])
    patch = patch_load(boundary_faces(model.connectivity), model.node_ids, xyz, top, radius_mm=2.0)
    hinged = replace(
        model,
        supports=(SupportSet("EDGE", edge),),
        load_cases=(UnitLoadCase("PUSH", patch.as_patch(), (0.0, 0.0, -1.0)),),
    )
    output = solve_model(hinged, tmp_path, threads=2)
    step = output.steps[0]
    points = integration_points(model.node_ids, xyz, model.element_ids, model.connectivity)
    rows = {int(n): i for i, n in enumerate(model.node_ids)}
    patch_rows = np.array([rows[int(n)] for n in patch.node_ids])
    reactions = step.reactions["EDGE"]
    checks = check_unit_case(
        applied_total=(0.0, 0.0, -1.0),
        reaction_total=step.reaction_totals["EDGE"],
        load_positions=xyz[patch_rows],
        load_forces=patch.nodal_forces((0.0, 0.0, -1.0)),
        reaction_positions=xyz[[rows[int(n)] for n in reactions.node_ids]],
        reaction_forces=reactions.values,
        load_displacements=step.displacements[patch_rows],
        stress=step.stress,
        strain=step.strain,
        ip_volumes=volumes_for(points, step.ip_element_ids, step.ip_numbers),
        residual=step.residual,
    )
    failed = {c.name for c in checks if not c.ok}
    assert "energy" in failed or "force balance" in failed, [c.model_dump() for c in checks]


def test_a_part_that_touches_nothing_fails_before_the_solve(tmp_path: Path) -> None:
    library = SnapshotLibrary()
    opened = open_template(builtin_template("single_crown_implant"), library)
    blocks = [
        b.model_copy(update={"placement": Placement(position_mm=(14.0, 0.0, 0.0))}) if b.instance_id == "crown-1" else b
        for b in opened.scenario.blocks
    ]
    scenario = opened.scenario.model_copy(update={"blocks": blocks})
    frost = [library.resolve(k) for k in ("frost.disuse_upper", "frost.adapted_upper", "frost.mild_overload_upper")]
    request = PipelineRequest(scenario, "preview", "preview", [r.version_ref() for r in opened.library], frost)

    class Ready:
        def get(self, capability_id: str) -> object:
            from dentalsim.persistence import CapabilityStatus
            from dentalsim.physics.registry import read_case_headers, required_cases_in

            cases = list(required_cases_in(capability_id, read_case_headers()))
            return CapabilityStatus(capability_id=capability_id, status="ready", report_id="R", cases=cases)

        def set(self, status: object) -> None: ...

        def list_all(self) -> list[object]:
            return []

    store = ContentStore(tmp_path / "store")
    with pytest.raises(
        PipelineFailed, match=r"Crown 'crown-1' \(body\) does not touch Abutment 'abutment-1'"
    ) as failure:
        run_pipeline(
            request, capabilities=Ready(), store=store, work_root=tmp_path / "work",  # type: ignore[arg-type]
            solver_build_id=solver_build().build_id,
        )  # fmt: skip
    assert failure.value.stage == "mesh"
    assert store.keys() == []  # nothing partial is stored (FR-022)
