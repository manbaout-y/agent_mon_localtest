from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair


class MethodCategory(str, Enum):
    REFERENCE = "oracle/reference"
    OFFLINE_TRAINABLE = "offline-trainable"
    DEPLOYMENT_ESTIMATOR = "deployment-like-estimator"
    UNAVAILABLE = "not-offline-executable"
    EXPERIMENTAL = "experimental-generic-baseline"


class StageType(str, Enum):
    MATERIALIZED_CORRECTION = "materialized_correction"
    METRIC_ONLY_REFERENCE = "metric_only_reference"


@dataclass(frozen=True)
class MethodCapabilities:
    oracle: bool = False
    needs_fit: bool = False
    uses_full_target: bool = False
    uses_probe_target: bool = False
    uses_repair_target: bool = False
    requires_query: bool = False
    requires_attention: bool = False
    requires_query_score: bool = False
    requires_position_metadata: bool = False
    requires_anchor_descriptor: bool = False
    requires_training: bool = False
    requires_architecture_change: bool = False
    requires_runtime: bool = False

    @property
    def executable_offline(self) -> bool:
        return not (
            self.requires_query
            or self.requires_attention
            or self.requires_query_score
            or self.requires_training
            or self.requires_architecture_change
            or self.requires_runtime
        )


@dataclass
class CostRecord:
    observed_probe_units: int = 0
    detector_recomputed_units: int = 0
    exact_repaired_units: int = 0
    total_units: int = 0
    basis_rank: int | None = None
    anchor_count: int | None = None
    detector_layers: int | None = None
    method_specific_cost: dict[str, Any] = field(default_factory=dict)

    @property
    def exact_compute_units(self) -> int:
        return self.detector_recomputed_units + self.exact_repaired_units

    @property
    def observed_units(self) -> int:
        return self.observed_probe_units + self.detector_recomputed_units + self.exact_repaired_units

    @property
    def logical_repair_fraction(self) -> float:
        return self.exact_compute_units / self.total_units if self.total_units else 0.0

    def add(self, other: "CostRecord") -> "CostRecord":
        specific = dict(self.method_specific_cost)
        specific.update(other.method_specific_cost)
        return CostRecord(
            observed_probe_units=self.observed_probe_units + other.observed_probe_units,
            detector_recomputed_units=self.detector_recomputed_units + other.detector_recomputed_units,
            exact_repaired_units=self.exact_repaired_units + other.exact_repaired_units,
            total_units=max(self.total_units, other.total_units),
            basis_rank=other.basis_rank if other.basis_rank is not None else self.basis_rank,
            anchor_count=other.anchor_count if other.anchor_count is not None else self.anchor_count,
            detector_layers=other.detector_layers if other.detector_layers is not None else self.detector_layers,
            method_specific_cost=specific,
        )


# Backwards-compatible import name; v2 records use CostRecord field semantics.
MethodCost = CostRecord


@dataclass
class CorrectionResult:
    corrected: np.ndarray
    cost: CostRecord
    diagnostics: dict[str, Any] = field(default_factory=dict)
    corrected_sse_override: float | None = None
    corrected_sse_multiplier: float | None = None
    artifact_hash: str | None = None


@dataclass(frozen=True)
class FitContext:
    train_ids: frozenset[tuple[object, ...]]
    config_hash: str = "standalone"


@dataclass(frozen=True)
class EvalContext:
    split: str = "test"


class CapabilityMissingError(RuntimeError):
    def __init__(self, missing: Sequence[str]):
        self.missing = tuple(missing)
        super().__init__("capability missing: " + ", ".join(self.missing))


class CorrectionMethod(ABC):
    name = "abstract"
    variant = "default"
    category = MethodCategory.DEPLOYMENT_ESTIMATOR
    capabilities = MethodCapabilities()
    stage_type = StageType.MATERIALIZED_CORRECTION

    @property
    def materializes_state(self) -> bool:
        return self.stage_type == StageType.MATERIALIZED_CORRECTION

    def __init__(self) -> None:
        if self.category == MethodCategory.DEPLOYMENT_ESTIMATOR and self.capabilities.uses_full_target:
            raise ValueError(f"{self.name}: full-target method cannot be deployable")
        self._frozen = not self.capabilities.needs_fit

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        if any(p.split != "train" or p.state_identity not in context.train_ids for p in pairs):
            raise ValueError(f"{self.name}: fit received non-train pair")
        self._frozen = True

    def freeze(self) -> None:
        self._frozen = True

    def _check_ready(self, pair: StatePair) -> None:
        if self.capabilities.needs_fit and not self._frozen:
            raise RuntimeError(f"{self.name}: method must be fit and frozen before evaluation")
        missing = []
        if self.capabilities.requires_anchor_descriptor and "anchor_descriptor" not in pair.metadata:
            missing.append("anchor_descriptor")
        if self.capabilities.requires_position_metadata and "rope_metadata" not in pair.metadata:
            missing.append("rope_metadata")
        if self.capabilities.requires_query and "query_info" not in pair.metadata:
            missing.append("query_info")
        if self.capabilities.requires_attention and "attention_info" not in pair.metadata:
            missing.append("attention_info")
        if self.capabilities.requires_query_score and "query_score" not in pair.metadata:
            missing.append("query_score")
        if self.capabilities.requires_training:
            missing.append("training_pipeline")
        if self.capabilities.requires_architecture_change:
            missing.append("architecture_change")
        if self.capabilities.requires_runtime:
            missing.append("runtime")
        if missing:
            raise CapabilityMissingError(missing)

    def apply(self, pair: StatePair, context: EvalContext | None = None) -> CorrectionResult:
        self._check_ready(pair)
        result = self.correct(pair, context or EvalContext())
        if result.corrected.shape != pair.a.shape:
            raise ValueError(f"{self.name}: corrected shape {result.corrected.shape} != {pair.a.shape}")
        if not np.isfinite(result.corrected).all():
            raise ValueError(f"pair {pair.pair_id}/layer {pair.layer_id}/method {self.name}: NaN or Inf")
        return result

    @abstractmethod
    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        raise NotImplementedError

    def output_parameters(self) -> Mapping[str, Any]:
        return {}


class CoefficientEstimator(ABC):
    @abstractmethod
    def estimate(self, pair: StatePair, basis: np.ndarray) -> np.ndarray:
        """Return [T,r] coefficients without accessing an unobserved full target."""
        raise NotImplementedError


class QueryImportanceProvider(ABC):
    @abstractmethod
    def scores(self, pair: StatePair) -> np.ndarray:
        raise NotImplementedError
