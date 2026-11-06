"""Convergence checks of one unit load case (FR-023; conventions "Mesh check and convergence checks").

- **Force balance**: the solver's double-precision total support reaction against the applied load, component by
  component, relative to |F| (`equilibrium_tolerance`).
- **Moment balance**: the reaction moment about the origin against the applied moment (patch nodal forces times
  positions), relative to max(|M applied|, |F| × 1 mm) (`moment_tolerance`).
- **Energy**: the external work Σ fᵢ · uᵢ over the loaded nodes against twice the strain energy summed at the
  integration points (`energy_tolerance`). Shear strains are tensor components (CalculiX `E` output).

These checks verify the solve and the post-processing; any exact solution passes them whatever its boundary
conditions, so a pass does not validate the model itself.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions

MOMENT_LEVER_MM = 1.0


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    relative_error: float
    tolerance: float

    @property
    def ok(self) -> bool:
        return bool(np.isfinite(self.relative_error) and self.relative_error <= self.tolerance)

    def reason(self) -> str:
        return f"the {self.name} error {self.relative_error:.3g} exceeds its tolerance {self.tolerance:.3g}"


def _vectors(values: ArrayLike) -> NDArray[np.float64]:
    return np.asarray(values, dtype=np.float64).reshape(-1, 3)


def force_balance_error(applied: ArrayLike, reaction_total: ArrayLike) -> float:
    """max over x, y, z of |R + F| / |F|."""
    f = np.asarray(applied, dtype=np.float64)
    r = np.asarray(reaction_total, dtype=np.float64)
    return float(np.max(np.abs(r + f)) / np.linalg.norm(f))


def moment_balance_error(
    load_positions: ArrayLike,
    load_forces: ArrayLike,
    reaction_positions: ArrayLike,
    reaction_forces: ArrayLike,
    *,
    lever_mm: float = MOMENT_LEVER_MM,
) -> float:
    """|M reactions + M applied| / max(|M applied|, |F| × lever), moments about the origin."""
    fl, fr = _vectors(load_forces), _vectors(reaction_forces)
    applied = np.cross(_vectors(load_positions), fl).sum(axis=0)
    reacted = np.cross(_vectors(reaction_positions), fr).sum(axis=0)
    scale = max(float(np.linalg.norm(applied)), float(np.linalg.norm(fl.sum(axis=0))) * lever_mm)
    return float(np.linalg.norm(applied + reacted) / scale)


def strain_energy(stress: ArrayLike, strain: ArrayLike, ip_volumes: ArrayLike) -> float:
    """½ Σ σ : ε V over the integration points (components xx, yy, zz, xy, xz, yz; tensor shear strains)."""
    s = np.asarray(stress, dtype=np.float64).reshape(-1, 6)
    e = np.asarray(strain, dtype=np.float64).reshape(-1, 6)
    density = (s[:, :3] * e[:, :3]).sum(axis=1) + 2.0 * (s[:, 3:] * e[:, 3:]).sum(axis=1)
    return 0.5 * float(np.sum(density * np.asarray(ip_volumes, dtype=np.float64)))


def energy_balance_error(
    load_forces: ArrayLike, load_displacements: ArrayLike, stress: ArrayLike, strain: ArrayLike, ip_volumes: ArrayLike
) -> float:
    """|W − 2U| / max(|W|, |2U|), with W = Σ fᵢ · uᵢ over the loaded nodes."""
    work = float(np.sum(_vectors(load_forces) * _vectors(load_displacements)))
    twice_u = 2.0 * strain_energy(stress, strain, ip_volumes)
    scale = max(abs(work), abs(twice_u))
    return float(abs(work - twice_u) / scale) if scale > 0 else float("inf")


def check_unit_case(
    *,
    applied_total: ArrayLike,
    reaction_total: ArrayLike,
    load_positions: ArrayLike,
    load_forces: ArrayLike,
    reaction_positions: ArrayLike,
    reaction_forces: ArrayLike,
    load_displacements: ArrayLike,
    stress: ArrayLike,
    strain: ArrayLike,
    ip_volumes: ArrayLike,
    residual: float | None = None,
    conventions: Conventions | None = None,
) -> list[Check]:
    """Every convergence check of one unit case, with the conventions' tolerances."""
    c = conventions or get_conventions()
    checks = [
        Check(
            name="force balance",
            relative_error=force_balance_error(applied_total, reaction_total),
            tolerance=c.equilibrium_tolerance,
        ),
        Check(
            name="moment balance",
            relative_error=moment_balance_error(load_positions, load_forces, reaction_positions, reaction_forces),
            tolerance=c.moment_tolerance,
        ),
        Check(
            name="energy",
            relative_error=energy_balance_error(load_forces, load_displacements, stress, strain, ip_volumes),
            tolerance=c.energy_tolerance,
        ),
    ]
    if residual is not None:
        checks.append(Check(name="solver residual", relative_error=residual, tolerance=c.residual_tolerance))
    return checks
