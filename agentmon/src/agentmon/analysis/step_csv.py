#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Sequence

from agentmon.profile_data import profile_quality_gate


MISSING = {"", "na", "n/a", "none", "null", "nan", "inf", "-inf"}
MIN_STRONG_COVERAGE = 0.30
DEFAULT_MIN_P2_MATCH_RATE = profile_quality_gate("min_p2_match_rate", profile="legacy_compat")
DEFAULT_MIN_P2_LIFECYCLE_RATE = profile_quality_gate("min_p2_lifecycle_rate", profile="legacy_compat")


RUN_SUMMARY_FIELDS = [
    "run_id",
    "step_count",
    "model_request_count",
    "tool_call_count",
    "model_extra_count",
    "run_wall_s",
    "run_wall_source",
    "run_model_e2e_sum_s",
    "run_p1_model_span_sum_s",
    "run_tool_duration_sum_s",
    "run_model_queue_sum_s",
    "run_model_ttft_sum_s",
    "run_model_prefill_sum_s",
    "run_model_prefill_to_first_token_sum_s",
    "run_model_decode_sum_s",
    "model_call_e2e_p50_s",
    "model_call_e2e_p95_s",
    "model_call_e2e_max_s",
    "tool_call_duration_p50_s",
    "tool_call_duration_p95_s",
    "tool_call_duration_max_s",
    "run_duration_s",
    "model_e2e_total_s",
    "tool_duration_total_s",
    "queue_total_s",
    "ttft_total_s",
    "prefill_total_s",
    "prefill_to_first_token_total_s",
    "decode_total_s",
    "model_time_share",
    "tool_time_share",
    "queue_ratio",
    "ttft_ratio",
    "prefill_ratio",
    "decode_ratio",
    "shared_scheduler_running_requests_max",
    "shared_scheduler_running_requests_mean",
    "shared_scheduler_waiting_requests_max",
    "shared_scheduler_waiting_requests_mean",
    "shared_scheduler_scheduled_tokens_max",
    "scheduler_observation_scope",
    "prompt_tokens",
    "generation_tokens",
    "cached_prompt_tokens",
    "cache_reuse_rate",
    "recomputed_prompt_rate",
    "decode_tokens_per_s",
    "tool_nonzero_return_count",
    "tool_timeout_count",
    "tool_timeout_rate",
    "agent_cpu_time_s",
    "agent_cpu_wall_ratio",
    "tool_cpu_time_s",
    "tool_cpu_user_time_s",
    "tool_cpu_system_time_s",
    "tool_cpu_time_quality",
    "tool_cpu_wall_ratio",
    "target_peak_hbm_used_bytes",
    "target_avg_hbm_used_bytes",
    "target_max_aicore_util_pct",
    "target_avg_aicore_util_pct",
    "kv_usage_after_max",
    "kv_evicted_block_count",
    "bottleneck",
    "warnings",
]


MODEL_HOTSPOT_FIELDS = [
    "run_id",
    "step_index",
    "step_type",
    "api_request_id",
    "vllm_request_id",
    "alignment_quality",
    "p2_match_status",
    "p2_match_method",
    "p2_match_confidence",
    "p2_missing_reason",
    "model_hotspot_duration_s",
    "model_hotspot_duration_source",
    "p1_model_span_duration_s",
    "model_call_e2e_s",
    "p2_e2e_s",
    "p2_queue_s",
    "p2_ttft_s",
    "p2_engine_ttft_s",
    "p2_prefill_s",
    "p2_prefill_to_first_token_s",
    "p2_decode_s",
    "p2_first_output_s",
    "p2_queue_quality",
    "p2_ttft_quality",
    "p2_engine_ttft_quality",
    "p2_prefill_quality",
    "p2_decode_quality",
    "p2_e2e_quality",
    "p2_lifecycle_status",
    "p2_lifecycle_missing_events",
    "p2_prompt_tokens",
    "p2_generation_tokens",
    "p2_cached_prompt_tokens",
    "p2_cache_reuse_rate",
    "p2_recomputed_prompt_rate",
    "p2_finish_reason",
    "step_target_peak_hbm_used_bytes",
    "step_target_avg_aicore_util_pct",
    "model_scheduler_running_requests_max",
    "model_scheduler_waiting_requests_max",
    "model_scheduler_scheduled_tokens_max",
    "model_scheduler_observation_scope",
]


TOOL_HOTSPOT_FIELDS = [
    "run_id",
    "step_index",
    "tool_call_id",
    "tool_name",
    "executable",
    "returncode",
    "tool_status",
    "timed_out",
    "timeout_limit_s",
    "termination_signal",
    "exception_type",
    "approx_tool_duration_s",
    "tool_call_duration_s",
    "tool_cpu_time_s",
    "tool_cpu_wall_ratio",
    "tool_peak_pss_bytes",
    "tool_resource_sample_count",
    "tool_resource_quality",
    "tool_pss_quality",
    "tool_read_bytes_delta",
    "tool_write_bytes_delta",
    "stdout_bytes",
    "stderr_bytes",
    "command_hash",
    "command",
]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analysis = analyze_step_csv(
            Path(args.csv_path),
            top_n=args.top,
            min_p2_match_rate=args.min_p2_match_rate,
            min_p2_lifecycle_rate=args.min_p2_lifecycle_rate,
        )
        if args.out_dir:
            write_outputs(analysis, Path(args.out_dir))
        print(format_analysis(analysis, args.format))
    except Exception as exc:
        print(f"agentmon step csv analysis error: {exc}", file=sys.stderr)
        return 2
    if args.strict and analysis["analysis_publication"]["status"] != "PUBLISHABLE":
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automatically diagnose AgentMon step sequence CSV bottlenecks and load signals",
    )
    parser.add_argument("csv_path", help="CSV exported by scripts/agentmon_step_sequence.py")
    parser.add_argument("--format", choices=["report", "json", "csv"], default="report")
    parser.add_argument("--top", type=int, default=10, help="number of hotspots to show")
    parser.add_argument(
        "--min-p2-match-rate",
        type=rate_argument,
        default=DEFAULT_MIN_P2_MATCH_RATE,
        help="minimum trajectory-to-P2 request match rate from the active schema profile",
    )
    parser.add_argument(
        "--min-p2-lifecycle-rate",
        type=rate_argument,
        default=DEFAULT_MIN_P2_LIFECYCLE_RATE,
        help="minimum complete P2 lifecycle rate from the active schema profile",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when blocking data-quality checks fail",
    )
    parser.add_argument(
        "--out-dir",
        help="optional directory for run_summary.csv, model_hotspots.csv, tool_hotspots.csv and analysis.json",
    )
    return parser


def analyze_step_csv(
    path: Path,
    *,
    top_n: int = 10,
    min_p2_match_rate: float | None = DEFAULT_MIN_P2_MATCH_RATE,
    min_p2_lifecycle_rate: float | None = DEFAULT_MIN_P2_LIFECYCLE_RATE,
) -> dict[str, Any]:
    rows = read_csv_rows(path)
    model_items = unique_items(rows, kind="model")
    tool_items = unique_items(rows, kind="tool")
    step_items = unique_items(rows, kind="step")
    run_ids = sorted({str(row.get("run_id") or "") for row in rows if row.get("run_id")})
    for row in model_items:
        duration, source = model_hotspot_duration(row)
        row["model_hotspot_duration_s"] = "" if duration is None else str(duration)
        row["model_hotspot_duration_source"] = source

    run_summaries = [
        summarize_run(
            run_id,
            [row for row in rows if row.get("run_id") == run_id],
            [row for row in model_items if row.get("run_id") == run_id],
            [row for row in tool_items if row.get("run_id") == run_id],
            [row for row in step_items if row.get("run_id") == run_id],
        )
        for run_id in run_ids
    ]

    quality = summarize_quality(rows, model_items, tool_items, step_items)
    model_hotspots = sorted(
        [row for row in model_items if has_value(row.get("model_hotspot_duration_s"))],
        key=lambda row: number(row.get("model_hotspot_duration_s")),
        reverse=True,
    )[:top_n]
    queue_hotspots = sorted(
        [row for row in model_items if has_value(row.get("p2_queue_s"))],
        key=lambda row: number(row.get("p2_queue_s")),
        reverse=True,
    )[:top_n]
    tool_hotspots = sorted(
        [row for row in tool_items if has_value(row.get("tool_call_duration_s") or row.get("approx_tool_duration_s"))],
        key=lambda row: number(row.get("tool_call_duration_s") or row.get("approx_tool_duration_s")),
        reverse=True,
    )[:top_n]
    kv_hotspots = sorted(
        [row for row in model_items if kv_pressure_score(row) > 0],
        key=kv_pressure_score,
        reverse=True,
    )[:top_n]
    npu_hotspots = sorted(
        [
            row
            for row in model_items
            if max_number(
                row.get("model_target_peak_hbm_used_bytes"),
                row.get("step_target_peak_hbm_used_bytes"),
            )
            is not None
        ],
        key=lambda row: max_number(
            row.get("model_target_peak_hbm_used_bytes"),
            row.get("step_target_peak_hbm_used_bytes"),
        )
        or -1.0,
        reverse=True,
    )[:top_n]

    recommendations = build_recommendations(run_summaries, quality, model_hotspots, tool_hotspots)
    overview = summarize_overview(rows, run_summaries, model_items, tool_items)
    overview["tool_hotspot_selected_count"] = len(tool_hotspots)
    quality_bundle = build_analysis_quality_bundle(
        overview,
        quality,
        min_p2_match_rate=min_p2_match_rate,
        min_p2_lifecycle_rate=min_p2_lifecycle_rate,
    )
    return {
        "schema_version": "2.1",
        "input": str(path),
        "analysis_publication": {
            "status": quality_bundle["validation_checks"]["publishability_status"],
            "blocking_failures": quality_bundle["validation_checks"]["blocking_failures"],
        },
        "overview": overview,
        "quality": quality,
        "run_summaries": run_summaries,
        "model_hotspots": [select_fields(row, MODEL_HOTSPOT_FIELDS) for row in model_hotspots],
        "queue_hotspots": [select_fields(row, MODEL_HOTSPOT_FIELDS) for row in queue_hotspots],
        "tool_hotspots": [select_fields(row, TOOL_HOTSPOT_FIELDS) for row in tool_hotspots],
        "tool_timeouts": summarize_tool_timeouts(tool_items),
        "kv_hotspots": [select_fields(row, MODEL_HOTSPOT_FIELDS) for row in kv_hotspots],
        "npu_hotspots": [select_fields(row, MODEL_HOTSPOT_FIELDS) for row in npu_hotspots],
        "recommendations": recommendations,
        "quality_bundle": quality_bundle,
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{key: value for key, value in row.items()} for row in reader]


def unique_items(rows: list[dict[str, str]], *, kind: str) -> list[dict[str, str]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for index, row in enumerate(rows):
        key = item_key(row, kind, index)
        if key is None:
            continue
        grouped.setdefault(key, []).append(row)
    return [merge_rows(group) for group in grouped.values()]


def item_key(row: dict[str, str], kind: str, index: int) -> tuple[str, ...] | None:
    run_id = str(row.get("run_id") or "")
    if kind == "model":
        if not has_model_signal(row):
            return None
        request_id = first_text(
            row.get("api_request_id"),
            row.get("vllm_request_id"),
            row.get("response_id"),
            row.get("step_id"),
        )
        return ("model", run_id, request_id or f"row-{index}")
    if kind == "tool":
        if not has_tool_signal(row):
            return None
        tool_id = first_text(row.get("tool_call_id"), row.get("command_hash"), row.get("command"))
        if tool_id:
            return ("tool", run_id, tool_id)
        return ("tool", run_id, str(row.get("step_index") or ""), str(row.get("item_index") or index))
    if kind == "step":
        step_id = first_text(row.get("step_id"), row.get("step_index"))
        if not step_id:
            return None
        return ("step", run_id, step_id)
    raise ValueError(f"unknown kind: {kind}")


def has_model_signal(row: dict[str, str]) -> bool:
    item_type = str(row.get("item_type") or row.get("step_type") or "")
    return (
        item_type in {"model", "model_extra"}
        or has_value(row.get("p2_e2e_s"))
        or has_value(row.get("api_request_id"))
        or has_value(row.get("vllm_request_id"))
    )


def model_hotspot_duration(row: dict[str, str]) -> tuple[float | None, str]:
    for field, source in (
        ("model_call_e2e_s", "p2_request_e2e"),
        ("p2_e2e_s", "p2_request_e2e"),
        ("p1_model_span_duration_s", "p1_model_span"),
        ("duration_s", "p1_model_span_legacy"),
    ):
        value = as_float(row.get(field))
        if value is not None:
            return value, source
    return None, "unavailable"


def has_tool_signal(row: dict[str, str]) -> bool:
    item_type = str(row.get("item_type") or "")
    return (
        item_type == "tool"
        or has_value(row.get("tool_call_id"))
        or has_value(row.get("command"))
        or has_value(row.get("approx_tool_duration_s"))
    )


def merge_rows(rows: list[dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    keys = sorted({key for row in rows for key in row})
    for key in keys:
        values = [row.get(key, "") for row in rows if has_value(row.get(key))]
        merged[key] = values[0] if values else ""
    return merged


def summarize_run(
    run_id: str,
    rows: list[dict[str, str]],
    models: list[dict[str, str]],
    tools: list[dict[str, str]],
    steps: list[dict[str, str]],
) -> dict[str, Any]:
    run_duration, run_duration_source = resolve_run_wall(rows, steps)
    model_e2e_values = [
        first_text(row.get("model_call_e2e_s"), row.get("p2_e2e_s")) for row in models
    ]
    p1_model_span_values = [row.get("p1_model_span_duration_s") for row in models]
    tool_duration_values = [
        first_text(row.get("tool_call_duration_s"), row.get("approx_tool_duration_s"), row.get("duration_s"))
        for row in tools
    ]
    model_e2e = sum_numbers(model_e2e_values)
    p1_model_span_time = sum_numbers(p1_model_span_values)
    queue = sum_numbers(row.get("p2_queue_s") for row in models)
    ttft = sum_numbers(row.get("p2_ttft_s") for row in models)
    prefill = sum_numbers(row.get("p2_prefill_s") for row in models)
    prefill_to_first_token = sum_numbers(row.get("p2_prefill_to_first_token_s") for row in models)
    decode = sum_numbers(row.get("p2_decode_s") for row in models)
    tool_duration = sum_numbers(tool_duration_values)
    prompt = sum_numbers(row.get("p2_prompt_tokens") or row.get("prompt_tokens") for row in models)
    generation = sum_numbers(row.get("p2_generation_tokens") or row.get("completion_tokens") for row in models)
    cached = sum_numbers(row.get("p2_cached_prompt_tokens") or row.get("cached_tokens") for row in models)
    recomputed = sum_numbers(row.get("p2_recomputed_prompt_tokens") for row in models)
    agent_cpu = sum_numbers(row.get("step_agent_cpu_time_s") for row in steps)
    tool_cpu = sum_numbers(row.get("tool_cpu_time_s") for row in tools)
    target_peak_hbm = max_number(
        *(row.get("step_target_peak_hbm_used_bytes") for row in steps),
        *(row.get("model_target_peak_hbm_used_bytes") for row in models),
    )
    target_avg_hbm = mean_number(row.get("step_target_avg_hbm_used_bytes") for row in steps)
    target_max_aicore = max_number(
        *(row.get("step_target_max_aicore_util_pct") for row in steps),
        *(row.get("model_target_max_aicore_util_pct") for row in models),
    )
    target_avg_aicore = mean_number(row.get("step_target_avg_aicore_util_pct") for row in steps)
    kv_usage_after = max_number(
        *(row.get("p2_kv_usage_after") for row in models),
        *(row.get("step_kv_usage_ratio_after_max") for row in steps),
        *(row.get("step_kv_snapshot_usage_ratio") for row in steps),
    )
    evicted = sum_numbers(row.get("p2_evicted_block_count") for row in models)
    scheduler_running_values = preferred_metric_values(
        models,
        "model_scheduler_running_requests_max",
        steps,
        "step_scheduler_running_requests_max",
    )
    scheduler_waiting_values = preferred_metric_values(
        models,
        "model_scheduler_waiting_requests_max",
        steps,
        "step_scheduler_waiting_requests_max",
    )
    scheduler_scheduled_token_values = preferred_metric_values(
        models,
        "model_scheduler_scheduled_tokens_max",
        steps,
        "step_scheduler_scheduled_tokens_max",
    )
    scheduler_scopes = sorted(
        {
            str(value)
            for value in (
                *(row.get("model_scheduler_observation_scope") for row in models),
                *(row.get("step_scheduler_observation_scope") for row in steps),
            )
            if has_value(value)
        }
    )
    nonzero = sum(1 for row in tools if is_nonzero_returncode(row.get("returncode")))
    timeouts = sum(1 for row in tools if truthy(row.get("timed_out")))
    run_coverage = {
        "model_e2e_s": field_coverage_any(models, ["model_call_e2e_s", "p2_e2e_s"]),
        "p1_model_span_duration_s": field_coverage(models, "p1_model_span_duration_s"),
        "model_queue_s": field_coverage(models, "p2_queue_s"),
        "model_ttft_s": field_coverage(models, "p2_ttft_s"),
        "model_prefill_s": field_coverage(models, "p2_prefill_s"),
        "model_prefill_to_first_token_s": field_coverage(models, "p2_prefill_to_first_token_s"),
        "model_decode_s": field_coverage(models, "p2_decode_s"),
        "model_queue_quality": accepted_quality_coverage(
            models,
            "p2_queue_s",
            "p2_queue_quality",
            {"engine_process_monotonic"},
        ),
        "model_ttft_quality": accepted_quality_coverage(
            models,
            "p2_ttft_s",
            "p2_ttft_quality",
            {"same_host_cross_process_monotonic", "same_process_monotonic"},
        ),
        "model_prefill_quality": accepted_quality_coverage(
            models,
            "p2_prefill_s",
            "p2_prefill_quality",
            {
                "engine_process_monotonic",
                "same_process_monotonic",
                "same_host_cross_process_monotonic",
            },
        ),
        "model_decode_quality": accepted_quality_coverage(
            models,
            "p2_decode_s",
            "p2_decode_quality",
            {"engine_process_monotonic"},
        ),
        "prompt_tokens": field_coverage_any(models, ["p2_prompt_tokens", "prompt_tokens"]),
        "generation_tokens": field_coverage_any(models, ["p2_generation_tokens", "completion_tokens"]),
        "cached_prompt_tokens": field_coverage_any(models, ["p2_cached_prompt_tokens", "cached_tokens"]),
        "recomputed_prompt_tokens": field_coverage(models, "p2_recomputed_prompt_tokens"),
        "tool_duration_s": field_coverage_any(
            tools,
            ["tool_call_duration_s", "approx_tool_duration_s", "duration_s"],
        ),
        "agent_cpu_time_s": field_coverage(steps, "step_agent_cpu_time_s"),
        "tool_cpu_time_s": field_coverage(tools, "tool_cpu_time_s"),
        "target_aicore_util_pct": field_coverage_any(
            steps,
            ["step_target_avg_aicore_util_pct", "step_target_max_aicore_util_pct"],
        ),
        "kv_evicted_block_count": field_coverage(models, "p2_evicted_block_count"),
        "scheduler_running_requests": preferred_field_coverage(
            models,
            "model_scheduler_running_requests_max",
            steps,
            "step_scheduler_running_requests_max",
        ),
        "scheduler_waiting_requests": preferred_field_coverage(
            models,
            "model_scheduler_waiting_requests_max",
            steps,
            "step_scheduler_waiting_requests_max",
        ),
    }
    cache_reuse_rate = (
        ratio(cached, prompt)
        if enough_coverage(run_coverage["prompt_tokens"], run_coverage["cached_prompt_tokens"])
        else None
    )
    recomputed_prompt_rate = (
        ratio(recomputed, prompt)
        if enough_coverage(run_coverage["prompt_tokens"], run_coverage["recomputed_prompt_tokens"])
        else None
    )

    summary = {
        "run_id": run_id,
        "step_count": len(steps),
        "model_request_count": len(models),
        "tool_call_count": len(tools),
        "model_extra_count": sum(1 for row in models if row.get("step_type") == "model_extra"),
        "run_wall_s": round_or_none(run_duration),
        "run_wall_source": run_duration_source,
        "run_model_e2e_sum_s": round_or_none(model_e2e),
        "run_p1_model_span_sum_s": round_or_none(p1_model_span_time),
        "run_tool_duration_sum_s": round_or_none(tool_duration),
        "run_model_queue_sum_s": round_or_none(queue),
        "run_model_ttft_sum_s": round_or_none(ttft),
        "run_model_prefill_sum_s": round_or_none(prefill),
        "run_model_prefill_to_first_token_sum_s": round_or_none(prefill_to_first_token),
        "run_model_decode_sum_s": round_or_none(decode),
        "model_call_e2e_p50_s": percentile(model_e2e_values, 50),
        "model_call_e2e_p95_s": percentile(model_e2e_values, 95),
        "model_call_e2e_max_s": max_number(*model_e2e_values),
        "tool_call_duration_p50_s": percentile(tool_duration_values, 50),
        "tool_call_duration_p95_s": percentile(tool_duration_values, 95),
        "tool_call_duration_max_s": max_number(*tool_duration_values),
        "run_duration_s": round_or_none(run_duration),
        "model_e2e_total_s": round_or_none(model_e2e),
        "tool_duration_total_s": round_or_none(tool_duration),
        "queue_total_s": round_or_none(queue),
        "ttft_total_s": round_or_none(ttft),
        "prefill_total_s": round_or_none(prefill),
        "prefill_to_first_token_total_s": round_or_none(prefill_to_first_token),
        "decode_total_s": round_or_none(decode),
        "model_time_share": ratio(model_e2e, run_duration),
        "tool_time_share": ratio(tool_duration, run_duration),
        "queue_ratio": ratio(queue, model_e2e),
        "ttft_ratio": ratio(ttft, model_e2e),
        "prefill_ratio": ratio(prefill, model_e2e),
        "decode_ratio": ratio(decode, model_e2e),
        "shared_scheduler_running_requests_max": max_number(*scheduler_running_values),
        "shared_scheduler_running_requests_mean": round_or_none(mean_number(scheduler_running_values)),
        "shared_scheduler_waiting_requests_max": max_number(*scheduler_waiting_values),
        "shared_scheduler_waiting_requests_mean": round_or_none(mean_number(scheduler_waiting_values)),
        "shared_scheduler_scheduled_tokens_max": max_number(*scheduler_scheduled_token_values),
        "scheduler_observation_scope": ",".join(scheduler_scopes) if scheduler_scopes else None,
        "prompt_tokens": int(prompt) if prompt is not None else None,
        "generation_tokens": int(generation) if generation is not None else None,
        "cached_prompt_tokens": int(cached) if cached is not None else None,
        "cache_reuse_rate": cache_reuse_rate,
        "recomputed_prompt_rate": recomputed_prompt_rate,
        "decode_tokens_per_s": (
            ratio(generation, decode)
            if enough_coverage(
                run_coverage["model_decode_quality"],
                run_coverage["generation_tokens"],
            )
            else None
        ),
        "tool_nonzero_return_count": nonzero,
        "tool_timeout_count": timeouts,
        "tool_timeout_rate": ratio(timeouts, len(tools)),
        "agent_cpu_time_s": round_or_none(agent_cpu),
        "agent_cpu_wall_ratio": ratio(agent_cpu, run_duration),
        "tool_cpu_time_s": round_or_none(tool_cpu),
        "tool_cpu_wall_ratio": ratio(tool_cpu, tool_duration),
        "target_peak_hbm_used_bytes": int(target_peak_hbm) if target_peak_hbm is not None else None,
        "target_avg_hbm_used_bytes": round_or_none(target_avg_hbm),
        "target_max_aicore_util_pct": round_or_none(target_max_aicore),
        "target_avg_aicore_util_pct": round_or_none(target_avg_aicore),
        "kv_usage_after_max": round_or_none(kv_usage_after),
        "kv_evicted_block_count": int(evicted) if evicted is not None else None,
        "coverage": run_coverage,
    }
    labels, warnings = classify_run(summary, models)
    summary["bottleneck"] = ",".join(labels) if labels else "balanced_or_unknown"
    summary["warnings"] = "; ".join(warnings)
    return summary


def resolve_run_wall(
    rows: list[dict[str, str]],
    steps: list[dict[str, str]],
) -> tuple[float | None, str]:
    explicit = [to_float(row.get("run_wall_s")) for row in rows]
    explicit_values = [value for value in explicit if value is not None]
    if explicit_values:
        source = first_text(*(row.get("run_wall_source") for row in rows)) or "run_wall_s"
        return max(explicit_values), source
    ends: list[float] = []
    for row in steps or rows:
        start = to_float(
            row.get("step_start_offset_from_run_s")
            or row.get("item_start_offset_from_run_s")
        )
        duration = to_float(row.get("step_duration_s") or row.get("duration_s"))
        if start is not None and duration is not None:
            ends.append(start + duration)
        elif start is not None:
            ends.append(start)
    if ends:
        return max(ends), "estimated_from_run_relative_offsets"
    return None, "unavailable"


def classify_run(summary: dict[str, Any], models: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    warnings: list[str] = []
    run_coverage = as_dict(summary.get("coverage"))
    model_share = as_float(summary.get("model_time_share"))
    tool_share = as_float(summary.get("tool_time_share"))
    queue_ratio = as_float(summary.get("queue_ratio"))
    ttft_ratio = as_float(summary.get("ttft_ratio"))
    prefill_ratio = as_float(summary.get("prefill_ratio"))
    decode_ratio = as_float(summary.get("decode_ratio"))
    scheduler_waiting_max = as_float(summary.get("shared_scheduler_waiting_requests_max"))
    cache_reuse = as_float(summary.get("cache_reuse_rate"))
    recomputed = as_float(summary.get("recomputed_prompt_rate"))
    agent_cpu_ratio = as_float(summary.get("agent_cpu_wall_ratio"))
    tool_cpu_ratio = as_float(summary.get("tool_cpu_wall_ratio"))
    target_avg_aicore = as_float(summary.get("target_avg_aicore_util_pct"))

    model_latency_ok = enough_coverage(run_coverage.get("model_e2e_s"))
    queue_ok = enough_coverage(
        run_coverage.get("model_queue_s"),
        run_coverage.get("model_queue_quality"),
        run_coverage.get("model_e2e_s"),
    )
    ttft_ok = enough_coverage(
        run_coverage.get("model_ttft_s"),
        run_coverage.get("model_ttft_quality"),
        run_coverage.get("model_e2e_s"),
    )
    prefill_ok = enough_coverage(
        run_coverage.get("model_prefill_s"),
        run_coverage.get("model_prefill_quality"),
        run_coverage.get("model_e2e_s"),
    )
    decode_ok = enough_coverage(
        run_coverage.get("model_decode_s"),
        run_coverage.get("model_decode_quality"),
        run_coverage.get("model_e2e_s"),
    )
    tool_duration_ok = enough_coverage(run_coverage.get("tool_duration_s")) if summary.get("tool_call_count", 0) else True
    agent_cpu_ok = enough_coverage(run_coverage.get("agent_cpu_time_s"))
    tool_cpu_ok = enough_coverage(run_coverage.get("tool_cpu_time_s")) if summary.get("tool_call_count", 0) else True
    cache_ok = enough_coverage(run_coverage.get("prompt_tokens"), run_coverage.get("cached_prompt_tokens"))
    recompute_ok = enough_coverage(run_coverage.get("prompt_tokens"), run_coverage.get("recomputed_prompt_tokens"))
    npu_ok = enough_coverage(run_coverage.get("target_aicore_util_pct"))
    kv_eviction_ok = enough_coverage(run_coverage.get("kv_evicted_block_count"))
    scheduler_waiting_ok = enough_coverage(run_coverage.get("scheduler_waiting_requests"))

    if model_share is not None and model_share >= 0.60 and model_latency_ok:
        labels.append("model_wait_bound")
    if (
        tool_share is not None
        and tool_share >= 0.35
        and number(summary.get("run_tool_duration_sum_s", summary.get("tool_duration_total_s"))) >= 1
        and tool_duration_ok
    ):
        labels.append("tool_wait_bound")
    if queue_ratio is not None and queue_ratio >= 0.20 and queue_ok:
        labels.append("vllm_queue_pressure")
    if ttft_ratio is not None and ttft_ratio >= 0.30 and ttft_ok:
        labels.append("ttft_heavy")
    if prefill_ratio is not None and prefill_ratio >= 0.30 and prefill_ok:
        labels.append("prefill_heavy")
    if decode_ratio is not None and decode_ratio >= 0.60 and decode_ok:
        labels.append("decode_heavy")
    if scheduler_waiting_max is not None and scheduler_waiting_max > 0 and scheduler_waiting_ok:
        labels.append("shared_scheduler_waiting_pressure")
    if agent_cpu_ratio is not None and agent_cpu_ratio >= 0.50 and agent_cpu_ok:
        labels.append("agent_cpu_bound")
    if tool_cpu_ratio is not None and tool_cpu_ratio >= 0.60 and tool_cpu_ok:
        labels.append("tool_cpu_bound")
    if number(summary.get("kv_evicted_block_count")) > 0 and kv_eviction_ok:
        labels.append("kv_eviction_pressure")
    if cache_reuse is not None and cache_reuse < 0.10 and number(summary.get("prompt_tokens")) >= 1000 and cache_ok:
        labels.append("low_cache_reuse")
    if recomputed is not None and recomputed >= 0.25 and recompute_ok:
        labels.append("high_prompt_recompute")
    if (
        model_share is not None
        and model_share >= 0.50
        and model_latency_ok
        and target_avg_aicore is not None
        and target_avg_aicore < 20.0
        and npu_ok
    ):
        labels.append("model_wait_with_low_aicore")

    if summary.get("model_request_count", 0) and not model_latency_ok:
        warnings.append("insufficient model latency coverage for strong model bottleneck diagnosis")
    if summary.get("model_request_count", 0) and not queue_ok:
        warnings.append("insufficient or unverified queue coverage for vLLM queue diagnosis")
    if summary.get("model_request_count", 0) and not ttft_ok:
        warnings.append("insufficient or unverified TTFT coverage for first-token diagnosis")
    if summary.get("model_request_count", 0) and not prefill_ok:
        warnings.append("explicit prefill timing is unavailable or insufficient; do not infer it from TTFT")
    if summary.get("model_request_count", 0) and not decode_ok:
        warnings.append("insufficient or unverified decode coverage for generation bottleneck diagnosis")
    if summary.get("run_wall_source") != "manifest.duration_ns":
        warnings.append("run wall time is not sourced from manifest.duration_ns")
    if summary.get("tool_call_count", 0) and not tool_duration_ok:
        warnings.append("insufficient tool duration coverage for tool bottleneck diagnosis")
    if summary.get("tool_call_count", 0) and not tool_cpu_ok:
        warnings.append("insufficient tool CPU coverage for tool CPU-bound diagnosis")
    if summary.get("step_count", 0) and not agent_cpu_ok:
        warnings.append("insufficient agent CPU coverage for agent CPU-bound diagnosis")
    if summary.get("model_request_count", 0) and not cache_ok:
        warnings.append("insufficient cached-token coverage for cache reuse diagnosis")
    if summary.get("model_request_count", 0) and not recompute_ok:
        warnings.append("insufficient recomputed-token coverage for prompt recompute diagnosis")
    if summary.get("model_request_count", 0) and not npu_ok:
        warnings.append("insufficient target NPU coverage for hardware-utilization diagnosis")
    if summary.get("model_request_count", 0) and not kv_eviction_ok:
        warnings.append("insufficient KV eviction coverage for KV pressure diagnosis")
    if scheduler_waiting_max is not None:
        warnings.append("scheduler values are shared-service window observations, not request-owned metrics")

    if summary.get("model_extra_count", 0) > 0:
        warnings.append("P2 has model_extra requests that are not in trajectory steps")
    if any(row.get("alignment_quality") == "ordinal_fallback" for row in models):
        warnings.append("some model requests used ordinal fallback alignment")
    if any(row.get("p2_correlation_quality") == "service_stream" for row in models):
        warnings.append("P2 attribution is service_stream; verify no unrelated traffic")
    if summary.get("tool_nonzero_return_count", 0) > 0:
        warnings.append("tool commands returned nonzero")
    if summary.get("tool_timeout_count", 0) > 0:
        warnings.append("tool timeout observed")
    return labels, warnings


def summarize_overview(
    rows: list[dict[str, str]],
    run_summaries: list[dict[str, Any]],
    models: list[dict[str, str]],
    tools: list[dict[str, str]],
) -> dict[str, Any]:
    durations = [as_float(row.get("run_wall_s")) for row in run_summaries]
    model_e2e = [as_float(row.get("run_model_e2e_sum_s")) for row in run_summaries]
    tool_duration = [as_float(row.get("run_tool_duration_sum_s")) for row in run_summaries]
    return {
        "row_count": len(rows),
        "run_count": len(run_summaries),
        "step_count": sum(int(row.get("step_count") or 0) for row in run_summaries),
        "model_request_count": len(models),
        "tool_call_count": len(tools),
        "trajectory_tool_call_count": sum(row.get("status") == "trajectory_fallback" for row in tools),
        "p1_tool_span_count": sum(
            bool(row.get("span_id")) and row.get("status") != "trajectory_fallback" for row in tools
        ),
        "tool_resource_complete_count": sum(
            all(has_value(row.get(field)) for field in ("tool_cpu_time_s", "tool_peak_rss_bytes", "tool_peak_pss_bytes"))
            for row in tools
        ),
        "analyzer_tool_row_count": len(tools),
        "model_extra_count": sum(int(row.get("model_extra_count") or 0) for row in run_summaries),
        "run_wall_p50_s": percentile(durations, 50),
        "run_wall_p95_s": percentile(durations, 95),
        "run_model_e2e_sum_p50_s": percentile(model_e2e, 50),
        "run_tool_duration_sum_p50_s": percentile(tool_duration, 50),
        # Backward-compatible aliases for existing consumers.
        "run_duration_p50_s": percentile(durations, 50),
        "run_duration_p95_s": percentile(durations, 95),
        "model_e2e_total_p50_s": percentile(model_e2e, 50),
        "tool_duration_total_p50_s": percentile(tool_duration, 50),
        "bottleneck_counts": count_labels(row.get("bottleneck") for row in run_summaries),
    }


def summarize_quality(
    rows: list[dict[str, str]],
    models: list[dict[str, str]],
    tools: list[dict[str, str]],
    steps: list[dict[str, str]],
) -> dict[str, Any]:
    alignment_counts = count_values(row.get("alignment_quality") for row in rows)
    status_counts = count_values(row.get("alignment_status") for row in rows)
    p2_quality_counts = count_values(row.get("p2_correlation_quality") for row in rows)
    p2_match_status_counts = count_values(row.get("p2_match_status") for row in models)
    p2_match_method_counts = count_values(row.get("p2_match_method") for row in models)
    p2_missing_reason_counts = count_values(row.get("p2_missing_reason") for row in models)
    expected_models = [row for row in models if row.get("step_type") != "model_extra"]
    matched_models = [row for row in expected_models if model_has_auditable_p2_match(row)]
    p2_evidence_models = [row for row in models if model_has_p2_evidence(row)]
    lifecycle_complete_models = [
        row for row in p2_evidence_models if row.get("p2_lifecycle_status") == "complete"
    ]
    model_coverage_fields = [
        "p2_e2e_s",
        "p2_queue_s",
        "p2_ttft_s",
        "p2_prefill_s",
        "p2_prefill_to_first_token_s",
        "p2_decode_s",
        "p2_prompt_tokens",
        "p2_generation_tokens",
        "p2_kv_usage_after",
        "model_scheduler_waiting_requests_max",
    ]
    step_coverage_fields = [
        "step_target_peak_hbm_used_bytes",
        "step_target_avg_aicore_util_pct",
    ]
    tool_coverage_fields = [
        "tool_cpu_time_s",
    ]
    field_domains = {
        **{field: "model_request" for field in model_coverage_fields},
        **{field: "agent_step" for field in step_coverage_fields},
        **{field: "tool_call" for field in tool_coverage_fields},
    }
    domain_rows = {"model_request": models, "agent_step": steps, "tool_call": tools}
    return {
        "alignment_quality_counts": alignment_counts,
        "alignment_status_counts": status_counts,
        "p2_correlation_quality_counts": p2_quality_counts,
        "p2_match_status_counts": p2_match_status_counts,
        "p2_match_method_counts": p2_match_method_counts,
        "p2_missing_reason_counts": p2_missing_reason_counts,
        "p2_expected_model_count": len(expected_models),
        "p2_matched_model_count": len(matched_models),
        "p2_unmatched_model_count": len(expected_models) - len(matched_models),
        "p2_match_rate": ratio(len(matched_models), len(expected_models)),
        "p2_evidence_request_count": len(p2_evidence_models),
        "p2_lifecycle_complete_count": len(lifecycle_complete_models),
        "p2_lifecycle_complete_rate": ratio(len(lifecycle_complete_models), len(p2_evidence_models)),
        "p2_clock_domain_mismatch_count": sum(
            any(
                row.get(field) == "clock_domain_mismatch"
                for field in (
                    "p2_queue_quality",
                    "p2_ttft_quality",
                    "p2_prefill_quality",
                    "p2_decode_quality",
                    "p2_e2e_quality",
                )
            )
            for row in models
        ),
        "coverage": {
            field: {**coverage(domain_rows[domain], field), "denominator": domain}
            for field, domain in field_domains.items()
        },
        "model_rows_without_response_id": sum(
            1 for row in models if not first_text(row.get("api_request_id"), row.get("response_id"))
        ),
        "model_extra_count": sum(1 for row in models if row.get("step_type") == "model_extra"),
    }


def model_has_p2_evidence(row: dict[str, str]) -> bool:
    return any(
        has_value(row.get(field))
        for field in (
            "p2_match_status",
            "p2_lifecycle_status",
            "p2_e2e_s",
            "p2_queue_s",
            "p2_ttft_s",
            "p2_decode_s",
            "p2_prompt_tokens",
            "p2_generation_tokens",
            "vllm_request_id",
        )
    )


def model_has_auditable_p2_match(row: dict[str, str]) -> bool:
    status = str(row.get("p2_match_status") or "")
    if status == "matched":
        return True
    if status:
        return False
    response_id = first_text(row.get("response_id"))
    api_request_id = first_text(row.get("api_request_id"))
    return bool(
        response_id
        and api_request_id
        and response_id == api_request_id
        and model_has_p2_evidence(row)
    )


def build_analysis_quality_bundle(
    overview: dict[str, Any],
    quality: dict[str, Any],
    *,
    min_p2_match_rate: float | None,
    min_p2_lifecycle_rate: float | None,
) -> dict[str, dict[str, Any]]:
    expected = int(quality.get("p2_expected_model_count") or 0)
    evidence = int(quality.get("p2_evidence_request_count") or 0)
    match_rate = as_float(quality.get("p2_match_rate"))
    lifecycle_rate = as_float(quality.get("p2_lifecycle_complete_rate"))
    checks = [
        analysis_quality_check("input_rows_present", int(overview.get("row_count") or 0) > 0),
        analysis_quality_check("run_ids_present", int(overview.get("run_count") or 0) > 0),
        analysis_quality_check(
            "p2_request_match_rate",
            expected == 0 or (match_rate is not None and (min_p2_match_rate is None or match_rate >= min_p2_match_rate)),
            details={
                "expected_model_count": expected,
                "matched_model_count": quality.get("p2_matched_model_count"),
                "actual_rate": match_rate,
                "minimum_rate": min_p2_match_rate,
            },
        ),
        analysis_quality_check(
            "p2_lifecycle_complete_rate",
            evidence == 0 or (lifecycle_rate is not None and (min_p2_lifecycle_rate is None or lifecycle_rate >= min_p2_lifecycle_rate)),
            details={
                "p2_evidence_request_count": evidence,
                "complete_count": quality.get("p2_lifecycle_complete_count"),
                "actual_rate": lifecycle_rate,
                "minimum_rate": min_p2_lifecycle_rate,
            },
        ),
        analysis_quality_check(
            "p2_clock_domain_integrity",
            int(quality.get("p2_clock_domain_mismatch_count") or 0) == 0,
            details={"mismatch_count": quality.get("p2_clock_domain_mismatch_count")},
        ),
        analysis_quality_check(
            "workload_model_rows_present",
            int(overview.get("model_request_count") or 0) > 0,
            blocking=False,
        ),
    ]
    blocking_failures = [item["name"] for item in checks if item["blocking"] and not item["passed"]]
    status = "PUBLISHABLE" if not blocking_failures else "UNPUBLISHABLE"
    limitations: list[dict[str, str]] = [
        {
            "code": "shared_service_resources",
            "message": "Scheduler, NPU, HBM and service KV values are window-overlap observations unless exact attribution is stated.",
        }
    ]
    if evidence == 0 and expected > 0:
        limitations.append(
            {"code": "p2_missing", "message": "P1 model hotspots remain usable, but P2 timing and KV analysis are unavailable."}
        )
    if min_p2_match_rate is not None and match_rate is not None and match_rate < min_p2_match_rate:
        limitations.append(
            {"code": "p2_low_match_rate", "message": "Request-level P2 attribution is below the configured publication threshold."}
        )
    if quality.get("alignment_quality_counts", {}).get("ordinal_fallback", 0):
        limitations.append(
            {"code": "ordinal_alignment", "message": "Ordinal fallback rows have lower attribution confidence than response-ID matches."}
        )
    return {
        "validation_checks": {
            "schema_version": "1.0",
            "validation_type": "agentmon.step_csv_analysis",
            "publishability_status": status,
            "blocking_failures": blocking_failures,
            "checks": checks,
        },
        "metric_definition": {
            "schema_version": "1.0",
            "time_unit": "seconds unless the field name ends in _ns",
            "latency": {
                "p1_model_span_duration_s": "agent-side model request wall span",
                "p2_e2e_s": "API received to API finished",
                "p2_queue_s": "engine queued to first scheduled",
                "p2_ttft_s": "API received to EngineCore first token",
                "p2_prefill_s": "first scheduled to explicit prefill-completed event; null without direct evidence",
                "p2_prefill_to_first_token_s": "proxy window from first scheduled to first token; not identical to explicit prefill",
                "p2_decode_s": "EngineCore first token to EngineCore finished",
            },
            "aggregation_scope": {
                "model_call_*": "one model request",
                "run_model_*_sum_s": "sum across model requests in one run",
                "run_wall_s": "one run wall-clock duration",
                "shared_scheduler_*": "shared vLLM service observed during request/step windows",
            },
        },
        "data_quality": {
            "schema_version": "1.0",
            "status": status,
            "quality": quality,
            "blocking_failures": blocking_failures,
        },
        "confidence": {
            "schema_version": "1.0",
            "p2_match_rate": match_rate,
            "p2_lifecycle_complete_rate": lifecycle_rate,
            "alignment_quality_counts": quality.get("alignment_quality_counts", {}),
            "p2_correlation_quality_counts": quality.get("p2_correlation_quality_counts", {}),
        },
        "limitations": {"schema_version": "1.0", "limitations": limitations},
    }


def analysis_quality_check(
    name: str,
    passed: bool,
    *,
    blocking: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "blocking": blocking, "details": details or {}}


def summarize_tool_timeouts(tools: list[dict[str, str]]) -> dict[str, Any]:
    timed_out = [row for row in tools if truthy(row.get("timed_out")) or row.get("tool_status") == "timeout"]
    by_executable = count_values(
        first_text(row.get("executable"), row.get("tool_name"), "unknown") for row in timed_out
    )
    return {
        "tool_call_count": len(tools),
        "timeout_count": len(timed_out),
        "timeout_rate": ratio(len(timed_out), len(tools)),
        "by_executable": by_executable,
        "timeout_limits_s": count_values(row.get("timeout_limit_s") for row in timed_out),
    }


def field_coverage(rows: list[dict[str, str]], field: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if has_value(row.get(field))) / len(rows)


def field_coverage_any(rows: list[dict[str, str]], fields: list[str]) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if any(has_value(row.get(field)) for field in fields)) / len(rows)


def preferred_metric_values(
    primary_rows: list[dict[str, str]],
    primary_field: str,
    fallback_rows: list[dict[str, str]],
    fallback_field: str,
) -> list[Any]:
    primary = [row.get(primary_field) for row in primary_rows if has_value(row.get(primary_field))]
    if primary:
        return primary
    return [row.get(fallback_field) for row in fallback_rows if has_value(row.get(fallback_field))]


def preferred_field_coverage(
    primary_rows: list[dict[str, str]],
    primary_field: str,
    fallback_rows: list[dict[str, str]],
    fallback_field: str,
) -> float | None:
    if any(has_value(row.get(primary_field)) for row in primary_rows):
        return field_coverage(primary_rows, primary_field)
    return field_coverage(fallback_rows, fallback_field)


def accepted_quality_coverage(
    rows: list[dict[str, str]],
    value_field: str,
    quality_field: str,
    accepted: set[str],
) -> float | None:
    if not rows:
        return None
    return sum(
        1
        for row in rows
        if has_value(row.get(value_field)) and str(row.get(quality_field) or "") in accepted
    ) / len(rows)


def enough_coverage(*values: Any, minimum: float = MIN_STRONG_COVERAGE) -> bool:
    usable = [as_float(value) for value in values if value is not None]
    return bool(usable) and all(value is not None and value >= minimum for value in usable)


def build_recommendations(
    run_summaries: list[dict[str, Any]],
    quality: dict[str, Any],
    model_hotspots: list[dict[str, str]],
    tool_hotspots: list[dict[str, str]],
) -> list[str]:
    labels = count_labels(row.get("bottleneck") for row in run_summaries)
    notes: list[str] = []
    if labels.get("vllm_queue_pressure", 0):
        notes.append("Queue pressure is visible: inspect concurrent batch size and vLLM scheduling capacity.")
    if labels.get("decode_heavy", 0):
        notes.append("Decode dominates model time: compare generation token counts and decode tokens/s.")
    if labels.get("ttft_heavy", 0):
        notes.append("TTFT is heavy: inspect API processing, tokenization, queueing, prefill evidence, and first-token delivery separately.")
    if labels.get("prefill_heavy", 0):
        notes.append("Explicit prefill time is high: inspect prompt length, computed prefill tokens, and cache reuse.")
    if labels.get("shared_scheduler_waiting_pressure", 0):
        notes.append("Shared scheduler waiting requests were observed: compare concurrency and service scheduling capacity.")
    if labels.get("tool_wait_bound", 0):
        notes.append("Tool time is significant: group by command_hash/executable and inspect nonzero return codes.")
    if labels.get("low_cache_reuse", 0) or labels.get("high_prompt_recompute", 0):
        notes.append("Cache reuse looks weak for some runs: check whether prefix cache is disabled or request prefixes diverge.")
    if labels.get("kv_eviction_pressure", 0):
        notes.append("KV evictions were observed: inspect KV capacity, max context, and concurrent request pressure.")
    if labels.get("model_wait_with_low_aicore", 0):
        notes.append("Model wait is high while AI Core utilization is low: inspect server-side scheduling or data pipeline stalls.")
    if quality.get("model_extra_count", 0):
        notes.append("Some P2 requests have no trajectory step; keep them in model totals but do not assign them to nearby steps.")
    if quality["alignment_quality_counts"].get("ordinal_fallback", 0):
        notes.append("Some rows used ordinal fallback alignment; treat those step-level attributions as lower confidence.")
    if model_hotspots:
        slow = model_hotspots[0]
        notes.append(
            "Slowest model request: "
            f"run={slow.get('run_id')} step={slow.get('step_index')} "
            f"e2e={display_number(slow.get('model_call_e2e_s') or slow.get('p2_e2e_s'))}s."
        )
    if tool_hotspots and number(
        tool_hotspots[0].get("tool_call_duration_s") or tool_hotspots[0].get("approx_tool_duration_s")
    ) > 0:
        slow_tool = tool_hotspots[0]
        notes.append(
            "Slowest tool call: "
            f"run={slow_tool.get('run_id')} step={slow_tool.get('step_index')} "
            f"duration={display_number(slow_tool.get('tool_call_duration_s') or slow_tool.get('approx_tool_duration_s'))}s "
            f"cmd={truncate(str(slow_tool.get('command') or ''), 80)}."
        )
    if not notes:
        notes.append("No dominant bottleneck detected from available columns; inspect coverage and raw hotspots.")
    return notes


def format_analysis(analysis: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(analysis, indent=2, ensure_ascii=False)
    if fmt == "csv":
        return rows_to_csv(analysis["run_summaries"], RUN_SUMMARY_FIELDS)
    return format_report(analysis)


def format_report(analysis: dict[str, Any]) -> str:
    overview = analysis["overview"]
    lines = [
        "AgentMon step CSV automatic analysis",
        "",
        f"Publication status: {analysis['analysis_publication']['status']}",
        f"Blocking failures: {analysis['analysis_publication']['blocking_failures']}",
        "",
        "Overview",
        f"- runs: {overview['run_count']}",
        f"- rows: {overview['row_count']}",
        f"- steps: {overview['step_count']}",
        f"- model requests: {overview['model_request_count']}",
        f"- tool calls: {overview['tool_call_count']}",
        f"- model_extra requests: {overview['model_extra_count']}",
        f"- run wall p50/p95: {fmt_s(overview['run_wall_p50_s'])} / {fmt_s(overview['run_wall_p95_s'])}",
        "",
        "Bottleneck counts",
    ]
    for label, count in sorted(overview["bottleneck_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {label}: {count}")

    lines.extend(["", "Top runs"])
    lines.extend(format_run_table(analysis["run_summaries"][:10]))
    lines.extend(["", "Slowest model requests"])
    lines.extend(format_model_table(analysis["model_hotspots"]))
    lines.extend(["", "Slowest tool calls"])
    lines.extend(format_tool_table(analysis["tool_hotspots"]))
    timeout_summary = analysis["tool_timeouts"]
    lines.extend(
        [
            "",
            "Tool timeouts",
            f"- count/rate: {timeout_summary['timeout_count']}/{timeout_summary['tool_call_count']} "
            f"({fmt_pct(timeout_summary['timeout_rate'])})",
            f"- by executable: {timeout_summary['by_executable']}",
        ]
    )
    lines.extend(["", "Data quality"])
    lines.extend(format_quality(analysis["quality"]))
    lines.extend(["", "Recommendations"])
    for note in analysis["recommendations"]:
        lines.append(f"- {note}")
    return "\n".join(lines)


def format_run_table(rows: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(rows, key=lambda row: as_float(row.get("run_wall_s")) or 0.0, reverse=True)
    lines = ["run_id | run wall | model sum | tool sum | queue sum | tokens | bottleneck"]
    lines.append("--- | ---: | ---: | ---: | ---: | ---: | ---")
    for row in ordered[:10]:
        lines.append(
            f"{row['run_id']} | {fmt_s(row.get('run_wall_s'))} | "
            f"{fmt_s(row.get('run_model_e2e_sum_s'))} | {fmt_s(row.get('run_tool_duration_sum_s'))} | "
            f"{fmt_s(row.get('run_model_queue_sum_s'))} | "
            f"{display_number(row.get('prompt_tokens'))}/{display_number(row.get('generation_tokens'))} | "
            f"{row.get('bottleneck') or ''}"
        )
    return lines


def format_model_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["run_id | step | api_request_id | duration | source | queue | ttft | prefill | decode | tokens | cache"]
    lines.append("--- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---:")
    for row in rows:
        lines.append(
            f"{row.get('run_id', '')} | {row.get('step_index', '')} | {row.get('api_request_id', '')} | "
            f"{fmt_s(row.get('model_hotspot_duration_s'))} | {row.get('model_hotspot_duration_source') or ''} | "
            f"{fmt_s(row.get('p2_queue_s'))} | "
            f"{fmt_s(row.get('p2_ttft_s'))} | {fmt_s(row.get('p2_prefill_s'))} | "
            f"{fmt_s(row.get('p2_decode_s'))} | "
            f"{display_number(row.get('p2_prompt_tokens'))}/{display_number(row.get('p2_generation_tokens'))} | "
            f"{fmt_pct(row.get('p2_cache_reuse_rate'))}"
        )
    return lines


def format_tool_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["run_id | step | duration | rc | timeout | cpu_ratio | command"]
    lines.append("--- | ---: | ---: | ---: | --- | ---: | ---")
    for row in rows:
        lines.append(
            f"{row.get('run_id', '')} | {row.get('step_index', '')} | "
            f"{fmt_s(row.get('tool_call_duration_s') or row.get('approx_tool_duration_s'))} | {row.get('returncode', '')} | "
            f"{row.get('timed_out', '')} | {fmt_pct(row.get('tool_cpu_wall_ratio'))} | "
            f"{truncate(str(row.get('command') or ''), 96)}"
        )
    return lines


def format_quality(quality: dict[str, Any]) -> list[str]:
    lines = [
        f"- alignment quality: {quality['alignment_quality_counts']}",
        f"- alignment status: {quality['alignment_status_counts']}",
        f"- P2 correlation quality: {quality['p2_correlation_quality_counts']}",
        f"- P2 match: {quality['p2_matched_model_count']}/{quality['p2_expected_model_count']} "
        f"({fmt_pct(quality['p2_match_rate'])})",
        f"- P2 lifecycle complete: {quality['p2_lifecycle_complete_count']}/{quality['p2_evidence_request_count']} "
        f"({fmt_pct(quality['p2_lifecycle_complete_rate'])})",
        f"- model rows without response id: {quality['model_rows_without_response_id']}",
        f"- model_extra count: {quality['model_extra_count']}",
        "- coverage:",
    ]
    for field, item in quality["coverage"].items():
        lines.append(
            f"  - {field}: {item['present']}/{item['total']} "
            f"({fmt_pct(item['coverage'])}), denominator={item['denominator']}"
        )
    return lines


def write_outputs(analysis: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "run_summary.csv").write_text(
        rows_to_csv(analysis["run_summaries"], RUN_SUMMARY_FIELDS),
        encoding="utf-8",
    )
    (out_dir / "model_hotspots.csv").write_text(
        rows_to_csv(analysis["model_hotspots"], MODEL_HOTSPOT_FIELDS),
        encoding="utf-8",
    )
    (out_dir / "tool_hotspots.csv").write_text(
        rows_to_csv(analysis["tool_hotspots"], TOOL_HOTSPOT_FIELDS),
        encoding="utf-8",
    )
    for filename, payload in analysis["quality_bundle"].items():
        (out_dir / f"{filename}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def rows_to_csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: "" if row.get(field) is None else row.get(field, "") for field in fields})
    return output.getvalue()


def select_fields(row: dict[str, str], fields: list[str]) -> dict[str, Any]:
    return {field: normalize_value(row.get(field)) for field in fields}


def normalize_value(value: Any) -> Any:
    if not has_value(value):
        return None
    parsed = to_float(value)
    if parsed is not None:
        if parsed.is_integer():
            return int(parsed)
        return round(parsed, 6)
    return value


def count_labels(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        for label in str(value or "").split(","):
            label = label.strip()
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
    return counts


def count_values(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not has_value(value):
            continue
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return counts


def rate_argument(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("rate must be a number between 0 and 1") from exc
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("rate must be between 0 and 1")
    return parsed


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def coverage(rows: list[dict[str, str]], field: str) -> dict[str, Any]:
    total = len(rows)
    present = sum(1 for row in rows if has_value(row.get(field)))
    return {"present": present, "total": total, "coverage": ratio(present, total)}


def percentile(values: Iterable[Any], pct: float) -> float | None:
    cleaned = sorted(value for value in (as_float(v) for v in values) if value is not None)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return round(cleaned[0], 6)
    rank = (len(cleaned) - 1) * pct / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(cleaned[int(rank)], 6)
    fraction = rank - lower
    return round(cleaned[lower] * (1 - fraction) + cleaned[upper] * fraction, 6)


def kv_pressure_score(row: dict[str, str]) -> float:
    return (
        number(row.get("p2_evicted_block_count")) * 1000.0
        + number(row.get("p2_kv_usage_after")) * 100.0
        + number(row.get("step_kv_usage_ratio_after_max")) * 100.0
    )


def sum_numbers(values: Iterable[Any]) -> float | None:
    cleaned = [value for value in (as_float(v) for v in values) if value is not None]
    return sum(cleaned) if cleaned else None


def mean_number(values: Iterable[Any]) -> float | None:
    cleaned = [value for value in (as_float(v) for v in values) if value is not None]
    if not cleaned:
        return None
    return statistics.fmean(cleaned)


def max_number(*values: Any) -> float | None:
    cleaned = [value for value in (as_float(v) for v in values) if value is not None]
    return max(cleaned) if cleaned else None


def ratio(numerator: Any, denominator: Any) -> float | None:
    top = as_float(numerator)
    bottom = as_float(denominator)
    if top is None or bottom in {None, 0.0}:
        return None
    return round(top / bottom, 6)


def number(value: Any) -> float:
    parsed = as_float(value)
    return parsed if parsed is not None else 0.0


def to_float(value: Any) -> float | None:
    if not has_value(value):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return to_float(value)


def round_or_none(value: Any, digits: int = 6) -> float | None:
    parsed = as_float(value)
    return round(parsed, digits) if parsed is not None else None


def has_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text.lower() not in MISSING


def first_text(*values: Any) -> str | None:
    for value in values:
        if has_value(value):
            return str(value)
    return None


def is_nonzero_returncode(value: Any) -> bool:
    parsed = as_float(value)
    return parsed is not None and int(parsed) != 0


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def display_number(value: Any) -> str:
    parsed = as_float(value)
    if parsed is None:
        return "N/A"
    if parsed.is_integer():
        return str(int(parsed))
    return f"{parsed:.3f}".rstrip("0").rstrip(".")


def fmt_s(value: Any) -> str:
    parsed = as_float(value)
    return "N/A" if parsed is None else f"{parsed:.3f}s"


def fmt_pct(value: Any) -> str:
    parsed = as_float(value)
    return "N/A" if parsed is None else f"{parsed * 100.0:.1f}%"


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
