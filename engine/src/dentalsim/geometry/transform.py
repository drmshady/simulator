"""Rigid transforms between a block's local frame and the conventions frame.

A `Transform` maps local coordinates to target coordinates: p_target = R · p_local + t. Every transform here is
proper (a rotation, det R = +1, plus a translation). Mirrored quadrants are handled by the jaw segment building the
mirror-image arch directly, so no reflection ever reaches the geometry and no element connectivity needs reordering.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
Point = tuple[float, float, float]

_ORTHONORMAL_TOLERANCE = 1e-9


def _vector(value: ArrayLike, name: str) -> FloatArray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be a finite 3-vector, not {value!r}")
    return vector


def unit(value: ArrayLike, name: str = "vector") -> FloatArray:
    """`value` scaled to length 1."""
    vector = _vector(value, name)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        raise ValueError(f"{name} must not be zero")
    return np.asarray(vector / norm, dtype=np.float64)


@dataclass(frozen=True, eq=False)
class Transform:
    """p_target = rotation @ p_local + translation, with `rotation` a proper rotation matrix."""

    rotation: FloatArray
    translation: FloatArray

    def __post_init__(self) -> None:
        rotation = np.asarray(self.rotation, dtype=np.float64)
        if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
            raise ValueError("rotation must be a finite 3 × 3 matrix")
        if not np.allclose(rotation @ rotation.T, np.eye(3), atol=_ORTHONORMAL_TOLERANCE):
            raise ValueError("rotation must be orthonormal")
        if np.linalg.det(rotation) < 0:
            raise ValueError("rotation must be proper (det = +1): mirroring is not a placement")
        object.__setattr__(self, "rotation", rotation)
        object.__setattr__(self, "translation", _vector(self.translation, "translation"))

    # --- construction ---------------------------------------------------------------------------------------------

    @classmethod
    def identity(cls) -> Transform:
        return cls(np.eye(3), np.zeros(3))

    @classmethod
    def translation_by(cls, offset: ArrayLike) -> Transform:
        return cls(np.eye(3), _vector(offset, "offset"))

    @classmethod
    def rotation_about(cls, axis: ArrayLike, angle_deg: float, centre: ArrayLike = (0.0, 0.0, 0.0)) -> Transform:
        """A right-handed rotation by `angle_deg` about `axis` through `centre` (Rodrigues' formula)."""
        k = unit(axis, "axis")
        a = math.radians(angle_deg)
        cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
        rotation = np.eye(3) + math.sin(a) * cross + (1.0 - math.cos(a)) * (cross @ cross)
        c = _vector(centre, "centre")
        return cls(rotation, c - rotation @ c)

    @classmethod
    def from_axes(cls, origin: ArrayLike, x: ArrayLike, y: ArrayLike, z: ArrayLike | None = None) -> Transform:
        """The frame with origin `origin` and axes `x`, `y` (and `z = x × y`), given in target coordinates.

        `x` and `y` must be at right angles; `z`, when given, must equal x × y.
        """
        ex, ey = unit(x, "x axis"), unit(y, "y axis")
        if abs(float(ex @ ey)) > 1e-9:
            raise ValueError("the x and y axes must be at right angles")
        ez = np.cross(ex, ey)
        if z is not None and not np.allclose(unit(z, "z axis"), ez, atol=1e-9):
            raise ValueError("the z axis must equal x × y (a right-handed frame)")
        return cls(np.column_stack([ex, ey, ez]), _vector(origin, "origin"))

    # --- use ------------------------------------------------------------------------------------------------------

    def apply(self, points: ArrayLike) -> FloatArray:
        """Transform points, shape (3,) or (N, 3)."""
        p = np.asarray(points, dtype=np.float64)
        return np.asarray(p @ self.rotation.T + self.translation, dtype=np.float64)

    def apply_vector(self, vectors: ArrayLike) -> FloatArray:
        """Rotate directions, shape (3,) or (N, 3); translation does not apply."""
        v = np.asarray(vectors, dtype=np.float64)
        return np.asarray(v @ self.rotation.T, dtype=np.float64)

    def point(self, local: Sequence[float]) -> Point:
        x, y, z = (float(c) for c in self.apply(local))
        return (x, y, z)

    def direction(self, local: Sequence[float]) -> Point:
        x, y, z = (float(c) for c in self.apply_vector(local))
        return (x, y, z)

    @property
    def origin(self) -> Point:
        return self.point((0.0, 0.0, 0.0))

    @property
    def axes(self) -> tuple[Point, Point, Point]:
        """The local x, y and z axes in target coordinates."""
        return (self.direction((1, 0, 0)), self.direction((0, 1, 0)), self.direction((0, 0, 1)))

    def inverse(self) -> Transform:
        return Transform(self.rotation.T, -(self.rotation.T @ self.translation))

    def __matmul__(self, other: Transform) -> Transform:
        """`(a @ b).apply(p) == a.apply(b.apply(p))`."""
        return Transform(self.rotation @ other.rotation, self.rotation @ other.translation + self.translation)

    def is_identity(self, tolerance: float = 1e-12) -> bool:
        return bool(
            np.allclose(self.rotation, np.eye(3), atol=tolerance) and np.allclose(self.translation, 0.0, atol=tolerance)
        )

    def __repr__(self) -> str:
        return f"Transform(origin={self.origin}, axes={self.axes})"


IDENTITY = Transform.identity()
