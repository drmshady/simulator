"""Stress and strain invariants at the integration points (task T076; conventions "Bone strain", "Stress compared
with strength").

Inputs are (k, 6) arrays in CalculiX's component order xx, yy, zz, xy, xz, yz; strain shear components are tensor
components (half the engineering shear strain). These rules are among the conventions' "rules defined in code", so a
change here needs a new conventions version.

- **Von Mises stress**: √(½[(σ₁ − σ₂)² + (σ₂ − σ₃)² + (σ₃ − σ₁)²]), from the components directly.
- **Principal values**: eigenvalues of the symmetric tensor, largest first.
- **Equivalent strain** (the Frost measure): ε_eq = 1/(1 + ν′) · √(½[(ε₁ − ε₂)² + (ε₂ − ε₃)² + (ε₃ − ε₁)²]), with
  ν′ the material's own Poisson's ratio (conventions `strain_measure.effective_poisson_ratio: material_nu`), so that
  under uniaxial stress ε_eq equals the axial strain. Reported in microstrain.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing

FloatArray = NDArray[np.float64]
MICRO = 1e6
SUPPORTED_STRAIN_MEASURE = ("von_mises_equivalent", "material_nu", "microstrain")


def _components(values: ArrayLike) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 6:
        raise ValueError(f"expected an (k, 6) array of tensor components, got shape {array.shape}")
    return array


def tensors(values: ArrayLike) -> FloatArray:
    """(k, 3, 3) symmetric tensors from (k, 6) components."""
    v = _components(values)
    t = np.empty((v.shape[0], 3, 3))
    t[:, 0, 0], t[:, 1, 1], t[:, 2, 2] = v[:, 0], v[:, 1], v[:, 2]
    t[:, 0, 1] = t[:, 1, 0] = v[:, 3]
    t[:, 0, 2] = t[:, 2, 0] = v[:, 4]
    t[:, 1, 2] = t[:, 2, 1] = v[:, 5]
    return t


def principal(values: ArrayLike) -> FloatArray:
    """(k, 3) principal values, largest first."""
    return np.asarray(np.linalg.eigvalsh(tensors(values))[:, ::-1], dtype=np.float64)


def von_mises(stress: ArrayLike) -> FloatArray:
    s = _components(stress)
    sxx, syy, szz, sxy, sxz, syz = s.T
    return np.asarray(
        np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy**2 + sxz**2 + syz**2)),
        dtype=np.float64,
    )


def max_principal(stress: ArrayLike) -> FloatArray:
    return np.asarray(principal(stress)[:, 0], dtype=np.float64)


def equivalent_strain_ue(strain: ArrayLike, nu: ArrayLike, conventions: Conventions | None = None) -> FloatArray:
    """The conventions' equivalent strain in microstrain; `nu` is each point's material Poisson's ratio."""
    measure = (conventions or get_conventions()).strain_measure
    if (measure.frost, measure.effective_poisson_ratio, measure.unit) != SUPPORTED_STRAIN_MEASURE:
        raise ConventionsMissing(f"this engine cannot compute the strain measure {measure.model_dump()}")
    e = principal(strain)
    deviation = np.sqrt(0.5 * ((e[:, 0] - e[:, 1]) ** 2 + (e[:, 1] - e[:, 2]) ** 2 + (e[:, 2] - e[:, 0]) ** 2))
    return np.asarray(MICRO * deviation / (1.0 + np.asarray(nu, dtype=np.float64)), dtype=np.float64)


def principal_strains_ue(strain: ArrayLike) -> FloatArray:
    """(k, 3) principal strains in microstrain, largest first."""
    return MICRO * principal(strain)


def stress_measure(stress: ArrayLike, measure: str) -> FloatArray:
    """The stress measure a strength rule names: `von_mises` or `max_principal`."""
    if measure == "von_mises":
        return von_mises(stress)
    if measure == "max_principal":
        return max_principal(stress)
    raise ConventionsMissing(f"this engine has no stress measure {measure!r}")
