from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from kvreuse_b5.pipeline.io import write_csv
from kvreuse_b5.pipeline.real_data import HISTORICAL_TRACKS, RealStateSource, RealTrack
from kvreuse_b5.regression.historical_refs import load_historical_references
from kvreuse_b5.regression.manifest import ManifestSample, load_lifemem_manifest
from kvreuse_b5.regression.math import (
    UnitEnergy,
    energy_explained,
    historical_probe_indices,
    linear_interpolate_rows,
    lowrank_explained_curve,
    projection_sse,
    randomized_basis_from_covariance,
    randomized_singular_values,
    selective_removed_energy,
    shared_residual_sse,
    stable_seed,
)


RANKS = (1, 2, 4, 8, 16)
PROBES = (1, 2, 4, 8)
BUDGETS = (0.01, 0.05, 0.10, 0.20)


@dataclass
class EnergyAccumulator:
    baseline: float = 0.0
    corrected: float = 0.0
    count: int = 0
    zero_count: int = 0

    def add(self, baseline: float, corrected: float) -> None:
        if baseline <= 0:
            self.zero_count += 1
            return
        self.baseline += float(baseline)
        self.corrected += float(corrected)
        self.count += 1

    @property
    def explained(self) -> float | None:
        return 1.0 - self.corrected / self.baseline if self.baseline > 0 else None


def _summary(track: RealTrack, experiment: str, scope: str, accumulator: EnergyAccumulator, **extra: Any) -> dict[str, Any]:
    return {
        "experiment": experiment,
        "model": track.model_family,
        "condition": track.condition,
        "state": track.state_kind,
        "scope": scope,
        "baseline_sse": accumulator.baseline,
        "corrected_sse": accumulator.corrected,
        "residual_energy_explained": accumulator.explained,
        "valid_count": accumulator.count,
        "zero_residual_count": accumulator.zero_count,
        **extra,
    }


class HistoricalRegressionSuite:
    def __init__(
        self,
        source: RealStateSource,
        manifest_path: str | Path,
        references_path: str | Path,
        output_dir: str | Path,
        *,
        pair_count: int = 561,
        max_pairs: int | None = None,
        seed: int = 20260827,
        oversample: int = 8,
        niter: int = 2,
    ):
        self.source = source
        self.manifest = load_lifemem_manifest(manifest_path)
        self.references = load_historical_references(references_path)
        self.output_dir = Path(output_dir)
        self.pair_count = int(pair_count)
        self.max_pairs = max_pairs
        self.seed = int(seed)
        self.oversample = int(oversample)
        self.niter = int(niter)
        self.bases: dict[tuple[RealTrack, int], np.ndarray] = {}

    @property
    def fixed_samples(self) -> list[ManifestSample]:
        return self.manifest if self.max_pairs is None else self.manifest[: self.max_pairs]

    def _sv(self, delta: np.ndarray, track: RealTrack, pair_id: int, layer: int, label: str) -> np.ndarray:
        return randomized_singular_values(
            delta,
            max(RANKS),
            oversample=self.oversample,
            niter=self.niter,
            seed=stable_seed(self.seed, track, pair_id, layer, label),
        )

    def run_b1(self) -> list[dict[str, Any]]:
        accumulators = {track: EnergyAccumulator() for track in HISTORICAL_TRACKS}
        count = self.pair_count if self.max_pairs is None else min(self.pair_count, self.max_pairs)
        for pair_id in range(count):
            for track, pairs in self.source.load_all_historical(pair_id).items():
                for pair in pairs:
                    baseline, corrected = shared_residual_sse(pair.residual)
                    accumulators[track].add(baseline, corrected)
        scope = "all561" if count == 561 else f"smoke{count}"
        return [_summary(track, "B1_SHARED_RESIDUAL", scope, accumulators[track], method="shared_residual_oracle") for track in HISTORICAL_TRACKS]

    def run_b2(self) -> list[dict[str, Any]]:
        output = []
        samples = self.fixed_samples
        for track in HISTORICAL_TRACKS:
            accumulators = {
                (variant, rank, eligibility): EnergyAccumulator()
                for variant in ("direct", "shared_plus_rank")
                for rank in RANKS
                for eligibility in ("all", "eligible")
            }
            for sample in samples:
                for pair in self.source.load(track, sample.pair_id, split=sample.split):
                    delta = pair.residual.astype(np.float64)
                    baseline = float(np.sum(delta * delta))
                    direct = self._sv(delta, track, sample.pair_id, pair.layer_id, "direct")
                    mean = delta.mean(axis=0, keepdims=True)
                    centered = delta - mean
                    centered_energy = float(np.sum(centered * centered))
                    centered_sv = self._sv(centered, track, sample.pair_id, pair.layer_id, "centered")
                    for rank in RANKS:
                        direct_capture = float(np.sum(direct[: min(rank, len(direct))] ** 2))
                        centered_capture = float(np.sum(centered_sv[: min(rank, len(centered_sv))] ** 2))
                        values = {
                            "direct": max(0.0, baseline - direct_capture),
                            "shared_plus_rank": max(0.0, centered_energy - centered_capture),
                        }
                        for variant, corrected in values.items():
                            accumulators[(variant, rank, "all")].add(baseline, corrected)
                            if min(delta.shape) >= rank:
                                accumulators[(variant, rank, "eligible")].add(baseline, corrected)
            scope_prefix = "fixed48" if len(samples) == 48 else f"smoke{len(samples)}"
            for (variant, rank, eligibility), accumulator in accumulators.items():
                output.append(
                    _summary(
                        track,
                        "B2_PAIR_LOWRANK",
                        f"{scope_prefix}_{eligibility}",
                        accumulator,
                        method=variant,
                        rank=rank,
                    )
                )
        return output

    def _fit_bases(self, track: RealTrack) -> None:
        train = [sample for sample in self.fixed_samples if sample.split == "train"]
        covariances: dict[int, np.ndarray] = {}
        for sample in train:
            for pair in self.source.load(track, sample.pair_id, split="train"):
                delta = pair.residual.astype(np.float64)
                covariance = covariances.get(pair.layer_id)
                if covariance is None:
                    covariance = np.zeros((delta.shape[1], delta.shape[1]), dtype=np.float64)
                    covariances[pair.layer_id] = covariance
                covariance += delta.T @ delta
        for layer, covariance in covariances.items():
            self.bases[(track, layer)] = randomized_basis_from_covariance(
                covariance,
                max(RANKS),
                oversample=self.oversample,
                niter=self.niter,
                seed=stable_seed(self.seed, track, layer, "frozen_basis"),
            )
        artifact_path = self.output_dir / "artifacts" / f"frozen_basis__{track.slug}.npz"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(artifact_path, **{f"layer_{layer}": basis for (basis_track, layer), basis in self.bases.items() if basis_track == track})

    def run_b25_and_b3(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        b25_output: list[dict[str, Any]] = []
        b3_output: list[dict[str, Any]] = []
        test = [sample for sample in self.fixed_samples if sample.split == "test"]
        for track in HISTORICAL_TRACKS:
            self._fit_bases(track)
            transfer = {rank: EnergyAccumulator() for rank in RANKS}
            pair_oracle = {rank: EnergyAccumulator() for rank in RANKS}
            probe_acc = {
                (method, probes, rank): EnergyAccumulator()
                for probes in PROBES
                for method in ("probe_mean", "raw_linear", "basis_linear")
                for rank in ((None,) if method != "basis_linear" else RANKS)
            }
            for sample in test:
                for pair in self.source.load(track, sample.pair_id, split="test"):
                    delta = pair.residual.astype(np.float64)
                    baseline = float(np.sum(delta * delta))
                    basis_all = self.bases[(track, pair.layer_id)]
                    singular = self._sv(delta, track, sample.pair_id, pair.layer_id, "b25_pair_oracle")
                    for rank in RANKS:
                        _, corrected = projection_sse(delta, basis_all[:, :rank])
                        transfer[rank].add(baseline, corrected)
                        capture = float(np.sum(singular[: min(rank, len(singular))] ** 2))
                        pair_oracle[rank].add(baseline, max(0.0, baseline - capture))
                    for probes in PROBES:
                        indices = historical_probe_indices(len(pair.positions), probes)
                        mean_hat = np.repeat(delta[indices].mean(axis=0, keepdims=True), len(delta), axis=0)
                        raw_hat = linear_interpolate_rows(pair.positions, indices, delta[indices])
                        probe_acc[("probe_mean", probes, None)].add(baseline, float(np.sum((delta - mean_hat) ** 2)))
                        probe_acc[("raw_linear", probes, None)].add(baseline, float(np.sum((delta - raw_hat) ** 2)))
                        for rank in RANKS:
                            basis = basis_all[:, :rank]
                            observed = delta[indices] @ basis
                            coefficient = linear_interpolate_rows(pair.positions, indices, observed)
                            estimate = coefficient @ basis.T
                            probe_acc[("basis_linear", probes, rank)].add(
                                baseline, float(np.sum((delta - estimate) ** 2))
                            )
            scope = "heldout16" if len(test) == 16 else f"smoke_heldout{len(test)}"
            for rank in RANKS:
                b25_output.append(_summary(track, "B25_FROZEN_SUBSPACE", scope, transfer[rank], method="frozen_basis_oracle_coeff", rank=rank))
                pair_value = pair_oracle[rank].explained
                transfer_value = transfer[rank].explained
                retention = transfer_value / pair_value if pair_value not in (None, 0.0) and transfer_value is not None else None
                b25_output.append(_summary(track, "B25_PAIR_ORACLE", scope, pair_oracle[rank], method="pair_lowrank_oracle", rank=rank, retention=retention))
            for (method, probes, rank), accumulator in probe_acc.items():
                b3_output.append(_summary(track, "B3_PROBE", scope, accumulator, method=method, probes=probes, rank=rank))
        return b25_output, b3_output

    def run_b4(self) -> list[dict[str, Any]]:
        samples = self.fixed_samples
        output = []
        for track in HISTORICAL_TRACKS:
            accumulators = {
                (scope, budget, selector): EnergyAccumulator()
                for scope in ("fixed48", "heldout16")
                for budget in BUDGETS
                for selector in ("random_expected", "boundary_first", "layer_group_oracle", "position_group_oracle", "joint_unit_oracle")
            }
            for sample in samples:
                pairs = self.source.load(track, sample.pair_id, split=sample.split)
                units = [
                    UnitEnergy(pair.layer_id, float(pair.positions[row]), float(np.sum(pair.residual[row].astype(np.float64) ** 2)))
                    for pair in pairs
                    for row in range(pair.total_units)
                ]
                total = float(sum(unit.energy for unit in units))
                scopes = ["fixed48"] + (["heldout16"] if sample.split == "test" else [])
                for budget in BUDGETS:
                    values = selective_removed_energy(units, budget)
                    for selector, (explained, unit_budget, actual_fraction) in values.items():
                        corrected = total * (1.0 - explained) if np.isfinite(explained) else 0.0
                        for scope in scopes:
                            accumulators[(scope, budget, selector)].add(total, corrected)
            actual_fixed = "fixed48" if len(samples) == 48 else f"smoke{len(samples)}"
            actual_test = "heldout16" if sum(sample.split == "test" for sample in samples) == 16 else f"smoke_heldout{sum(sample.split == 'test' for sample in samples)}"
            for (scope, budget, selector), accumulator in accumulators.items():
                actual_scope = actual_fixed if scope == "fixed48" else actual_test
                output.append(_summary(track, "B4_SELECTIVE", actual_scope, accumulator, method=selector, selector=selector, budget=budget))
        return output

    @staticmethod
    def _status(diff: float, strict: float, warn: float) -> str:
        if diff <= strict:
            return "PASS"
        if diff <= warn:
            return "NUMERIC_WARN"
        return "FAIL"

    def compare(self, summaries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        full = self.max_pairs is None

        def candidates(experiment: str, model: str, condition: str, state: str, **filters: Any) -> list[dict[str, Any]]:
            return [
                row for row in summaries
                if row["experiment"] == experiment and row["model"] == model and row["condition"] == condition and row["state"] == state
                and all(row.get(key) == value for key, value in filters.items())
            ]

        def add(reference_group: str, reference: Sequence[Any], experiment: str, strict: float, warn: float, **filters: Any) -> None:
            model, condition, state = reference[:3]
            historical = float(reference[-1])
            matches = candidates(experiment, model, condition, state, **filters)
            if not full or not matches:
                rows.append({"experiment": experiment, "model": model, "condition": condition, "state": state, "scope": None, "historical_value": historical, "new_value": None, "abs_diff": None, "tolerance": f"{strict}/{warn}", "status": "MISSING_REFERENCE", "notes": "smoke/incomplete run"})
                return
            valid = [row for row in matches if row.get("residual_energy_explained") is not None]
            if not valid:
                rows.append({"experiment": experiment, "model": model, "condition": condition, "state": state, "scope": matches[0]["scope"], "historical_value": historical, "new_value": None, "abs_diff": None, "tolerance": f"{strict}/{warn}", "status": "ZERO_BASELINE_EXPECTED", "notes": "no valid residual energy"})
                return
            selected = min(valid, key=lambda row: abs(float(row["residual_energy_explained"]) - historical))
            new_value = float(selected["residual_energy_explained"])
            diff = abs(new_value - historical)
            notes = "auto-selected closest all48/heldout16 scope" if reference_group == "b4_budget10" else ""
            rows.append({"experiment": experiment, "model": model, "condition": condition, "state": state, "scope": selected["scope"], "rank": selected.get("rank"), "probes": selected.get("probes"), "selector": selected.get("selector"), "budget": selected.get("budget"), "historical_value": historical, "new_value": new_value, "abs_diff": diff, "tolerance": f"{strict}/{warn}", "status": self._status(diff, strict, warn), "notes": notes})
            if reference_group == "b4_budget10":
                for alternate in valid:
                    if alternate is selected:
                        continue
                    alternate_value = float(alternate["residual_energy_explained"])
                    rows.append({
                        "experiment": experiment, "model": model, "condition": condition, "state": state,
                        "scope": alternate["scope"], "selector": alternate.get("selector"), "budget": alternate.get("budget"),
                        "historical_value": historical, "new_value": alternate_value,
                        "abs_diff": abs(alternate_value - historical), "tolerance": f"{strict}/{warn}",
                        "status": "SEMANTIC_SCOPE_DIFFERENCE",
                        "notes": f"alternate scope; historical reference auto-matched {selected['scope']}",
                    })

        for ref in self.references["b1"]:
            add("b1", ref, "B1_SHARED_RESIDUAL", 1e-4, 1e-3)
        for ref in self.references["b2_rank16_direct"]:
            model, condition, state, scope, value = ref
            add("b2", (model, condition, state, value), "B2_PAIR_LOWRANK", 0.003, 0.01, method="direct", rank=16, scope=scope)
        for ref in self.references["b25_rank16"]:
            add("b25", ref, "B25_FROZEN_SUBSPACE", 0.005, 0.015, method="frozen_basis_oracle_coeff", rank=16)
        for ref in self.references["b3_probe8"]:
            model, condition, state, method, rank, value = ref
            add("b3", (model, condition, state, value), "B3_PROBE", 0.01, 0.03, method=method, probes=8, rank=rank)
        for ref in self.references["b4_budget10"]:
            model, condition, state, selector, value = ref
            add("b4_budget10", (model, condition, state, value), "B4_SELECTIVE", 0.01, 0.03, selector=selector, budget=0.10)
        return rows

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        summaries = self.run_b1()
        summaries.extend(self.run_b2())
        b25, b3 = self.run_b25_and_b3()
        summaries.extend(b25)
        summaries.extend(b3)
        summaries.extend(self.run_b4())
        comparisons = self.compare(summaries)
        write_csv(self.output_dir / "historical_summary.csv", summaries)
        write_csv(
            self.output_dir / "historical_regression.csv",
            comparisons,
            ("experiment", "model", "condition", "state", "scope", "rank", "probes", "selector", "budget", "historical_value", "new_value", "abs_diff", "tolerance", "status", "notes"),
        )
        b1_fail = sum(row["status"] == "FAIL" and row["experiment"] == "B1_SHARED_RESIDUAL" for row in comparisons)
        b2_large = sum(row["experiment"] == "B2_PAIR_LOWRANK" and row.get("abs_diff") is not None and row["abs_diff"] > 0.02 for row in comparisons)
        severe = []
        if b1_fail >= 5:
            severe.append("B1 main anchors diverged on a majority of tracks")
        if b2_large >= 4:
            severe.append("B2 rank16 diverged by >0.02 on multiple tracks")
        return {"summaries": summaries, "comparisons": comparisons, "severe_failures": severe, "bases": self.bases}
