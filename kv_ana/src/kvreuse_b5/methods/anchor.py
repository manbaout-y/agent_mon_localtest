from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair, as_numpy
from .base import (
    CorrectionMethod,
    CorrectionResult,
    EvalContext,
    FitContext,
    MethodCapabilities,
    MethodCategory,
    MethodCost,
)


@dataclass(frozen=True)
class AnchorEntry:
    descriptor: np.ndarray
    residual: np.ndarray


class ResidualAlignmentStrategy(ABC):
    name = "abstract"

    @abstractmethod
    def align(self, residual: np.ndarray, target_length: int) -> np.ndarray:
        raise NotImplementedError


class NormalizedLinearAlignment(ResidualAlignmentStrategy):
    name = "normalized_linear"

    def align(self, residual: np.ndarray, target_length: int) -> np.ndarray:
        if len(residual) == target_length:
            return residual
        source = np.linspace(0.0, 1.0, len(residual))
        target = np.linspace(0.0, 1.0, target_length)
        return np.stack(
            [np.interp(target, source, residual[:, feature]) for feature in range(residual.shape[1])], axis=1
        )


class AnchorResidualCorrection(CorrectionMethod):
    name = "anchor_residual"
    category = MethodCategory.EXPERIMENTAL
    capabilities = MethodCapabilities(needs_fit=True, requires_anchor_descriptor=True)

    def __init__(
        self,
        k: int,
        weighting: str = "uniform",
        alignment: ResidualAlignmentStrategy | None = None,
        distance_metric: str = "euclidean",
    ):
        self.k = int(k)
        if weighting not in {"uniform", "inverse_distance"}:
            raise ValueError("weighting must be uniform or inverse_distance")
        self.weighting = weighting
        if distance_metric != "euclidean":
            raise ValueError("v2 currently supports only euclidean descriptor distance")
        self.distance_metric = distance_metric
        self.alignment = alignment or NormalizedLinearAlignment()
        self.variant = f"generic_anchor_residual/{self.alignment.name}"
        self.pool: dict[tuple[str, str, str, int], list[AnchorEntry]] = defaultdict(list)
        super().__init__()

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        missing_descriptors = []
        for pair in pairs:
            if pair.split != "train" or pair.state_identity not in context.train_ids:
                raise ValueError("AnchorPool.fit received a non-train pair")
            if "anchor_descriptor" not in pair.metadata:
                missing_descriptors.append(pair.state_identity)
                continue
            descriptor = as_numpy(pair.metadata["anchor_descriptor"], dtype=float).reshape(-1)
            if not np.isfinite(descriptor).all():
                raise ValueError(f"anchor descriptor for {pair.state_identity} contains NaN or Inf")
            self.pool[pair.fit_key].append(AnchorEntry(descriptor, pair.residual.copy()))
        if missing_descriptors:
            from .base import CapabilityMissingError

            raise CapabilityMissingError([f"anchor_descriptor for train states {missing_descriptors}"])
        self.freeze()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        entries = self.pool.get(pair.fit_key, [])
        if not entries:
            raise RuntimeError(f"no train anchors for key {pair.fit_key}")
        query = as_numpy(pair.metadata["anchor_descriptor"], dtype=float).reshape(-1)
        if not np.isfinite(query).all():
            raise ValueError(f"anchor descriptor for {pair.state_identity} contains NaN or Inf")
        if any(entry.descriptor.shape != query.shape for entry in entries):
            raise ValueError("train/test anchor descriptor dimensions do not match")
        distances = np.array([np.linalg.norm(query - entry.descriptor) for entry in entries])
        chosen = np.argsort(distances)[: min(self.k, len(entries))]
        residuals = np.stack([self.alignment.align(entries[i].residual, pair.total_units) for i in chosen])
        if self.weighting == "uniform":
            weights = np.ones(len(chosen), dtype=float) / len(chosen)
        else:
            inv = 1.0 / np.maximum(distances[chosen], 1e-12)
            weights = inv / inv.sum()
        delta_hat = np.tensordot(weights, residuals, axes=(0, 0))
        return CorrectionResult(
            pair.a + delta_hat,
            MethodCost(total_units=pair.total_units, anchor_count=len(chosen)),
            {
                "neighbor_distances": distances[chosen].tolist(),
                "descriptor_source": pair.metadata.get("anchor_descriptor_source", "external:anchor_descriptor"),
                "distance_metric": self.distance_metric,
                "k": self.k,
                "weighting": self.weighting,
                "alignment_strategy": self.alignment.name,
                "method_family": "generic_anchor_residual",
                "literature_relation": "inspired_by/mechanism_probe",
            },
        )

    def output_parameters(self):
        return {
            "anchor_count": self.k,
            "weighting": self.weighting,
            "distance_metric": self.distance_metric,
            "alignment_strategy": self.alignment.name,
            "method_family": "generic_anchor_residual",
            "literature_relation": "inspired_by",
        }
