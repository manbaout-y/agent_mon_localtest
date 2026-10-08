from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
import json
from collections import defaultdict
from hashlib import sha256

import numpy as np

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.types import StatePair
from kvreuse_b5.methods.base import CapabilityMissingError, EvalContext, FitContext
from kvreuse_b5.methods.registry import MethodRegistry, default_registry
from kvreuse_b5.metrics.aggregate import aggregate_records
from kvreuse_b5.metrics.residual import compute_residual_metrics
from kvreuse_b5.observability import ObservabilityWriter, TraceCollector
from kvreuse_b5.observability.method_adapter import observe_method_record
from .artifacts import ArtifactStore
from .checkpoint import CheckpointStore, atomic_write_json
from .config import ExperimentConfig, canonical_hash
from .invariants import validate_artifact_audit, validate_result_invariants, validate_sample_costs

FRAMEWORK_VERSION = "2.0.1"


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _dataset_fingerprint(dataset: StatePairDataset) -> str:
    digest = sha256()
    for pair in sorted(dataset.pairs, key=lambda item: repr(item.state_identity)):
        digest.update(repr((pair.state_identity, pair.split, pair.a.shape, pair.positions.shape)).encode())
        digest.update(pair.a.tobytes())
        digest.update(pair.b.tobytes())
        digest.update(pair.positions.tobytes())
        digest.update(json.dumps(_jsonable(pair.metadata), sort_keys=True, default=repr).encode())
    return digest.hexdigest()


class ExperimentRunner:
    def __init__(
        self,
        config: ExperimentConfig,
        registry: MethodRegistry | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.config = config
        self.artifact_store = artifact_store or ArtifactStore()
        self.registry = registry or default_registry(self.artifact_store)

    def run(self, dataset: StatePairDataset | Sequence[StatePair], output_dir: str | Path | None = None):
        dataset = dataset if isinstance(dataset, StatePairDataset) else StatePairDataset(list(dataset))
        train, test = dataset.split()
        methods = self.registry.build_all(self.config.methods)
        output = Path(output_dir or self.config.output_dir)
        observer = TraceCollector.from_env(output / "observability")
        dataset_hash = _dataset_fingerprint(dataset)
        checkpoint_store = CheckpointStore(output / "checkpoints", self.config.config_hash, dataset_hash)
        method_parameters = [_jsonable(dict(method.output_parameters())) for method in methods]
        method_config_hashes = [
            canonical_hash(
                {
                    "method": method.name,
                    "variant": method.variant,
                    "parameters": parameters,
                }
            )
            for method, parameters in zip(methods, method_parameters)
        ]
        method_keys = [
            canonical_hash(
                {
                    "method_config_hash": method_config_hash,
                    "config_hash": self.config.config_hash,
                    "dataset_hash": dataset_hash,
                    "framework_version": FRAMEWORK_VERSION,
                }
            )
            for method_config_hash in method_config_hashes
        ]
        if len(set(method_keys)) != len(method_keys):
            raise ValueError("config expands to duplicate method/variant parameter combinations")
        cached_records = {
            (method_index, pair.state_identity): checkpoint_store.load(pair.state_identity, method_keys[method_index])
            for method_index in range(len(methods))
            for pair in test
        }
        active_indices = {
            method_index
            for method_index in range(len(methods))
            if any(cached_records[(method_index, pair.state_identity)] is None for pair in test)
        }
        fit_context = FitContext(
            frozenset(pair.state_identity for pair in train), config_hash=self.config.config_hash
        )
        self.artifact_store.prepare_methods(
            [method for index, method in enumerate(methods) if index in active_indices], train, fit_context
        )
        validate_artifact_audit(self.artifact_store, {pair.state_identity for pair in test})

        fit_errors: dict[int, Exception] = {}
        for method_index, method in enumerate(methods):
            if method_index not in active_indices:
                continue
            if method.capabilities.needs_fit:
                try:
                    method.fit(train, fit_context)
                    method.freeze()
                except Exception as exc:
                    fit_errors[method_index] = exc

        group_results: dict[int, dict[tuple[object, ...], Any]] = {}
        for method_index, method in enumerate(methods):
            if method_index not in active_indices or not hasattr(method, "apply_group") or method_index in fit_errors:
                continue
            grouped: dict[tuple[Any, ...], list[StatePair]] = {}
            for pair in test:
                grouped.setdefault(pair.dataset_group_identity, []).append(pair)
            outcomes: dict[tuple[object, ...], Any] = {}
            for group in grouped.values():
                try:
                    outcomes.update(method.apply_group(group))
                except Exception as exc:
                    for pair in group:
                        outcomes[pair.state_identity] = exc
            group_results[method_index] = outcomes

        records: list[dict[str, Any]] = []
        for pair in test:
            for method_index, method in enumerate(methods):
                parameters = method_parameters[method_index]
                method_key = method_keys[method_index]
                cached = cached_records[(method_index, pair.state_identity)]
                if cached is not None:
                    records.append(cached)
                    observe_method_record(
                        observer, pair, method, method_config_hashes[method_index], cached
                    )
                    continue
                base = {
                    **self._base_record(pair, method, parameters),
                    "method_config_hash": method_config_hashes[method_index],
                    "checkpoint_key": method_key,
                    "dataset_hash": dataset_hash,
                }
                corrected_state = None
                try:
                    if method_index in fit_errors:
                        raise fit_errors[method_index]
                    if method_index in group_results:
                        correction = group_results[method_index][pair.state_identity]
                        if isinstance(correction, Exception):
                            raise correction
                    else:
                        correction = method.apply(pair, EvalContext())
                    metrics = compute_residual_metrics(
                        pair.a,
                        pair.b,
                        correction.corrected,
                        correction.corrected_sse_override,
                        correction.corrected_sse_multiplier,
                    )
                    corrected_state = correction.corrected
                    diagnostics = _jsonable(correction.diagnostics)
                    actual_budget = diagnostics.get(
                        "actual_budget",
                        correction.cost.exact_compute_units / pair.total_units if pair.total_units else 0.0,
                    )
                    record = {
                        **base,
                        "baseline_sse": metrics.baseline_sse,
                        "corrected_sse": metrics.corrected_sse,
                        "energy_explained": metrics.energy_explained,
                        "unit_budget": diagnostics.get("unit_budget"),
                        "actual_budget": actual_budget if parameters.get("requested_budget") is not None else None,
                        "budget_overshoot": diagnostics.get("budget_overshoot"),
                        "observed_probe_units": correction.cost.observed_probe_units,
                        "detector_recomputed_units": correction.cost.detector_recomputed_units,
                        "exact_repaired_units": correction.cost.exact_repaired_units,
                        "exact_compute_units": correction.cost.exact_compute_units,
                        "observed_units": correction.cost.observed_units,
                        "total_units": correction.cost.total_units,
                        "logical_repair_fraction": correction.cost.logical_repair_fraction,
                        "basis_rank": correction.cost.basis_rank,
                        "detector_layers": correction.cost.detector_layers,
                        "method_specific_cost": _jsonable(correction.cost.method_specific_cost),
                        "artifact_hash": correction.artifact_hash,
                        "status": metrics.status,
                        "diagnostics": diagnostics,
                    }
                except CapabilityMissingError as exc:
                    record = {**base, "status": "CAPABILITY_MISSING", "error": str(exc)}
                except Exception as exc:
                    record = {**base, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
                checkpoint_store.save(pair.state_identity, method_key, record)
                records.append(record)
                observe_method_record(
                    observer, pair, method, method_config_hashes[method_index], record,
                    corrected=corrected_state,
                )

        sample_costs = self._sample_cost_records(records)
        degradation_policy = str(self.config.experiment.get("zero_baseline_degradation_policy", "error"))
        validate_result_invariants(records, zero_baseline_degradation_policy=degradation_policy)
        validate_sample_costs(sample_costs)
        summary = {"version": FRAMEWORK_VERSION, **aggregate_records(records)}
        atomic_write_json(output / "results.json", records)
        atomic_write_json(output / "summary.json", summary)
        atomic_write_json(output / "sample_costs.json", sample_costs)
        artifact_path = output / "artifacts.json"
        if active_indices or not artifact_path.exists():
            atomic_write_json(artifact_path, self.artifact_store.audit_manifest())
        with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, allow_nan=True, sort_keys=True) + "\n")
        ObservabilityWriter(observer.trace_dir or (output / "observability")).write(observer)
        return records, summary

    def _sample_cost_records(self, result_records):
        grouped = defaultdict(list)
        for row in result_records:
            if row["status"] not in {"OK", "ZERO_RESIDUAL", "ZERO_BASELINE_DEGRADED"}:
                continue
            key = (
                tuple(row["dataset_group_identity"]), row["method"], row["variant"], row["method_config_hash"],
                row["rank"], row["requested_budget"], row["probe_count"], row["anchor_count"], row["detector_count"],
            )
            grouped[key].append(row)
        output = []
        for key, rows in grouped.items():
            detector_units = sum(row["detector_recomputed_units"] for row in rows)
            repair_units = sum(row["exact_repaired_units"] for row in rows)
            output.append(
                {
                    "dataset_group_identity": list(key[0]),
                    "sample_identity": rows[0]["sample_identity"],
                    "method": key[1], "variant": key[2], "method_config_hash": key[3],
                    "rank": key[4], "requested_budget": key[5],
                    "probe_count": key[6], "anchor_count": key[7], "detector_count": key[8],
                    "observed_probe_units": sum(row["observed_probe_units"] for row in rows),
                    "detector_recomputed_units": detector_units,
                    "additional_repair_units": repair_units,
                    "total_corrected_units": detector_units + repair_units,
                    "total_units": sum(row["total_units"] for row in rows),
                    "config_hash": self.config.config_hash,
                    "dataset_hash": rows[0]["dataset_hash"],
                    "version": FRAMEWORK_VERSION,
                }
            )
        return output

    def _base_record(self, pair, method, parameters):
        deployable_like = (
            not method.capabilities.oracle
            and not method.capabilities.uses_full_target
            and method.capabilities.executable_offline
            and method.category.value != "not-offline-executable"
        )
        return {
            "version": FRAMEWORK_VERSION,
            "model_family": pair.model_family,
            "condition": pair.condition,
            "state_kind": pair.state_kind,
            "pair_id": pair.pair_id,
            "layer_id": pair.layer_id,
            "state_identity": _jsonable(pair.state_identity),
            "sample_identity": _jsonable(pair.sample_identity),
            "dataset_group_identity": _jsonable(pair.dataset_group_identity),
            "split": pair.split,
            "method": method.name,
            "variant": method.variant,
            "rank": parameters.get("rank"),
            "requested_budget": parameters.get("requested_budget", parameters.get("budget")),
            "budget": parameters.get("requested_budget", parameters.get("budget")),
            "actual_budget": None,
            "unit_budget": None,
            "budget_overshoot": None,
            "probe_count": parameters.get("probe_count"),
            "anchor_count": parameters.get("anchor_count"),
            "detector_count": parameters.get("detector_count"),
            "oracle": method.capabilities.oracle,
            "deployable_like": deployable_like,
            "category": method.category.value,
            "capabilities": _jsonable(method.capabilities.__dict__),
            "config_hash": self.config.config_hash,
            "artifact_hash": None,
            "baseline_sse": None,
            "corrected_sse": None,
            "energy_explained": None,
            "observed_probe_units": None,
            "detector_recomputed_units": None,
            "exact_repaired_units": None,
            "exact_compute_units": None,
            "observed_units": None,
            "total_units": pair.total_units,
            "logical_repair_fraction": None,
            "basis_rank": None,
            "detector_layers": None,
            "method_specific_cost": {},
            "diagnostics": {},
        }
