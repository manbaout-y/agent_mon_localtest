from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from kvreuse_b5.data.types import StatePair
from kvreuse_b5.regression.manifest import load_lifemem_manifest
from kvreuse_b5.regression.math import (
    UnitEnergy,
    energy_explained,
    historical_budget_units,
    historical_probe_indices,
    linear_interpolate_rows,
    lowrank_explained_curve,
    selective_removed_energy,
    shared_residual_sse,
)


ROOT = Path(__file__).resolve().parents[1]


def test_fixed48_manifest_and_split() -> None:
    samples = load_lifemem_manifest(ROOT / "manifests/lifemem_48_pairs.csv")
    assert len(samples) == 48
    assert sum(sample.split == "train" for sample in samples) == 32
    assert sum(sample.split == "test" for sample in samples) == 16
    assert [sample.pair_id for sample in samples[:5]] == [355, 72, 221, 467, 146]
    assert all((sample.sample_order % 3 == 2) == (sample.split == "test") for sample in samples)


def test_b1_shared_residual_formula() -> None:
    residual = np.asarray([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    baseline, corrected = shared_residual_sse(residual)
    centered = residual - residual.mean(axis=0, keepdims=True)
    assert baseline == pytest.approx(float(np.sum(residual**2)))
    assert corrected == pytest.approx(float(np.sum(centered**2)))


def test_b2_rank_curve_monotonic_and_zero_case() -> None:
    rng = np.random.default_rng(3)
    left = rng.normal(size=(12, 4)); right = rng.normal(size=(4, 9))
    curve = lowrank_explained_curve(left @ right, [1, 2, 4, 8], seed=7)
    assert curve[1] <= curve[2] <= curve[4] <= curve[8]
    assert curve[4] == pytest.approx(1.0, abs=1e-10)
    zero = lowrank_explained_curve(np.zeros((3, 4)), [1, 2], seed=1)
    assert np.isnan(zero[1]) and np.isnan(zero[2])


def test_b25_manifest_prevents_train_test_contamination() -> None:
    samples = load_lifemem_manifest(ROOT / "manifests/lifemem_48_pairs.csv")
    train = {sample.pair_id for sample in samples if sample.split == "train"}
    test = {sample.pair_id for sample in samples if sample.split == "test"}
    assert train.isdisjoint(test)
    assert len(train) == 32 and len(test) == 16


def test_b3_probe_indices_and_endpoint_interpolation() -> None:
    assert historical_probe_indices(10, 1).tolist() == [4]
    assert historical_probe_indices(10, 4).tolist() == [0, 3, 6, 9]
    assert historical_probe_indices(2, 8).tolist() == [0, 1]
    positions = np.arange(5, dtype=float)
    observed = np.asarray([0, 4])
    values = np.asarray([[0.0, 2.0], [4.0, 10.0]])
    estimate = linear_interpolate_rows(positions, observed, values)
    np.testing.assert_allclose(estimate[[0, -1]], values)
    np.testing.assert_allclose(estimate[2], [2.0, 6.0])


def test_b4_historical_budget_rounding() -> None:
    assert historical_budget_units(0, 0.1) == 0
    assert historical_budget_units(10, 0.01) == 1
    assert historical_budget_units(10, 0.25) == 2  # exact historical int(round()) semantics
    assert historical_budget_units(10, 1.0) == 10


def test_b4_fractional_final_group() -> None:
    units = [UnitEnergy(0, float(i), 4.0) for i in range(2)] + [UnitEnergy(1, float(i), 1.0) for i in range(4)]
    values = selective_removed_energy(units, 0.5)
    explained, unit_budget, actual = values["layer_group_oracle"]
    assert unit_budget == 3 and actual == pytest.approx(0.5)
    assert explained == pytest.approx(9.0 / 12.0)


def test_b4_zero_residual_is_nan() -> None:
    values = selective_removed_energy([UnitEnergy(0, 0.0, 0.0)], 0.1)
    assert all(np.isnan(explained) for explained, _, _ in values.values())
    assert np.isnan(energy_explained(0.0, 0.0))

