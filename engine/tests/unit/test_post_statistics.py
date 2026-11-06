"""Volume-weighted mean and percentile (conventions `statistics.percentile_method`)."""

import numpy as np
import pytest

from dentalsim.post.statistics import region_percentile, weighted_mean, weighted_percentile


def test_percentile_is_the_smallest_value_reaching_the_volume_fraction() -> None:
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    weights = [1.0, 1.0, 1.0, 1.0, 1.0]  # cumulative fractions of the sorted values: 0.2, 0.4, 0.6, 0.8, 1.0
    assert weighted_percentile(values, weights, 95) == 5.0
    assert weighted_percentile(values, weights, 80) == 4.0  # 0.8 reached exactly at 4
    assert weighted_percentile(values, weights, 79.9) == 4.0
    assert weighted_percentile(values, weights, 20) == 1.0


def test_volumes_weigh_the_percentile() -> None:
    # One large element of value 1 holds 96% of the volume: the 95th percentile is 1, not the small high values.
    values = [1.0, 10.0, 20.0]
    weights = [96.0, 2.0, 2.0]
    assert weighted_percentile(values, weights, 95) == 1.0
    assert weighted_percentile(values, weights, 97) == 10.0


def test_the_order_of_the_input_does_not_matter() -> None:
    rng = np.random.default_rng(1)
    values, weights = rng.random(200), rng.random(200) + 0.1
    order = rng.permutation(200)
    assert weighted_percentile(values, weights, 95) == weighted_percentile(values[order], weights[order], 95)


def test_weighted_mean() -> None:
    assert weighted_mean([1.0, 3.0], [3.0, 1.0]) == pytest.approx(1.5)


def test_region_percentile_uses_the_conventions_percentile() -> None:
    values = np.arange(1.0, 101.0)
    assert region_percentile(values, np.ones(100)) == 95.0


@pytest.mark.parametrize(
    ("values", "weights", "message"),
    [([], [], "at least one"), ([1.0], [0.0], "positive"), ([1.0, 2.0], [1.0], "weights"), ([np.nan], [1.0], "finite")],
)
def test_bad_input_is_refused(values: list[float], weights: list[float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        weighted_percentile(values, weights, 95)
