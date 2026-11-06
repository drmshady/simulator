"""The coordinate frame and the load-angle sign of the conventions (FR-050, research R6).

Every load direction in the engine comes from `force_direction`, so the sign rule written in docs/conventions.md
is applied in exactly one place.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing, Refused

Vector = NDArray[np.float64]

# The only rule the schema admits; the formula in `force_direction` implements it.
SUPPORTED_LOAD_ANGLE_RULE = ("axial", "mesial", "buccal", "away_from_azimuth")


def frame_axes(conventions: Conventions | None = None) -> tuple[Vector, Vector, Vector]:
    """The x, y and z axis vectors of the frame, as written in the conventions block."""
    axes = (conventions or get_conventions()).frame.axes
    return (
        np.array(axes.x.vector, dtype=np.float64),
        np.array(axes.y.vector, dtype=np.float64),
        np.array(axes.z.vector, dtype=np.float64),
    )


def is_right_handed(conventions: Conventions | None = None, *, tolerance: float = 1e-12) -> bool:
    """True when the axes are orthonormal and x × y = z."""
    x, y, z = frame_axes(conventions)
    basis = np.stack([x, y, z])
    orthonormal = np.allclose(basis @ basis.T, np.eye(3), atol=tolerance)
    return bool(orthonormal and np.allclose(np.cross(x, y), z, atol=tolerance))


def _axis_with_direction(conventions: Conventions, direction: str) -> Vector:
    axes = conventions.frame.axes
    for axis in (axes.x, axes.y, axes.z):
        if axis.direction == direction:
            return np.array(axis.vector, dtype=np.float64)
    raise ConventionsMissing(f"the load-angle rule refers to {direction!r}, which is not an axis of the frame")


def force_direction(
    angle_deg: float, azimuth_deg: float | None = None, conventions: Conventions | None = None
) -> Vector:
    """The unit direction, shape (3,), of a load with angle `angle_deg` and azimuth `azimuth_deg`.

    F̂ = (−sin a·cos b, −sin a·sin b, −cos a) in the frame: 0° is axial, toward apical (−z); the azimuth, from +x
    (mesial) toward +y (buccal), is the side the load comes from; a positive angle tilts the force away from that
    side. With the default azimuth of 90°, +30° tilts the force toward lingual (−y). `azimuth_deg=None` uses the
    conventions' default azimuth.
    """
    conventions = conventions or get_conventions()
    rule = conventions.load_angle
    if (rule.zero, rule.azimuth_reference, rule.azimuth_toward, rule.positive_tilt) != SUPPORTED_LOAD_ANGLE_RULE:
        raise ConventionsMissing(f"this engine cannot apply the load-angle rule {rule.model_dump()}")
    low, high = rule.angle_range_deg
    if not low <= angle_deg <= high:
        raise Refused(
            f"the load angle {angle_deg:g}° is outside {low:g}°–{high:g}°; use azimuth + 180° instead of a "
            "negative angle",
            code="validation_failed",
        )
    reference = _axis_with_direction(conventions, rule.azimuth_reference)
    toward = _axis_with_direction(conventions, rule.azimuth_toward)
    occlusal = frame_axes(conventions)[2]
    a = math.radians(angle_deg)
    b = math.radians(rule.default_azimuth_deg if azimuth_deg is None else azimuth_deg)
    direction = -math.sin(a) * math.cos(b) * reference - math.sin(a) * math.sin(b) * toward - math.cos(a) * occlusal
    return np.asarray(direction / np.linalg.norm(direction), dtype=np.float64)
