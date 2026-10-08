from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .base import (
    AnchorStateProvider,
    FreshProbeProvider,
    PartialForwardProvider,
    ProviderCapabilities,
    ProviderOutput,
)


class SyntheticPartialForwardProvider(PartialForwardProvider):
    """Test-only exact provider. Ground truth stays behind the provider boundary."""

    capabilities = ProviderCapabilities(
        "PARTIAL_FORWARD", exact=True, oracle=False, uses_full_target=False, source="synthetic_exact_provider"
    )

    def __init__(self, targets: Mapping[str, np.ndarray]):
        self.targets = {k: np.asarray(v, dtype=float).copy() for k, v in targets.items()}
        self.calls: list[dict[str, Any]] = []

    def score(
        self, *, layer: int, kinds: Sequence[str], current: Mapping[str, np.ndarray], positions: np.ndarray | None = None
    ) -> ProviderOutput:
        token_count = current[kinds[0]].shape[1]
        observed = np.arange(token_count) if positions is None else np.asarray(positions, dtype=int)
        scores = np.full(token_count, -np.inf, dtype=float)
        scores[observed] = 0.0
        for kind in kinds:
            delta = self.targets[kind][layer, observed] - current[kind][layer, observed]
            scores[observed] += np.sum(delta * delta, axis=-1)
        self.calls.append({"op": "score", "layer": layer, "positions": observed.copy(), "current_snapshot": {k: v.copy() for k, v in current.items()}})
        return ProviderOutput(scores, {"partial_forward_units": float(len(observed))}, {"synthetic_exact_provider": True, "observed_positions": observed.tolist()})

    def recompute(
        self, *, layer: int, positions: np.ndarray, kinds: Sequence[str], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        positions = np.asarray(positions, dtype=int)
        value = {kind: self.targets[kind][layer, positions].copy() for kind in kinds}
        self.calls.append({"op": "recompute", "layer": layer, "positions": positions.copy(), "current_snapshot": {k: v.copy() for k, v in current.items()}})
        return ProviderOutput(value, {"partial_forward_units": float(len(positions))}, {"synthetic_exact_provider": True})


class FullExactFreshProbeProvider(FreshProbeProvider):
    capabilities = ProviderCapabilities(
        "PARTIAL_FORWARD", exact=True, oracle=False, uses_full_target=False, source="synthetic_full_exact_probe"
    )
    semantics_variant = "full_exact"

    def __init__(self, targets: Mapping[str, np.ndarray]):
        self.targets = {k: np.asarray(v, dtype=float).copy() for k, v in targets.items()}

    def checkpoint_states(self, *, current: Mapping[str, np.ndarray]) -> ProviderOutput:
        value = {k: v[0].copy() for k, v in self.targets.items()}
        units = sum(v.shape[0] for v in value.values())
        return ProviderOutput(value, {"partial_forward_units": float(units)}, {"probe_semantics": self.semantics_variant})

    def probe_states(
        self, *, layer: int, positions_by_kind: Mapping[str, np.ndarray], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        value = {k: self.targets[k][layer, np.asarray(pos, dtype=int)].copy() for k, pos in positions_by_kind.items()}
        return ProviderOutput(value, {"partial_forward_units": float(sum(len(p) for p in positions_by_kind.values()))}, {"probe_semantics": self.semantics_variant})


class ApproximateContextFreshProbeProvider(FullExactFreshProbeProvider):
    capabilities = ProviderCapabilities(
        "PARTIAL_FORWARD", exact=False, oracle=False, uses_full_target=False, source="synthetic_approximate_context_probe"
    )
    semantics_variant = "approximate_context"

    def __init__(self, targets: Mapping[str, np.ndarray], approximation_bias: float = 0.0):
        super().__init__(targets)
        self.approximation_bias = float(approximation_bias)

    def probe_states(self, **kwargs: Any) -> ProviderOutput:
        out = super().probe_states(**kwargs)
        out.value = {k: v + self.approximation_bias for k, v in out.value.items()}
        out.metadata["probe_semantics"] = self.semantics_variant
        return out


@dataclass
class SyntheticAnchor:
    anchor_id: str
    base: dict[str, np.ndarray]
    offsets: dict[str, np.ndarray]
    length: int


class SyntheticAnchorStateProvider(AnchorStateProvider):
    capabilities = ProviderCapabilities(
        "ONLINE_ANCHOR_STATE", exact=False, oracle=False, uses_full_target=False, source="synthetic_anchor_state"
    )

    def __init__(self, anchors: Sequence[SyntheticAnchor]):
        self.anchors = list(anchors)
        self.updates: list[SyntheticAnchor] = []

    def candidates(self, *, kind: str, current_base: np.ndarray) -> ProviderOutput:
        eligible = [a for a in self.anchors if a.length >= len(current_base)]
        values = []
        for anchor in eligible:
            ref = anchor.base[kind][: len(current_base)]
            values.append({"anchor_id": anchor.anchor_id, "distance": float(np.linalg.norm(ref - current_base)), "offset": anchor.offsets[kind]})
        return ProviderOutput(values, {"anchor_distance_evaluations": float(len(values))})

    def update(self, *, base: Mapping[str, np.ndarray], fresh: Mapping[str, np.ndarray], metadata: Mapping[str, Any]) -> ProviderOutput:
        anchor = SyntheticAnchor(
            anchor_id=str(metadata.get("anchor_id", f"update-{len(self.updates)}")),
            base={k: np.asarray(v).copy() for k, v in base.items()},
            offsets={k: np.asarray(fresh[k] - v).copy() for k, v in base.items()},
            length=len(next(iter(base.values()))),
        )
        self.updates.append(anchor)
        self.anchors.append(anchor)
        return ProviderOutput(anchor.anchor_id, {"anchor_updates": 1.0})
