from __future__ import annotations

from typing import Any

def count_result_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "missing")
        counts[value] = counts.get(value, 0) + 1
    return counts


def number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "n/a", "null", "none", "nan", "inf", "-inf"}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def aggregate_optional_metric(
    rows: list[dict[str, Any]],
    key: str,
    *,
    alias: str | None = None,
) -> dict[str, Any]:
    values: list[float] = []
    for row in rows:
        value = optional_number(row.get(key))
        if value is None and alias is not None:
            value = optional_number(row.get(alias))
        if value is not None:
            values.append(value)
    return {
        "total": sum(values) if values else None,
        "valid_run_count": len(values),
        "run_count": len(rows),
        "coverage": len(values) / len(rows) if rows else None,
    }


def ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    return float(numerator) / float(denominator) if numerator is not None and denominator else None


def throughput_metric(
    numerator: float | int | None,
    denominator_s: float | int | None,
    formula: str,
    time_window: str,
    metric_kind: str,
    *,
    confidence: str = "exact",
) -> dict[str, Any]:
    return {
        "value_per_second": ratio(numerator, denominator_s),
        "formula": formula,
        "numerator": numerator,
        "denominator_seconds": denominator_s,
        "time_window": time_window,
        "kind": metric_kind,
        "confidence": confidence if denominator_s is not None and denominator_s > 0 else "unavailable",
    }


def steady_state_completion_rate(results: list[dict[str, Any]]) -> dict[str, Any]:
    completions = sorted(
        value
        for row in results
        if row.get("execution_success") is True
        if (value := optional_number(row.get("workload_end_mono_ns"))) is not None
    )
    if len(completions) < 3:
        return {
            **throughput_metric(
                None,
                None,
                "completions_between_t10_and_t90 / (t90 - t10)",
                "successful_completion_t10_t90",
                "observed",
                confidence="unavailable",
            ),
            "missing_reason": "at_least_three_successful_completion_timestamps_required",
        }
    start_ns = percentile_linear(completions, 10.0)
    end_ns = percentile_linear(completions, 90.0)
    denominator_s = (end_ns - start_ns) / 1_000_000_000 if end_ns > start_ns else None
    count = sum(start_ns <= value <= end_ns for value in completions)
    return throughput_metric(
        count,
        denominator_s,
        "completions_between_t10_and_t90 / (t90 - t10)",
        "successful_completion_t10_t90",
        "observed",
        confidence="sampled",
    )


def percentile_linear(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(100.0, percentile)) / 100.0 * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
