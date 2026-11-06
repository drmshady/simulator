"""Meshing the joined single-crown model (task T070): validity, determinism, supports, surfaces, connectivity."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from dentalsim.assembly.placement import place
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import assemble
from dentalsim.mesh.build import MeshedModel, MeshFailed, build_mesh
from dentalsim.mesh.levels import LEVELS, mesh_level
from dentalsim.mesh.load_patch import patch_load
from dentalsim.mesh.predict import predict_dof
from dentalsim.model.scenario import Placement, Scenario
from dentalsim.post.integration import integration_points
from dentalsim.solvers.calculix.inp_writer import (
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    validate_model,
)
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import SnapshotLibrary

POINTS = ("central_fossa", "buccal_cusp")


def scenario(**crown: Any) -> Scenario:
    opened = open_template(builtin_template("single_crown_implant"), SnapshotLibrary()).scenario
    if not crown:
        return opened
    blocks = [b.model_copy(update=crown) if b.instance_id == "crown-1" else b for b in opened.blocks]
    return opened.model_copy(update={"blocks": blocks})


def mesh_of(source: Scenario, level: str = "preview") -> tuple[MeshedModel, dict[str, float], int]:
    with occ_tools.session("mesh-test"):
        model = assemble(place(source))
        points = [model.occlusal_point("crown-1", name).position_mm for name in POINTS]
        volumes = {p.key: occ_tools.volume_mm3(p.volumes) for p in model.parts}
        predicted = predict_dof(model, mesh_level(level), points)  # type: ignore[arg-type]
        return build_mesh(model, mesh_level(level), load_points=points), volumes, predicted  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def preview() -> tuple[MeshedModel, dict[str, float], int]:
    return mesh_of(scenario())


def test_every_element_is_a_valid_c3d10(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, _, _ = preview
    assert mesh.connectivity.shape == (mesh.element_ids.size, 10)
    assert mesh.min_quality >= 0.01
    points = integration_points(mesh.node_ids, mesh.coordinates, mesh.element_ids, mesh.connectivity)
    assert np.all(points.volumes > 0)
    model = SolverModel(
        node_ids=mesh.node_ids,
        coordinates=mesh.coordinates,
        element_ids=mesh.element_ids,
        connectivity=mesh.connectivity,
        materials=tuple(MaterialDef(p.set_name, p.e_mpa, p.nu) for p in mesh.parts),
        parts=tuple(PartSet(p.set_name + "_", p.set_name, p.element_ids) for p in mesh.parts),
        supports=tuple(SupportSet(s.set_name, s.node_ids) for s in mesh.supports),
        load_cases=(UnitLoadCase("x", ((int(mesh.node_ids[0]), 1.0),), (1.0, 0.0, 0.0)),),
    )
    validate_model(model)


def test_the_mesh_keeps_the_volume_of_every_part(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, volumes, _ = preview
    points = integration_points(mesh.node_ids, mesh.coordinates, mesh.element_ids, mesh.connectivity)
    for index, part in enumerate(mesh.parts):
        meshed = float(points.volumes[np.repeat(mesh.element_part == index, 4)].sum())
        # At the coarse preview level straight-edged elements narrow small holes (the 1.3 mm screw channel).
        assert meshed == pytest.approx(volumes[part.key], rel=0.05), part.key
    assert [p.key for p in mesh.parts][:2] == ["jaw.cortical", "jaw.cancellous"]
    assert {p.component for p in mesh.parts} == {"bone", "implant", "abutment", "screw", "crown"}


def test_meshing_is_reproducible(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    again, _, _ = mesh_of(scenario())
    mesh, _, _ = preview
    assert np.array_equal(again.connectivity, mesh.connectivity)
    assert np.array_equal(again.coordinates, mesh.coordinates)


def test_the_supports_are_the_two_cut_faces(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, _, _ = preview
    assert [s.name for s in mesh.supports] == ["mesial_cut", "distal_cut"]
    for support in mesh.supports:
        xyz = mesh.coordinates[support.node_ids - 1]
        centred = xyz - xyz.mean(axis=0)
        flatness = np.linalg.svd(centred, compute_uv=False)[-1] / np.sqrt(len(xyz))
        assert flatness < 1e-6  # every node on one plane
        assert support.set_name.startswith("S_jaw")
    assert not np.intersect1d(mesh.supports[0].node_ids, mesh.supports[1].node_ids).size


def test_the_outer_surface_is_closed_and_faces_outward(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, volumes, _ = preview
    xyz = mesh.coordinates[mesh.outer_faces[:, :3] - 1]
    normals = 0.5 * np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    assert np.linalg.norm(normals.sum(axis=0)) < 1e-6 * np.abs(normals).sum()
    enclosed = float(np.einsum("ij,ij->", xyz.mean(axis=1), normals) / 3.0)  # divergence theorem
    assert enclosed == pytest.approx(sum(volumes.values()), rel=0.02)
    crown = mesh.part_index("crown-1.body")
    assert np.all(mesh.outer_face_part[np.isin(mesh.outer_face_part, [crown])] == crown)
    assert len(mesh.outer_faces_of("implant-1")) == 0  # wholly embedded
    assert len(mesh.part_faces(mesh.part_index("implant-1.body"))) > 0


def test_a_load_patch_fits_on_the_crown(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, _, _ = preview
    with occ_tools.session("points"):
        model = assemble(place(scenario()))
        cusp = model.occlusal_point("crown-1", "buccal_cusp").position_mm
    patch = patch_load(mesh.outer_faces_of("crown-1"), mesh.node_ids, mesh.coordinates, cusp)
    assert len(patch.faces) >= 4
    assert patch.weights.sum() == pytest.approx(1.0)
    assert 1.5 < patch.area_mm2 < 6.0  # about π · 1 mm², on a curved cusp


def test_the_dof_prediction_is_close(preview: tuple[MeshedModel, dict[str, float], int]) -> None:
    mesh, _, predicted = preview
    assert predicted == pytest.approx(mesh.dof, rel=0.25)
    assert [level.max_size_mm for level in LEVELS.values()] == sorted(
        (level.max_size_mm for level in LEVELS.values()), reverse=True
    )


def test_a_part_that_touches_nothing_fails_before_the_solve() -> None:
    floating = scenario(placement=Placement(position_mm=(14.0, 0.0, 0.0)))
    with pytest.raises(MeshFailed, match=r"Crown 'crown-1' \(body\) does not touch Abutment 'abutment-1'"):
        mesh_of(floating)
