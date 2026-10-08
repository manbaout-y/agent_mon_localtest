from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from hashlib import sha256
from typing import Any, Hashable, Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair
from .base import CapabilityMissingError, CorrectionMethod, CorrectionResult, EvalContext, FitContext, MethodCapabilities, MethodCategory, MethodCost, StageType


def budget_units(total: int, budget: float) -> int:
    if not 0.0 <= budget <= 1.0:
        raise ValueError(f"budget must be in [0,1], got {budget}")
    return 0 if budget == 0.0 else min(total, max(1, int(np.ceil(total * budget))))


def _neutral_hash(seed: int, pair: StatePair, row: int, namespace: str = "row") -> int:
    # Position names the logical unit, so reordering input rows does not create prefix preference.
    unit_ids = pair.metadata.get("unit_ids")
    logical_unit = unit_ids[row] if unit_ids is not None else float(pair.positions[row])
    token = repr((seed, pair.state_identity, namespace, logical_unit)).encode()
    return int.from_bytes(sha256(token).digest()[:8], "big")


def _neutral_group_hash(seed: int, group_key: Hashable) -> int:
    return int.from_bytes(sha256(repr((seed, "atomic_group", group_key)).encode()).digest()[:8], "big")


def repair_rows(pair: StatePair, indices: np.ndarray, budget: float, diagnostics=None) -> CorrectionResult:
    corrected = pair.a.copy()
    corrected[indices] = pair.b[indices]
    return CorrectionResult(
        corrected,
        MethodCost(exact_repaired_units=len(indices), total_units=pair.total_units),
        diagnostics or {},
    )


class SelectiveRepair(CorrectionMethod):
    capabilities = MethodCapabilities(uses_repair_target=True)
    GRANULARITIES = {"neutral_partial", "group_atomic"}

    def __init__(self, budget: float, granularity: str = "neutral_partial", config_seed: int = 0):
        self.budget = float(budget)
        budget_units(1, self.budget)
        if granularity not in self.GRANULARITIES:
            raise ValueError(f"unknown selector granularity {granularity!r}")
        self.granularity = granularity
        self.config_seed = int(config_seed)
        self.variant = f"{self.name}/{granularity}"
        super().__init__()

    @abstractmethod
    def unit_scores(self, pair: StatePair) -> np.ndarray:
        raise NotImplementedError

    def atomic_groups(self, pairs: Sequence[StatePair]) -> list[tuple[float, Hashable, list[tuple[int, int]]]]:
        groups = []
        for pair_index, pair in enumerate(pairs):
            scores = self.unit_scores(pair)
            for row, score in enumerate(scores):
                groups.append((float(score), (pair.state_identity, float(pair.positions[row])), [(pair_index, row)]))
        return groups

    def _select_group(self, pairs: Sequence[StatePair]) -> tuple[dict[int, list[int]], dict[str, Any]]:
        total = sum(pair.total_units for pair in pairs)
        requested_units = budget_units(total, self.budget)
        selected: dict[int, list[int]] = defaultdict(list)
        if self.granularity == "neutral_partial":
            entries = []
            for pair_index, pair in enumerate(pairs):
                unit_ids = pair.metadata.get("unit_ids")
                if unit_ids is not None and (len(unit_ids) != pair.total_units or len(set(map(repr, unit_ids))) != pair.total_units):
                    raise ValueError("metadata.unit_ids must contain one unique ID per row")
                if unit_ids is None and len(np.unique(pair.positions)) != pair.total_units:
                    raise ValueError("neutral tie-breaking requires unique positions or explicit metadata.unit_ids")
                scores = self.unit_scores(pair)
                if scores.shape != (pair.total_units,):
                    raise ValueError(f"{self.name}: unit score must have shape [T]")
                if not np.isfinite(scores).all():
                    raise CapabilityMissingError(["train selector score for every test unit/bin"])
                for row, score in enumerate(scores):
                    entries.append((float(score), _neutral_hash(self.config_seed, pair, row), pair_index, row))
            entries.sort(key=lambda item: (-item[0], item[1]))
            for _, _, pair_index, row in entries[:requested_units]:
                selected[pair_index].append(row)
        else:
            groups = self.atomic_groups(pairs)
            if any(not np.isfinite(score) for score, _, _ in groups):
                raise CapabilityMissingError(["train selector score for every test atomic group"])
            groups.sort(key=lambda item: (-item[0], _neutral_group_hash(self.config_seed, item[1])))
            corrected_count = 0
            for _, _, members in groups:
                if corrected_count >= requested_units:
                    break
                for pair_index, row in members:
                    selected[pair_index].append(row)
                corrected_count += len(members)
        actual_units = sum(len(rows) for rows in selected.values())
        if self.granularity == "neutral_partial" and actual_units != requested_units:
            raise AssertionError(f"{self.name}: neutral selector violated exact unit budget")
        if self.granularity == "group_atomic" and requested_units and actual_units < requested_units:
            raise AssertionError(f"{self.name}: atomic selector did not reach requested budget")
        diagnostics = {
            "requested_budget": self.budget,
            "unit_budget": requested_units,
            "group_total_units": total,
            "actual_budget": actual_units / total if total else 0.0,
            "budget_overshoot": actual_units / total - self.budget if total else 0.0,
            "selector_granularity": self.granularity,
            "tie_policy": "sha256(config_seed,state_identity,position)" if self.granularity == "neutral_partial" else "group_atomic",
        }
        return selected, diagnostics

    def apply_group(self, pairs: Sequence[StatePair]) -> dict[tuple[object, ...], CorrectionResult]:
        if not pairs:
            raise ValueError("selector group is empty")
        if any(pair.dataset_group_identity != pairs[0].dataset_group_identity for pair in pairs):
            raise ValueError("selector apply_group requires one explicit dataset group")
        selected, diagnostics = self._select_group(pairs)
        output = {}
        for pair_index, pair in enumerate(pairs):
            rows = np.asarray(selected.get(pair_index, []), dtype=int)
            result = repair_rows(pair, rows, self.budget, {**diagnostics, "selected_indices": rows.tolist()})
            output[pair.state_identity] = result
        return output

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        return self.apply_group([pair])[pair.state_identity]

    def output_parameters(self):
        return {
            "requested_budget": self.budget,
            "budget": self.budget,
            "selector_granularity": self.granularity,
            "config_seed": self.config_seed,
        }


class RandomExpected(SelectiveRepair):
    name = "random_expected"
    capabilities = MethodCapabilities()
    stage_type = StageType.METRIC_ONLY_REFERENCE

    def __init__(self, budget: float, config_seed: int = 0):
        super().__init__(budget, "neutral_partial", config_seed)
        self.variant = "random_expected/metric_only"

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        return np.zeros(pair.total_units)

    def apply_group(self, pairs: Sequence[StatePair]) -> dict[tuple[object, ...], CorrectionResult]:
        if not pairs:
            raise ValueError("random-expected group is empty")
        if any(pair.dataset_group_identity != pairs[0].dataset_group_identity for pair in pairs):
            raise ValueError("random-expected apply_group requires one explicit dataset group")
        total = sum(pair.total_units for pair in pairs)
        units = budget_units(total, self.budget)
        ratio = 1.0 - units / total
        diagnostics = {
            "theoretical_expectation": True,
            "requested_budget": self.budget,
            "unit_budget": units,
            "group_total_units": total,
            "actual_budget": units / total,
            "budget_overshoot": units / total - self.budget,
        }
        return {
            pair.state_identity: CorrectionResult(
                pair.a.copy(),
                MethodCost(total_units=pair.total_units, method_specific_cost={"expected_repair_units": units}),
                diagnostics,
                corrected_sse_multiplier=ratio,
            )
            for pair in pairs
        }


class BoundaryFirst(SelectiveRepair):
    name = "boundary_first"

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        return -(pair.positions.astype(float) - float(pair.positions.min()))


class JointUnitOracle(SelectiveRepair):
    name = "joint_unit_oracle"
    category = MethodCategory.REFERENCE
    capabilities = MethodCapabilities(oracle=True, uses_full_target=True)

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        return np.sum(pair.residual.astype(float) ** 2, axis=1)


class _StaticSelector(SelectiveRepair):
    category = MethodCategory.OFFLINE_TRAINABLE
    capabilities = MethodCapabilities(needs_fit=True, uses_repair_target=True)

    def _validate_fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        if any(pair.split != "train" or pair.state_identity not in context.train_ids for pair in pairs):
            raise ValueError(f"{self.name}.fit received a non-train pair")


class StaticLayerSelector(_StaticSelector):
    name = "static_layer"

    def __init__(self, budget: float, granularity: str = "neutral_partial", config_seed: int = 0):
        self.layer_scores: dict[tuple[str, str, str, int], float] = {}
        super().__init__(budget, granularity, config_seed)

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self._validate_fit(pairs, context)
        values: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
        for pair in pairs:
            values[pair.fit_key].extend(np.sum(pair.residual.astype(float) ** 2, axis=1).tolist())
        self.layer_scores = {key: float(np.mean(items)) for key, items in values.items()}
        self.freeze()

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        if pair.fit_key not in self.layer_scores:
            raise KeyError(f"no static layer score for {pair.fit_key}")
        return np.full(pair.total_units, self.layer_scores[pair.fit_key])

    def atomic_groups(self, pairs: Sequence[StatePair]):
        return [
            (float(self.layer_scores[pair.fit_key]), pair.state_identity, [(index, row) for row in range(pair.total_units)])
            for index, pair in enumerate(pairs)
        ]


class PositionBinner(ABC):
    name = "abstract"

    @abstractmethod
    def bins(self, pair: StatePair) -> np.ndarray:
        raise NotImplementedError


class FixedEdgesPositionBinner(PositionBinner):
    name = "fixed_edges_relative_to_x_start"

    def __init__(self, edges: Sequence[float]):
        self.edges = np.asarray(edges, dtype=float)
        if (
            len(self.edges) < 2
            or self.edges[0] != 0
            or not np.all(np.diff(self.edges) > 0)
            or not np.isinf(self.edges[-1])
        ):
            raise ValueError("fixed edges must start at 0, end at inf, and be strictly increasing")

    def bins(self, pair: StatePair) -> np.ndarray:
        if "x_start_position" not in pair.metadata:
            raise ValueError("fixed-edge binning requires metadata.x_start_position")
        relative = pair.positions.astype(float) - float(pair.metadata["x_start_position"])
        if np.any(relative < 0):
            raise ValueError("positions precede x_start_position")
        return np.searchsorted(self.edges, relative, side="right") - 1


class NormalizedDepthBinner(PositionBinner):
    name = "normalized_depth"

    def __init__(self, bin_count: int):
        if bin_count <= 0:
            raise ValueError("bin_count must be positive")
        self.bin_count = int(bin_count)

    def bins(self, pair: StatePair) -> np.ndarray:
        positions = pair.positions.astype(float)
        if len(positions) <= 1 or positions.max() == positions.min():
            return np.zeros(len(positions), dtype=int)
        depth = (positions - positions.min()) / (positions.max() - positions.min())
        return np.minimum((depth * self.bin_count).astype(int), self.bin_count - 1)


class UniformAbsoluteBinner(PositionBinner):
    name = "uniform_absolute_relative_to_x_start"

    def __init__(self, width: float):
        if width <= 0:
            raise ValueError("width must be positive")
        self.width = float(width)

    def bins(self, pair: StatePair) -> np.ndarray:
        if "x_start_position" not in pair.metadata:
            raise ValueError("uniform absolute binning requires metadata.x_start_position")
        relative = pair.positions.astype(float) - float(pair.metadata["x_start_position"])
        if np.any(relative < 0):
            raise ValueError("positions precede x_start_position")
        return np.floor(relative / self.width).astype(int)


class _StaticBinnedSelector(_StaticSelector):
    def __init__(
        self,
        budget: float,
        binner: PositionBinner | None = None,
        granularity: str = "neutral_partial",
        config_seed: int = 0,
    ):
        self.binner = binner or NormalizedDepthBinner(16)
        super().__init__(budget, granularity, config_seed)

    def output_parameters(self):
        result = {**super().output_parameters(), "position_binner": self.binner.name}
        if hasattr(self.binner, "bin_count"):
            result["bin_count"] = self.binner.bin_count
        if hasattr(self.binner, "edges"):
            result["edges"] = self.binner.edges.tolist()
        if hasattr(self.binner, "width"):
            result["width"] = self.binner.width
        return result


class StaticPositionSelector(_StaticBinnedSelector):
    name = "static_position"

    def __init__(self, budget: float, binner=None, granularity="neutral_partial", config_seed=0):
        self.scores: dict[tuple[tuple[str, str, str], int], float] = {}
        super().__init__(budget, binner, granularity, config_seed)

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self._validate_fit(pairs, context)
        values: dict[tuple[tuple[str, str, str], int], list[float]] = defaultdict(list)
        for pair in pairs:
            for bin_id, energy in zip(self.binner.bins(pair), np.sum(pair.residual.astype(float) ** 2, axis=1)):
                values[(pair.family_key, int(bin_id))].append(float(energy))
        self.scores = {key: float(np.mean(items)) for key, items in values.items()}
        self.freeze()

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        return np.array([self.scores.get((pair.family_key, int(bin_id)), -np.inf) for bin_id in self.binner.bins(pair)])

    def atomic_groups(self, pairs: Sequence[StatePair]):
        grouped: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for pair_index, pair in enumerate(pairs):
            for row, bin_id in enumerate(self.binner.bins(pair)):
                grouped[int(bin_id)].append((pair_index, row))
        return [(self.scores.get((pairs[0].family_key, bin_id), -np.inf), bin_id, members) for bin_id, members in grouped.items()]


class StaticLayerPositionSelector(_StaticBinnedSelector):
    name = "static_joint"

    def __init__(self, budget: float, binner=None, granularity="neutral_partial", config_seed=0):
        self.scores: dict[tuple[tuple[str, str, str], int, int], float] = {}
        super().__init__(budget, binner, granularity, config_seed)

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self._validate_fit(pairs, context)
        values: dict[tuple[tuple[str, str, str], int, int], list[float]] = defaultdict(list)
        for pair in pairs:
            for bin_id, energy in zip(self.binner.bins(pair), np.sum(pair.residual.astype(float) ** 2, axis=1)):
                values[(pair.family_key, pair.layer_id, int(bin_id))].append(float(energy))
        self.scores = {key: float(np.mean(items)) for key, items in values.items()}
        self.freeze()

    def unit_scores(self, pair: StatePair) -> np.ndarray:
        return np.array(
            [self.scores.get((pair.family_key, pair.layer_id, int(bin_id)), -np.inf) for bin_id in self.binner.bins(pair)]
        )

    def atomic_groups(self, pairs: Sequence[StatePair]):
        groups: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for pair_index, pair in enumerate(pairs):
            for row, bin_id in enumerate(self.binner.bins(pair)):
                groups[(pair.layer_id, int(bin_id))].append((pair_index, row))
        return [
            (self.scores.get((pairs[0].family_key, layer, bin_id), -np.inf), (layer, bin_id), members)
            for (layer, bin_id), members in groups.items()
        ]
