"""Predicting the degrees of freedom of a mesh from its size fields, before meshing (research R8; FR-009).

The number of elements of a tetrahedral mesh grows as the integral of 1/h³ over the volume, h being the local
element size. The size fields of a level (`dentalsim.mesh.levels`) give h:

- the bulk size everywhere: V / h_max³;
- near each refinement hint, the threshold rises linearly from the hint size at the surface to the bulk size at
  `extent_mm`; the band is counted on both sides of the hinted surfaces (an implant's surface has bone outside
  and implant inside): 2·A·∫₀ᴰ (1/h(d)³ − 1/h_max³) dd;
- around each loaded point, half a ball (the point is on the surface) of the patch size, blending to the bulk size.

DOF = `DOF_SCALE` × S^`DOF_EXPONENT` + `SURFACE_DOF` × A / h_max², S being that integral and A the surface area of
every part (a face between two parts counted from both sides). The surface term counts the elements every part's
surface mesh forces at the bulk size, which the fields alone miss: it grows with the number of parts, so a full arch
of 37 parts needs it most. Fitted on 2026-11-07 to meshes of the five built-in templates at preview and standard,
and of the single crown and the full arch at fine (66 k to 1.66 M DOF): within −13% and +26% of each. Fitted on the
single crown alone, the earlier law (653 × S^0.705) under-predicted the full arch at fine by 37% and let a run start
that did not fit in memory. The estimate models refit time and memory from real runs (`dentalsim.estimate`); this
only needs to be right to a few tens of percent, and should be refitted when the blocks or templates change much.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import gmsh
import numpy as np

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import AssembledModel
from dentalsim.geometry.transform import Point
from dentalsim.mesh.levels import PATCH_BLEND_MM, PATCH_REACH_MM, MeshLevel

DOF_SCALE = 113.3
DOF_EXPONENT = 0.857
SURFACE_DOF = 74.9
"""DOF = DOF_SCALE × (∫ 1/h³ dV)^DOF_EXPONENT + SURFACE_DOF × A / h_max², fitted on the built-in templates."""
_STEPS = 64


def _band(area: float, h_min: float, h_max: float, depth: float) -> float:
    """2·A·∫₀ᴰ (1/h(d)³ − 1/h_max³) dd for h rising linearly from h_min to h_max over D."""
    if h_min >= h_max or depth <= 0:
        return 0.0
    d = (np.arange(_STEPS) + 0.5) / _STEPS * depth
    h = h_min + (h_max - h_min) * d / depth
    return float(2.0 * area * np.sum(1.0 / h**3 - 1.0 / h_max**3) * depth / _STEPS)


def _ball(radius: float, blend: float, h_in: float, h_max: float) -> float:
    """Half of ∫ (1/h³ − 1/h_max³) dV over a ball of `radius` at h_in, blending linearly to h_max over `blend`."""
    if h_in >= h_max:
        return 0.0
    inner = (2.0 / 3.0) * math.pi * radius**3 * (1.0 / h_in**3 - 1.0 / h_max**3)
    r = radius + (np.arange(_STEPS) + 0.5) / _STEPS * blend
    h = h_in + (h_max - h_in) * (r - radius) / blend
    shell = float(np.sum(2.0 * math.pi * r**2 * (1.0 / h**3 - 1.0 / h_max**3)) * blend / _STEPS)
    return inner + shell


def size_integral(
    model: AssembledModel,
    level: MeshLevel,
    load_points: Sequence[Point] = (),
    conventions: Conventions | None = None,
) -> float:
    """∫ 1/h³ dV over the model (elements per unit of the size field, before the fitted factor)."""
    kernel = occ_tools.occ()
    volume = math.fsum(occ_tools.volume_mm3(p.volumes) for p in model.parts)
    total = volume / level.max_size_mm**3
    for hinted in model.hints:
        area = math.fsum(kernel.getMass(2, tag) for tag in hinted.surfaces)
        total += _band(area, level.hint_size(hinted.hint.feature_mm), level.max_size_mm, hinted.hint.extent_mm)
    radius = (conventions or get_conventions()).load_patch.radius_mm + PATCH_REACH_MM
    total += len(load_points) * _ball(radius, PATCH_BLEND_MM, level.patch_size_mm, level.max_size_mm)
    return total


def surface_area(model: AssembledModel) -> float:
    """The boundary area of every part's volumes (mm²); a face shared by two parts counts once for each."""
    kernel = occ_tools.occ()
    return math.fsum(
        kernel.getMass(2, tag)
        for part in model.parts
        for volume in part.volumes
        for _, tag in gmsh.model.getBoundary([(3, volume)], oriented=False)
    )


def predict_dof(
    model: AssembledModel,
    level: MeshLevel,
    load_points: Sequence[Point] = (),
    conventions: Conventions | None = None,
) -> int:
    """The expected degrees of freedom of the mesh of `model` at `level`."""
    bulk = DOF_SCALE * size_integral(model, level, load_points, conventions) ** DOF_EXPONENT
    return int(round(bulk + SURFACE_DOF * surface_area(model) / level.max_size_mm**2))
