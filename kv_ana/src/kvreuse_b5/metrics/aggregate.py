from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

import numpy as np

EXPERIMENT_DIMENSIONS = (
    "model_family",
    "condition",
    "state_kind",
    "method",
    "variant",
    "method_config_hash",
    "rank",
    "requested_budget",
    "probe_count",
    "anchor_count",
    "detector_count",
)


def canonical_experiment_key(record: dict[str, Any], extra: Sequence[str] = ()) -> tuple[Any, ...]:
    missing = [field for field in EXPERIMENT_DIMENSIONS if field not in record]
    if missing:
        raise ValueError(f"raw result lacks canonical experiment dimensions: {missing}")
    return tuple(record[field] for field in (*EXPERIMENT_DIMENSIONS, *extra))


def _summarize(key: tuple[Any, ...], fields: Sequence[str], items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in items if row["status"] == "OK" and np.isfinite(row["energy_explained"])]
    zero = [row for row in items if row["status"] == "ZERO_RESIDUAL"]
    degraded = [row for row in items if row["status"] == "ZERO_BASELINE_DEGRADED"]
    baseline = sum(float(row["baseline_sse"]) for row in valid)
    corrected = sum(float(row["corrected_sse"]) for row in valid)
    return {
        **dict(zip(fields, key)),
        "mean_explained": float(np.mean([row["energy_explained"] for row in valid])) if valid else None,
        "median_explained": float(np.median([row["energy_explained"] for row in valid])) if valid else None,
        "pooled_explained": 1.0 - corrected / baseline if baseline else None,
        "valid_count": len(valid),
        "zero_residual_count": len(zero),
        "zero_baseline_degraded_count": len(degraded),
        "error_count": sum(row["status"] in {"ERROR", "CAPABILITY_MISSING"} for row in items),
        "record_count": len(items),
    }


def _group(records: list[dict[str, Any]], extra: Sequence[str] = ()) -> list[dict[str, Any]]:
    fields = (*EXPERIMENT_DIMENSIONS, *extra)
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[canonical_experiment_key(record, extra)].append(record)
    return [_summarize(key, fields, items) for key, items in sorted(grouped.items(), key=lambda item: repr(item[0]))]


def aggregate_records(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    records = list(records)
    global_summary = _group(records)
    budget_summary = [row for row in global_summary if row["requested_budget"] is not None]
    layer_summary = _group(records, ("layer_id",))
    ranking_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in global_summary:
        ranking_groups[(row["model_family"], row["condition"], row["state_kind"])].append(row)
    method_ranking = []
    for group_key, rows in sorted(ranking_groups.items()):
        ranked = sorted(
            rows,
            key=lambda row: (row["pooled_explained"] is None, -(row["pooled_explained"] or 0.0), repr(canonical_experiment_key(row))),
        )
        for order, row in enumerate(ranked, 1):
            method_ranking.append({**row, "ranking_group": group_key, "rank_order": order})
    return {
        "global_summary": global_summary,
        "budget_summary": budget_summary,
        "layer_summary": layer_summary,
        "method_ranking": method_ranking,
    }
