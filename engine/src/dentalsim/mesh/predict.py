"""Predicting the degrees of freedom of a mesh from its size fields, before meshing (research R8; FR-009).

The number of elements of a tetrahedral mesh grows as the integral of 1/h³ over the volume, h being the local
element size. The size fields of a level (`dentalsim.mesh.levels`) give h:

- the bulk size everywhere: V / h_max³;
- near each refinement hint, the threshold rises linearly from the hint size at the surface to the bulk size at
  `extent_mm`; the band is counted on both sides of the hinted surfaces (an implant's surface has bone outside
  and implant inside): 2·A·∫₀ᴰ (1/h(d)³ − 1/h_max³) dd;
- around each loaded point, half a ball (the point is on the surface) of the patch size, blending to the bulk size.

DOF = `DOF_SCALE` × S^`DOF_EXPONENT`, S being that integral, fitted to meshes of the single-crown template at the
preview, standard and fine levels (59 k, 209 k and 382 k DOF; the fit is within 14% of each). The exponent is below 1
because threads, thin walls and curvature force elements the fields alone do not, more so at coarse levels. The
estimate models refit time and memory from real runs (`dentalsim.estimate`); this only needs to be right to a few
tens of percent, and should be refitted when more templates exist.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.assemble import AssembledModel
from dentalsim.geometry.transform import Point
from dentalsim.mesh.levels import PATCH_BLEND_MM, PATCH_REACH_MM, MeshLevel

DOF_SCALE = 653.0
DOF_EXPONENT = 0.705
"""DOF = DOF_SCALE × (∫ 1/h³ dV)^DOF_EXPONENT, fitted on the single-crown template (preview, standard and fine)."""
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


def predict_dof(
    model: AssembledModel,
    level: MeshLevel,
    load_points: Sequence[Point] = (),
    conventions: Conventions | None = None,
) -> int:
    """The expected degrees of freedom of the mesh of `model` at `level`."""
    return int(round(DOF_SCALE * size_integral(model, level, load_points, conventions) ** DOF_EXPONENT))
