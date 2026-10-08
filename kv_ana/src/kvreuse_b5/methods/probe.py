from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair, stable_matmul
from kvreuse_b5.execution.artifacts import ArtifactStore
from .base import (
    CorrectionMethod,
    CorrectionResult,
    EvalContext,
    FitContext,
    MethodCapabilities,
    MethodCategory,
    MethodCost,
)
from .lowrank import FrozenSubspace


def probe_indices(positions: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(positions))
    if count <= 0:
        raise ValueError("probe count must be positive")
    if count == 1:
        midpoint = (float(positions[0]) + float(positions[-1])) / 2.0
        return np.array([int(np.argmin(np.abs(positions - midpoint)))], dtype=int)
    return np.unique(np.rint(np.linspace(0, len(positions) - 1, count)).astype(int))


class CoefficientStrategy(ABC):
    name = "abstract"

    @abstractmethod
    def interpolate(
        self, positions: np.ndarray, observed_indices: np.ndarray, observed_coefficients: np.ndarray
    ) -> np.ndarray:
        raise NotImplementedError


class LinearDepthStrategy(CoefficientStrategy):
    name = "linear_depth"

    def interpolate(self, positions, observed_indices, observed_coefficients):
        x = positions.astype(float)
        xp = x[observed_indices]
        if len(observed_indices) == 1:
            return np.repeat(observed_coefficients, len(positions), axis=0)
        columns = [np.interp(x, xp, observed_coefficients[:, j]) for j in range(observed_coefficients.shape[1])]
        return np.stack(columns, axis=1)


class ProbeMean(CorrectionMethod):
    name = "probe_mean"
    category = MethodCategory.DEPLOYMENT_ESTIMATOR
    capabilities = MethodCapabilities(uses_probe_target=True)

    def __init__(self, probes: int):
        self.probes = int(probes)
        super().__init__()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        indices = probe_indices(pair.positions, self.probes)
        mean = pair.residual[indices].mean(axis=0, keepdims=True)
        return CorrectionResult(
            pair.a + mean,
            MethodCost(observed_probe_units=len(indices), total_units=pair.total_units),
            {"probe_indices": indices.tolist()},
        )

    def output_parameters(self):
        return {"probe_count": self.probes}


class ProbeFrozenBasis(CorrectionMethod):
    name = "probe_frozen_basis"
    category = MethodCategory.OFFLINE_TRAINABLE
    capabilities = MethodCapabilities(needs_fit=True, uses_probe_target=True)
    uses_frozen_subspace = True

    def __init__(
        self,
        rank: int,
        probes: int,
        strategy: CoefficientStrategy | None = None,
        subspace: FrozenSubspace | None = None,
    ):
        self.rank = int(rank)
        self.probes = int(probes)
        self.strategy = strategy or LinearDepthStrategy()
        self.subspace = subspace or FrozenSubspace(self.rank)
        super().__init__()

    def bind_artifact_store(self, store: ArtifactStore) -> None:
        self.subspace = FrozenSubspace(self.rank, store)

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self.subspace.fit(pairs, context)
        self.freeze()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        artifact = self.subspace.artifact_for(pair, self.rank)
        basis = artifact.basis[:, : self.rank]
        indices = probe_indices(pair.positions, self.probes)
        observed = stable_matmul(pair.residual[indices], basis)
        coefficients = self.strategy.interpolate(pair.positions, indices, observed)
        return CorrectionResult(
            pair.a + stable_matmul(coefficients, basis.T),
            MethodCost(
                observed_probe_units=len(indices), total_units=pair.total_units, basis_rank=basis.shape[1]
            ),
            {"probe_indices": indices.tolist(), "coefficient_strategy": self.strategy.name},
            artifact_hash=artifact.artifact_hash,
        )

    def output_parameters(self):
        return {"rank": self.rank, "probe_count": self.probes, "strategy": self.strategy.name}
