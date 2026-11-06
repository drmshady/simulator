"""Unit load cases and their superposition (task T077; research R4, R7; FR-008).

Bonded-elastic physics is linear, so the solver solves unit cases and every load case is a linear combination of
them, applied to the displacement, stress and strain tensors before any invariant is taken:

- **Full runs** solve 3 unit cases per contact point, 1 N along x, y and z. A load case of force F, direction d̂
  (conventions `force_direction`) and contact points with shares sᵢ is then Σᵢ F·sᵢ·(d̂x u_{i,x} + d̂y u_{i,y} +
  d̂z u_{i,z}). One run covers every force and bite angle (FR-063, studies over angle or force).
- **Previews** solve one unit case per load-case instant, 1 N in that instant's direction shared over its contact
  points, so a preview needs as many solves as the scenario has instants (research R7); the combination is F times it.
- A **chewing cycle** is combined instant by instant, each instant with its own contact points, direction and
  share of the cycle's force; its key numbers are those of the worst instant, which is named (FR-008).

Superposition is refused when any capability in use is not linear (contact, preload and the others of Story 9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from dentalsim.conventions.frame import force_direction
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import Refused
from dentalsim.model.scenario import ContactPoint, Direction, LoadCase
from dentalsim.physics.registry import get_capability

FloatArray = NDArray[np.float64]
AXES = (("x", (1.0, 0.0, 0.0)), ("y", (0.0, 1.0, 0.0)), ("z", (0.0, 0.0, 1.0)))


def point_key(point: ContactPoint) -> str:
    """A contact point's identity: the block and its named point, or the block and the surface position."""
    if point.point is not None:
        return f"{point.block}.{point.point}"
    assert point.position_mm is not None
    return f"{point.block}@" + ",".join(f"{c:.4f}" for c in point.position_mm)


@dataclass(frozen=True)
class UnitCase:
    """One solver step: `forces` lists (contact point, force in N) whose sum is a 1 N load."""

    name: str
    forces: tuple[tuple[ContactPoint, tuple[float, float, float]], ...]

    def total(self) -> FloatArray:
        return np.asarray(np.sum([f for _, f in self.forces], axis=0), dtype=np.float64)


@dataclass(frozen=True)
class Combination:
    """One load case (or one instant of a chewing cycle) as a combination of unit cases."""

    load_case: str
    instant: str | None
    force_n: float
    direction: tuple[float, float, float]
    contact_points: tuple[ContactPoint, ...]
    coefficients: dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.load_case if self.instant is None else f"{self.load_case}/{self.instant}"


def require_linear(capability_ids: Sequence[str]) -> None:
    nonlinear = [c for c in capability_ids if not get_capability(c).linear]
    if nonlinear:
        raise Refused(
            "Unit load cases cannot be superposed with non-linear physics: "
            + ", ".join(get_capability(c).label.lower() for c in nonlinear)
            + ".",
            code="run_refused",
        )


def _instants(load: LoadCase) -> list[tuple[str | None, float, Direction, list[ContactPoint]]]:
    if load.pattern == "chewing_cycle":
        return [(i.id, load.force.value * i.force_share, i.direction, list(i.contact_points)) for i in load.instants]
    assert load.direction is not None
    return [(None, load.force.value, load.direction, list(load.contact_points))]


def plan(
    loads: Sequence[LoadCase], *, per_axis: bool, conventions: Conventions | None = None
) -> tuple[list[UnitCase], list[Combination]]:
    """The unit cases to solve and how each load case (instant) combines them."""
    cases: dict[str, UnitCase] = {}
    combinations: list[Combination] = []
    for load in loads:
        for instant, force, direction, points in _instants(load):
            d = force_direction(direction.angle_deg, direction.azimuth_deg, conventions)
            combination = Combination(load.id, instant, force, (float(d[0]), float(d[1]), float(d[2])), tuple(points))
            if per_axis:
                for point in points:
                    for k, (axis, unit) in enumerate(AXES):
                        name = f"{point_key(point)}.{axis}"
                        cases.setdefault(name, UnitCase(name, ((point, unit),)))
                        weight = force * point.share * float(d[k])
                        combination.coefficients[name] = combination.coefficients.get(name, 0.0) + weight
            else:
                name = combination.label
                vector = (float(d[0]), float(d[1]), float(d[2]))
                share = tuple((p, (p.share * vector[0], p.share * vector[1], p.share * vector[2])) for p in points)
                cases[name] = UnitCase(name, share)
                combination.coefficients[name] = force
            combinations.append(combination)
    return list(cases.values()), combinations


def combine(fields: Mapping[str, FloatArray], coefficients: Mapping[str, float]) -> FloatArray:
    """Σ cₖ · fieldₖ over the unit cases named in `coefficients`."""
    names = [name for name, c in coefficients.items() if c != 0.0]
    if not names:
        first = next(iter(fields.values()))
        return np.zeros_like(first)
    total = coefficients[names[0]] * fields[names[0]]
    for name in names[1:]:
        total = total + coefficients[name] * fields[name]
    return np.asarray(total, dtype=np.float64)
