#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

from agentmon.reporting.ascend import resolve_ascend_target_devices, summarize_ascend_devices
from agentmon.reporting.report import build_report


SCHEMA_VERSION = "1.1"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = build_perf_summary(
            Path(args.run_dir),
            p2_events=Path(args.p2_events) if args.p2_events else None,
            p2_attribution_quality=args.p2_attribution_quality,
            ascend_devices=args.ascend_devices,
            p2_service_exclusive=args.p2_service_exclusive,
        )
        print(format_summary(summary, args.format))
    except Exception as exc:
        print(f"agentmon perf summary error: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize AgentMon run performance metrics")
    parser.add_argument("run_dir", help="AgentMon run directory containing report.json")
    parser.add_argument("--p2-events", help="optional service-scoped P2 events.jsonl")
    parser.add_argument("--ascend-devices", help="comma-separated physical npu-smi device ids for this run")
    parser.add_argument(
        "--p2-service-exclusive",
        action="store_true",
        help="allow service-scoped P2 token totals to backfill Agent tokens",
    )
    parser.add_argument(
        "--p2-attribution-quality",
        default="service_stream",
        choices=["service_stream", "service_exclusive"],
        help="quality label for an independent P2 service event stream",
    )
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    return parser


def build_perf_summary(
    run_dir: Path,
    *,
    p2_events: Path | None = None,
    p2_attribution_quality: str = "service_stream",
    ascend_devices: str | None = None,
    p2_service_exclusive: bool = False,
) -> dict[str, Any]:
    report = load_report(run_dir)
    p2_event_rows = read_jsonl_events(p2_events) if p2_events is not None else []
    if p2_service_exclusive:
        p2_attribution_quality = "service_exclusive"
    p2_summary = (
        flatten_p2_summary(p2_event_rows, p2_attribution_quality=p2_attribution_quality)
        if p2_events is not None
        else {}
    )
    proc = as_dict(report.get("proc"))
    ascend = as_dict(report.get("ascend"))
    agent = as_dict(report.get("agent"))
    p1 = as_dict(report.get("p1_spans"))
    vllm = as_dict(report.get("vllm"))
    kv_cache = as_dict(report.get("kv_cache"))
    data_quality = as_dict(report.get("data_quality"))
    target_resolution = resolve_ascend_target_devices(explicit_devices=ascend_devices, p2_events=p2_event_rows)
    ascend_devices_summary = summarize_ascend_devices(
        ascend,
        target_devices=target_resolution["devices"],
        target_device_source=target_resolution["source"],
        visible_devices_raw=target_resolution["raw"],
        mapping_quality=target_resolution["mapping_quality"],
    )
    token_fields = resolve_agent_token_fields(agent, p2_summary, p2_service_exclusive=p2_service_exclusive)

    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "summary_type": "agentmon.performance_summary",
        "run_id": report.get("run_id"),
        "run_status": report.get("status"),
        "exit_code": report.get("exit_code"),
        "duration_seconds": report.get("duration_s"),
        "data_quality": data_quality,
        "agent_status": agent.get("status"),
        "agent_exit_status": agent.get("exit_status"),
        "agent_message_count": agent.get("message_count"),
        "agent_assistant_turns": agent.get("assistant_turns"),
        "agent_api_calls": agent.get("api_calls"),
        "agent_tool_calls": agent.get("tool_calls"),
        "agent_instance_cost": agent.get("instance_cost"),
        "p1_status": p1.get("status"),
        "p1_agent_steps": p1.get("agent_steps"),
        "p1_model_requests": p1.get("model_requests"),
        "p1_tool_calls": p1.get("tool_calls"),
        "p1_open_spans": p1.get("open_span_count"),
        "p1_abandoned_spans": p1.get("abandoned_span_count"),
        "proc_status": proc.get("status"),
        "proc_sample_count": proc.get("sample_count"),
        "proc_agent_process_count_last": proc.get("agent_process_count_last"),
        "proc_agent_process_count_max": proc.get("agent_process_count_max"),
        "proc_agent_process_count_active_last": proc.get("agent_process_count_active_last"),
        "proc_agent_peak_pss_bytes": proc.get("agent_peak_pss_bytes"),
        "proc_agent_peak_rss_bytes": proc.get("agent_peak_rss_bytes"),
        "proc_agent_pss_quality": proc.get("agent_pss_quality"),
        "ascend_status": ascend.get("status"),
        "ascend_sample_count": ascend.get("sample_count"),
        "ascend_collection_scope": ascend_devices_summary.get("collection_scope"),
        "ascend_collected_devices": ascend_devices_summary.get("collected_devices"),
        "ascend_visible_devices_raw": ascend_devices_summary.get("visible_devices_raw"),
        "ascend_target_devices": ascend_devices_summary.get("target_devices"),
        "ascend_target_device_source": ascend_devices_summary.get("target_device_source"),
        "ascend_target_device_mapping_quality": ascend_devices_summary.get("target_device_mapping_quality"),
        "ascend_target_attribution_status": ascend_devices_summary.get("target_attribution_status"),
        "ascend_parser_confidence": ascend.get("parser_confidence"),
        "ascend_hbm_quality": ascend.get("hbm_quality"),
        "ascend_aicore_quality": ascend.get("aicore_quality"),
        "ascend_target_peak_hbm_used_bytes": ascend_devices_summary.get("target_peak_hbm_used_bytes"),
        "ascend_target_max_aicore_util_pct": ascend_devices_summary.get("target_max_aicore_util_pct"),
        "ascend_host_peak_hbm_used_bytes": ascend_devices_summary.get("host_peak_hbm_used_bytes"),
        "ascend_host_max_aicore_util_pct": ascend_devices_summary.get("host_max_aicore_util_pct"),
        "ascend_peak_hbm_used_bytes": ascend_devices_summary.get("target_peak_hbm_used_bytes"),
        "ascend_max_aicore_util_pct": ascend_devices_summary.get("target_max_aicore_util_pct"),
        "vllm_status": vllm.get("status"),
        "vllm_sample_count": vllm.get("sample_count"),
        "vllm_metric_count_last": vllm.get("metric_count_last"),
        "vllm_counter_delta_count": len(as_dict(vllm.get("counter_deltas"))),
        "kv_cache_status": kv_cache.get("prefix_cache_status"),
        "kv_cache_hit_rate": None if kv_cache.get("prefix_cache_status") == "disabled" else kv_cache.get("prefix_cache_hit_rate"),
        "kv_cache_hit_rate_quality": kv_cache.get("prefix_cache_hit_rate_quality"),
        "kv_cache_usage_ratio_max": kv_cache.get("kv_cache_usage_ratio_max"),
        "kv_cache_usage_quality": kv_cache.get("kv_cache_usage_quality"),
    }
    row.update(token_fields)
    row.update(p2_summary)
    return row


def load_report(run_dir: Path) -> dict[str, Any]:
    report_path = run_dir / "report.json"
    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))
    return build_report(run_dir)


def read_jsonl_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def flatten_p2_summary(events: list[dict[str, Any]], *, p2_attribution_quality: str) -> dict[str, Any]:
    request_rows: dict[str, dict[str, Any]] = {}
    kv_usage_values: list[float] = []
    latest_prefix_status: str | None = None
    latest_prefix_queries: float | int | None = None
    latest_prefix_hits: float | int | None = None
    for event in events:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        if event_type.startswith("vllm.request."):
            request_id = payload.get("vllm_request_id") or payload.get("request_id") or payload.get("agent_request_id")
            if request_id in {None, ""}:
                continue
            row = request_rows.setdefault(str(request_id), {})
            for key in (
                "received_ns",
                "queued_ns",
                "first_scheduled_ns",
                "finished_ns",
                "prompt_tokens",
                "cached_prompt_tokens",
                "generation_tokens",
                "preemption_count",
            ):
                if payload.get(key) is not None:
                    row[key] = payload.get(key)
        elif event_type == "vllm.kv.snapshot":
            latest_prefix_status = none_if_empty(payload.get("prefix_cache_status"))
            latest_prefix_queries = numeric_or_none(payload.get("prefix_cache_queries"))
            latest_prefix_hits = numeric_or_none(payload.get("prefix_cache_hits"))
            usage = numeric_or_none(payload.get("kv_usage_ratio"))
            if usage is not None:
                kv_usage_values.append(float(usage))
        elif event_type == "vllm.kv.block_event":
            for key in ("kv_usage_before", "kv_usage_after"):
                usage = numeric_or_none(payload.get(key))
                if usage is not None:
                    kv_usage_values.append(float(usage))

    rows = list(request_rows.values())
    prefix_status = latest_prefix_status or "unknown"
    if prefix_status == "disabled":
        prefix_hit_rate = None
    elif latest_prefix_queries is not None and latest_prefix_queries > 0 and latest_prefix_hits is not None:
        prefix_hit_rate = latest_prefix_hits / latest_prefix_queries
    else:
        prefix_hit_rate = None
    return {
        "p2_correlation_scope": "service_stream",
        "p2_attribution_quality": p2_attribution_quality,
        "p2_request_count": len(rows),
        "p2_received_to_queued_ms": latency_ms_stats(
            [ns_delta_ms(row.get("received_ns"), row.get("queued_ns")) for row in rows]
        ),
        "p2_queued_to_first_scheduled_ms": latency_ms_stats(
            [ns_delta_ms(row.get("queued_ns"), row.get("first_scheduled_ns")) for row in rows]
        ),
        "p2_first_scheduled_to_finished_ms": latency_ms_stats(
            [ns_delta_ms(row.get("first_scheduled_ns"), row.get("finished_ns")) for row in rows]
        ),
        "p2_total_ms": latency_ms_stats(
            [ns_delta_ms(row.get("received_ns"), row.get("finished_ns")) for row in rows]
        ),
        "p2_prompt_tokens_total": sum_numeric(row.get("prompt_tokens") for row in rows),
        "p2_cached_prompt_tokens_total": sum_numeric(row.get("cached_prompt_tokens") for row in rows),
        "p2_generation_tokens_total": sum_numeric(row.get("generation_tokens") for row in rows),
        "p2_preemption_count_total": sum_numeric(row.get("preemption_count") for row in rows),
        "p2_kv_usage_peak": max(kv_usage_values) if kv_usage_values else None,
        "p2_prefix_cache_status": prefix_status,
        "p2_prefix_cache_hit_rate": prefix_hit_rate,
    }


def resolve_agent_token_fields(
    agent: dict[str, Any],
    p2_summary: dict[str, Any],
    *,
    p2_service_exclusive: bool,
) -> dict[str, Any]:
    prompt = numeric_or_none(agent.get("prompt_tokens"))
    completion = numeric_or_none(agent.get("completion_tokens"))
    cached = numeric_or_none(agent.get("cached_tokens"))
    if prompt is not None or completion is not None or cached is not None:
        return {
            "agent_prompt_tokens": prompt,
            "agent_completion_tokens": completion,
            "agent_cached_tokens": cached,
            "agent_token_source": agent.get("token_source") or "trajectory",
            "agent_token_quality": agent.get("token_quality") or "exact",
            "agent_token_missing_reason": agent.get("token_missing_reason"),
        }

    p2_prompt = numeric_or_none(p2_summary.get("p2_prompt_tokens_total"))
    p2_completion = numeric_or_none(p2_summary.get("p2_generation_tokens_total"))
    p2_cached = numeric_or_none(p2_summary.get("p2_cached_prompt_tokens_total"))
    if p2_service_exclusive and (p2_prompt is not None or p2_completion is not None or p2_cached is not None):
        return {
            "agent_prompt_tokens": p2_prompt,
            "agent_completion_tokens": p2_completion,
            "agent_cached_tokens": p2_cached,
            "agent_token_source": "p2_service_exclusive",
            "agent_token_quality": "inferred_service_exclusive",
            "agent_token_missing_reason": None,
        }

    missing_reasons = ["trajectory_usage_fields_absent", "p1_usage_fields_absent"]
    if p2_summary:
        missing_reasons.append("p2_attribution_not_exclusive")
    else:
        missing_reasons.append("p2_usage_absent")
    return {
        "agent_prompt_tokens": None,
        "agent_completion_tokens": None,
        "agent_cached_tokens": None,
        "agent_token_source": agent.get("token_source") or "unavailable",
        "agent_token_quality": agent.get("token_quality") or "unavailable",
        "agent_token_missing_reason": agent.get("token_missing_reason") or ";".join(missing_reasons),
    }


def format_summary(summary: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["metric", "value"])
        for key, value in summary.items():
            writer.writerow([key, display_value(value)])
        return output.getvalue().rstrip("\n")
    lines = ["Metric | Value", "--- | ---"]
    for key, value in summary.items():
        lines.append(f"{key} | {display_value(value)}")
    return "\n".join(lines)


def display_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def ascend_peak(ascend: dict[str, Any], key: str) -> Any:
    values = []
    devices = ascend.get("devices")
    if not isinstance(devices, dict):
        return None
    for device in devices.values():
        if isinstance(device, dict):
            value = numeric_or_none(device.get(key))
            if value is not None:
                values.append(value)
    return max(values) if values else None


def latency_ms_stats(values: list[float | None]) -> dict[str, Any]:
    clean = [value for value in values if value is not None and math.isfinite(float(value))]
    return {
        "mean": (sum(clean) / len(clean)) if clean else None,
        "max": max(clean) if clean else None,
        "sample_count": len(clean),
    }


def ns_delta_ms(start: Any, end: Any) -> float | None:
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    delta = end - start
    if delta < 0:
        return None
    return delta / 1_000_000


def sum_numeric(values: Any) -> float | int | None:
    clean = [value for value in (numeric_or_none(item) for item in values) if value is not None]
    return sum(clean) if clean else None


def numeric_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    return None


def none_if_empty(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
