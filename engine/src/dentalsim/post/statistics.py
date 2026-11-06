"""Volume-weighted statistics over integration points (constitution, Principle II; research R5).

Reported values are a region's mean and its 95th percentile, never a nodal peak. Each integration point weighs by
its volume. The percentile method is the one named in the conventions (`statistics.percentile_method`): values are
sorted from smallest to largest and the p-th percentile is the smallest value whose cumulative volume fraction is at
least p/100.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import ConventionsMissing

SUPPORTED_PERCENTILE_METHOD = "smallest_value_with_cumulative_volume_fraction_at_least_p"


def _checked(values: ArrayLike, weights: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    v = np.asarray(values, dtype=np.float64).ravel()
    w = np.asarray(weights, dtype=np.float64).ravel()
    if v.shape != w.shape:
        raise ValueError(f"{v.size} values but {w.size} weights")
    if v.size == 0:
        raise ValueError("a statistic needs at least one integration point")
    if not (np.all(np.isfinite(v)) and np.all(np.isfinite(w))):
        raise ValueError("values and weights must be finite")
    if np.any(w <= 0):
        raise ValueError("integration-point volumes must be positive")
    return v, w


def weighted_mean(values: ArrayLike, weights: ArrayLike) -> float:
    v, w = _checked(values, weights)
    return float(np.sum(v * w) / np.sum(w))


def weighted_percentile(values: ArrayLike, weights: ArrayLike, percentile: float) -> float:
    """The smallest value at which the cumulative volume fraction (values sorted ascending) reaches percentile/100."""
    if not 0 < percentile <= 100:
        raise ValueError("the percentile must lie in (0, 100]")
    v, w = _checked(values, weights)
    order = np.argsort(v, kind="stable")
    fractions = np.cumsum(w[order]) / np.sum(w)
    index = int(np.searchsorted(fractions, percentile / 100.0, side="left"))
    return float(v[order][min(index, v.size - 1)])


def region_percentile(values: ArrayLike, weights: ArrayLike, conventions: Conventions | None = None) -> float:
    """The reported percentile (`statistics.percentile`, 95) by the conventions' method."""
    stats = (conventions or get_conventions()).statistics
    if stats.percentile_method != SUPPORTED_PERCENTILE_METHOD:
        raise ConventionsMissing(f"this engine cannot compute the percentile method {stats.percentile_method!r}")
    return weighted_percentile(values, weights, stats.percentile)


def away_from(positions: ArrayLike, contacts: ArrayLike, radius_mm: float) -> NDArray[np.bool_]:
    """The points farther than `radius_mm` from every contact point (conventions
    `load_patch.crown_stats_exclude_radius_mm`): the statistics of a load-bearing component leave out the points
    nearer a contact point, where the stress is set by the patch radius rather than by the design."""
    p = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    c = np.asarray(contacts, dtype=np.float64).reshape(-1, 3)
    if not c.size or radius_mm <= 0:
        return np.ones(p.shape[0], dtype=np.bool_)
    distance = np.linalg.norm(p[:, None, :] - c[None, :, :], axis=2).min(axis=1)
    return np.asarray(distance > radius_mm, dtype=np.bool_)
