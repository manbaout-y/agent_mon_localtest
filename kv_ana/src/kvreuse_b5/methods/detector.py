from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence
from hashlib import sha256

import numpy as np

from kvreuse_b5.data.types import StatePair
from .base import CapabilityMissingError, CorrectionMethod, CorrectionResult, EvalContext, FitContext, MethodCapabilities, MethodCategory, MethodCost
from .selective import budget_units


class DetectorScoreProvider(ABC):
    @abstractmethod
    def position_scores(self, pairs: Sequence[StatePair], detector_layer_ids: Sequence[int]) -> np.ndarray:
        """Return an external [T] score without reading target B."""
        raise NotImplementedError


@dataclass
class DetectorEvaluation:
    detector_layer_ids: list[int]
    selected_positions: list[int]
    top_k_position_recall: float
    oracle_unit_energy_coverage: float
    repair_energy_explained: float
    corrected: dict[tuple[object, ...], np.ndarray]
    costs: dict[tuple[object, ...], MethodCost]


def _tie_hash(seed: int, value) -> int:
    return int.from_bytes(sha256(repr((seed, value)).encode()).digest()[:8], "big")


class DetectorLayerSelector:
    def __init__(self, detector_count: int, budget: float, selection="train_selected", fixed_ids=None, config_seed=0):
        if detector_count not in {1, 2, 4}:
            raise ValueError("detector_count must be 1, 2, or 4")
        if selection not in {"train_selected", "fixed"}:
            raise ValueError("selection must be train_selected or fixed")
        self.detector_count = int(detector_count)
        self.budget = float(budget)
        self.selection = selection
        self.fixed_ids = list(fixed_ids or [])
        self.config_seed = int(config_seed)
        self.detectors: dict[tuple[str, str, str], list[int]] = {}

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        if any(pair.split != "train" or pair.state_identity not in context.train_ids for pair in pairs):
            raise ValueError("detector selection fit received a non-train pair")
        families = {pair.family_key for pair in pairs}
        if self.selection == "fixed":
            if len(self.fixed_ids) < self.detector_count:
                raise ValueError("not enough fixed detector IDs")
            for family in families:
                self.detectors[family] = self.fixed_ids[: self.detector_count]
            return
        values: dict[tuple[tuple[str, str, str], int], list[float]] = defaultdict(list)
        for pair in pairs:
            values[(pair.family_key, pair.layer_id)].append(float(np.mean(np.sum(pair.residual ** 2, axis=1))))
        for family in families:
            scores = [(float(np.mean(items)), layer) for (key, layer), items in values.items() if key == family]
            scores.sort(key=lambda item: (-item[0], _tie_hash(self.config_seed, (family, item[1]))))
            self.detectors[family] = [layer for _, layer in scores[: self.detector_count]]

    @staticmethod
    def _validate_group(pairs: Sequence[StatePair]) -> tuple[tuple[str, str, str], dict[int, StatePair]]:
        if not pairs:
            raise ValueError("detector evaluation group is empty")
        family = pairs[0].family_key
        if any(pair.family_key != family or pair.split != "test" for pair in pairs):
            raise ValueError("detector evaluation needs one test family group")
        if any(pair.dataset_group_identity != pairs[0].dataset_group_identity for pair in pairs):
            raise ValueError("detector evaluation needs one explicit dataset group")
        if any(
            pair.total_units != pairs[0].total_units or not np.array_equal(pair.positions, pairs[0].positions)
            for pair in pairs
        ):
            raise ValueError("detector evaluation requires aligned positions across layers")
        return family, {pair.layer_id: pair for pair in pairs}

    def evaluate_exact(self, pairs: Sequence[StatePair]) -> DetectorEvaluation:
        family, by_layer = self._validate_group(pairs)
        detector_ids = self.detectors.get(family)
        if not detector_ids:
            raise KeyError(f"no detector layers for family {family}")
        missing = [layer for layer in detector_ids if layer not in by_layer]
        if missing:
            raise ValueError(f"test group lacks detector layers {missing}")
        detector_energy = np.stack(
            [np.sum(by_layer[layer].residual.astype(float) ** 2, axis=1) for layer in detector_ids]
        ).mean(axis=0)
        return self._evaluate_with_scores(pairs, detector_ids, detector_energy, exact_detectors=True)

    def evaluate(self, pairs: Sequence[StatePair]) -> DetectorEvaluation:
        """Compatibility alias with explicit v2 exact-recompute semantics."""
        return self.evaluate_exact(pairs)

    def evaluate_external(self, pairs: Sequence[StatePair], provider: DetectorScoreProvider) -> DetectorEvaluation:
        family, _ = self._validate_group(pairs)
        detector_ids = self.detectors.get(family)
        if not detector_ids:
            raise KeyError(f"no detector layers for family {family}")
        scores = np.asarray(provider.position_scores(pairs, detector_ids), dtype=float)
        if scores.shape != (pairs[0].total_units,) or not np.isfinite(scores).all():
            raise ValueError("external detector provider must return finite [T] scores")
        return self._evaluate_with_scores(pairs, detector_ids, scores, exact_detectors=False)

    def _evaluate_with_scores(self, pairs, detector_ids, scores, exact_detectors):
        count = budget_units(pairs[0].total_units, self.budget)
        order = sorted(
            range(len(scores)),
            key=lambda row: (-float(scores[row]), _tie_hash(self.config_seed, (pairs[0].sample_identity, float(pairs[0].positions[row])))),
        )
        predicted = np.asarray(order[:count], dtype=int)
        all_energy = np.stack([np.sum(pair.residual.astype(float) ** 2, axis=1) for pair in pairs])
        position_energy = all_energy.sum(axis=0)
        oracle_positions = np.asarray(
            sorted(
                range(len(position_energy)),
                key=lambda row: (-float(position_energy[row]), _tie_hash(self.config_seed, ("oracle", pairs[0].sample_identity, float(pairs[0].positions[row])))),
            )[:count],
            dtype=int,
        )
        recall = len(set(predicted.tolist()) & set(oracle_positions.tolist())) / max(1, count)
        selected_energy = 0.0
        corrected = {}
        costs = {}
        for layer_index, pair in enumerate(pairs):
            state = pair.a.copy()
            if exact_detectors and pair.layer_id in detector_ids:
                state[:] = pair.b
                selected_energy += float(all_energy[layer_index].sum())
                cost = MethodCost(detector_recomputed_units=pair.total_units, total_units=pair.total_units, detector_layers=len(detector_ids))
            else:
                state[predicted] = pair.b[predicted]
                selected_energy += float(all_energy[layer_index, predicted].sum())
                cost = MethodCost(exact_repaired_units=len(predicted), total_units=pair.total_units, detector_layers=len(detector_ids))
            corrected[pair.state_identity] = state
            costs[pair.state_identity] = cost
        total_energy = float(all_energy.sum())
        oracle_unit_count = min(
            all_energy.size,
            len(detector_ids) * pairs[0].total_units + (len(pairs) - len(detector_ids)) * count
            if exact_detectors
            else count * len(pairs),
        )
        oracle_energy = float(np.sort(all_energy.ravel())[::-1][:oracle_unit_count].sum())
        return DetectorEvaluation(
            detector_layer_ids=list(detector_ids),
            selected_positions=predicted.tolist(),
            top_k_position_recall=recall,
            oracle_unit_energy_coverage=selected_energy / oracle_energy if oracle_energy else float("nan"),
            repair_energy_explained=selected_energy / total_energy if total_energy else float("nan"),
            corrected=corrected,
            costs=costs,
        )


class DetectorLayerCorrection(CorrectionMethod):
    name = "detector_layer_selector"
    category = MethodCategory.OFFLINE_TRAINABLE

    def __init__(
        self,
        detector_count: int,
        budget: float,
        selection="train_selected",
        fixed_ids=None,
        mode: str = "exact_recompute",
        score_provider: DetectorScoreProvider | None = None,
        config_seed: int = 0,
    ):
        if mode not in {"exact_recompute", "external_score"}:
            raise ValueError("detector mode must be exact_recompute or external_score")
        self.selector = DetectorLayerSelector(detector_count, budget, selection, fixed_ids, config_seed)
        self.detector_count = int(detector_count)
        self.budget = float(budget)
        self.selection = selection
        self.mode = mode
        self.score_provider = score_provider
        self.config_seed = int(config_seed)
        self.variant = f"detector/{mode}"
        self.capabilities = MethodCapabilities(
            needs_fit=True,
            uses_probe_target=mode == "exact_recompute",
            uses_repair_target=True,
        )
        super().__init__()

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        self.selector.fit(pairs, context)
        self.freeze()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        raise RuntimeError("DetectorLayerCorrection requires aligned multi-layer group evaluation")

    def apply_group(self, pairs: Sequence[StatePair]) -> dict[tuple[object, ...], CorrectionResult]:
        if self.mode == "external_score":
            if self.score_provider is None:
                raise CapabilityMissingError(["detector_score_provider"])
            evaluation = self.selector.evaluate_external(pairs, self.score_provider)
        else:
            evaluation = self.selector.evaluate_exact(pairs)
        diagnostics = {
            "detector_mode": self.mode,
            "detector_layer_ids": evaluation.detector_layer_ids,
            "selected_positions": evaluation.selected_positions,
            "top_k_position_recall": evaluation.top_k_position_recall,
            "oracle_unit_energy_coverage": evaluation.oracle_unit_energy_coverage,
            "repair_energy_explained": evaluation.repair_energy_explained,
            "requested_budget": self.budget,
            "unit_budget": budget_units(pairs[0].total_units, self.budget),
            "actual_budget": budget_units(pairs[0].total_units, self.budget) / pairs[0].total_units,
        }
        return {
            pair.state_identity: CorrectionResult(
                evaluation.corrected[pair.state_identity], evaluation.costs[pair.state_identity], diagnostics
            )
            for pair in pairs
        }

    def output_parameters(self):
        return {
            "detector_count": self.detector_count,
            "requested_budget": self.budget,
            "budget": self.budget,
            "detector_selection": self.selection,
            "detector_mode": self.mode,
            "config_seed": self.config_seed,
        }
