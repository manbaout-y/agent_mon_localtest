from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Mapping, Sequence

import numpy as np


def stable_seed(base_seed: int, *parts: object) -> int:
    token = repr((base_seed, *parts)).encode("utf-8")
    return int.from_bytes(sha256(token).digest()[:8], "big") % (2**32)


def energy_explained(baseline_sse: float, corrected_sse: float) -> float:
    if baseline_sse <= 0:
        return float("nan")
    return 1.0 - corrected_sse / baseline_sse


def shared_residual_sse(delta: np.ndarray) -> tuple[float, float]:
    values = np.asarray(delta, dtype=np.float64)
    baseline = float(np.sum(values * values))
    centered = values - values.mean(axis=0, keepdims=True)
    return baseline, float(np.sum(centered * centered))


def randomized_singular_values(
    matrix: np.ndarray,
    max_rank: int,
    *,
    oversample: int = 8,
    niter: int = 2,
    seed: int = 0,
) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float64)
    min_dim = min(values.shape)
    rank = min(int(max_rank), min_dim)
    if rank <= 0:
        return np.empty(0, dtype=np.float64)
    if min_dim <= 64 or rank == min_dim:
        return np.linalg.svd(values, compute_uv=False)[:rank]
    width = min(min_dim, rank + int(oversample))
    rng = np.random.default_rng(seed)
    omega = rng.standard_normal((values.shape[1], width))
    y = values @ omega
    for _ in range(int(niter)):
        y, _ = np.linalg.qr(y, mode="reduced")
        y = values @ (values.T @ y)
    q, _ = np.linalg.qr(y, mode="reduced")
    small = q.T @ values
    return np.linalg.svd(small, compute_uv=False)[:rank]


def lowrank_explained_curve(
    delta: np.ndarray,
    ranks: Sequence[int],
    *,
    oversample: int = 8,
    niter: int = 2,
    seed: int = 0,
) -> dict[int, float]:
    values = np.asarray(delta, dtype=np.float64)
    total = float(np.sum(values * values))
    singular = randomized_singular_values(
        values, max(ranks), oversample=oversample, niter=niter, seed=seed
    )
    cumulative = np.cumsum(singular * singular)
    output = {}
    previous = 0.0
    for rank in sorted(ranks):
        effective = min(int(rank), len(cumulative))
        explained = float(cumulative[effective - 1] / total) if total > 0 and effective else float("nan")
        if np.isfinite(explained):
            explained = max(previous, min(1.0, explained))
            previous = explained
        output[int(rank)] = explained
    return output


def randomized_basis_from_covariance(
    covariance: np.ndarray,
    max_rank: int,
    *,
    oversample: int = 8,
    niter: int = 2,
    seed: int = 0,
) -> np.ndarray:
    cov = np.asarray(covariance, dtype=np.float64)
    rank = min(int(max_rank), cov.shape[0])
    width = min(cov.shape[0], rank + int(oversample))
    rng = np.random.default_rng(seed)
    y = cov @ rng.standard_normal((cov.shape[0], width))
    for _ in range(int(niter)):
        y, _ = np.linalg.qr(y, mode="reduced")
        y = cov @ y
    q, _ = np.linalg.qr(y, mode="reduced")
    small = q.T @ cov @ q
    eigenvalues, eigenvectors = np.linalg.eigh(small)
    order = np.argsort(eigenvalues)[::-1][:rank]
    return (q @ eigenvectors[:, order]).astype(np.float64, copy=False)


def projection_sse(delta: np.ndarray, basis: np.ndarray) -> tuple[float, float]:
    values = np.asarray(delta, dtype=np.float64)
    coefficients = values @ basis
    residual = values - coefficients @ basis.T
    return float(np.sum(values * values)), float(np.sum(residual * residual))


def linear_interpolate_rows(
    positions: np.ndarray, observed_indices: np.ndarray, observed_values: np.ndarray
) -> np.ndarray:
    x = np.asarray(positions, dtype=np.float64)
    indices = np.asarray(observed_indices, dtype=int)
    observed = np.asarray(observed_values, dtype=np.float64)
    if len(indices) == 1:
        return np.repeat(observed, len(x), axis=0)
    return np.stack([np.interp(x, x[indices], observed[:, column]) for column in range(observed.shape[1])], axis=1)


def historical_probe_indices(length: int, requested: int) -> np.ndarray:
    count = min(int(requested), int(length))
    if count <= 0:
        raise ValueError("probe count must be positive")
    if count == 1:
        return np.asarray([(length - 1) // 2], dtype=int)
    return np.unique(np.rint(np.linspace(0, length - 1, count)).astype(int))


def historical_budget_units(total_units: int, budget: float) -> int:
    if total_units < 0 or not 0 <= budget <= 1:
        raise ValueError("invalid historical budget")
    if total_units == 0:
        return 0
    return min(total_units, max(1, int(round(float(budget) * total_units))))


@dataclass(frozen=True)
class UnitEnergy:
    layer: int
    position: float
    energy: float


def _fractional_group_removed(
    groups: Iterable[tuple[object, int, float]], unit_budget: int
) -> float:
    removed = 0.0
    remaining = int(unit_budget)
    for _, size, energy in sorted(groups, key=lambda item: (-item[2], repr(item[0]))):
        if remaining <= 0:
            break
        if remaining >= size:
            removed += energy
            remaining -= size
        else:
            removed += energy * (remaining / size)
            remaining = 0
    return float(removed)


def selective_removed_energy(
    units: Sequence[UnitEnergy], budget: float
) -> dict[str, tuple[float, int, float]]:
    total_units = len(units)
    k = historical_budget_units(total_units, budget)
    total_energy = float(sum(unit.energy for unit in units))
    actual_fraction = k / total_units if total_units else 0.0
    if total_energy <= 0:
        return {name: (float("nan"), k, actual_fraction) for name in (
            "random_expected", "boundary_first", "layer_group_oracle", "position_group_oracle", "joint_unit_oracle"
        )}
    boundary = sorted(units, key=lambda unit: (unit.position, unit.layer))[:k]
    joint = sorted(units, key=lambda unit: (-unit.energy, unit.layer, unit.position))[:k]
    layer_groups: dict[int, list[UnitEnergy]] = defaultdict(list)
    position_groups: dict[float, list[UnitEnergy]] = defaultdict(list)
    for unit in units:
        layer_groups[unit.layer].append(unit)
        position_groups[unit.position].append(unit)
    removed = {
        "random_expected": total_energy * actual_fraction,
        "boundary_first": sum(unit.energy for unit in boundary),
        "layer_group_oracle": _fractional_group_removed(
            ((key, len(group), sum(unit.energy for unit in group)) for key, group in layer_groups.items()), k
        ),
        "position_group_oracle": _fractional_group_removed(
            ((key, len(group), sum(unit.energy for unit in group)) for key, group in position_groups.items()), k
        ),
        "joint_unit_oracle": sum(unit.energy for unit in joint),
    }
    return {name: (float(value / total_energy), k, actual_fraction) for name, value in removed.items()}
