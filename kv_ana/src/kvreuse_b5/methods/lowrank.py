from __future__ import annotations

from typing import Sequence

from kvreuse_b5.data.types import StatePair, stable_matmul
from kvreuse_b5.execution.artifacts import ArtifactStore, FrozenSubspaceArtifact
from .base import CoefficientEstimator, CorrectionMethod, CorrectionResult, EvalContext, FitContext, MethodCapabilities, MethodCategory, MethodCost


class PairLowRankOracle(CorrectionMethod):
    name = "pair_lowrank_oracle"
    category = MethodCategory.REFERENCE
    capabilities = MethodCapabilities(oracle=True, uses_full_target=True)
    uses_pair_svd = True

    def __init__(self, rank: int, artifact_store: ArtifactStore | None = None):
        self.rank = int(rank)
        if self.rank <= 0:
            raise ValueError("rank must be positive")
        self.artifact_store = artifact_store or ArtifactStore()
        super().__init__()

    def bind_artifact_store(self, store: ArtifactStore) -> None:
        self.artifact_store = store

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        artifact = self.artifact_store.pair_svd_for(pair)
        rank = min(self.rank, len(artifact.singular_values))
        delta_hat = stable_matmul(artifact.u[:, :rank] * artifact.singular_values[:rank], artifact.vt[:rank])
        return CorrectionResult(
            pair.a + delta_hat,
            MethodCost(total_units=pair.total_units, basis_rank=rank),
            artifact_hash=artifact.artifact_hash,
        )

    def output_parameters(self):
        return {"rank": self.rank}


class FrozenSubspace:
    """Facade over run-scoped FrozenSubspaceArtifact objects."""

    def __init__(self, max_rank: int, artifact_store: ArtifactStore | None = None):
        self.max_rank = int(max_rank)
        self.artifact_store = artifact_store or ArtifactStore()

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self.artifact_store.prepare_frozen(pairs, self.max_rank, context)

    def artifact_for(self, pair: StatePair, rank: int) -> FrozenSubspaceArtifact:
        return self.artifact_store.frozen_for(pair, rank)

    def basis_for(self, pair: StatePair, rank: int):
        artifact = self.artifact_for(pair, rank)
        return artifact.basis[:, :rank]


class _FrozenMethod(CorrectionMethod):
    uses_frozen_subspace = True

    def bind_artifact_store(self, store: ArtifactStore) -> None:
        self.subspace = FrozenSubspace(self.rank, store)

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self.subspace.fit(pairs, context)
        self.freeze()


class FrozenSubspaceOracleCoeff(_FrozenMethod):
    name = "frozen_lowrank_oracle"
    category = MethodCategory.REFERENCE
    capabilities = MethodCapabilities(oracle=True, needs_fit=True, uses_full_target=True)

    def __init__(self, rank: int, subspace: FrozenSubspace | None = None, artifact_store=None):
        self.rank = int(rank)
        self.subspace = subspace or FrozenSubspace(self.rank, artifact_store)
        super().__init__()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        artifact = self.subspace.artifact_for(pair, self.rank)
        basis = artifact.basis[:, : self.rank]
        coefficients = stable_matmul(pair.residual, basis)
        delta_hat = stable_matmul(coefficients, basis.T)
        return CorrectionResult(
            pair.a + delta_hat,
            MethodCost(total_units=pair.total_units, basis_rank=basis.shape[1]),
            artifact_hash=artifact.artifact_hash,
        )

    def output_parameters(self):
        return {"rank": self.rank}


class FrozenLowRankCorrection(_FrozenMethod):
    name = "frozen_lowrank_estimator"
    category = MethodCategory.OFFLINE_TRAINABLE
    capabilities = MethodCapabilities(needs_fit=True)

    def __init__(self, rank: int, estimator: CoefficientEstimator, subspace=None, artifact_store=None):
        self.rank = int(rank)
        self.estimator = estimator
        self.subspace = subspace or FrozenSubspace(self.rank, artifact_store)
        super().__init__()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        artifact = self.subspace.artifact_for(pair, self.rank)
        basis = artifact.basis[:, : self.rank]
        coefficients = self.estimator.estimate(pair, basis)
        return CorrectionResult(
            pair.a + stable_matmul(coefficients, basis.T),
            MethodCost(total_units=pair.total_units, basis_rank=basis.shape[1]),
            artifact_hash=artifact.artifact_hash,
        )
