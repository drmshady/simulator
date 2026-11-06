"""The frame and the load-angle sign of the conventions (conventions/frame.py, FR-050)."""

import math

import numpy as np
import pytest

from dentalsim.conventions.frame import force_direction, frame_axes, is_right_handed
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Axes, Axis, Conventions
from dentalsim.errors import Refused


@pytest.fixture
def conventions() -> Conventions:
    return get_conventions()


def _with_axes(conventions: Conventions, **axes: Axis) -> Conventions:
    current = conventions.frame.axes
    new_axes = Axes(x=axes.get("x", current.x), y=axes.get("y", current.y), z=axes.get("z", current.z))
    return conventions.model_copy(update={"frame": conventions.frame.model_copy(update={"axes": new_axes})})


def test_frame_axes_are_the_unit_vectors(conventions: Conventions) -> None:
    x, y, z = frame_axes(conventions)
    assert np.array_equal(np.stack([x, y, z]), np.eye(3))


def test_the_frame_is_right_handed(conventions: Conventions) -> None:
    assert is_right_handed(conventions)


def test_a_left_handed_or_skewed_frame_is_detected(conventions: Conventions) -> None:
    assert not is_right_handed(_with_axes(conventions, y=Axis(direction="buccal", vector=(0, -1, 0))))
    assert not is_right_handed(_with_axes(conventions, x=Axis(direction="mesial", vector=(2, 0, 0))))


@pytest.mark.parametrize("azimuth", [0.0, 45.0, 90.0, 200.0, None])
def test_zero_degrees_is_axial_toward_apical(azimuth: float | None, conventions: Conventions) -> None:
    assert np.allclose(force_direction(0.0, azimuth, conventions), [0.0, 0.0, -1.0], atol=1e-15)


def test_positive_angle_at_buccal_azimuth_tilts_lingually_and_apically(conventions: Conventions) -> None:
    direction = force_direction(30.0, 90.0, conventions)
    assert direction.shape == (3,)
    assert direction[1] < 0  # toward lingual
    assert direction[2] < 0  # toward apical
    assert direction == pytest.approx([0.0, -0.5, -math.sqrt(3) / 2], abs=1e-12)


@pytest.mark.parametrize("angle", [-30.0, -0.001, 90.001, 120.0])
def test_angles_outside_the_range_are_refused(conventions: Conventions, angle: float) -> None:
    with pytest.raises(Refused, match="outside 0°–90°"):
        force_direction(angle, 90.0, conventions)


def test_the_range_ends_are_allowed(conventions: Conventions) -> None:
    assert force_direction(0.0, 90.0, conventions) == pytest.approx([0.0, 0.0, -1.0], abs=1e-12)
    assert force_direction(90.0, 90.0, conventions) == pytest.approx([0.0, -1.0, 0.0], abs=1e-12)


def test_a_negative_angle_is_the_same_as_azimuth_plus_180(conventions: Conventions) -> None:
    # What -30° at azimuth 90° would mean is written as +30° at azimuth 270°: tilted toward buccal.
    assert force_direction(30.0, 270.0, conventions) == pytest.approx([0.0, 0.5, -math.sqrt(3) / 2], abs=1e-12)


def test_azimuth_is_measured_from_mesial_toward_buccal(conventions: Conventions) -> None:
    # A load from the mesial side (azimuth 0) tilts the force distally (-x).
    assert force_direction(30.0, 0.0, conventions) == pytest.approx([-0.5, 0.0, -math.sqrt(3) / 2], abs=1e-12)
    # From the distal side (180) it tilts mesially; from lingual (270) buccally.
    assert force_direction(30.0, 180.0, conventions) == pytest.approx([0.5, 0.0, -math.sqrt(3) / 2], abs=1e-12)
    assert force_direction(30.0, 270.0, conventions) == pytest.approx([0.0, 0.5, -math.sqrt(3) / 2], abs=1e-12)


def test_ninety_degrees_is_horizontal(conventions: Conventions) -> None:
    assert force_direction(90.0, 90.0, conventions) == pytest.approx([0.0, -1.0, 0.0], abs=1e-12)


def test_default_azimuth_comes_from_the_conventions(conventions: Conventions) -> None:
    assert conventions.load_angle.default_azimuth_deg == 90
    assert np.array_equal(force_direction(30.0, None, conventions), force_direction(30.0, 90.0, conventions))
    assert np.array_equal(force_direction(30.0), force_direction(30.0, 90.0, conventions))


@pytest.mark.parametrize(("angle", "azimuth"), [(12.5, 33.0), (45.0, 300.0), (60.0, 315.0), (89.0, 1.0)])
def test_directions_are_unit_vectors_with_the_stated_split(angle: float, azimuth: float) -> None:
    direction = force_direction(angle, azimuth)
    assert np.linalg.norm(direction) == pytest.approx(1.0, abs=1e-14)
    assert -direction[2] == pytest.approx(math.cos(math.radians(angle)), abs=1e-14)
    assert np.hypot(direction[0], direction[1]) == pytest.approx(abs(math.sin(math.radians(angle))), abs=1e-14)
