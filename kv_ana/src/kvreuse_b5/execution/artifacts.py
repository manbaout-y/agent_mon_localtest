from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair
from kvreuse_b5.methods.base import FitContext

FitKey = tuple[str, str, str, int]


def _artifact_hash(*parts: Any) -> str:
    digest = sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            digest.update(str(part.shape).encode())
            digest.update(part.tobytes())
        else:
            digest.update(repr(part).encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenSubspaceArtifact:
    fit_key: FitKey
    basis: np.ndarray
    singular_values: np.ndarray
    fit_pair_ids: frozenset[tuple[object, ...]]
    fit_config_hash: str
    feature_dim: int
    artifact_hash: str

    @property
    def max_rank(self) -> int:
        return int(self.basis.shape[1])


@dataclass(frozen=True)
class PairSVDArtifact:
    state_identity: tuple[object, ...]
    u: np.ndarray
    singular_values: np.ndarray
    vt: np.ndarray
    artifact_hash: str


class PrecomputedArtifactProvider(ABC):
    """Future boundary for loading externally trained B2.5 artifacts."""

    @abstractmethod
    def frozen_subspace(self, fit_key: FitKey, min_rank: int) -> FrozenSubspaceArtifact | None:
        raise NotImplementedError


class ArtifactStore:
    """Run-scoped artifact cache shared by all rank/probe/composite variants."""

    def __init__(self, provider: PrecomputedArtifactProvider | None = None):
        self.provider = provider
        self.frozen_subspaces: dict[FitKey, FrozenSubspaceArtifact] = {}
        self.pair_svds: dict[tuple[object, ...], PairSVDArtifact] = {}
        self.svd_fit_count = 0
        self.pair_svd_count = 0

    def prepare_methods(self, methods: Sequence[Any], pairs: Sequence[StatePair], context: FitContext) -> None:
        lowrank_methods: list[Any] = []

        def visit(method: Any) -> None:
            if hasattr(method, "methods"):
                for stage in method.methods:
                    visit(stage)
            if getattr(method, "uses_frozen_subspace", False):
                method.bind_artifact_store(self)
                lowrank_methods.append(method)
            if getattr(method, "uses_pair_svd", False):
                method.bind_artifact_store(self)

        for method in methods:
            visit(method)
        if lowrank_methods:
            self.prepare_frozen(pairs, max(int(method.rank) for method in lowrank_methods), context)

    def prepare_frozen(self, pairs: Sequence[StatePair], max_rank: int, context: FitContext) -> None:
        grouped: dict[FitKey, list[StatePair]] = defaultdict(list)
        for pair in pairs:
            if pair.split != "train" or pair.state_identity not in context.train_ids:
                raise ValueError("FrozenSubspaceArtifact fit received a non-train pair")
            grouped[pair.fit_key].append(pair)
        for key, group in grouped.items():
            current = self.frozen_subspaces.get(key)
            if current is not None and current.max_rank >= max_rank:
                continue
            if self.provider is not None:
                supplied = self.provider.frozen_subspace(key, max_rank)
                if supplied is not None:
                    if supplied.fit_key != key:
                        raise ValueError(f"precomputed artifact fit_key {supplied.fit_key} != requested {key}")
                    self._validate_artifact(supplied, group[0].feature_dim, max_rank)
                    self.frozen_subspaces[key] = supplied
                    continue
            matrix = np.concatenate([pair.residual for pair in group], axis=0)
            _, singular_values, vt = np.linalg.svd(matrix, full_matrices=False)
            rank = min(max_rank, vt.shape[0])
            basis = vt[:rank].T.copy()
            fit_ids = frozenset(pair.state_identity for pair in group)
            artifact = FrozenSubspaceArtifact(
                fit_key=key,
                basis=basis,
                singular_values=singular_values[:rank].copy(),
                fit_pair_ids=fit_ids,
                fit_config_hash=context.config_hash,
                feature_dim=matrix.shape[1],
                # Set iteration order varies across Python processes; identity must not.
                artifact_hash=_artifact_hash(key, basis, singular_values[:rank],
                                             tuple(sorted(fit_ids, key=repr)), context.config_hash),
            )
            self.frozen_subspaces[key] = artifact
            self.svd_fit_count += 1

    def frozen_for(self, pair: StatePair, rank: int) -> FrozenSubspaceArtifact:
        artifact = self.frozen_subspaces.get(pair.fit_key)
        if artifact is None:
            raise KeyError(f"no frozen basis for key {pair.fit_key}")
        self._validate_artifact(artifact, pair.feature_dim, rank)
        return artifact

    def pair_svd_for(self, pair: StatePair) -> PairSVDArtifact:
        artifact = self.pair_svds.get(pair.state_identity)
        if artifact is None:
            u, singular_values, vt = np.linalg.svd(pair.residual, full_matrices=False)
            artifact = PairSVDArtifact(
                state_identity=pair.state_identity,
                u=u,
                singular_values=singular_values,
                vt=vt,
                artifact_hash=_artifact_hash(pair.state_identity, u, singular_values, vt),
            )
            self.pair_svds[pair.state_identity] = artifact
            self.pair_svd_count += 1
        return artifact

    @staticmethod
    def _validate_artifact(artifact: FrozenSubspaceArtifact, feature_dim: int, rank: int) -> None:
        if rank > artifact.max_rank:
            raise ValueError(f"requested basis rank {rank} exceeds trained max_rank {artifact.max_rank}")
        if artifact.feature_dim != feature_dim or artifact.basis.shape[0] != feature_dim:
            raise ValueError(
                f"basis feature_dim {artifact.feature_dim} does not match StatePair feature_dim {feature_dim}"
            )

    def audit_manifest(self) -> dict[str, Any]:
        return {
            "version": "2.0.1",
            "svd_fit_count": self.svd_fit_count,
            "pair_svd_count": self.pair_svd_count,
            "frozen_subspaces": [
                {
                    "fit_key": list(artifact.fit_key),
                    "max_rank": artifact.max_rank,
                    "feature_dim": artifact.feature_dim,
                    "fit_pair_ids": [list(identity) for identity in sorted(artifact.fit_pair_ids, key=repr)],
                    "fit_config_hash": artifact.fit_config_hash,
                    "artifact_hash": artifact.artifact_hash,
                }
                for artifact in sorted(self.frozen_subspaces.values(), key=lambda item: repr(item.fit_key))
            ],
            "pair_svds": [
                {"state_identity": list(artifact.state_identity), "artifact_hash": artifact.artifact_hash}
                for artifact in sorted(self.pair_svds.values(), key=lambda item: repr(item.state_identity))
            ],
        }
