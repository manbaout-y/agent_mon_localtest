from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable

from agentmon.paths import write_private_text


TIMELINE_SCHEMA_VERSION = "1.0"


def generate_batch_timeline(
    batch_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    bucket_sizes: Iterable[float] = (1.0, 5.0),
) -> dict[str, Any]:
    batch_path = Path(batch_dir).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve() if output_dir else batch_path / "timeline"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        destination.chmod(0o700)
    except OSError:
        pass

    manifest = read_json(batch_path / "batch-manifest.json")
    batch_summary = read_json(batch_path / "batch-summary.json")
    result_rows = read_delimited(batch_path / "batch-results.tsv", delimiter="\t")
    step_path = resolve_step_csv(batch_path, manifest)
    step_rows = read_delimited(step_path) if step_path else []

    items, invalid_items = build_item_intervals(step_rows)
    runs, invalid_runs = build_run_intervals(result_rows, step_rows)
    all_intervals = runs + items
    starts = [row["start_mono_ns"] for row in all_intervals]
    ends = [row["end_mono_ns"] for row in all_intervals]
    batch_start_ns = integer_or_none(manifest.get("start_mono_ns"))
    origin_ns = batch_start_ns if batch_start_ns is not None else (min(starts) if starts else None)
    end_ns = integer_or_none(manifest.get("end_mono_ns"))
    timeline_end_ns = end_ns if end_ns is not None else (max(ends) if ends else origin_ns)

    run_timeline_rows = project_run_timeline(runs, origin_ns)
    write_csv(destination / "run_timeline.csv", run_timeline_rows)
    resource_windows = build_service_resource_windows(step_rows, origin_ns)
    write_csv(destination / "service_resource_windows.csv", resource_windows)

    bucket_artifacts: dict[str, str] = {}
    bucket_rows_by_size: dict[float, list[dict[str, Any]]] = {}
    normalized_buckets = sorted({float(size) for size in bucket_sizes if float(size) > 0}) or [1.0]
    for size in normalized_buckets:
        rows = build_bucket_timeline(runs, items, origin_ns, timeline_end_ns, size)
        bucket_rows_by_size[size] = rows
        label = bucket_label(size)
        path = destination / f"batch_timeline_{label}.csv"
        write_csv(path, rows)
        bucket_artifacts[label] = str(path)

    transitions = build_transition_gaps(items, origin_ns)
    write_csv(destination / "transition_gap.csv", transitions)
    phase_rows = build_phase_summary(manifest)
    write_csv(destination / "phase_summary.csv", phase_rows)
    tail_rows = build_tail_summary(runs, origin_ns)
    write_csv(destination / "tail_summary.csv", tail_rows)

    finest_size = min(bucket_rows_by_size)
    integral_rows = build_integral_checks(runs, items, bucket_rows_by_size[finest_size])
    write_csv(destination / "concurrency_integral_check.csv", integral_rows)
    validation = build_timeline_validation(
        manifest,
        batch_summary,
        runs,
        items,
        step_rows,
        tail_rows,
        integral_rows,
        invalid_runs + invalid_items,
        bucket_rows_by_size[finest_size],
    )
    write_json(destination / "validation_checks.json", validation)

    summary = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "summary_type": "agentmon.batch_timeline",
        "batch_dir": str(batch_path),
        "output_dir": str(destination),
        "publishability_status": validation["publishability_status"],
        "run_count": len(runs),
        "model_interval_count": sum(row["interval_type"] == "model" for row in items),
        "tool_interval_count": sum(row["interval_type"] == "tool" for row in items),
        "clock_domains": sorted(nonempty_values(step_rows, "clock_domain_id")),
        "host_boot_ids": sorted(nonempty_values(step_rows, "host_boot_id")),
        "artifacts": {
            "run_timeline": str(destination / "run_timeline.csv"),
            "service_resource_windows": str(destination / "service_resource_windows.csv"),
            "bucket_timelines": bucket_artifacts,
            "phase_summary": str(destination / "phase_summary.csv"),
            "tail_summary": str(destination / "tail_summary.csv"),
            "transition_gap": str(destination / "transition_gap.csv"),
            "concurrency_integral_check": str(destination / "concurrency_integral_check.csv"),
            "validation_checks": str(destination / "validation_checks.json"),
            "timeline_report": str(destination / "timeline_report.md"),
        },
    }
    write_json(destination / "timeline_summary.json", summary)
    write_private_text(destination / "timeline_report.md", format_timeline_report(summary, validation, tail_rows, integral_rows))
    return summary


def build_service_resource_windows(
    step_rows: list[dict[str, str]],
    origin_ns: int | None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for row in step_rows:
        run_id = text_or_none(row.get("run_id"))
        step_id = text_or_none(row.get("step_id")) or text_or_none(row.get("step_index"))
        if run_id is None or step_id is None:
            continue
        current = grouped.setdefault((run_id, step_id), {})
        for key, value in row.items():
            if current.get(key) in {None, ""} and value not in {None, ""}:
                current[key] = value

    result: list[dict[str, Any]] = []
    for (run_id, step_id), row in grouped.items():
        start_ns = integer_or_none(row.get("step_start_mono_ns"))
        end_ns = integer_or_none(row.get("step_end_mono_ns"))
        result.append(
            {
                "run_id": run_id,
                "step_id": step_id,
                "step_index": row.get("step_index"),
                "step_start_mono_ns": start_ns,
                "step_end_mono_ns": end_ns,
                "step_start_offset_from_batch_s": ns_offset(start_ns, origin_ns),
                "step_end_offset_from_batch_s": ns_offset(end_ns, origin_ns),
                "clock_domain_id": row.get("clock_domain_id"),
                "host_boot_id": row.get("host_boot_id"),
                "observation_scope": first_nonempty(
                    row.get("step_npu_observation_scope"),
                    row.get("step_scheduler_observation_scope"),
                    "shared_service_observation",
                ),
                "request_attribution": first_nonempty(
                    row.get("step_npu_request_attribution"),
                    "not_request_attributed",
                ),
                "target_peak_hbm_used_bytes": row.get("step_target_peak_hbm_used_bytes"),
                "target_avg_hbm_used_bytes": row.get("step_target_avg_hbm_used_bytes"),
                "target_max_aicore_util_pct": row.get("step_target_max_aicore_util_pct"),
                "target_avg_aicore_util_pct": row.get("step_target_avg_aicore_util_pct"),
                "npu_device_metrics_json": row.get("step_npu_device_metrics_json"),
                "kv_usage_ratio_after_max": row.get("step_kv_usage_ratio_after_max"),
                "kv_snapshot_usage_ratio": row.get("step_kv_snapshot_usage_ratio"),
                "kv_pool_allocated_bytes_per_rank": row.get("step_kv_pool_allocated_bytes_per_rank"),
                "scheduler_running_requests_max": row.get("step_scheduler_running_requests_max"),
                "scheduler_running_requests_mean": row.get("step_scheduler_running_requests_mean"),
                "scheduler_waiting_requests_max": row.get("step_scheduler_waiting_requests_max"),
                "scheduler_waiting_requests_mean": row.get("step_scheduler_waiting_requests_mean"),
                "scheduler_scheduled_tokens_max": row.get("step_scheduler_scheduled_tokens_max"),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            integer_or_none(row.get("step_start_mono_ns")) is None,
            integer_or_none(row.get("step_start_mono_ns")) or 0,
            str(row.get("run_id") or ""),
            str(row.get("step_id") or ""),
        ),
    )


def resolve_step_csv(batch_dir: Path, manifest: dict[str, Any]) -> Path | None:
    candidates = [
        path
        for raw in (
            manifest.get("all_steps_csv"),
            as_dict(manifest.get("artifacts")).get("all_steps_csv"),
            batch_dir / "step-sequence" / "all-steps.csv",
        )
        if raw not in {None, ""}
        if (path := Path(raw))
    ]
    for path in candidates:
        candidate = path if path.is_absolute() else batch_dir / path
        if candidate.is_file():
            return candidate
    return None


def build_run_intervals(
    result_rows: list[dict[str, str]],
    step_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bounds: dict[str, list[int]] = {}
    for row in step_rows:
        run_id = text_or_none(row.get("run_id"))
        start = integer_or_none(row.get("item_start_mono_ns"))
        end = integer_or_none(row.get("item_end_mono_ns"))
        if run_id and start is not None and end is not None and end >= start:
            current = bounds.setdefault(run_id, [start, end])
            current[0] = min(current[0], start)
            current[1] = max(current[1], end)

    intervals: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for row in result_rows:
        run_id = text_or_none(row.get("run_id"))
        start = integer_or_none(row.get("workload_start_mono_ns"))
        end = integer_or_none(row.get("workload_end_mono_ns"))
        source = "batch_results.workload_mono_ns"
        if (start is None or end is None) and run_id in bounds:
            start, end = bounds[run_id]
            source = "step_item_envelope_fallback"
        if start is None or end is None or end < start:
            invalid.append({"type": "run", "run_id": run_id, "reason": "missing_or_negative_interval"})
            continue
        intervals.append(
            {
                "interval_type": "run",
                "task_id": row.get("task_id"),
                "run_id": run_id,
                "status": row.get("status"),
                "execution_success": parse_bool(row.get("execution_success")),
                "analysis_success": parse_bool(row.get("analysis_pipeline_success") or row.get("pipeline_success")),
                "judge_success": parse_optional_bool(row.get("judge_success")),
                "start_mono_ns": start,
                "end_mono_ns": end,
                "duration_s": (end - start) / 1_000_000_000,
                "time_source": source,
            }
        )
    return intervals, invalid


def build_item_intervals(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intervals: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for row in rows:
        item_type = text_or_none(row.get("item_type"))
        if item_type not in {"model", "tool"}:
            continue
        start = integer_or_none(row.get("item_start_mono_ns"))
        end = integer_or_none(row.get("item_end_mono_ns"))
        if start is None or end is None or end < start:
            invalid.append(
                {
                    "type": item_type,
                    "run_id": row.get("run_id"),
                    "span_id": row.get("span_id"),
                    "reason": "missing_or_negative_interval",
                }
            )
            continue
        intervals.append(
            {
                "interval_type": item_type,
                "task_id": row.get("task_id") or row.get("run_id"),
                "run_id": row.get("run_id"),
                "step_id": row.get("step_id"),
                "step_index": row.get("step_index"),
                "span_id": row.get("span_id"),
                "start_mono_ns": start,
                "end_mono_ns": end,
                "duration_s": (end - start) / 1_000_000_000,
                "status": row.get("status"),
                "clock_domain_id": row.get("clock_domain_id"),
                "host_boot_id": row.get("host_boot_id"),
            }
        )
    return intervals, invalid


def project_run_timeline(runs: list[dict[str, Any]], origin_ns: int | None) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for row in sorted(runs, key=lambda item: item["start_mono_ns"]):
        projected.append(
            {
                **row,
                "start_offset_from_batch_s": ns_offset(row["start_mono_ns"], origin_ns),
                "end_offset_from_batch_s": ns_offset(row["end_mono_ns"], origin_ns),
            }
        )
    return projected


def build_bucket_timeline(
    runs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    origin_ns: int | None,
    end_ns: int | None,
    bucket_s: float,
) -> list[dict[str, Any]]:
    if origin_ns is None or end_ns is None or end_ns < origin_ns:
        return []
    width_ns = max(1, int(bucket_s * 1_000_000_000))
    model = [row for row in items if row["interval_type"] == "model"]
    tool = [row for row in items if row["interval_type"] == "tool"]
    rows: list[dict[str, Any]] = []
    cursor = origin_ns
    index = 0
    while cursor < end_ns or (cursor == end_ns and not rows):
        bucket_end = min(cursor + width_ns, end_ns) if end_ns > cursor else cursor + width_ns
        midpoint = cursor + (bucket_end - cursor) // 2
        duration_s = (bucket_end - cursor) / 1_000_000_000
        run_seconds = overlap_seconds(runs, cursor, bucket_end)
        model_seconds = overlap_seconds(model, cursor, bucket_end)
        tool_seconds = overlap_seconds(tool, cursor, bucket_end)
        rows.append(
            {
                "bucket_index": index,
                "bucket_start_offset_s": ns_offset(cursor, origin_ns),
                "bucket_end_offset_s": ns_offset(bucket_end, origin_ns),
                "bucket_duration_s": duration_s,
                "active_run_count": active_count(runs, midpoint),
                "active_model_count": active_count(model, midpoint),
                "active_tool_count": active_count(tool, midpoint),
                "avg_active_run_count": run_seconds / duration_s if duration_s else None,
                "avg_active_model_count": model_seconds / duration_s if duration_s else None,
                "avg_active_tool_count": tool_seconds / duration_s if duration_s else None,
                "active_run_seconds": run_seconds,
                "active_model_seconds": model_seconds,
                "active_tool_seconds": tool_seconds,
                "started_run_count": sum(cursor <= row["start_mono_ns"] < bucket_end for row in runs),
                "completed_run_count": sum(cursor < row["end_mono_ns"] <= bucket_end for row in runs),
            }
        )
        cursor = bucket_end
        index += 1
    return rows


def build_transition_gaps(items: list[dict[str, Any]], origin_ns: int | None) -> list[dict[str, Any]]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        by_run.setdefault(str(row.get("run_id") or "unknown"), []).append(row)
    output: list[dict[str, Any]] = []
    for run_id, rows in sorted(by_run.items()):
        ordered = sorted(rows, key=lambda item: (item["start_mono_ns"], item["end_mono_ns"]))
        for previous, current in zip(ordered, ordered[1:]):
            gap_s = (current["start_mono_ns"] - previous["end_mono_ns"]) / 1_000_000_000
            output.append(
                {
                    "run_id": run_id,
                    "from_type": previous["interval_type"],
                    "to_type": current["interval_type"],
                    "from_span_id": previous.get("span_id"),
                    "to_span_id": current.get("span_id"),
                    "transition_offset_from_batch_s": ns_offset(current["start_mono_ns"], origin_ns),
                    "gap_s": gap_s,
                    "classification": "overlap" if gap_s < 0 else ("contiguous" if gap_s == 0 else "idle_or_orchestration"),
                }
            )
    return output


def build_phase_summary(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    timings = as_dict(manifest.get("phase_timings"))
    phases = (
        ("preprocessing", "preprocessing_s", False),
        ("workload", "workload_phase_s", False),
        ("p2_index", "p2_index_s", False),
        ("analysis", "analysis_phase_s", False),
        ("finalization", "finalization_s", False),
        ("postprocessing_envelope", "postprocessing_s", True),
    )
    return [
        {
            "phase": name,
            "duration_s": number_or_none(timings.get(key)),
            "aggregation_role": "overlapping_envelope" if envelope else "disjoint_phase",
            "metric_source": f"batch-manifest.json:phase_timings.{key}",
        }
        for name, key, envelope in phases
    ]


def build_tail_summary(runs: list[dict[str, Any]], origin_ns: int | None) -> list[dict[str, Any]]:
    if origin_ns is None or not runs:
        return []
    completions = sorted((row["end_mono_ns"] - origin_ns) / 1_000_000_000 for row in runs)
    output: list[dict[str, Any]] = []
    for percentile in (50, 90, 95, 100):
        value = percentile_linear(completions, percentile)
        output.append(
            {
                "percentile": f"t{percentile}",
                "completion_offset_s": value,
                "remaining_after_threshold": sum(item > value for item in completions),
            }
        )
    return output


def build_integral_checks(
    runs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    bucket_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for name, intervals, bucket_field in (
        ("run", runs, "active_run_seconds"),
        ("model", [row for row in items if row["interval_type"] == "model"], "active_model_seconds"),
        ("tool", [row for row in items if row["interval_type"] == "tool"], "active_tool_seconds"),
    ):
        interval_sum = sum(row["duration_s"] for row in intervals)
        timeline_sum = sum(number_or_none(row.get(bucket_field)) or 0.0 for row in bucket_rows)
        absolute_error = abs(interval_sum - timeline_sum)
        relative_error = absolute_error / interval_sum if interval_sum > 0 else (0.0 if timeline_sum == 0 else None)
        output.append(
            {
                "interval_type": name,
                "interval_count": len(intervals),
                "interval_duration_sum_s": interval_sum,
                "timeline_integral_s": timeline_sum,
                "absolute_error_s": absolute_error,
                "relative_error": relative_error,
                "passed": relative_error is not None and relative_error < 0.01,
            }
        )
    return output


def build_timeline_validation(
    manifest: dict[str, Any],
    batch_summary: dict[str, Any],
    runs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    step_rows: list[dict[str, str]],
    tail_rows: list[dict[str, Any]],
    integral_rows: list[dict[str, Any]],
    invalid_intervals: list[dict[str, Any]],
    bucket_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    configured = integer_or_none(manifest.get("concurrency"))
    max_active = max((integer_or_none(row.get("active_run_count")) or 0 for row in bucket_rows), default=0)
    clock_domains = nonempty_values(step_rows, "clock_domain_id")
    boot_ids = nonempty_values(step_rows, "host_boot_id")
    tail_values = [number_or_none(row.get("completion_offset_s")) for row in tail_rows]
    tail_ordered = all(
        left is not None and right is not None and left <= right
        for left, right in zip(tail_values, tail_values[1:])
    ) if tail_values else False
    expected = integer_or_none(manifest.get("task_count"))
    stage_counts = as_dict(batch_summary.get("stage_counts"))
    checks = [
        check("run_intervals_present", bool(runs)),
        check("no_invalid_intervals", not invalid_intervals, details={"invalid": invalid_intervals[:50]}),
        check("clock_domain_consistent", len(clock_domains) <= 1, details={"values": sorted(clock_domains)}),
        check("host_boot_consistent", len(boot_ids) <= 1, details={"values": sorted(boot_ids)}),
        check(
            "configured_concurrency_not_exceeded",
            configured is None or max_active <= configured,
            details={"configured": configured, "observed_max": max_active},
        ),
        check(
            "started_completed_count_equal",
            expected is None or len(runs) == expected,
            details={"expected": expected, "completed_intervals": len(runs)},
        ),
        check("completion_percentiles_ordered", tail_ordered),
        check(
            "concurrency_integral_error_below_1pct",
            bool(integral_rows) and all(row.get("passed") is True for row in integral_rows),
            details={"checks": integral_rows},
        ),
        check(
            "batch_analysis_has_usable_runs",
            not stage_counts or (number_or_none(stage_counts.get("usable_run")) or 0) > 0,
        ),
    ]
    failures = [item["name"] for item in checks if item["blocking"] and not item["passed"]]
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "validation_type": "agentmon.batch_timeline",
        "publishability_status": "PUBLISHABLE" if not failures else "UNPUBLISHABLE",
        "blocking_failures": failures,
        "checks": checks,
    }


def check(name: str, passed: bool, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "blocking": True, "details": details or {}}


def format_timeline_report(
    summary: dict[str, Any],
    validation: dict[str, Any],
    tail_rows: list[dict[str, Any]],
    integral_rows: list[dict[str, Any]],
) -> str:
    lines = [
        "# AgentMon Batch Timeline",
        "",
        f"Publishability: **{validation['publishability_status']}**",
        f"Runs: {summary['run_count']}",
        f"Model intervals: {summary['model_interval_count']}",
        f"Tool intervals: {summary['tool_interval_count']}",
        "",
        "## Completion Tail",
        "",
        "| Percentile | Offset (s) | Remaining |",
        "|---|---:|---:|",
    ]
    for row in tail_rows:
        lines.append(f"| {row['percentile']} | {format_number(row['completion_offset_s'])} | {row['remaining_after_threshold']} |")
    lines.extend(["", "## Integral Checks", "", "| Type | Interval (s) | Timeline (s) | Error | Passed |", "|---|---:|---:|---:|---|"])
    for row in integral_rows:
        lines.append(
            f"| {row['interval_type']} | {format_number(row['interval_duration_sum_s'])} | "
            f"{format_number(row['timeline_integral_s'])} | {format_number(row['relative_error'])} | {row['passed']} |"
        )
    if validation["blocking_failures"]:
        lines.extend(["", "## Blocking Failures", ""])
        lines.extend(f"- {name}" for name in validation["blocking_failures"])
    lines.append("")
    return "\n".join(lines)


def format_timeline_table(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Batch: {summary['batch_dir']}",
            f"Status: {summary['publishability_status']}",
            f"Runs: {summary['run_count']}",
            f"Model intervals: {summary['model_interval_count']}",
            f"Tool intervals: {summary['tool_interval_count']}",
            f"Output: {summary['output_dir']}",
        ]
    )


def overlap_seconds(intervals: list[dict[str, Any]], start_ns: int, end_ns: int) -> float:
    overlap_ns = sum(
        max(0, min(row["end_mono_ns"], end_ns) - max(row["start_mono_ns"], start_ns))
        for row in intervals
    )
    return overlap_ns / 1_000_000_000


def active_count(intervals: list[dict[str, Any]], timestamp_ns: int) -> int:
    return sum(row["start_mono_ns"] <= timestamp_ns < row["end_mono_ns"] for row in intervals)


def percentile_linear(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires data")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(100.0, percentile)) / 100.0 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def nonempty_values(rows: list[dict[str, str]], key: str) -> set[str]:
    return {str(row[key]) for row in rows if row.get(key) not in {None, ""}}


def read_delimited(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    private(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def bucket_label(value: float) -> str:
    return f"{int(value)}s" if value.is_integer() else f"{value:g}s".replace(".", "p")


def ns_offset(value: int | None, origin: int | None) -> float | None:
    return (value - origin) / 1_000_000_000 if value is not None and origin is not None else None


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def text_or_none(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "passed", "success"}


def parse_optional_bool(value: Any) -> bool | None:
    if value in {None, ""}:
        return None
    return parse_bool(value)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def format_number(value: Any) -> str:
    number = number_or_none(value)
    return "N/A" if number is None else f"{number:.6f}"
