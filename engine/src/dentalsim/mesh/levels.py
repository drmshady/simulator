"""Detail levels: mesh density only (conventions "Detail levels"; research R2, R7).

The geometry is the same at every level; a level sets only gmsh size fields:

- the **bulk size** (`max_size_mm`) everywhere;
- near each block's **refinement hint** (implant threads, the retention screw, later connectors), a Distance field
  from the hinted part's surfaces and a Threshold field from `feature_mm × hint_factor` at the surface to the bulk
  size at the hint's `extent_mm`;
- around each **loaded contact point**, a Ball field of `patch_size_mm` reaching `PATCH_REACH_MM` beyond the load
  patch radius, so that each patch has several faces;
- **curvature**: `curvature_elements` elements per full turn of a curved surface (0 leaves it to the fields).

Measured on the single-crown template (threaded implant): preview about 46 k DOF, standard about 200 k, fine about
390 k. The preview is twice research R7's 15–25 k because the geometry keeps its threads and thin walls (the 0.4 mm
implant wall round the conical connection) at every level.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import gmsh

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.geometry.assemble import AssembledModel
from dentalsim.geometry.transform import Point
from dentalsim.model.scenario import DETAIL_LEVELS, DetailLevel

PATCH_REACH_MM = 0.5
"""How far beyond the load patch radius the patch size holds."""
PATCH_BLEND_MM = 2.5
"""Over how much farther the size grows from the patch size to the bulk size."""
DISTANCE_SAMPLING = 20


@dataclass(frozen=True)
class MeshLevel:
    name: DetailLevel
    max_size_mm: float
    hint_factor: float
    patch_size_mm: float
    curvature_elements: int

    def hint_size(self, feature_mm: float) -> float:
        return min(self.max_size_mm, feature_mm * self.hint_factor)


LEVELS: dict[DetailLevel, MeshLevel] = {
    "preview": MeshLevel("preview", max_size_mm=3.0, hint_factor=1.0, patch_size_mm=0.8, curvature_elements=0),
    "standard": MeshLevel("standard", max_size_mm=2.0, hint_factor=0.5, patch_size_mm=0.4, curvature_elements=12),
    "fine": MeshLevel("fine", max_size_mm=1.4, hint_factor=0.35, patch_size_mm=0.28, curvature_elements=16),
    "extra_fine": MeshLevel("extra_fine", max_size_mm=1.0, hint_factor=0.25, patch_size_mm=0.2, curvature_elements=24),
}
assert tuple(LEVELS) == DETAIL_LEVELS


def mesh_level(name: DetailLevel) -> MeshLevel:
    return LEVELS[name]


def set_mesh_options(level: MeshLevel) -> None:
    """Global gmsh options: single-threaded with a fixed seed (research R2), sizes from the fields only."""
    for option, value in (
        ("General.NumThreads", 1),
        ("Mesh.MaxNumThreads1D", 1),
        ("Mesh.MaxNumThreads2D", 1),
        ("Mesh.MaxNumThreads3D", 1),
        ("Mesh.RandomSeed", 1),
        ("Mesh.Algorithm", 6),
        ("Mesh.Algorithm3D", 1),
        ("Mesh.Optimize", 1),
        ("Mesh.OptimizeNetgen", 1),
        ("Mesh.MeshSizeExtendFromBoundary", 0),
        ("Mesh.MeshSizeFromPoints", 0),
        ("Mesh.MeshSizeFromCurvature", level.curvature_elements),
        ("Mesh.MeshSizeMax", level.max_size_mm),
        ("Mesh.MeshSizeMin", 0.0),
        ("Mesh.ElementOrder", 1),
        ("Mesh.SecondOrderLinear", 0),
        ("Mesh.HighOrderOptimize", 0),
    ):
        gmsh.option.setNumber(option, value)


def add_size_fields(
    model: AssembledModel,
    level: MeshLevel,
    load_points: Sequence[Point] = (),
    conventions: Conventions | None = None,
) -> int:
    """Add the level's size fields to the current gmsh model and make their minimum the background field."""
    patch_radius = (conventions or get_conventions()).load_patch.radius_mm
    field = gmsh.model.mesh.field
    fields = []
    for hinted in model.hints:
        distance = field.add("Distance")
        field.setNumbers(distance, "SurfacesList", list(hinted.surfaces))
        field.setNumber(distance, "Sampling", DISTANCE_SAMPLING)
        threshold = field.add("Threshold")
        field.setNumber(threshold, "InField", distance)
        field.setNumber(threshold, "SizeMin", level.hint_size(hinted.hint.feature_mm))
        field.setNumber(threshold, "SizeMax", level.max_size_mm)
        field.setNumber(threshold, "DistMin", 0.0)
        field.setNumber(threshold, "DistMax", hinted.hint.extent_mm)
        fields.append(threshold)
    for x, y, z in load_points:
        ball = field.add("Ball")
        field.setNumber(ball, "Radius", patch_radius + PATCH_REACH_MM)
        field.setNumber(ball, "Thickness", PATCH_BLEND_MM)
        field.setNumber(ball, "VIn", level.patch_size_mm)
        field.setNumber(ball, "VOut", level.max_size_mm)
        field.setNumber(ball, "XCenter", x)
        field.setNumber(ball, "YCenter", y)
        field.setNumber(ball, "ZCenter", z)
        fields.append(ball)
    bulk = field.add("MathEval")
    field.setString(bulk, "F", repr(level.max_size_mm))
    fields.append(bulk)
    smallest = field.add("Min")
    field.setNumbers(smallest, "FieldsList", fields)
    field.setAsBackgroundMesh(smallest)
    return int(smallest)
