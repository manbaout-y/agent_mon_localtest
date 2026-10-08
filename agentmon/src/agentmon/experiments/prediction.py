from __future__ import annotations

from typing import Any

from agentmon.optimizer.harness.schemas import (
    COMPLETE,
    DEFAULT_STEP_CSV_PROFILE,
    FORBIDDEN_CONCLUSIONS,
    PARTIAL,
    SCHEMA_VERSION,
    UNKNOWN,
    merge_schema_profile,
)

from .evidence import BatchExperimentData, first_float, int_or_none
from .snapshots import OptimizerReplaySnapshot


STEP_CSV_PROFILE = DEFAULT_STEP_CSV_PROFILE


def build_prediction_variable_validation(
    data: BatchExperimentData,
    snapshots: list[OptimizerReplaySnapshot],
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = build_prediction_validation_rows(data, snapshots, schema_profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.prediction_variable_validation",
        "validation_mode": "lagged_observation_pairs",
        "rows": rows,
        "summary": {
            "candidate_count": len(rows),
            "validated_count": sum(1 for row in rows if row.get("data_quality") == COMPLETE),
            "partial_count": sum(1 for row in rows if row.get("data_quality") == PARTIAL),
            "unknown_count": sum(1 for row in rows if row.get("data_quality") == UNKNOWN),
        },
        "limitations": [
            "This validates candidate variable availability and lagged association only.",
            "It does not train a predictor or claim causal control effect.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def build_prediction_validation_rows(
    data: BatchExperimentData,
    snapshots: list[OptimizerReplaySnapshot],
    schema_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    step_profile = merge_schema_profile(schema_profile)["step_csv_profile"]
    future_model_rows = future_model_observations(data, step_profile)
    candidates = {
        "queue.running_count": lambda snapshot: snapshot.queue.running_count,
        "queue.capacity_utilization": lambda snapshot: (
            snapshot.queue.running_count / snapshot.queue.max_running_runs
            if snapshot.queue.max_running_runs > 0
            else None
        ),
        "model.running_requests": lambda snapshot: snapshot.model_service.running_requests if snapshot.model_service else None,
        "model.waiting_requests": lambda snapshot: snapshot.model_service.waiting_requests if snapshot.model_service else None,
        "model.kv_usage_ratio": lambda snapshot: snapshot.model_service.kv_usage_ratio if snapshot.model_service else None,
    }
    outcomes = {
        "next_request.queue_s": "queue_s",
        "next_request.ttft_s": "ttft_s",
        "next_request.decode_s": "decode_s",
    }
    rows: list[dict[str, Any]] = []
    for variable_name, extractor in candidates.items():
        for outcome_name, outcome_field in outcomes.items():
            pairs: list[tuple[float, float]] = []
            x_observed = 0
            y_observed = 0
            for snapshot in snapshots:
                x_value = number_or_none(extractor(snapshot))
                if x_value is not None:
                    x_observed += 1
                y_value = next_outcome_value(future_model_rows, snapshot.timestamp_mono_ns, outcome_field)
                if y_value is not None:
                    y_observed += 1
                if x_value is not None and y_value is not None:
                    pairs.append((x_value, y_value))
            correlation = pearson(pairs)
            rows.append(
                {
                    "candidate_variable": variable_name,
                    "future_outcome": outcome_name,
                    "snapshot_count": len(snapshots),
                    "candidate_observed_count": x_observed,
                    "outcome_observed_count": y_observed,
                    "paired_observation_count": len(pairs),
                    "association_direction": association_direction(correlation),
                    "pearson_correlation": correlation,
                    "validation_method": "next_future_model_request",
                    "data_quality": COMPLETE if len(pairs) >= 2 else PARTIAL if pairs else UNKNOWN,
                    "evidence_source": "snapshot_timeline;step_csv",
                    "forbidden_conclusion": "Do not treat lagged association as causal prediction quality.",
                }
            )
    return rows


def future_model_observations(
    data: BatchExperimentData,
    step_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    step_profile = step_profile or STEP_CSV_PROFILE
    rows: list[dict[str, Any]] = []
    for row in data.step_rows:
        if row.get("item_type") != "model":
            continue
        start = int_or_none(row.get("item_start_mono_ns"))
        if start is None:
            continue
        rows.append(
            {
                "start_mono_ns": start,
                "queue_s": first_float(*(row.get(field) for field in step_profile["model_phase_duration_fields"]["model_queue"])),
                "ttft_s": first_float(*(row.get(field) for field in step_profile["request_ttft_fields"])),
                "decode_s": first_float(*(row.get(field) for field in step_profile["model_phase_duration_fields"]["model_decode"])),
            }
        )
    rows.sort(key=lambda item: int(item["start_mono_ns"]))
    return rows


def next_outcome_value(
    future_model_rows: list[dict[str, Any]],
    timestamp_mono_ns: int | None,
    field: str,
) -> float | None:
    if timestamp_mono_ns is None:
        return None
    for row in future_model_rows:
        start = int_or_none(row.get("start_mono_ns"))
        if start is not None and start >= timestamp_mono_ns:
            return number_or_none(row.get(field))
    return None


def pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / ((var_x * var_y) ** 0.5)


def association_direction(value: float | None) -> str:
    if value is None:
        return UNKNOWN
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
