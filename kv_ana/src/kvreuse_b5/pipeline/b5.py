from __future__ import annotations

from typing import Any, Iterable, Mapping


ORACLE_METHODS = {
    "shared_residual_oracle",
    "direct",
    "shared_plus_rank",
    "frozen_basis_oracle_coeff",
    "pair_lowrank_oracle",
    "layer_group_oracle",
    "position_group_oracle",
    "joint_unit_oracle",
}


def _scope_is_comparable(row: Mapping[str, Any]) -> bool:
    experiment = row.get("experiment")
    scope = str(row.get("scope", ""))
    if experiment in {"B25_FROZEN_SUBSPACE", "B25_PAIR_ORACLE", "B3_PROBE"}:
        return scope.startswith("heldout") or scope.startswith("smoke_heldout")
    if experiment == "B4_SELECTIVE":
        return scope.startswith("heldout") or scope.startswith("smoke_heldout")
    return True


def build_b5_rows(historical: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize B1--B4 summaries into the common tournament schema.

    These remain mechanism/reference rows.  Nothing here is relabelled as a paper
    reproduction, and target-derived rows are explicitly marked oracle.
    """
    rows: list[dict[str, Any]] = []
    for source in historical:
        if not _scope_is_comparable(source):
            continue
        experiment = str(source["experiment"])
        method = str(source.get("method", experiment))
        is_oracle = method in ORACLE_METHODS or experiment in {"B1_SHARED_RESIDUAL", "B2_PAIR_LOWRANK"}
        if experiment == "B3_PROBE":
            method_class = "PAPER-COMPONENT" if method == "probe_mean" else "TRAINABLE/OFFLINE-FIT"
            target_usage = "probe_target_only"
        elif experiment == "B4_SELECTIVE" and method in {"random_expected", "boundary_first"}:
            method_class = "DEPLOYMENT-VISIBLE"
            target_usage = "none" if method == "boundary_first" else "metric_only_expectation"
        elif is_oracle:
            method_class = "ORACLE"
            target_usage = "full_target_oracle"
        else:
            method_class = "TRAINABLE/OFFLINE-FIT"
            target_usage = "full_test_target_coefficients" if experiment == "B25_FROZEN_SUBSPACE" else "none"
        rows.append(
            {
                "method": method,
                "paper": "",
                "variant": experiment,
                "fidelity": "F2_MECHANISM_BASELINE",
                "component": "historical representation-level baseline",
                "method_class": method_class,
                "model": source["model"],
                "condition": source["condition"],
                "state": source["state"],
                "scope": source["scope"],
                "pair_count": source.get("valid_count"),
                "baseline_sse": source.get("baseline_sse"),
                "corrected_sse": source.get("corrected_sse"),
                "residual_energy_explained": source.get("residual_energy_explained"),
                "before_cosine": None,
                "after_cosine": None,
                "repair_fraction": source.get("budget") if experiment == "B4_SELECTIVE" else None,
                "probe_fraction": None,
                "rank": source.get("rank"),
                "probes": source.get("probes"),
                "budget": source.get("budget"),
                "oracle_target_usage": target_usage,
                "train_target_usage": "train_only" if experiment in {"B25_FROZEN_SUBSPACE", "B3_PROBE"} else "none",
                "runtime_required": False,
                "notes": "B1-B4 do not establish functional/logit safety",
            }
        )
    return rows


def validate_target_usage(rows: Iterable[Mapping[str, Any]]) -> None:
    """Reject target-derived deployment-visible corrections.

    Metric-only random expectation is allowed because it never materializes a
    corrected state and is labelled as such.
    """
    violations = []
    for row in rows:
        if row.get("method_class") != "DEPLOYMENT-VISIBLE":
            continue
        usage = str(row.get("oracle_target_usage", "none"))
        if usage not in {"", "none", "metric_only_expectation"}:
            violations.append(f"{row.get('method')}:{usage}")
    if violations:
        raise ValueError("target leakage in deployment-visible rows: " + ", ".join(violations))

