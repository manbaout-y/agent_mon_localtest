from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ProviderCapabilities:
    capability: str
    exact: bool = False
    oracle: bool = False
    uses_full_target: bool = False
    source: str = "external"


@dataclass
class ProviderOutput:
    value: Any
    cost: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class RecipeProvider(ABC):
    capabilities: ProviderCapabilities

    @property
    def capability(self) -> str:
        return self.capabilities.capability


class PartialForwardProvider(RecipeProvider):
    capabilities = ProviderCapabilities("PARTIAL_FORWARD")

    @abstractmethod
    def score(
        self, *, layer: int, kinds: Sequence[str], current: Mapping[str, np.ndarray], positions: np.ndarray | None = None
    ) -> ProviderOutput:
        raise NotImplementedError

    @abstractmethod
    def recompute(
        self, *, layer: int, positions: np.ndarray, kinds: Sequence[str], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        raise NotImplementedError


class QuerySignalProvider(RecipeProvider):
    capabilities = ProviderCapabilities("QUERY_SIGNAL")

    @abstractmethod
    def query_signal(self, **scope: Any) -> ProviderOutput:
        raise NotImplementedError


class AttentionSignalProvider(RecipeProvider):
    capabilities = ProviderCapabilities("ATTENTION_SIGNAL")

    @abstractmethod
    def attention_signal(self, **scope: Any) -> ProviderOutput:
        raise NotImplementedError


class FreshProbeProvider(RecipeProvider):
    capabilities = ProviderCapabilities("PARTIAL_FORWARD")
    semantics_variant: str

    @abstractmethod
    def checkpoint_states(self, *, current: Mapping[str, np.ndarray]) -> ProviderOutput:
        raise NotImplementedError

    @abstractmethod
    def probe_states(
        self, *, layer: int, positions_by_kind: Mapping[str, np.ndarray], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        raise NotImplementedError


class DetectorScoreProvider(RecipeProvider):
    capabilities = ProviderCapabilities("DETECTOR_SCORE")

    @abstractmethod
    def scores(self, **scope: Any) -> ProviderOutput:
        raise NotImplementedError


class AnchorStateProvider(RecipeProvider):
    capabilities = ProviderCapabilities("ONLINE_ANCHOR_STATE")

    @abstractmethod
    def candidates(self, *, kind: str, current_base: np.ndarray) -> ProviderOutput:
        raise NotImplementedError

    @abstractmethod
    def update(self, *, base: Mapping[str, np.ndarray], fresh: Mapping[str, np.ndarray], metadata: Mapping[str, Any]) -> ProviderOutput:
        raise NotImplementedError


class SemanticEmbeddingProvider(RecipeProvider):
    capabilities = ProviderCapabilities("SEMANTIC_EMBEDDING")

    @abstractmethod
    def embed(self, objects: Sequence[Any]) -> ProviderOutput:
        raise NotImplementedError


class PositionTransformProvider(RecipeProvider):
    capabilities = ProviderCapabilities("POSITION_TRANSFORM")

    @abstractmethod
    def transform(self, states: Mapping[str, np.ndarray], **scope: Any) -> ProviderOutput:
        raise NotImplementedError


class CacheRuntimeProvider(RecipeProvider):
    capabilities = ProviderCapabilities("CACHE_RUNTIME")

    @abstractmethod
    def cache_event(self, action: str, **scope: Any) -> ProviderOutput:
        raise NotImplementedError
