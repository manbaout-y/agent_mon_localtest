from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from kvreuse_b5.providers import FreshProbeProvider, PartialForwardProvider, ProviderCapabilities, ProviderOutput


class OfflineFullTargetProbeProvider(FreshProbeProvider):
    """Offline paper-component provider: B is exposed only at requested fresh-probe scope."""

    capabilities = ProviderCapabilities(
        "PARTIAL_FORWARD", exact=True, oracle=True, uses_full_target=True, source="offline_saved_B_full_exact_probe"
    )
    semantics_variant = "full_exact"
    target_B_usage = "oracle_probe/full_exact_fresh_probe"

    def __init__(self, targets: Mapping[str, np.ndarray]):
        self.targets = {key: np.asarray(value, dtype=np.float64) for key, value in targets.items()}

    def checkpoint_states(self, *, current: Mapping[str, np.ndarray]) -> ProviderOutput:
        values = {kind: target[0].copy() for kind, target in self.targets.items()}
        return ProviderOutput(
            values,
            {"full_exact_fresh_units": float(sum(value.shape[0] for value in values.values()))},
            {"target_B_usage": self.target_B_usage},
        )

    def probe_states(
        self, *, layer: int, positions_by_kind: Mapping[str, np.ndarray], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        values = {
            kind: self.targets[kind][layer, np.asarray(positions, dtype=int)].copy()
            for kind, positions in positions_by_kind.items()
        }
        return ProviderOutput(
            values,
            {"full_exact_fresh_units": float(sum(len(positions) for positions in positions_by_kind.values()))},
            {"target_B_usage": self.target_B_usage},
        )


class OfflineFullTargetPartialForwardProvider(PartialForwardProvider):
    """Offline WCA component provider. Full target use is explicit and never deployable-like."""

    capabilities = ProviderCapabilities(
        "PARTIAL_FORWARD", exact=True, oracle=True, uses_full_target=True, source="offline_saved_B_exact_recompute"
    )
    target_B_usage = "oracle_probe/full_exact_fresh_recompute"

    def __init__(self, targets: Mapping[str, np.ndarray]):
        self.targets = {key: np.asarray(value, dtype=np.float64) for key, value in targets.items()}

    def score(
        self, *, layer: int, kinds: Sequence[str], current: Mapping[str, np.ndarray], positions: np.ndarray | None = None
    ) -> ProviderOutput:
        total = current[kinds[0]].shape[1]
        observed = np.arange(total) if positions is None else np.asarray(positions, dtype=int)
        scores = np.full(total, -np.inf, dtype=np.float64)
        scores[observed] = 0.0
        for kind in kinds:
            residual = self.targets[kind][layer, observed] - current[kind][layer, observed]
            scores[observed] += np.sum(residual * residual, axis=-1)
        return ProviderOutput(
            scores,
            {"full_exact_fresh_units": float(len(observed))},
            {"target_B_usage": self.target_B_usage, "observed_positions": observed.tolist()},
        )

    def recompute(
        self, *, layer: int, positions: np.ndarray, kinds: Sequence[str], current: Mapping[str, np.ndarray]
    ) -> ProviderOutput:
        observed = np.asarray(positions, dtype=int)
        values = {kind: self.targets[kind][layer, observed].copy() for kind in kinds}
        return ProviderOutput(
            values,
            {"full_exact_fresh_units": float(len(observed))},
            {"target_B_usage": self.target_B_usage},
        )
