from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable
import math

from kvreuse_b5.execution.artifacts import ArtifactStore
from kvreuse_b5.metrics.aggregate import aggregate_records


def validate_artifact_audit(store: ArtifactStore, test_ids: set[tuple[object, ...]]) -> None:
    for artifact in store.frozen_subspaces.values():
        overlap = artifact.fit_pair_ids & test_ids
        if overlap:
            raise AssertionError(f"frozen artifact contains test state identities: {sorted(overlap, key=repr)}")
        if artifact.basis.shape[0] != artifact.feature_dim:
            raise AssertionError("frozen artifact feature_dim mismatch")


def validate_sample_costs(sample_costs: Iterable[dict[str, Any]]) -> None:
    seen = set()
    for row in sample_costs:
        key = (
            tuple(row["dataset_group_identity"]),
            row["method"],
            row["variant"],
            row["method_config_hash"],
            row["rank"],
            row["requested_budget"],
            row["probe_count"],
            row["anchor_count"],
            row["detector_count"],
        )
        if key in seen:
            raise AssertionError(f"duplicate sample cost record {key}")
        seen.add(key)
        if row["total_corrected_units"] != row["detector_recomputed_units"] + row["additional_repair_units"]:
            raise AssertionError("sample detector/repair cost double-count mismatch")


def validate_result_invariants(
    records: Iterable[dict[str, Any]],
    tolerance: float = 1e-8,
    zero_baseline_degradation_policy: str = "error",
) -> None:
    records = list(records)
    if zero_baseline_degradation_policy not in {"error", "record"}:
        raise ValueError("zero_baseline_degradation_policy must be error or record")
    degraded = [row for row in records if row["status"] == "ZERO_BASELINE_DEGRADED"]
    if degraded and zero_baseline_degradation_policy == "error":
        identities = [row["state_identity"] for row in degraded]
        raise AssertionError(f"zero baseline correction degradation detected for states {identities}")
    for row in records:
        expected_identity = [
            row["model_family"],
            row["condition"],
            row["state_kind"],
            row["pair_id"],
            row["layer_id"],
        ]
        if row["state_identity"] != expected_identity:
            raise AssertionError("result state_identity does not match its dimensions")
        if row["status"] != "OK":
            continue
        if row["method"] == "no_correction" and abs(row["energy_explained"]) > tolerance:
            raise AssertionError("no_correction explained != 0")
        diagnostics = row.get("diagnostics", {})
        if row["requested_budget"] is not None and "group_total_units" in diagnostics:
            expected = budget = 0 if row["requested_budget"] == 0 else max(
                1, math.ceil(diagnostics["group_total_units"] * row["requested_budget"])
            )
            if diagnostics["unit_budget"] != expected:
                raise AssertionError(f"selector unit budget mismatch for {row['method']}")
            actual_units = round(diagnostics["actual_budget"] * diagnostics["group_total_units"])
            if diagnostics.get("selector_granularity") == "neutral_partial" and actual_units != budget:
                raise AssertionError("neutral selector did not use exact row budget")
            if diagnostics.get("selector_granularity") == "group_atomic" and actual_units < budget:
                raise AssertionError("group-atomic selector undershot requested budget")
        if row["requested_budget"] == 1.0 and row["method"] != "detector_layer_selector":
            if row["corrected_sse"] > tolerance * max(1.0, row["baseline_sse"]):
                raise AssertionError("full selective repair did not reach zero SSE")

    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row["status"] == "OK":
            grouped[tuple(row["state_identity"])].append(row)
    for rows in grouped.values():
        pair_oracle = {row["rank"]: row for row in rows if row["method"] == "pair_lowrank_oracle"}
        frozen = {row["rank"]: row for row in rows if row["method"] == "frozen_lowrank_oracle"}
        for rank in pair_oracle.keys() & frozen.keys():
            if pair_oracle[rank]["corrected_sse"] > frozen[rank]["corrected_sse"] + tolerance:
                raise AssertionError("pair-specific low-rank oracle worse than frozen projection")
        for probe in (row for row in rows if row["method"] == "probe_frozen_basis"):
            upper = frozen.get(probe["rank"])
            if upper and probe["corrected_sse"] + tolerance < upper["corrected_sse"]:
                raise AssertionError("probe correction exceeded frozen full-target upper bound")

    # Aggregation itself is a preflight invariant: all canonical dimensions must exist and remain separate.
    summary = aggregate_records(records)["global_summary"]
    raw_keys = {
        (
            row["model_family"], row["condition"], row["state_kind"], row["method"], row["variant"],
            row["method_config_hash"], row["rank"], row["requested_budget"], row["probe_count"],
            row["anchor_count"], row["detector_count"],
        )
        for row in records
    }
    if len(summary) != len(raw_keys):
        raise AssertionError("aggregation merged distinct experiment dimensions")
