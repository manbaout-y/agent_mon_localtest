from __future__ import annotations

import csv
import gzip
import io
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from agentmon.events import safe_json_dumps
from agentmon.paths import open_private_text, write_private_text
from agentmon.reporting.ascend import summarize_ascend_devices
from agentmon.profile_data import profile_quality_gate
from agentmon.reporting.event_log import load_events, open_event_log, read_event_log












def build_report_quality_bundle(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    event_log = as_dict(report.get("event_log"))
    p1 = as_dict(report.get("p1_spans"))
    p2 = as_dict(report.get("vllm_p2"))
    client_server = as_dict(report.get("client_server"))
    integrity = as_dict(report.get("semantic_integrity"))
    p2_request_count = integer_or_none(p2.get("request_count")) or 0
    model_request_count = integer_or_none(p1.get("model_requests")) or 0
    p2_lifecycle_rate = numeric_or_none(p2.get("lifecycle_complete_rate"))
    request_match_rate = numeric_or_none(client_server.get("match_rate"))
    lifecycle_threshold = profile_quality_gate("min_p2_lifecycle_rate")
    match_threshold = profile_quality_gate("min_p2_match_rate")
    checks = [
        report_quality_check("invalid_json_lines", integer_or_none(event_log.get("jsonl_invalid_lines")) in {None, 0}),
        report_quality_check("duplicate_event_ids", integer_or_none(event_log.get("duplicate_event_ids")) in {None, 0}),
        report_quality_check("p0_jsonl_sqlite_match", event_log.get("p0_jsonl_sqlite_mismatch") is not True),
        report_quality_check("event_count_match", event_log.get("event_count_mismatch") is not True),
        report_quality_check("open_spans", integer_or_none(p1.get("open_span_count")) in {None, 0}),
        report_quality_check("abandoned_spans", integer_or_none(p1.get("abandoned_span_count")) in {None, 0}),
        report_quality_check("negative_span_durations", integer_or_none(p1.get("negative_duration_count")) in {None, 0}),
        report_quality_check("orphan_end_spans", integer_or_none(p1.get("orphan_end_count")) in {None, 0}),
        report_quality_check(
            "p2_lifecycle_complete",
            p2_request_count == 0
            or lifecycle_threshold is None
            or (p2_lifecycle_rate is not None and p2_lifecycle_rate >= lifecycle_threshold),
            details={
                "status": p2.get("status"),
                "lifecycle_complete_rate": p2_lifecycle_rate,
                "minimum_required": lifecycle_threshold,
            },
        ),
        report_quality_check(
            "request_match_rate",
            p2_request_count == 0
            or model_request_count == 0
            or match_threshold is None
            or (request_match_rate is not None and request_match_rate >= match_threshold),
            details={
                "match_rate": request_match_rate,
                "minimum_required": match_threshold,
                "p1_model_request_count": model_request_count,
                "p2_request_count": p2_request_count,
            },
        ),
        report_quality_check(
            "request_identity_unique",
            integer_or_none(client_server.get("ambiguous_identifier_count")) in {None, 0},
            details={"ambiguous_identifier_count": client_server.get("ambiguous_identifier_count")},
        ),
        report_quality_check(
            "event_run_id_consistency",
            integer_or_none(integrity.get("foreign_run_event_count")) in {None, 0},
            details={"foreign_run_event_count": integrity.get("foreign_run_event_count")},
        ),
        report_quality_check(
            "span_within_run_bounds",
            integer_or_none(integrity.get("span_outside_run_count")) in {None, 0},
            details={"span_outside_run_count": integrity.get("span_outside_run_count")},
        ),
        report_quality_check(
            "token_values_legal",
            integer_or_none(integrity.get("invalid_token_value_count")) in {None, 0},
            details={"invalid_token_value_count": integrity.get("invalid_token_value_count")},
        ),
        report_quality_check(
            "single_model_per_run",
            integer_or_none(integrity.get("model_name_count")) in {None, 0, 1},
            details={
                "model_name_count": integrity.get("model_name_count"),
                "model_names": integrity.get("model_names"),
            },
        ),
        report_quality_check(
            "throughput_denominator_valid",
            report.get("duration_s") is None or (numeric_or_none(report.get("duration_s")) or 0) > 0,
            details={"run_duration_s": report.get("duration_s")},
        ),
        report_quality_check(
            "workload_execution_success",
            report.get("status") == "success",
            blocking=False,
            details={"run_status": report.get("status"), "exit_code": report.get("exit_code")},
        ),
    ]
    blocking_failures = [item["name"] for item in checks if item["blocking"] and not item["passed"]]
    correlation = as_dict(p2.get("correlation"))
    limitations: list[dict[str, str]] = []
    if p1.get("status") == "missing":
        limitations.append({"code": "p1_missing", "message": "Native agent/model/tool spans are unavailable."})
    if p2.get("status") == "missing":
        limitations.append({"code": "p2_missing", "message": "Request-level vLLM timing and KV data are unavailable."})
    npu = as_dict(report.get("ascend"))
    limitations.append(
        {
            "code": "npu_shared_observation",
            "message": "NPU samples are windowed service observations unless request attribution explicitly says otherwise.",
        }
    )
    return {
        "validation_checks": {
            "schema_version": "1.0",
            "validation_type": "agentmon.run_report",
            "run_id": report.get("run_id"),
            "publishability_status": "PUBLISHABLE" if not blocking_failures else "UNPUBLISHABLE",
            "blocking_failures": blocking_failures,
            "checks": checks,
        },
        "metric_definition": {
            "schema_version": "1.0",
            "time_unit": "seconds unless field name ends in _ns",
            "memory_units": {"*_bytes": "bytes", "*_ratio": "dimensionless [0,1]", "*_pct": "percent [0,100]"},
            "latency": {
                "p1_model_span_duration_s": "agent-side model request wall span",
                "p2_request_e2e_s": "P2 received to API finished",
                "p2_queue_s": "engine queued to first scheduled",
                "p2_prefill_s": "first scheduled to a true post-prefill event; null when that boundary is unavailable",
                "p2_prefill_to_first_token_s": "first scheduled to EngineCore first token proxy; includes first-token compute",
                "p2_decode_s": "EngineCore first token to EngineCore finished; never inferred from API final output",
                "prompt_cache_accounted": "get_computed_blocks token accounting before prefill; never a prefill-completed boundary",
                "exact_ttft_s": "server received to EngineCore first token; populated only with verified clock and engine boundary",
                "exact_prefill_s": "first scheduled to execution-observed prefill completion",
                "exact_decode_s": "EngineCore first token to EngineCore finished",
                "proxy_ttft_s": "server received to API first-output observation; never presented as exact TTFT",
                "proxy_decode_s": "API first-output observation to API finished; never presented as exact decode",
            },
            "canonical_dataset": "canonical/canonical_manifest.json",
            "attribution": {
                "shared_service_observation": "window overlap only; not request-exclusive",
                "exact": "direct request or process identity",
            },
        },
        "data_quality": {
            "schema_version": "1.0",
            "run_id": report.get("run_id"),
            "status": "PUBLISHABLE" if not blocking_failures else "UNPUBLISHABLE",
            "collector_status": as_dict(report.get("data_quality")),
            "event_log": event_log,
            "p1_status": p1.get("status"),
            "p2_status": p2.get("status"),
            "ascend_status": npu.get("status"),
            "blocking_failures": blocking_failures,
        },
        "confidence": {
            "schema_version": "1.0",
            "p2_correlation_counts": correlation,
            "p2_lifecycle_complete_rate": p2.get("lifecycle_complete_rate"),
            "p2_latency_quality": as_dict(as_dict(p2.get("latency")).get("quality")),
            "ascend_parser_confidence": npu.get("parser_confidence"),
            "npu_attribution": "shared_service_observation",
        },
        "limitations": {"schema_version": "1.0", "limitations": limitations},
    }


def report_quality_check(
    name: str,
    passed: bool,
    *,
    blocking: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "blocking": blocking, "details": details or {}}


def summarize_semantic_integrity(
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
    p1: dict[str, Any],
    p2: dict[str, Any],
) -> dict[str, Any]:
    run_id = manifest.get("run_id")
    foreign_run_events = [
        event
        for event in events
        if run_id not in {None, ""} and event.get("run_id") not in {None, "", run_id}
    ]
    run_start = integer_or_none(manifest.get("start_mono_ns"))
    run_end = integer_or_none(manifest.get("end_mono_ns"))
    run_clock = manifest.get("clock_domain_id")
    span_outside = 0
    for event in events:
        payload = as_dict(event.get("payload"))
        if payload.get("span_id") in {None, ""}:
            continue
        if run_clock not in {None, ""} and payload.get("clock_domain") not in {None, "", run_clock}:
            continue
        for key in ("start_mono_ns", "end_mono_ns"):
            value = integer_or_none(payload.get(key))
            if value is None:
                continue
            if (run_start is not None and value < run_start) or (run_end is not None and value > run_end):
                span_outside += 1
                break
    invalid_tokens = 0
    model_names: set[str] = set()
    for row in p2.get("requests") or []:
        if not isinstance(row, dict):
            continue
        model_name = row.get("model_name")
        if model_name not in {None, ""}:
            model_names.add(str(model_name))
        prompt = numeric_or_none(row.get("prompt_tokens"))
        cached = numeric_or_none(row.get("cached_prompt_tokens"))
        generation = numeric_or_none(row.get("generation_tokens"))
        recomputed = numeric_or_none(row.get("recomputed_prompt_tokens"))
        if any(value is not None and value < 0 for value in (prompt, cached, generation, recomputed)):
            invalid_tokens += 1
            continue
        if prompt is not None and cached is not None and cached > prompt:
            invalid_tokens += 1
    if not model_names:
        model_names.update(
            str(value)
            for value in p1.get("model_names") or []
            if value not in {None, ""}
        )
    return {
        "schema_version": "1.0",
        "foreign_run_event_count": len(foreign_run_events),
        "span_outside_run_count": span_outside,
        "invalid_token_value_count": invalid_tokens,
        "model_name_count": len(model_names),
        "model_names": sorted(model_names),
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def sqlite_event_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            return int(row[0]) if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def sqlite_event_ids(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            return {str(row[0]) for row in conn.execute("SELECT event_id FROM events")}
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def summarize_event_log(
    event_log: dict[str, Any],
    sqlite_count: int | None,
    sqlite_ids: set[str] | None = None,
) -> dict[str, Any]:
    events = event_log["events"]
    jsonl_p1_count = sum(1 for event in events if is_p1_event(event))
    jsonl_p2_count = sum(1 for event in events if is_p2_event(event))
    jsonl_trajectory_count = sum(
        1
        for event in events
        if is_jsonl_only_trajectory_event(event)
        and not event_is_in_sqlite(event, sqlite_ids)
    )
    jsonl_adapter_count = jsonl_p1_count + jsonl_p2_count
    jsonl_p0_count = len(events) - jsonl_adapter_count - jsonl_trajectory_count
    duplicate_event_ids = duplicate_event_id_count(events)
    p0_difference = None if sqlite_count is None else jsonl_p0_count - sqlite_count
    return {
        "source_of_truth": "events.jsonl",
        "jsonl_total_lines": event_log["total_lines"],
        "jsonl_total_count": event_log["valid_lines"],
        "jsonl_p0_count": jsonl_p0_count,
        "jsonl_p1_count": jsonl_p1_count,
        "jsonl_p2_count": jsonl_p2_count,
        "jsonl_trajectory_count": jsonl_trajectory_count,
        "jsonl_adapter_count": jsonl_adapter_count,
        "jsonl_invalid_count": event_log["invalid_lines"],
        "jsonl_valid_events": event_log["valid_lines"],
        "jsonl_invalid_lines": event_log["invalid_lines"],
        "duplicate_event_ids": duplicate_event_ids,
        "sqlite_p0_count": sqlite_count,
        "sqlite_event_count": sqlite_count,
        "sqlite_status": "AVAILABLE" if sqlite_count is not None else "DISABLED_OR_MISSING",
        "sqlite_role": "p0_index_cache_rebuildable_from_jsonl",
        "p0_jsonl_sqlite_difference": p0_difference,
        "p0_jsonl_sqlite_mismatch": p0_difference not in (None, 0),
        "event_count_mismatch": p0_difference not in (None, 0),
    }


def summarize_event_snapshot(event_log: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    finalization = as_dict(manifest.get("finalization"))
    return {
        "source": "events.jsonl",
        "path": event_log.get("path"),
        "valid_event_count": event_log.get("valid_lines"),
        "invalid_line_count": event_log.get("invalid_lines"),
        "total_line_count": event_log.get("total_lines"),
        "file_size_bytes": event_log.get("file_size_bytes"),
        "snapshot_after_writer_close": finalization.get("event_writer_closed") is True,
        "writer_joined": finalization.get("event_writer_joined") is True,
        "finalization_completed": finalization.get("completed") is True,
    }


def summarize_analysis_state(manifest: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    finalization = as_dict(manifest.get("finalization"))
    runtime_trajectory_status = finalization.get("trajectory_status")
    trajectory_events = [
        event
        for event in events
        if event.get("source") == "trajectory"
    ]
    trajectory_samples = [
        event
        for event in trajectory_events
        if event.get("event_type") == "sample"
    ]
    trajectory_errors = [
        event
        for event in trajectory_events
        if event.get("event_type") == "error"
    ]
    trajectory_status_events = [
        event
        for event in trajectory_events
        if event.get("event_type") == "status"
    ]
    if trajectory_samples:
        trajectory_status = "IMPORTED"
        source = "events_jsonl"
    elif trajectory_errors:
        payload = as_dict(trajectory_errors[-1].get("payload"))
        trajectory_status = payload.get("status") or "ERROR"
        source = "events_jsonl"
    elif trajectory_status_events:
        payload = as_dict(trajectory_status_events[-1].get("payload"))
        trajectory_status = payload.get("status") or "UNKNOWN"
        source = "events_jsonl"
    elif runtime_trajectory_status not in {None, ""}:
        trajectory_status = runtime_trajectory_status
        source = "runtime_finalization"
    else:
        trajectory_status = "UNKNOWN"
        source = "unavailable"
    return {
        "schema_version": "1.0",
        "trajectory": {
            "status": trajectory_status,
            "source": source,
            "runtime_finalization_status": runtime_trajectory_status,
            "post_run_import_status": "IMPORTED" if trajectory_samples else None,
            "sample_event_count": len(trajectory_samples),
            "error_event_count": len(trajectory_errors),
            "status_event_count": len(trajectory_status_events),
        },
    }


def duplicate_event_id_count(events: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            continue
        event_id = str(event_id)
        if event_id in seen:
            duplicates += 1
        seen.add(event_id)
    return duplicates


SPAN_TYPES = {"run", "agent_step", "model_request", "tool_call"}
P1_EVENT_PREFIXES = (
    "agent_step.",
    "model_request.",
    "model_request.attempt.",
    "agent.action_batch.",
    "tool_call.",
)


def is_p1_event(event: dict[str, Any]) -> bool:
    if event.get("source") == "mini_swe":
        return True
    event_type = str(event.get("event_type") or "")
    return event_type.startswith(P1_EVENT_PREFIXES)


def is_p2_event(event: dict[str, Any]) -> bool:
    return event.get("source") == "vllm_p2"


def is_jsonl_only_trajectory_event(event: dict[str, Any]) -> bool:
    if event.get("source") != "trajectory" or event.get("event_type") != "sample":
        return False
    payload = event.get("payload")
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("summary"), dict)
        and payload.get("raw_ref") not in {None, ""}
        and payload.get("source_path") not in {None, ""}
    )


def event_is_in_sqlite(event: dict[str, Any], sqlite_ids: set[str] | None) -> bool:
    if sqlite_ids is None:
        return False
    event_id = event.get("event_id")
    return event_id is not None and str(event_id) in sqlite_ids


def ns_to_s(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) / 1_000_000_000


def summarize_trajectory_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": "missing"}
    for event in reversed(events):
        if event.get("source") == "trajectory" and event.get("event_type") == "sample":
            payload_summary = event.get("payload", {}).get("summary", {})
            summary = dict(payload_summary) if isinstance(payload_summary, dict) else {"status": "partial"}
            break

    p1_usage = summarize_p1_model_usage(events)
    trajectory_usage = {
        "prompt_tokens": integer_or_none(summary.get("prompt_tokens")),
        "completion_tokens": integer_or_none(summary.get("completion_tokens")),
        "cached_tokens": integer_or_none(summary.get("cached_tokens")),
    }
    has_trajectory_usage = any(value is not None for value in trajectory_usage.values())
    if p1_usage:
        filled = False
        for target_key, usage_key in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("cached_tokens", "cached_tokens"),
        ):
            if summary.get(target_key) is None and p1_usage.get(usage_key) is not None:
                summary[target_key] = p1_usage[usage_key]
                filled = True
        if filled:
            summary["token_source"] = "p1_model_request"
            summary["token_quality"] = "exact"
        elif has_trajectory_usage:
            summary.setdefault("token_source", "trajectory")
            summary.setdefault("token_quality", token_quality_from_sources(trajectory_usage, p1_usage))
            if summary["token_quality"] == "inconsistent":
                summary["token_count_difference"] = token_count_difference(trajectory_usage, p1_usage)
    elif has_trajectory_usage:
        summary.setdefault("token_source", "trajectory")
        summary.setdefault("token_quality", "exact")
    else:
        summary.setdefault("token_source", "unavailable")
        summary.setdefault("token_quality", "unavailable")
        summary.setdefault(
            "token_missing_reason",
            "trajectory_usage_fields_absent;p1_usage_fields_absent;p2_attribution_not_exclusive",
        )
    return summary


def summarize_p1_model_usage(events: list[dict[str, Any]]) -> dict[str, int] | None:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    saw_usage = False
    for event in events:
        if not is_p1_event(event):
            continue
        if event.get("event_type") not in {"model_request.end", "model_request_end"}:
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict) or payload.get("span_type") not in {None, "model_request"}:
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        prompt = integer_or_none(usage.get("prompt_tokens"))
        completion = integer_or_none(usage.get("completion_tokens"))
        cached = integer_or_none(usage.get("cached_tokens"))
        details = usage.get("prompt_tokens_details")
        if cached is None and isinstance(details, dict):
            cached = integer_or_none(details.get("cached_tokens"))
        if prompt is not None:
            totals["prompt_tokens"] += prompt
            saw_usage = True
        if completion is not None:
            totals["completion_tokens"] += completion
            saw_usage = True
        if cached is not None:
            totals["cached_tokens"] += cached
            saw_usage = True
    return totals if saw_usage else None


def token_quality_from_sources(primary: dict[str, int | None], secondary: dict[str, int | None]) -> str:
    compared = False
    for key, primary_value in primary.items():
        secondary_value = secondary.get(key)
        if primary_value is None or secondary_value is None:
            continue
        compared = True
        if primary_value != secondary_value:
            return "inconsistent"
    return "exact" if compared else "exact"


def token_count_difference(primary: dict[str, int | None], secondary: dict[str, int | None]) -> dict[str, int]:
    diff: dict[str, int] = {}
    for key, primary_value in primary.items():
        secondary_value = secondary.get(key)
        if primary_value is None or secondary_value is None:
            continue
        diff[key] = primary_value - secondary_value
    return diff


def summarize_p1_spans(
    events: list[dict[str, Any]],
    *,
    run_end_mono_ns: int | None = None,
    finalize: bool = False,
) -> dict[str, Any]:
    span_starts: dict[str, dict[str, Any]] = {}
    ended_span_ids: set[str] = set()
    completed: list[dict[str, Any]] = []
    orphan_end_count = 0
    negative_duration_count = 0
    for event in events:
        lifecycle = span_lifecycle(event)
        payload = event.get("payload", {})
        if lifecycle == "start":
            span_id = payload.get("span_id")
            if span_id:
                span_starts[str(span_id)] = event
        elif lifecycle == "finish":
            span_id = payload.get("span_id")
            span_type = payload.get("span_type")
            if span_id is not None:
                span_id_str = str(span_id)
                ended_span_ids.add(span_id_str)
                if span_id_str not in span_starts:
                    orphan_end_count += 1
            duration_ns = payload.get("duration_ns")
            if isinstance(duration_ns, int) and duration_ns < 0:
                negative_duration_count += 1
            completed.append(
                {
                    "span_id": span_id,
                    "trace_id": payload.get("trace_id"),
                    "parent_span_id": payload.get("parent_span_id"),
                    "span_type": span_type,
                    "status": payload.get("status"),
                    "terminal_reason": payload.get("terminal_reason"),
                    "exit_status": payload.get("exit_status"),
                    "submission_control_flow": payload.get("submission_control_flow"),
                    "duration_s": ns_to_s(payload.get("duration_ns")),
                    "step_id": payload.get("step_id"),
                    "duration_estimated": False,
                }
            )
    open_spans: list[dict[str, Any]] = []
    for span_id, start_event in span_starts.items():
        if span_id in ended_span_ids:
            continue
        payload = start_event.get("payload", {})
        start_mono = payload.get("start_mono_ns")
        estimated_duration = None
        if finalize and isinstance(run_end_mono_ns, int) and isinstance(start_mono, int):
            estimated_duration = run_end_mono_ns - start_mono
        row = {
            "span_id": span_id,
            "trace_id": payload.get("trace_id"),
            "parent_span_id": payload.get("parent_span_id"),
            "span_type": payload.get("span_type"),
            "status": "abandoned" if finalize else "open",
            "duration_s": ns_to_s(estimated_duration),
            "step_id": payload.get("step_id"),
            "duration_estimated": bool(finalize and estimated_duration is not None),
        }
        open_spans.append(row)
    rows = completed + open_spans
    counts = count_spans_by_type(rows)
    if rows:
        status = "complete" if not open_spans or finalize else "running"
    else:
        status = "missing"
    return {
        "status": status,
        "agent_steps": counts.get("agent_step", 0),
        "model_requests": counts.get("model_request", 0),
        "tool_calls": counts.get("tool_call", 0),
        "completed_span_count": len(ended_span_ids),
        "open_span_count": 0 if finalize else len(open_spans),
        "abandoned_span_count": len(open_spans) if finalize else 0,
        "orphan_end_count": orphan_end_count,
        "negative_duration_count": negative_duration_count,
        "workload": summarize_agent_workload(events),
        "spans": rows[:100],
    }


def summarize_agent_workload(events: list[dict[str, Any]]) -> dict[str, Any]:
    model_starts: list[dict[str, Any]] = []
    model_ends: list[dict[str, Any]] = []
    step_ends: list[dict[str, Any]] = []
    attempt_errors = 0
    for event in events:
        if not is_p1_event(event):
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "model_request.start":
            model_starts.append(payload)
        elif event_type in {"model_request.end", "model_request.error"}:
            model_ends.append(payload)
        elif event_type in {"agent_step.end", "agent_step.error"}:
            step_ends.append(payload)
        elif event_type == "model_request.attempt.error":
            attempt_errors += 1
    if not model_starts and not step_ends:
        return {"status": "missing"}
    retry_counts = [integer_or_none(payload.get("retry_count")) for payload in model_ends]
    transport_attempts = [integer_or_none(payload.get("transport_attempt_count")) for payload in model_ends]
    return {
        "status": "complete",
        "context": {
            "model_request_count": len(model_starts),
            "message_count_max": max_or_none(
                [numeric_or_none(payload.get("context_message_count")) for payload in model_starts]
            ),
            "input_chars_max": max_or_none(
                [numeric_or_none(payload.get("context_input_chars")) for payload in model_starts]
            ),
            "prompt_tokens_max": max_or_none(
                [numeric_or_none(payload.get("context_prompt_tokens")) for payload in model_ends]
            ),
            "prompt_token_delta": p2_latency_stats(
                [numeric_or_none(payload.get("context_prompt_tokens_delta")) for payload in model_ends]
            ),
            "tool_result_chars_max": max_or_none(
                [numeric_or_none(payload.get("tool_result_chars")) for payload in model_starts]
            ),
            "tool_result_tokens_estimated_max": max_or_none(
                [numeric_or_none(payload.get("tool_result_tokens_estimated")) for payload in model_starts]
            ),
            "tool_result_token_quality_counts": count_values_from_payloads(
                model_starts,
                "tool_result_token_quality",
                missing="unavailable",
            ),
            "change_type_counts": count_values_from_payloads(
                model_starts,
                "context_change_type",
                missing="unavailable",
            ),
            "truncation_count": sum(
                1 for payload in model_starts if payload.get("context_truncation_detected") is True
            ),
            "rewrite_count": sum(
                1 for payload in model_starts if payload.get("context_rewrite_detected") is True
            ),
            "summarization_count": sum(
                1 for payload in model_starts if payload.get("context_summarization_detected") is True
            ),
            "compression": summarize_context_compression(model_starts),
        },
        "retry": {
            "transport_attempt_total": sum_optional(transport_attempts),
            "retry_total": sum_optional(retry_counts),
            "retried_model_request_count": sum(1 for value in retry_counts if value is not None and value > 0),
            "transport_attempt_error_count": attempt_errors,
            "quality_counts": count_values_from_payloads(
                model_ends,
                "retry_observation_quality",
                missing="unavailable",
            ),
        },
        "semantic_phase": {
            "model_phase_counts": count_values_from_payloads(
                model_starts,
                "agent_semantic_phase",
                missing="unavailable",
            ),
            "step_phase_counts": count_values_from_payloads(
                step_ends,
                "step_semantic_phase",
                missing="unavailable",
            ),
            "path_expansion_step_count": sum(
                1 for payload in step_ends if payload.get("path_expansion_observed") is True
            ),
            "path_expansion_total": sum_optional(
                [integer_or_none(payload.get("path_expansion_count")) for payload in step_ends]
            ),
            "format_retry_step_count": sum(
                1 for payload in step_ends if payload.get("format_retry_observed") is True
            ),
        },
    }


def summarize_context_compression(
    model_starts: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [
        payload
        for payload in model_starts
        if payload.get("context_compression_status") not in {None, ""}
    ]
    if not rows:
        return {
            "status": "missing",
            "limitations": ["context_compression_events_unavailable"],
        }
    applied = [
        payload
        for payload in rows
        if payload.get("context_compression_applied") is True
    ]
    limitations = sorted(
        {
            str(value)
            for payload in rows
            for value in payload.get("context_compression_limitations", [])
            if value not in {None, ""}
        }
    )
    prohibited = sorted(
        {
            str(value)
            for payload in rows
            for value in payload.get(
                "context_compression_prohibited_conclusions",
                [],
            )
            if value not in {None, ""}
        }
    )
    return {
        "status": "observed",
        "request_count": len(rows),
        "applied_request_count": len(applied),
        "target_achieved_count": sum(
            1
            for payload in rows
            if payload.get("context_compression_target_achieved") is True
        ),
        "status_counts": count_values_from_payloads(
            rows,
            "context_compression_status",
            missing="UNRESOLVED",
        ),
        "strategy_counts": count_values_from_payloads(
            rows,
            "context_compression_strategy",
            missing="UNRESOLVED",
        ),
        "trigger_strategy_counts": count_values_from_payloads(
            rows,
            "context_compression_trigger_strategy",
            missing="UNRESOLVED",
        ),
        "trigger_reason_counts": count_values_from_payloads(
            rows,
            "context_compression_trigger_reason",
            missing="UNRESOLVED",
        ),
        "measurement_quality_counts": count_values_from_payloads(
            rows,
            "context_compression_measurement_quality",
            missing="UNRESOLVED",
        ),
        "original_tokens_max": max_or_none(
            [
                numeric_or_none(payload.get("context_compression_original_tokens"))
                for payload in rows
            ]
        ),
        "compressed_tokens_max": max_or_none(
            [
                numeric_or_none(payload.get("context_compression_compressed_tokens"))
                for payload in applied
            ]
        ),
        "removed_tokens_total": sum_optional(
            [
                numeric_or_none(payload.get("context_compression_removed_tokens"))
                for payload in applied
            ]
        ),
        "compression_ratio": p2_latency_stats(
            [
                numeric_or_none(payload.get("context_compression_ratio"))
                for payload in applied
            ]
        ),
        "compression_duration_s": p2_latency_stats(
            [
                numeric_or_none(payload.get("context_compression_duration_s"))
                for payload in applied
            ]
        ),
        "summary_request_count": sum(
            1
            for payload in rows
            if payload.get("context_summary_request_id") not in {None, ""}
        ),
        "summary_status_counts": count_values_from_payloads(
            [
                payload
                for payload in rows
                if payload.get("context_summary_request_id") not in {None, ""}
            ],
            "context_summary_status",
            missing="UNRESOLVED",
        ),
        "summary_input_tokens_total": sum_optional(
            [
                numeric_or_none(payload.get("context_summary_input_tokens"))
                for payload in applied
            ]
        ),
        "summary_output_tokens_total": sum_optional(
            [
                numeric_or_none(payload.get("context_summary_output_tokens"))
                for payload in applied
            ]
        ),
        "summary_cached_tokens_total": sum_optional(
            [
                numeric_or_none(payload.get("context_summary_cached_tokens"))
                for payload in applied
            ]
        ),
        "summary_uncached_prompt_tokens_total": sum_optional(
            [
                numeric_or_none(
                    payload.get("context_summary_uncached_prompt_tokens")
                )
                for payload in applied
            ]
        ),
        "summary_duration_s": p2_latency_stats(
            [
                numeric_or_none(payload.get("context_summary_duration_s"))
                for payload in applied
            ]
        ),
        "summary_token_quality_counts": count_values_from_payloads(
            [
                payload
                for payload in rows
                if payload.get("context_summary_request_id") not in {None, ""}
            ],
            "context_summary_token_quality",
            missing="UNRESOLVED",
        ),
        "limitations": limitations,
        "prohibited_conclusions": prohibited,
    }


def count_spans_by_type(spans: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        span_type = span.get("span_type")
        if isinstance(span_type, str):
            counts[span_type] = counts.get(span_type, 0) + 1
    return counts


def span_lifecycle(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("event_type", ""))
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return None
    span_type = payload.get("span_type")
    if span_type not in SPAN_TYPES:
        return None
    if event_type == "span_start" or event_type.endswith(".start"):
        return "start"
    if event_type in {"span_end", "span_error", "span_cancelled", "span_timeout"}:
        return "finish"
    if event_type.endswith((".end", ".error", ".cancelled", ".timeout")):
        return "finish"
    return None


def summarize_tool_attribution(events: list[dict[str, Any]]) -> dict[str, Any]:
    tool_records = collect_tool_records(events)
    proc_samples = [event for event in events if event.get("source") == "proc" and event.get("event_type") == "sample"]
    if not tool_records:
        return {"status": "missing"}
    attributed: list[dict[str, Any]] = []
    for tool in tool_records:
        attributed.append(attribute_tool(tool, proc_samples))
    instrumentation_errors = sum(1 for item in attributed if item.get("instrumentation_error") is True)
    timeouts = sum(1 for item in attributed if item.get("timed_out") is True)
    nonzero_returns = sum(1 for item in attributed if item.get("returncode") not in (0, None))
    failures = sum(
        1
        for item in attributed
        if item.get("instrumentation_error") is True
        or item.get("timed_out") is True
        or item.get("returncode") not in (0, None)
    )
    resource_fields = ("cpu_time_s", "peak_rss_bytes", "peak_pss_bytes", "read_bytes", "write_bytes")
    resource_coverage = {
        field: {
            "sample_count": sum(item.get(field) is not None for item in attributed),
            "tool_count": len(attributed),
            "coverage": sum(item.get(field) is not None for item in attributed) / len(attributed),
        }
        for field in resource_fields
    }
    resource_complete = all(item["coverage"] == 1.0 for item in resource_coverage.values())
    return {
        "status": "complete" if resource_complete else "partial",
        "tool_calls": len(attributed),
        "tool_instrumentation_error_count": instrumentation_errors,
        "tool_timeout_count": timeouts,
        "command_nonzero_return_count": nonzero_returns,
        "tool_failures": failures,
        "resource_coverage": resource_coverage,
        "tools": attributed[:100],
    }


def collect_tool_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        event_type = event.get("event_type")
        if span_lifecycle(event) == "start" and payload.get("span_type") == "tool_call":
            span_id = str(payload.get("span_id"))
            starts[span_id] = payload
        elif event_type in {"tool_call.end", "tool_call.error", "tool_call_end"}:
            tool_call_id = str(payload.get("tool_call_id") or payload.get("span_id"))
            start = starts.get(tool_call_id, {})
            records.append(
                {
                    "tool_call_id": tool_call_id,
                    "step_id": payload.get("step_id") or start.get("step_id"),
                    "start_mono_ns": start.get("start_mono_ns"),
                    "end_mono_ns": payload.get("end_mono_ns") or event.get("monotonic_time_ns"),
                    "duration_ns": (
                        (payload.get("end_mono_ns") or event.get("monotonic_time_ns")) - start.get("start_mono_ns")
                        if isinstance(payload.get("end_mono_ns") or event.get("monotonic_time_ns"), int)
                        and isinstance(start.get("start_mono_ns"), int)
                        else None
                    ),
                    "tool_name": payload.get("tool_name"),
                    "pid": payload.get("pid"),
                    "pgid": payload.get("pgid"),
                    "pid_starttime_ticks": payload.get("pid_starttime_ticks"),
                    "returncode": payload.get("returncode"),
                    "tool_status": payload.get("tool_status"),
                    "timed_out": payload.get("timed_out"),
                    "timeout_limit_s": payload.get("timeout_limit_s"),
                    "termination_signal": payload.get("termination_signal"),
                    "exception_type": payload.get("exception_type"),
                    "event_type": event_type,
                    "instrumentation_error": event_type in {"tool_call.error", "tool_call_error"},
                    "status": payload.get("status"),
                    "attribution_quality": payload.get("attribution_quality"),
                    "cpu_time_s": payload.get("cpu_time_s"),
                    "cpu_user_time_s": payload.get("cpu_user_time_s"),
                    "cpu_system_time_s": payload.get("cpu_system_time_s"),
                    "cpu_time_quality": payload.get("cpu_time_quality"),
                    "tool_resource_sample_count": payload.get("tool_resource_sample_count"),
                    "tool_resource_sample_interval_s": payload.get("tool_resource_sample_interval_s"),
                    "tool_resource_quality": payload.get("tool_resource_quality"),
                    "tool_pss_quality": payload.get("tool_pss_quality"),
                    "tool_peak_rss_bytes": payload.get("tool_peak_rss_bytes"),
                    "tool_peak_pss_bytes": payload.get("tool_peak_pss_bytes"),
                    "tool_read_bytes_delta": payload.get("tool_read_bytes_delta"),
                    "tool_write_bytes_delta": payload.get("tool_write_bytes_delta"),
                }
            )
    return records


def attribute_tool(tool: dict[str, Any], proc_events: list[dict[str, Any]]) -> dict[str, Any]:
    direct = direct_tool_resources(tool)
    start = tool.get("start_mono_ns")
    end = tool.get("end_mono_ns")
    pgid = tool.get("pgid")
    pid = tool.get("pid")
    if not isinstance(start, int) or not isinstance(end, int) or (pgid is None and pid is None):
        return {**tool, **direct, "resource_quality": direct.get("resource_quality") or "unavailable"}
    rows: list[tuple[int, dict[str, Any]]] = []
    for event in proc_events:
        mono = event.get("monotonic_time_ns")
        if not isinstance(mono, int) or mono < start or mono > end:
            continue
        for process in event.get("payload", {}).get("processes", []):
            if not isinstance(process, dict):
                continue
            if process.get("pgid") == pgid or process.get("pid") == pid:
                rows.append((mono, process))
    if not rows:
        return {
            **tool,
            **direct,
            "resource_quality": direct.get("resource_quality") or "sampled_process_tree_missing",
        }
    first_by_identity: dict[tuple[Any, Any], dict[str, Any]] = {}
    last_by_identity: dict[tuple[Any, Any], dict[str, Any]] = {}
    peak_pss = 0
    peak_rss = 0
    for _mono, process in rows:
        ident = (process.get("pid"), process.get("starttime_ticks"))
        first_by_identity.setdefault(ident, process)
        last_by_identity[ident] = process
        peak_pss = max(peak_pss, int(process.get("pss_bytes") or 0))
        peak_rss = max(peak_rss, int(process.get("rss_bytes") or 0))
    cpu_ticks = 0
    read_bytes = 0
    write_bytes = 0
    for ident, last in last_by_identity.items():
        first = first_by_identity[ident]
        cpu_ticks += max(
            0,
            int(last.get("utime_ticks") or 0)
            + int(last.get("stime_ticks") or 0)
            - int(first.get("utime_ticks") or 0)
            - int(first.get("stime_ticks") or 0),
        )
        read_bytes += max(0, int(last.get("read_bytes") or 0) - int(first.get("read_bytes") or 0))
        write_bytes += max(0, int(last.get("write_bytes") or 0) - int(first.get("write_bytes") or 0))
    sampled = {
        **tool,
        "cpu_ticks": cpu_ticks,
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "peak_pss_bytes": peak_pss or None,
        "peak_rss_bytes": peak_rss or None,
    }
    return {
        **sampled,
        **direct,
        "read_bytes": first_present(direct.get("read_bytes"), sampled.get("read_bytes")),
        "write_bytes": first_present(direct.get("write_bytes"), sampled.get("write_bytes")),
        "peak_pss_bytes": max_optional(direct.get("peak_pss_bytes"), sampled.get("peak_pss_bytes")),
        "peak_rss_bytes": max_optional(direct.get("peak_rss_bytes"), sampled.get("peak_rss_bytes")),
        "resource_quality": (
            "p1_tool_observer+rusage+procfs"
            if direct.get("resource_quality")
            else "sampled_process_tree"
        ),
    }


def direct_tool_resources(tool: dict[str, Any]) -> dict[str, Any]:
    cpu_time = numeric_or_none(tool.get("cpu_time_s"))
    peak_rss = integer_or_none(tool.get("tool_peak_rss_bytes"))
    peak_pss = integer_or_none(tool.get("tool_peak_pss_bytes"))
    read_bytes = integer_or_none(tool.get("tool_read_bytes_delta"))
    write_bytes = integer_or_none(tool.get("tool_write_bytes_delta"))
    if all(value is None for value in (cpu_time, peak_rss, peak_pss, read_bytes, write_bytes)):
        return {}
    quality = tool.get("tool_resource_quality")
    if quality in {None, "unavailable"} and cpu_time is not None:
        quality = tool.get("cpu_time_quality") or "rusage_children_delta"
    return {
        "cpu_time_s": cpu_time,
        "cpu_user_time_s": numeric_or_none(tool.get("cpu_user_time_s")),
        "cpu_system_time_s": numeric_or_none(tool.get("cpu_system_time_s")),
        "cpu_time_quality": tool.get("cpu_time_quality"),
        "peak_rss_bytes": peak_rss,
        "peak_pss_bytes": peak_pss,
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "tool_resource_sample_count": integer_or_none(tool.get("tool_resource_sample_count")),
        "tool_resource_sample_interval_s": numeric_or_none(tool.get("tool_resource_sample_interval_s")),
        "tool_pss_quality": tool.get("tool_pss_quality"),
        "resource_quality": quality,
    }


def max_optional(*values: Any) -> int | float | None:
    numbers = [value for value in (numeric_or_none(item) for item in values) if value is not None]
    return max(numbers) if numbers else None


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def text_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def first_text(*values: Any) -> str | None:
    for value in values:
        text = text_or_none(value)
        if text is not None:
            return text
    return None


def summarize_host_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    host_samples = [event for event in events if event.get("source") == "host" and event.get("event_type") == "sample"]
    if not host_samples:
        return {"status": "missing"}
    latest = host_samples[-1].get("payload", {})
    meminfo = latest.get("meminfo") or {}
    return {
        "status": "complete",
        "sample_count": len(host_samples),
        "loadavg": latest.get("loadavg"),
        "mem_available_bytes": meminfo.get("MemAvailable"),
        "mem_total_bytes": meminfo.get("MemTotal"),
    }


def summarize_proc_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [event for event in events if event.get("source") == "proc" and event.get("event_type") == "sample"]
    if not samples:
        return {"status": "missing"}
    peak_agent_pss = 0
    peak_agent_rss = 0
    agent_process_count_max = 0
    last_agent: dict[str, Any] = {}
    last_active_agent: dict[str, Any] = {}
    for event in samples:
        aggregate = event.get("payload", {}).get("aggregate", {}).get("agent", {})
        peak_agent_pss = max(peak_agent_pss, int(aggregate.get("pss_bytes") or 0))
        peak_agent_rss = max(peak_agent_rss, int(aggregate.get("rss_bytes") or 0))
        process_count = int(aggregate.get("process_count") or 0)
        agent_process_count_max = max(agent_process_count_max, process_count)
        if process_count > 0:
            last_active_agent = aggregate
        last_agent = aggregate
    return {
        "status": "complete",
        "sample_count": len(samples),
        "agent_process_count_last": last_agent.get("process_count"),
        "agent_process_count_max": agent_process_count_max or None,
        "agent_process_count_active_last": last_active_agent.get("process_count"),
        "agent_peak_pss_bytes": peak_agent_pss or None,
        "agent_peak_rss_bytes": peak_agent_rss or None,
        "agent_pss_quality": last_agent.get("pss_quality"),
    }


def summarize_vllm_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [event for event in events if event.get("source") == "vllm" and event.get("event_type") == "sample"]
    errors = [event for event in events if event.get("source") == "vllm" and event.get("event_type") == "error"]
    if not samples:
        return {"status": "missing" if not errors else "partial", "error_count": len(errors)}
    first = flatten_metric_samples(samples[0])
    last = flatten_metric_samples(samples[-1])
    deltas: dict[str, float] = {}
    for name, value in last.items():
        if name in first and value >= first[name]:
            deltas[name] = value - first[name]
    latest_payload = samples[-1].get("payload", {})
    return {
        "status": "complete" if not errors else "partial",
        "sample_count": len(samples),
        "error_count": len(errors),
        "metric_count_last": latest_payload.get("metric_count"),
        "semantic_matches": latest_payload.get("semantic_matches", {}),
        "counter_deltas": deltas,
    }


def summarize_vllm_p2_events(
    events: list[dict[str, Any]],
    *,
    expected_prefix_cache_status: Any = None,
) -> dict[str, Any]:
    p2_events = [event for event in events if is_p2_event(event)]
    if not p2_events:
        return {"status": "missing"}
    request_rows_by_key: dict[str, dict[str, Any]] = {}
    adapter_errors = 0
    capabilities: list[dict[str, Any]] = []
    kv_snapshots: list[dict[str, Any]] = []
    scheduler_snapshots: list[dict[str, Any]] = []
    scheduler_iterations: list[tuple[str, dict[str, Any]]] = []
    kv_block_events: list[dict[str, Any]] = []
    kv_reset_events: list[dict[str, Any]] = []
    npu_attributions: list[dict[str, Any]] = []
    moe_capabilities: list[dict[str, Any]] = []
    moe_summaries: list[dict[str, Any]] = []
    moe_chunks: list[dict[str, Any]] = []
    for event in p2_events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "vllm.adapter.error":
            adapter_errors += 1
            continue
        if event_type == "vllm.adapter.capability":
            capabilities.append(payload)
            continue
        if event_type == "vllm.moe.routing.capability":
            moe_capabilities.append(payload)
            continue
        if event_type in {"vllm.moe.routing.summary", "vllm.moe.routing.chunk"}:
            row = request_row_for_payload(request_rows_by_key, payload)
            merge_request_payload(row, payload, event_type=event_type, event=event)
            if event_type == "vllm.moe.routing.summary":
                moe_summaries.append(payload)
            else:
                moe_chunks.append(payload)
            continue
        if event_type == "vllm.kv.snapshot":
            kv_snapshots.append(payload)
            continue
        if event_type == "vllm.scheduler.snapshot":
            scheduler_snapshots.append(payload)
            continue
        if event_type in {
            "vllm.scheduler.iteration.scheduled",
            "vllm.scheduler.iteration.completed",
        }:
            scheduler_iterations.append((event_type, payload))
            continue
        if event_type == "vllm.kv.block_event":
            kv_block_events.append(payload)
            if any(payload.get(key) not in {None, ""} for key in REQUEST_IDENTITY_KEYS):
                row = request_row_for_payload(request_rows_by_key, payload)
                merge_request_payload(row, payload, event_type=event_type, event=event)
            continue
        if event_type == "vllm.kv.reset":
            kv_reset_events.append(payload)
            continue
        if event_type == "vllm.request.npu_attribution":
            npu_attributions.append(payload)
            continue
        if not event_type.startswith("vllm.request."):
            continue
        row = request_row_for_payload(request_rows_by_key, payload)
        merge_request_payload(row, payload, event_type=event_type, event=event)
        row["last_event_type"] = event_type
    request_values = unique_request_rows(request_rows_by_key.values())
    for row in request_values:
        finalize_request_lifecycle(row)
    correlation_counts = count_request_correlations(request_values)
    incomplete_lifecycle_count = sum(row.get("lifecycle_status") != "complete" for row in request_values)
    required_capability_failures = [
        capability
        for capability in capabilities
        if capability_required_failure(capability)
    ]
    return {
        "status": (
            "partial"
            if adapter_errors or incomplete_lifecycle_count or required_capability_failures
            else "complete"
        ),
        "event_count": len(p2_events),
        "request_count": len(request_values),
        "adapter_error_count": adapter_errors,
        "lifecycle_complete_count": len(request_values) - incomplete_lifecycle_count,
        "lifecycle_incomplete_count": incomplete_lifecycle_count,
        "lifecycle_complete_rate": (
            (len(request_values) - incomplete_lifecycle_count) / len(request_values)
            if request_values
            else None
        ),
        "capability_count": len(capabilities),
        "capability_required_failure_count": len(required_capability_failures),
        "capabilities": capabilities[:20],
        "correlation": correlation_counts,
        "latency": summarize_p2_request_latencies(request_values),
        "tokens": summarize_p2_request_tokens(request_values),
        "kv_cache": summarize_p2_kv_cache(
            request_values,
            kv_snapshots,
            kv_block_events,
            kv_reset_events,
            expected_prefix_cache_status=expected_prefix_cache_status,
        ),
        "scheduler": summarize_p2_scheduler(scheduler_snapshots, scheduler_iterations),
        "moe_routing": summarize_p2_moe_routing(
            moe_capabilities,
            moe_summaries,
            moe_chunks,
        ),
        "npu_attribution": summarize_p2_npu_attribution(npu_attributions),
        "requests": request_values,
        "requests_truncated": False,
    }


def capability_required_failure(capability: dict[str, Any]) -> bool:
    details = as_dict(capability.get("details"))
    return bool(
        capability.get("status") == "unsupported"
        or details.get("missing_required")
        or details.get("deep_missing_required_targets")
        or details.get("engine_token_boundary_collection") == "unsupported"
        or details.get("prefix_cache_expectation_status") == "failed"
    )


def summarize_p2_moe_routing(
    capabilities: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not capabilities and not summaries and not chunks:
        return {"status": "missing", "summary_count": 0, "chunk_count": 0}
    latest_capability = capabilities[-1] if capabilities else {}
    mode = latest_capability.get("mode") or first_present(
        *[summary.get("mode") for summary in summaries]
    )
    status_counts = count_values_from_payloads(summaries, "status", missing="UNKNOWN")
    if mode == "off" or latest_capability.get("status") == "DISABLED":
        status = "disabled"
    elif not summaries:
        status = "unresolved"
    elif status_counts.get("UNRESOLVED") or status_counts.get("PARTIAL"):
        status = "partial"
    else:
        status = "complete"
    request_ids = {request_key(payload) for payload in summaries}
    return {
        "status": status,
        "mode": mode,
        "capability_status": latest_capability.get("status"),
        "enable_return_routed_experts": latest_capability.get(
            "enable_return_routed_experts"
        ),
        "expert_count": latest_capability.get("expert_count"),
        "request_count": len(request_ids),
        "summary_count": len(summaries),
        "chunk_count": len(chunks),
        "status_counts": status_counts,
        "routed_token_count": sum_optional(
            [numeric_or_none(payload.get("routed_token_count")) for payload in summaries]
        ),
        "assignment_count": sum_optional(
            [numeric_or_none(payload.get("assignment_count")) for payload in summaries]
        ),
        "max_expert_share": max_or_none(
            [numeric_or_none(payload.get("max_expert_share")) for payload in summaries]
        ),
        "min_normalized_entropy": min_or_none(
            [numeric_or_none(payload.get("min_normalized_entropy")) for payload in summaries]
        ),
        "max_load_coefficient_of_variation": max_or_none(
            [
                numeric_or_none(payload.get("max_load_coefficient_of_variation"))
                for payload in summaries
            ]
        ),
        "token_alignment_counts": count_values_from_payloads(
            summaries,
            "token_alignment",
            missing="UNKNOWN",
        ),
        "quality_counts": count_values_from_payloads(
            summaries,
            "quality",
            missing="UNKNOWN",
        ),
        "limitations": sorted(
            {
                str(item)
                for payload in [*capabilities, *summaries]
                for item in payload.get("limitations", [])
                if item not in {None, ""}
            }
        ),
        "prohibited_conclusions": sorted(
            {
                str(item)
                for payload in [*capabilities, *summaries]
                for item in payload.get("prohibited_conclusions", [])
                if item not in {None, ""}
            }
        ),
        "capabilities": capabilities[:20],
    }


def summarize_p2_scheduler(
    snapshots: list[dict[str, Any]],
    iterations: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    iterations = iterations or []
    if not snapshots and not iterations:
        return {"status": "missing", "sample_count": 0}
    fields = ("running_requests", "waiting_requests", "scheduled_requests", "scheduled_tokens")
    result: dict[str, Any] = {
        "status": "complete",
        "sample_count": len(snapshots),
        "observation_scope": "shared_service_observation",
        "request_attribution": "not_request_attributed",
    }
    for field in fields:
        values = [value for row in snapshots if (value := numeric_or_none(row.get(field))) is not None]
        result[field] = {
            "latest": values[-1] if values else None,
            "mean": sum(values) / len(values) if values else None,
            "max": max(values) if values else None,
            "valid_sample_count": len(values),
            "coverage": len(values) / len(snapshots) if snapshots else None,
        }
    scheduled = [payload for event_type, payload in iterations if event_type.endswith(".scheduled")]
    completed = [payload for event_type, payload in iterations if event_type.endswith(".completed")]
    scheduled_ids = {
        value
        for payload in scheduled
        if (value := integer_or_none(payload.get("scheduler_iteration_id"))) is not None
    }
    completed_ids = {
        value
        for payload in completed
        if (value := integer_or_none(payload.get("scheduler_iteration_id"))) is not None
    }
    result["iterations"] = {
        "scheduled_count": len(scheduled),
        "completed_count": len(completed),
        "matched_count": len(scheduled_ids & completed_ids),
        "completion_rate": ratio_or_none(len(scheduled_ids & completed_ids), len(scheduled_ids)),
        "batch_request_count": p2_latency_stats(
            [numeric_or_none(payload.get("batch_request_count")) for payload in scheduled]
        ),
        "scheduled_tokens": p2_latency_stats(
            [numeric_or_none(payload.get("scheduled_tokens")) for payload in scheduled]
        ),
        "schedule_duration_s": p2_latency_stats(
            [ns_to_seconds(payload.get("scheduler_duration_ns")) for payload in scheduled]
        ),
        "execution_duration_s": p2_latency_stats(
            [ns_to_seconds(payload.get("schedule_to_execution_complete_ns")) for payload in completed]
        ),
        "prefill_request_total": sum_numeric_payloads(scheduled, "prefill_request_count"),
        "decode_request_total": sum_numeric_payloads(scheduled, "decode_request_count"),
        "chunked_prefill_request_total": sum_numeric_payloads(scheduled, "chunked_prefill_request_count"),
        "mixed_prefill_decode_iteration_count": sum(
            1 for payload in scheduled if payload.get("mixed_prefill_decode_batch") is True
        ),
        "continuous_batching_iteration_count": sum(
            1 for payload in scheduled if payload.get("continuous_batching_observed") is True
        ),
        "observation_scope": "shared_service_observation",
        "request_attribution": "request_ids_listed_not_resource_exclusive",
    }
    if snapshots and not any(result[field]["valid_sample_count"] for field in fields):
        result["status"] = "partial"
    if scheduled and len(scheduled_ids & completed_ids) < len(scheduled_ids):
        result["status"] = "partial"
    return result


def request_key(payload: dict[str, Any]) -> str:
    for key in REQUEST_IDENTITY_KEYS:
        value = payload.get(key)
        if value not in {None, ""}:
            return f"{key}:{value}"
    return f"unmapped:{payload.get('sequence') or len(str(payload))}"


REQUEST_IDENTITY_KEYS = (
    "model_call_id",
    "client_request_id",
    "agent_request_id",
    "agentmon_model_request_id",
    "engine_request_id",
    "vllm_request_id",
    "request_id",
    "server_request_id",
    "api_request_id",
)
CANONICAL_REQUEST_ID_PRIORITY = (
    "agentmon_model_request_id",
    "api_request_id",
    "server_request_id",
    "vllm_request_id",
    "engine_request_id",
    "agent_request_id",
    "client_request_id",
    "model_call_id",
    "request_id",
)
P2_REQUIRED_LIFECYCLE_EVENTS = (
    "vllm.request.received",
    "vllm.request.queued",
    "vllm.request.first_scheduled",
    "vllm.request.finished",
)


def request_row_for_payload(
    rows_by_key: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    keys = [
        f"{key}:{payload[key]}"
        for key in REQUEST_IDENTITY_KEYS
        if payload.get(key) not in {None, ""}
    ]
    matched = unique_request_rows(rows_by_key[key] for key in keys if key in rows_by_key)
    row = matched[0] if matched else {
        "agent_request_id": payload.get("agent_request_id"),
        "vllm_request_id": payload.get("vllm_request_id") or payload.get("request_id"),
        "api_request_id": payload.get("api_request_id"),
        "agentmon_run_id": payload.get("agentmon_run_id"),
        "agentmon_model_request_id": payload.get("agentmon_model_request_id"),
        "correlation_quality": payload.get("correlation_quality") or "unavailable",
    }
    for duplicate in matched[1:]:
        merge_request_rows(row, duplicate)
        for existing_key, existing_row in list(rows_by_key.items()):
            if existing_row is duplicate:
                rows_by_key[existing_key] = row
    if not keys:
        keys = [f"unmapped:{len(rows_by_key)}"]
    for key in keys:
        rows_by_key[key] = row
    return row


def unique_request_rows(rows: Any) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        identity = id(row)
        if identity not in seen:
            seen.add(identity)
            unique.append(row)
    return unique


def merge_request_rows(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key == "_event_types":
            current = target.setdefault(key, [])
            for event_type in value if isinstance(value, list) else []:
                if event_type not in current:
                    current.append(event_type)
        elif key in {
            "_scheduler_iterations",
            "_kv_lifecycle",
            "_moe_routing_summaries",
            "_moe_routing_chunks",
        }:
            target.setdefault(key, []).extend(value if isinstance(value, list) else [])
        elif target.get(key) is None and value is not None:
            target[key] = value


def finalize_request_lifecycle(row: dict[str, Any]) -> None:
    event_types = set(row.pop("_event_types", []) or [])
    scheduler_iterations = merge_scheduler_iteration_rows(
        list(row.pop("_scheduler_iterations", []) or [])
    )
    kv_lifecycle = list(row.pop("_kv_lifecycle", []) or [])
    moe_summaries = list(row.pop("_moe_routing_summaries", []) or [])
    moe_chunks = list(row.pop("_moe_routing_chunks", []) or [])
    required = list(P2_REQUIRED_LIFECYCLE_EVENTS)
    generation_tokens = integer_or_none(row.get("generation_tokens"))
    if generation_tokens is not None and generation_tokens > 0:
        required.extend(
            [
                "vllm.request.first_token",
                "vllm.request.first_output",
                "vllm.request.engine_finished",
            ]
        )
    if any(item.get("prefill_completion_candidate") is True for item in scheduler_iterations):
        required.append("vllm.request.prefill_completed")
    missing = [event_type for event_type in required if event_type not in event_types]
    row["lifecycle_event_types"] = sorted(event_types)
    row["lifecycle_missing_events"] = missing
    row["lifecycle_status"] = "complete" if not missing else "partial"
    row["scheduler_iteration_count"] = len(scheduler_iterations)
    row["first_scheduler_iteration_id"] = min_or_none(
        [numeric_or_none(item.get("scheduler_iteration_id")) for item in scheduler_iterations]
    )
    row["last_scheduler_iteration_id"] = max_or_none(
        [numeric_or_none(item.get("scheduler_iteration_id")) for item in scheduler_iterations]
    )
    row["first_batch_enter_ns"] = min_or_none(
        [numeric_or_none(item.get("batch_enter_ns")) for item in scheduler_iterations]
    )
    row["last_batch_exit_ns"] = max_or_none(
        [numeric_or_none(item.get("batch_exit_ns")) for item in scheduler_iterations]
    )
    row["batch_residence_s_total"] = (
        sum(
            int(value)
            for item in scheduler_iterations
            if (value := numeric_or_none(item.get("batch_residence_ns"))) is not None
        )
        / 1_000_000_000
        if any(item.get("batch_residence_ns") is not None for item in scheduler_iterations)
        else None
    )
    row["prefill_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("scheduler_phase") == "prefill"
    )
    row["decode_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("scheduler_phase") == "decode"
    )
    row["chunked_prefill_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("chunked_prefill") is True
    )
    row["mixed_batch_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("mixed_prefill_decode_batch") is True
    )
    row["max_batch_request_count"] = max_or_none(
        [numeric_or_none(item.get("batch_request_count")) for item in scheduler_iterations]
    )
    row["total_scheduled_tokens"] = sum_optional(
        [numeric_or_none(item.get("scheduled_token_count")) for item in scheduler_iterations]
    )
    row["scheduled_prompt_tokens"] = sum_optional(
        [
            numeric_or_none(item.get("scheduled_token_count"))
            for item in scheduler_iterations
            if item.get("scheduler_phase") == "prefill"
        ]
    )
    row["scheduled_decode_tokens"] = sum_optional(
        [
            numeric_or_none(item.get("scheduled_token_count"))
            for item in scheduler_iterations
            if item.get("scheduler_phase") == "decode"
        ]
    )
    uncached_prompt_tokens = numeric_or_none(
        row.get("uncached_prompt_tokens") or row.get("computed_prefill_tokens")
    )
    scheduled_prompt_tokens = numeric_or_none(row.get("scheduled_prompt_tokens"))
    if scheduled_prompt_tokens is not None and uncached_prompt_tokens is not None:
        row["recomputed_prompt_tokens"] = max(0, scheduled_prompt_tokens - uncached_prompt_tokens)
        row["recomputed_token_quality"] = "derived_scheduler_prefill_minus_uncached"
    else:
        row.setdefault("recomputed_prompt_tokens", None)
        row.setdefault("recomputed_token_quality", "unavailable_not_observed")
    row["kv_lifecycle_event_count"] = len(kv_lifecycle)
    row["kv_lifecycle_operation_counts"] = count_values_from_payloads(
        kv_lifecycle,
        "operation",
        missing="unknown",
    )
    for key in (
        "allocated_block_count",
        "cached_block_count",
        "cache_hit_block_count",
        "freed_block_count",
        "evicted_block_count",
    ):
        total = sum_optional([numeric_or_none(item.get(key)) for item in kv_lifecycle])
        if total is not None:
            row[key] = total
    if kv_lifecycle:
        before_values = [numeric_or_none(item.get("kv_usage_before")) for item in kv_lifecycle]
        after_values = [numeric_or_none(item.get("kv_usage_after")) for item in kv_lifecycle]
        row["kv_usage_before"] = next(
            (value for value in before_values if value is not None),
            row.get("kv_usage_before"),
        )
        row["kv_usage_after"] = next(
            (value for value in reversed(after_values) if value is not None),
            row.get("kv_usage_after"),
        )
    add_kv_residency(row, kv_lifecycle)
    add_moe_routing_summary(row, moe_summaries, moe_chunks)
    add_request_identity_fields(row)


def add_request_identity_fields(row: dict[str, Any]) -> None:
    canonical = canonical_request_identity(row)
    row["canonical_request_id"] = canonical.get("value")
    row["canonical_request_id_kind"] = canonical.get("kind")
    if canonical.get("value") is not None:
        row["request_id"] = canonical["value"]
        row["request_id_kind"] = canonical.get("kind")
    aliases = request_identity_aliases(row)
    row["request_id_aliases"] = aliases
    row["request_id_alias_count"] = len(aliases)
    response_id = first_text(row.get("api_request_id"), row.get("server_request_id"))
    if response_id is not None:
        row["response_id"] = response_id
        row["trajectory_request_id"] = response_id
        row["trajectory_request_id_quality"] = "api_response_id_candidate"


def canonical_request_identity(row: dict[str, Any]) -> dict[str, str | None]:
    for key in CANONICAL_REQUEST_ID_PRIORITY:
        value = text_or_none(row.get(key))
        if value is not None:
            return {"kind": key, "value": value}
    return {"kind": None, "value": None}


def request_identity_aliases(row: dict[str, Any]) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key in REQUEST_IDENTITY_KEYS:
        value = text_or_none(row.get(key))
        if value is None:
            continue
        identity = (key, value)
        if identity in seen:
            continue
        seen.add(identity)
        aliases.append({"kind": key, "value": value})
    return aliases


def merge_scheduler_iteration_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        iteration_id = row.get("scheduler_iteration_id")
        if iteration_id is None:
            unresolved.append(dict(row))
            continue
        target = merged.setdefault(iteration_id, {})
        for key, value in row.items():
            if value is not None:
                target[key] = value
    return [*merged.values(), *unresolved]


def add_moe_routing_summary(
    row: dict[str, Any],
    summaries: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> None:
    if not summaries and not chunks:
        row["moe_routing_status"] = "missing"
        row["moe_routing_summary_count"] = 0
        row["moe_routing_chunk_count"] = 0
        return
    status_counts = count_values_from_payloads(summaries, "status", missing="UNKNOWN")
    row["moe_routing_status"] = (
        "partial"
        if status_counts.get("PARTIAL") or status_counts.get("UNRESOLVED")
        else "complete"
    )
    row["moe_routing_summary_count"] = len(summaries)
    row["moe_routing_chunk_count"] = len(chunks)
    row["moe_routing_mode"] = first_present(
        *[payload.get("mode") for payload in summaries]
    )
    row["moe_routed_token_count"] = sum_optional(
        [numeric_or_none(payload.get("routed_token_count")) for payload in summaries]
    )
    row["moe_assignment_count"] = sum_optional(
        [numeric_or_none(payload.get("assignment_count")) for payload in summaries]
    )
    row["moe_max_expert_share"] = max_or_none(
        [numeric_or_none(payload.get("max_expert_share")) for payload in summaries]
    )
    row["moe_min_normalized_entropy"] = min_or_none(
        [numeric_or_none(payload.get("min_normalized_entropy")) for payload in summaries]
    )
    row["moe_max_load_coefficient_of_variation"] = max_or_none(
        [
            numeric_or_none(payload.get("max_load_coefficient_of_variation"))
            for payload in summaries
        ]
    )
    row["moe_token_alignment_counts"] = count_values_from_payloads(
        summaries,
        "token_alignment",
        missing="UNKNOWN",
    )
    row["moe_routing_limitations"] = sorted(
        {
            str(item)
            for payload in summaries
            for item in payload.get("limitations", [])
            if item not in {None, ""}
        }
    )


def add_kv_residency(row: dict[str, Any], lifecycle: list[dict[str, Any]]) -> None:
    allocation_times = [
        integer_or_none(item.get("monotonic_time_ns"))
        for item in lifecycle
        if item.get("operation") == "allocate"
    ]
    release_times = [
        integer_or_none(item.get("monotonic_time_ns"))
        for item in lifecycle
        if item.get("operation") == "free"
    ]
    allocation_times = [value for value in allocation_times if value is not None]
    release_times = [value for value in release_times if value is not None]
    row["kv_allocated_ns"] = min(allocation_times) if allocation_times else None
    row["kv_released_ns"] = max(release_times) if release_times else None
    row["kv_residency_s"] = ns_delta_s(row["kv_allocated_ns"], row["kv_released_ns"])
    row["kv_residency_quality"] = (
        "exact_request_lifecycle"
        if row["kv_residency_s"] is not None
        else "partial_lifecycle"
        if allocation_times or release_times
        else "unavailable_not_observed"
    )
    engine_generation = integer_or_none(row.get("engine_generation_tokens_observed"))
    api_generation = integer_or_none(
        first_present(
            row.get("api_usage_generation_tokens"),
            row.get("api_generation_tokens_observed"),
        )
    )
    if engine_generation is not None and api_generation is not None:
        row["generation_tokens_consistency"] = (
            "match" if engine_generation == api_generation else "mismatch"
        )


def merge_request_payload(
    row: dict[str, Any],
    payload: dict[str, Any],
    *,
    event_type: str | None = None,
    event: dict[str, Any] | None = None,
) -> None:
    if event:
        event_run_id = event.get("run_id")
        if event_run_id not in {None, ""}:
            row.setdefault("run_id", event_run_id)
        labels = as_dict(event.get("labels"))
        task_id = first_present(
            labels.get("task_id"),
            labels.get("agentmon_task_id"),
            labels.get("batch_task_id"),
            labels.get("AGENTMON_BATCH_TASK_ID"),
        )
        if task_id not in {None, ""}:
            row.setdefault("task_id", task_id)
        source_path = event.get("_source_path")
        source_line = event.get("_source_line")
        event_id = event.get("event_id")
        if source_path not in {None, ""}:
            row.setdefault("_source_paths", []).append(str(source_path))
        if source_line not in {None, ""}:
            row.setdefault("_source_lines", []).append(source_line)
        if event_id not in {None, ""}:
            row.setdefault("_source_event_ids", []).append(str(event_id))
    if event_type in {
        "vllm.request.scheduler_iteration",
        "vllm.request.scheduler_iteration_completed",
    }:
        row.setdefault("_scheduler_iterations", []).append(
            {
                key: payload.get(key)
                for key in (
                    "scheduler_iteration_id",
                    "scheduler_phase",
                    "scheduled_token_count",
                    "chunked_prefill",
                    "prefill_completion_candidate",
                    "batch_request_count",
                    "mixed_prefill_decode_batch",
                    "scheduler_request_kind",
                    "num_computed_tokens_before",
                    "num_computed_tokens_after",
                    "preemption_count",
                    "running_requests_after_schedule",
                    "waiting_requests_after_schedule",
                    "schedule_start_ns",
                    "schedule_end_ns",
                    "scheduler_duration_ns",
                    "batch_enter_ns",
                    "batch_exit_ns",
                    "batch_residence_ns",
                    "scheduler_batch_position",
                    "batch_prefill_request_count",
                    "batch_decode_request_count",
                )
            }
        )
    if event_type == "vllm.kv.block_event":
        row.setdefault("_kv_lifecycle", []).append(
            {
                key: payload.get(key)
                for key in (
                    "operation",
                    "allocated_block_count",
                    "cached_block_count",
                    "cache_hit_block_count",
                    "freed_block_count",
                    "evicted_block_count",
                    "kv_usage_before",
                    "kv_usage_after",
                    "kv_quality",
                )
            }
        )
        row["_kv_lifecycle"][-1]["monotonic_time_ns"] = as_dict(event).get("monotonic_time_ns")
    if event_type == "vllm.moe.routing.summary":
        row.setdefault("_moe_routing_summaries", []).append(dict(payload))
    elif event_type == "vllm.moe.routing.chunk":
        row.setdefault("_moe_routing_chunks", []).append(
            {
                key: payload.get(key)
                for key in (
                    "completion_index",
                    "chunk_index",
                    "chunk_count",
                    "token_start",
                    "token_end",
                    "shape",
                    "dtype",
                    "encoding",
                    "raw_sha256",
                )
            }
        )
    for key in (
        "model_call_id",
        "agent_id",
        "client_request_id",
        "agent_request_id",
        "agentmon_run_id",
        "agentmon_model_request_id",
        "server_request_id",
        "api_request_id",
        "engine_request_id",
        "vllm_request_id",
        "request_id",
        "run_id",
        "model_name",
        "correlation_quality",
        "received_ns",
        "queued_ns",
        "first_scheduled_ns",
        "prompt_cache_accounted_ns",
        "prefill_completed_ns",
        "first_token_ns",
        "last_token_ns",
        "first_output_ns",
        "engine_finished_ns",
        "finished_ns",
        "first_token_timestamp_source",
        "first_token_timestamp_quality",
        "last_token_timestamp_source",
        "last_token_timestamp_quality",
        "first_output_timestamp_source",
        "first_output_timestamp_quality",
        "engine_finished_timestamp_source",
        "engine_finished_timestamp_quality",
        "finished_timestamp_source",
        "finished_timestamp_quality",
        "prefill_boundary_source",
        "prefill_boundary_quality",
        "scheduler_iteration_id",
        "scheduler_phase",
        "scheduled_token_count",
        "batch_request_count",
        "mixed_prefill_decode_batch",
        "queue_depth_on_arrival",
        "running_requests_on_arrival",
        "running_requests_after_schedule",
        "waiting_requests_after_schedule",
        "finish_reason",
        "prompt_tokens",
        "cached_prompt_tokens",
        "new_prompt_tokens",
        "uncached_prompt_tokens",
        "computed_prefill_tokens",
        "recomputed_prompt_tokens",
        "recomputed_token_quality",
        "prompt_token_accounting_quality",
        "generation_tokens",
        "generation_tokens_source",
        "engine_generation_tokens_observed",
        "api_generation_tokens_observed",
        "api_usage_generation_tokens",
        "generation_tokens_consistency",
        "api_output_kind",
        "preemption_count",
        "allocated_block_count",
        "cached_block_count",
        "cache_hit_block_count",
        "freed_block_count",
        "evicted_block_count",
        "kv_usage_before",
        "kv_usage_after",
        "kv_quality",
    ):
        value = payload.get(key)
        if value is not None:
            target_key = "vllm_request_id" if key == "request_id" else key
            if event_type == "vllm.kv.block_event" and key in {
                "allocated_block_count",
                "cached_block_count",
                "cache_hit_block_count",
                "freed_block_count",
                "evicted_block_count",
            }:
                row[target_key] = (numeric_or_none(row.get(target_key)) or 0) + value
            elif event_type == "vllm.kv.block_event" and key == "kv_usage_before":
                row.setdefault(target_key, value)
            elif key == "generation_tokens":
                if event_type == "vllm.request.engine_finished":
                    row[target_key] = value
                elif row.get("generation_tokens_source") != "engine_core_new_token_ids":
                    row[target_key] = value
            elif key == "generation_tokens_source":
                if value == "engine_core_new_token_ids" or row.get(target_key) != "engine_core_new_token_ids":
                    row[target_key] = value
            else:
                row[target_key] = value
    timestamp_prefix = {
        "vllm.request.received": "received",
        "vllm.request.queued": "queued",
        "vllm.request.first_scheduled": "first_scheduled",
        "vllm.request.prompt_cache_accounted": "prompt_cache_accounted",
        "vllm.request.prefill_completed": "prefill_completed",
        "vllm.request.first_token": "first_token",
        "vllm.request.first_output": "first_output",
        "vllm.request.engine_finished": "engine_finished",
        "vllm.request.finished": "finished",
    }.get(str(event_type or ""))
    if timestamp_prefix:
        for key in ("source_pid", "source_host", "clock_domain", "host_boot_id"):
            if payload.get(key) is not None:
                row[f"{timestamp_prefix}_{key}"] = payload[key]
                if event_type == "vllm.request.engine_finished" and payload.get("last_token_ns") is not None:
                    row[f"last_token_{key}"] = payload[key]
    event_types = row.setdefault("_event_types", [])
    if event_type and event_type not in event_types:
        event_types.append(event_type)


def count_request_correlations(rows: Any) -> dict[str, int]:
    counts = {"direct": 0, "server_generated": 0, "heuristic": 0, "unavailable": 0}
    for row in rows:
        quality = row.get("correlation_quality")
        if quality not in counts:
            quality = "unavailable"
        counts[str(quality)] += 1
    return counts


def summarize_p2_request_latencies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "queue": p2_latency_stats(
            [ns_delta_s(row.get("queued_ns") or row.get("received_ns"), row.get("first_scheduled_ns")) for row in rows]
        ),
        "prefill": p2_latency_stats(
            [ns_delta_s(row.get("first_scheduled_ns"), row.get("prefill_completed_ns")) for row in rows]
        ),
        "prefill_to_first_token": p2_latency_stats(
            [ns_delta_s(row.get("first_scheduled_ns"), row.get("first_token_ns")) for row in rows]
        ),
        "decode": p2_latency_stats(
            [ns_delta_s(row.get("first_token_ns"), p2_decode_end_ns(row)) for row in rows]
        ),
        "token_decode": p2_latency_stats(
            [ns_delta_s(row.get("first_token_ns"), row.get("last_token_ns")) for row in rows]
        ),
        "exact_tpot": p2_latency_stats(
            [request_tpot_s(row) for row in rows]
        ),
        "ttft": p2_latency_stats(
            [ns_delta_s(row.get("received_ns"), row.get("first_token_ns")) for row in rows]
        ),
        "e2e": p2_latency_stats(
            [ns_delta_s(row.get("received_ns"), row.get("finished_ns")) for row in rows]
        ),
        "engine_ttft": p2_latency_stats(
            [ns_delta_s(row.get("queued_ns"), row.get("first_token_ns")) for row in rows]
        ),
    }
    quality_specs = {
        "queue": ("queued", "first_scheduled", "engine_process_monotonic"),
        "prefill": ("first_scheduled", "prefill_completed", "engine_process_monotonic"),
        "prefill_to_first_token": ("first_scheduled", "first_token", "engine_process_monotonic"),
        "decode": ("first_token", "engine_finished", "engine_process_monotonic"),
        "token_decode": ("first_token", "last_token", "engine_process_monotonic"),
        "exact_tpot": ("first_token", "last_token", "engine_process_monotonic"),
        "engine_ttft": ("queued", "first_token", "engine_process_monotonic"),
        "ttft": ("received", "first_token", "same_process_monotonic"),
        "e2e": ("received", "finished", "api_process_monotonic"),
    }
    quality_details = {
        name: summarize_timestamp_pair_quality(rows, start, end, same_process_label=label)
        for name, (start, end, label) in quality_specs.items()
    }
    result["quality"] = {name: details["quality"] for name, details in quality_details.items()}
    result["quality_coverage"] = quality_details
    return result


def summarize_client_server_stages(
    events: list[dict[str, Any]],
    vllm_p2: dict[str, Any],
) -> dict[str, Any]:
    p2_rows = [row for row in vllm_p2.get("requests") or [] if isinstance(row, dict)]
    if not p2_rows:
        return {"status": "missing", "matched_request_count": 0}
    p2_candidates: dict[str, list[dict[str, Any]]] = {}
    for row in p2_rows:
        for key in REQUEST_IDENTITY_KEYS:
            value = row.get(key)
            if value not in {None, ""}:
                bucket = p2_candidates.setdefault(str(value), [])
                if all(existing is not row for existing in bucket):
                    bucket.append(row)
    p2_by_id = {
        identity: rows[0]
        for identity, rows in p2_candidates.items()
        if len(rows) == 1
    }

    starts: dict[str, dict[str, Any]] = {}
    matched_rows: list[dict[str, Any]] = []
    used_p2_ids: set[int] = set()
    matched_request_records: list[dict[str, Any]] = []
    unmatched_request_records: list[dict[str, Any]] = []
    model_request_count = 0
    for event in events:
        if not is_p1_event(event):
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("span_type") != "model_request":
            continue
        span_id = payload.get("span_id")
        if span_id in {None, ""}:
            continue
        if event_type == "model_request.start":
            starts[str(span_id)] = payload
            continue
        if event_type not in {"model_request.end", "model_request.error"}:
            continue
        model_request_count += 1
        start = starts.get(str(span_id), {})
        candidate_pairs = [
            ("api_request_id", payload.get("api_request_id")),
            ("response_id", payload.get("response_id")),
            ("agentmon_model_request_id", start.get("agentmon_model_request_id")),
            ("request_id", start.get("request_id")),
            ("span_id", span_id),
        ]
        candidate_values = unique_text_values(value for _, value in candidate_pairs)
        p2_row = next(
            (p2_by_id[str(value)] for value in candidate_values if str(value) in p2_by_id),
            None,
        )
        if p2_row is None:
            unmatched_request_records.append(
                {
                    "span_id": str(span_id),
                    "candidate_ids": candidate_values,
                    "request_id": first_text(*candidate_values, span_id),
                    "missing_reason": (
                        "p2_request_events_missing"
                        if not p2_rows
                        else "p1_model_span_has_no_correlation_identifier"
                        if not candidate_values
                        else "p1_identifiers_not_found_in_p2"
                    ),
                }
            )
            continue
        used_p2_ids.add(id(p2_row))
        p2_identity = canonical_request_identity(p2_row)
        matched_request_records.append(
            {
                "span_id": str(span_id),
                "request_id": p2_identity.get("value"),
                "request_id_kind": p2_identity.get("kind"),
                "candidate_ids": candidate_values,
                "p2_aliases": request_identity_aliases(p2_row),
            }
        )
        outbound_quality = cross_event_timestamp_quality(start, p2_row, p2_prefix="received")
        inbound_quality = cross_event_timestamp_quality(payload, p2_row, p2_prefix="finished")
        client_e2e = ns_delta_s(start.get("start_mono_ns"), payload.get("end_mono_ns"))
        server_e2e = ns_delta_s(p2_row.get("received_ns"), p2_row.get("finished_ns"))
        matched_rows.append(
            {
                "client_model_call_to_server_received_s": verified_ns_delta_s(
                    start.get("start_mono_ns"),
                    p2_row.get("received_ns"),
                    outbound_quality,
                ),
                "server_finished_to_client_return_s": verified_ns_delta_s(
                    p2_row.get("finished_ns"),
                    payload.get("end_mono_ns"),
                    inbound_quality,
                ),
                "client_observed_e2e_s": client_e2e,
                "server_e2e_s": server_e2e,
                "client_non_server_overhead_s": (
                    client_e2e - server_e2e
                    if client_e2e is not None and server_e2e is not None
                    else None
                ),
                "outbound_quality": outbound_quality,
                "inbound_quality": inbound_quality,
            }
        )
    if not matched_rows:
        audit = build_client_server_request_identity_audit(
            p2_rows,
            matched_request_records,
            unmatched_request_records,
            used_p2_ids,
            p2_candidates,
        )
        return {
            "status": "missing",
            "model_request_count": model_request_count,
            "matched_request_count": 0,
            "match_rate": ratio_or_none(0, model_request_count),
            "request_identity_audit": audit,
            "matched_request_ids": audit["matched_request_ids"],
            "unmatched_request_ids": audit["unmatched_request_ids"],
            "orphan_request_ids": audit["orphan_request_ids"],
        }
    audit = build_client_server_request_identity_audit(
        p2_rows,
        matched_request_records,
        unmatched_request_records,
        used_p2_ids,
        p2_candidates,
    )
    return {
        "status": (
            "complete"
            if len(matched_rows) == model_request_count
            and numeric_or_none(vllm_p2.get("request_count")) == len(p2_rows)
            else "partial"
        ),
        "model_request_count": model_request_count,
        "matched_request_count": len(matched_rows),
        "match_rate": ratio_or_none(len(matched_rows), model_request_count),
        "p2_request_count": numeric_or_none(vllm_p2.get("request_count")),
        "p2_request_rows_available": len(p2_rows),
        "ambiguous_identifier_count": sum(1 for rows in p2_candidates.values() if len(rows) > 1),
        "request_identity_audit": audit,
        "matched_request_ids": audit["matched_request_ids"],
        "unmatched_request_ids": audit["unmatched_request_ids"],
        "orphan_request_ids": audit["orphan_request_ids"],
        "client_model_call_to_server_received": p2_latency_stats(
            [row["client_model_call_to_server_received_s"] for row in matched_rows]
        ),
        "server_finished_to_client_return": p2_latency_stats(
            [row["server_finished_to_client_return_s"] for row in matched_rows]
        ),
        "client_observed_e2e": p2_latency_stats([row["client_observed_e2e_s"] for row in matched_rows]),
        "server_e2e": p2_latency_stats([row["server_e2e_s"] for row in matched_rows]),
        "client_non_server_overhead": p2_latency_stats(
            [row["client_non_server_overhead_s"] for row in matched_rows]
        ),
        "negative_client_non_server_overhead_count": sum(
            1
            for row in matched_rows
            if row["client_non_server_overhead_s"] is not None
            and row["client_non_server_overhead_s"] < 0
        ),
        "outbound_quality_counts": count_values_from_payloads(
            matched_rows,
            "outbound_quality",
            missing="unavailable",
        ),
        "inbound_quality_counts": count_values_from_payloads(
            matched_rows,
            "inbound_quality",
            missing="unavailable",
        ),
        "definition": {
            "client_model_call_to_server_received": "P1 model span start to P2 server received; includes client preparation, transport, and server ingress",
            "server_finished_to_client_return": "P2 API-process finished observation to P1 model span end; includes streaming/transport/client parsing",
            "client_non_server_overhead": "client observed model span minus P2 server E2E; not a pure network metric",
        },
    }


def build_client_server_request_identity_audit(
    p2_rows: list[dict[str, Any]],
    matched_records: list[dict[str, Any]],
    unmatched_records: list[dict[str, Any]],
    used_p2_ids: set[int],
    p2_candidates: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    orphan_records: list[dict[str, Any]] = []
    for row in p2_rows:
        if id(row) in used_p2_ids:
            continue
        identity = canonical_request_identity(row)
        orphan_records.append(
            {
                "request_id": identity.get("value"),
                "request_id_kind": identity.get("kind"),
                "aliases": request_identity_aliases(row),
                "lifecycle_status": row.get("lifecycle_status"),
                "correlation_quality": row.get("correlation_quality"),
            }
        )
    ambiguous_ids = sorted(
        identity
        for identity, rows in p2_candidates.items()
        if len(rows) > 1
    )
    return {
        "schema_version": "1.0",
        "attribution_method": "request_id",
        "matched_count": len(matched_records),
        "unmatched_p1_count": len(unmatched_records),
        "orphan_p2_count": len(orphan_records),
        "ambiguous_identifier_count": len(ambiguous_ids),
        "matched_request_ids": sorted_unique_text(record.get("request_id") for record in matched_records),
        "unmatched_request_ids": sorted_unique_text(record.get("request_id") for record in unmatched_records),
        "orphan_request_ids": sorted_unique_text(record.get("request_id") for record in orphan_records),
        "matched_requests": matched_records,
        "unmatched_p1_requests": unmatched_records,
        "orphan_p2_requests": orphan_records,
        "ambiguous_identifiers": ambiguous_ids,
    }


def unique_text_values(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = text_or_none(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def sorted_unique_text(values: Any) -> list[str]:
    return sorted(unique_text_values(values))


def cross_event_timestamp_quality(
    client_payload: dict[str, Any],
    p2_row: dict[str, Any],
    *,
    p2_prefix: str,
) -> str:
    client_domain = client_payload.get("clock_domain")
    p2_domain = p2_row.get(f"{p2_prefix}_clock_domain")
    client_host = client_payload.get("source_host")
    p2_host = p2_row.get(f"{p2_prefix}_source_host")
    if client_domain is not None and p2_domain is not None and client_domain != p2_domain:
        return "clock_domain_mismatch"
    if client_host is not None and p2_host is not None and client_host != p2_host:
        return "clock_domain_mismatch"
    if client_domain is not None and client_domain == p2_domain:
        return "same_host_cross_process_monotonic"
    return "legacy_unverified"


def verified_ns_delta_s(start: Any, end: Any, quality: str) -> float | None:
    if quality == "clock_domain_mismatch":
        return None
    return ns_delta_s(start, end)


def summarize_timestamp_pair_quality(
    rows: list[dict[str, Any]],
    start_prefix: str,
    end_prefix: str,
    *,
    same_process_label: str,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    eligible = 0
    for row in rows:
        if row.get(f"{start_prefix}_ns") is None or row.get(f"{end_prefix}_ns") is None:
            continue
        eligible += 1
        quality = timestamp_pair_quality(
            row,
            start_prefix,
            end_prefix,
            same_process_label=same_process_label,
        )
        counts[quality] = counts.get(quality, 0) + 1
    valid = sum(
        count
        for quality, count in counts.items()
        if quality not in {"legacy_unverified", "clock_domain_mismatch"}
    )
    if eligible == 0:
        aggregate_quality = "unavailable"
    elif len(counts) == 1:
        aggregate_quality = next(iter(counts))
    elif valid == eligible:
        aggregate_quality = "mixed_verified"
    elif valid:
        aggregate_quality = "partial"
    elif counts.get("clock_domain_mismatch"):
        aggregate_quality = "clock_domain_mismatch"
    else:
        aggregate_quality = "legacy_unverified"
    return {
        "quality": aggregate_quality,
        "eligible_sample_count": eligible,
        "verified_sample_count": valid,
        "coverage": valid / eligible if eligible else None,
        "quality_counts": counts,
    }


def timestamp_pair_quality(
    row: dict[str, Any],
    start_prefix: str,
    end_prefix: str,
    *,
    same_process_label: str,
) -> str:
    start_domain = row.get(f"{start_prefix}_clock_domain")
    end_domain = row.get(f"{end_prefix}_clock_domain")
    start_host = row.get(f"{start_prefix}_source_host")
    end_host = row.get(f"{end_prefix}_source_host")
    start_pid = integer_or_none(row.get(f"{start_prefix}_source_pid"))
    end_pid = integer_or_none(row.get(f"{end_prefix}_source_pid"))
    if start_domain is not None and end_domain is not None and start_domain != end_domain:
        return "clock_domain_mismatch"
    if start_host is not None and end_host is not None and start_host != end_host:
        return "clock_domain_mismatch"
    if start_pid is not None and end_pid is not None and start_pid == end_pid:
        return same_process_label
    if start_domain is not None and start_domain == end_domain:
        return "same_host_cross_process_monotonic"
    return "legacy_unverified"


def p2_decode_end_ns(row: dict[str, Any]) -> Any:
    if row.get("engine_finished_ns") is not None:
        return row.get("engine_finished_ns")
    if row.get("first_token_timestamp_source") is None:
        return row.get("finished_ns")
    return None


def request_tpot_s(row: dict[str, Any]) -> float | None:
    generation_tokens = integer_or_none(row.get("generation_tokens"))
    if generation_tokens is None or generation_tokens <= 1:
        return None
    quality = timestamp_pair_quality(
        row,
        "first_token",
        "last_token",
        same_process_label="engine_process_monotonic",
    )
    if quality in {"clock_domain_mismatch", "legacy_unverified"}:
        return None
    duration = ns_delta_s(row.get("first_token_ns"), row.get("last_token_ns"))
    return duration / (generation_tokens - 1) if duration is not None else None


def summarize_p2_request_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality_counts: dict[str, int] = {}
    for row in rows:
        quality = str(row.get("prompt_token_accounting_quality") or "unavailable")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
    return {
        "prompt_tokens": p2_total_stats([numeric_or_none(row.get("prompt_tokens")) for row in rows], len(rows)),
        "cached_prompt_tokens": p2_total_stats(
            [numeric_or_none(row.get("cached_prompt_tokens")) for row in rows],
            len(rows),
        ),
        "computed_prefill_tokens": p2_total_stats(
            [numeric_or_none(row.get("computed_prefill_tokens")) for row in rows],
            len(rows),
        ),
        "recomputed_prompt_tokens": p2_total_stats(
            [numeric_or_none(row.get("recomputed_prompt_tokens")) for row in rows],
            len(rows),
        ),
        "generation_tokens": p2_total_stats(
            [numeric_or_none(row.get("generation_tokens")) for row in rows],
            len(rows),
        ),
        "preemption_count": p2_total_stats(
            [numeric_or_none(row.get("preemption_count")) for row in rows],
            len(rows),
        ),
        "prompt_token_accounting_quality_counts": quality_counts,
    }


def summarize_p2_kv_cache(
    rows: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    block_events: list[dict[str, Any]],
    reset_events: list[dict[str, Any]] | None = None,
    *,
    expected_prefix_cache_status: Any = None,
) -> dict[str, Any]:
    reset_events = reset_events or []
    prefix_resolution = resolve_p2_prefix_cache_status(
        snapshots,
        expected_prefix_cache_status=expected_prefix_cache_status,
    )
    latest = prefix_resolution.get("latest_snapshot") if isinstance(prefix_resolution.get("latest_snapshot"), dict) else {}
    if not latest and snapshots:
        latest = snapshots[-1]
    prefix_status = prefix_resolution["status"]
    queries = numeric_or_none(latest.get("prefix_cache_queries"))
    hits = numeric_or_none(latest.get("prefix_cache_hits"))

    prompt_total = sum_values(rows, "prompt_tokens")
    cached_total = sum_values(rows, "cached_prompt_tokens")
    recomputed_total = sum_values(rows, "recomputed_prompt_tokens")
    conflict_reasons = list(prefix_resolution.get("conflict_reasons") or [])
    if prefix_status == "disabled" and numeric_or_none(cached_total) not in {None, 0}:
        prefix_status = "CONFLICT"
        conflict_reasons.append("positive_cached_prompt_tokens_while_disabled")

    if prefix_status == "disabled":
        prefix_hit_rate = None
        prefix_quality = "disabled"
    elif prefix_status == "CONFLICT":
        prefix_hit_rate = None
        prefix_quality = "conflict"
    elif queries is not None and queries > 0 and hits is not None:
        prefix_hit_rate = hits / queries
        prefix_quality = "exact"
    else:
        prefix_hit_rate = None
        prefix_quality = "unavailable"

    return {
        "prefix_cache_status": prefix_status,
        "prefix_cache_status_source": prefix_resolution.get("source"),
        "prefix_cache_status_conflict": prefix_status == "CONFLICT",
        "prefix_cache_status_conflict_reasons": conflict_reasons,
        "prefix_cache_status_counts": prefix_resolution.get("observed_status_counts", {}),
        "expected_prefix_cache_status": prefix_resolution.get("expected_status"),
        "prefix_cache_hit_rate": prefix_hit_rate,
        "prefix_cache_hit_rate_quality": prefix_quality,
        "cached_prompt_ratio": ratio_or_none(cached_total, prompt_total),
        "recomputed_prompt_ratio": ratio_or_none(recomputed_total, prompt_total),
        "state_counts": count_p2_kv_states(rows, block_events, prefix_status),
        "block_events": {
            "event_count": len(block_events),
            "operation_counts": count_values_from_payloads(block_events, "operation", missing="unknown"),
            "allocated_block_count": p2_total_stats(
                [numeric_or_none(event.get("allocated_block_count")) for event in block_events],
                len(block_events),
            ),
            "cached_block_count": p2_total_stats(
                [numeric_or_none(event.get("cached_block_count")) for event in block_events],
                len(block_events),
            ),
            "cache_hit_block_count": p2_total_stats(
                [numeric_or_none(event.get("cache_hit_block_count")) for event in block_events],
                len(block_events),
            ),
            "freed_block_count": p2_total_stats(
                [numeric_or_none(event.get("freed_block_count")) for event in block_events],
                len(block_events),
            ),
            "evicted_block_count": p2_total_stats(
                [numeric_or_none(event.get("evicted_block_count")) for event in block_events],
                len(block_events),
            ),
        },
        "reset_events": {
            "event_count": len(reset_events),
            "success_count": sum(1 for event in reset_events if event.get("succeeded") is True),
            "failure_count": sum(1 for event in reset_events if event.get("succeeded") is False),
        },
        "kv_usage_ratio": {
            "before": p2_latency_stats([numeric_or_none(event.get("kv_usage_before")) for event in block_events]),
            "after": p2_latency_stats([numeric_or_none(event.get("kv_usage_after")) for event in block_events]),
            "snapshot": numeric_or_none(latest.get("kv_usage_ratio")),
        },
        "kv_pool": latest.get("kv_pool") if isinstance(latest.get("kv_pool"), dict) else None,
        "snapshot_count": len(snapshots),
    }


def resolve_p2_prefix_cache_status(
    snapshots: list[dict[str, Any]],
    *,
    expected_prefix_cache_status: Any = None,
) -> dict[str, Any]:
    expected = normalize_prefix_cache_status(expected_prefix_cache_status)
    known: list[tuple[int, str, dict[str, Any]]] = []
    observed_status_counts: dict[str, int] = {}
    for index, snapshot in enumerate(snapshots):
        raw_status = snapshot.get("prefix_cache_status")
        if raw_status not in {None, ""}:
            observed_status_counts[str(raw_status)] = observed_status_counts.get(str(raw_status), 0) + 1
        status = normalize_prefix_cache_status(raw_status)
        if status in {"enabled", "disabled"}:
            known.append((index, status, snapshot))

    if expected in {"enabled", "disabled"}:
        conflicting = sorted({status for _, status, _ in known if status != expected})
        latest_matching = next(
            (snapshot for _, status, snapshot in reversed(known) if status == expected),
            None,
        )
        if conflicting:
            return {
                "status": "CONFLICT",
                "source": "expected_status_vs_p2_snapshot",
                "expected_status": expected,
                "observed_status_counts": observed_status_counts,
                "conflict_reasons": ["observed_status_conflicts_with_expected"],
                "conflicting_observed_statuses": conflicting,
                "latest_snapshot": latest_matching or (known[-1][2] if known else {}),
            }
        return {
            "status": expected,
            "source": "expected_status",
            "expected_status": expected,
            "observed_status_counts": observed_status_counts,
            "conflict_reasons": [],
            "latest_snapshot": latest_matching or (snapshots[-1] if snapshots else {}),
        }

    observed = sorted({status for _, status, _ in known})
    if len(observed) > 1:
        return {
            "status": "CONFLICT",
            "source": "p2_snapshot",
            "expected_status": expected,
            "observed_status_counts": observed_status_counts,
            "conflict_reasons": ["mixed_enabled_disabled_snapshots"],
            "conflicting_observed_statuses": observed,
            "latest_snapshot": known[-1][2],
        }
    if observed:
        status = observed[0]
        latest_matching = next(
            (snapshot for _, row_status, snapshot in reversed(known) if row_status == status),
            known[-1][2],
        )
        return {
            "status": status,
            "source": "p2_snapshot",
            "expected_status": expected,
            "observed_status_counts": observed_status_counts,
            "conflict_reasons": [],
            "latest_snapshot": latest_matching,
        }
    return {
        "status": "unknown",
        "source": "unavailable",
        "expected_status": expected,
        "observed_status_counts": observed_status_counts,
        "conflict_reasons": [],
        "latest_snapshot": snapshots[-1] if snapshots else {},
    }


def normalize_prefix_cache_status(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return "enabled" if value is True else "disabled" if value is False else None
    text = str(value).strip().lower()
    if text in {"enabled", "enable", "true", "1", "yes", "on"}:
        return "enabled"
    if text in {"disabled", "disable", "false", "0", "no", "off"}:
        return "disabled"
    if text in {"unknown", "unavailable", "not_configured"}:
        return "unknown"
    if text == "conflict":
        return "CONFLICT"
    return None


def count_p2_kv_states(
    rows: list[dict[str, Any]],
    block_events: list[dict[str, Any]],
    prefix_status: Any,
) -> dict[str, int]:
    counts = {
        "disabled": 0,
        "enabled_zero_hit": 0,
        "hit": 0,
        "recomputed": 0,
        "evicted": 0,
        "preempted": 0,
        "conflict": 0,
        "unknown": 0,
    }
    for row in rows:
        cached = numeric_or_none(row.get("cached_prompt_tokens"))
        prompt = numeric_or_none(row.get("prompt_tokens"))
        recomputed = numeric_or_none(row.get("recomputed_prompt_tokens"))
        preemptions = numeric_or_none(row.get("preemption_count"))
        if prefix_status == "disabled":
            counts["disabled"] += 1
        elif prefix_status == "CONFLICT":
            counts["conflict"] += 1
        elif cached is not None and cached > 0:
            counts["hit"] += 1
        elif cached == 0 and prompt is not None:
            counts["enabled_zero_hit"] += 1
        else:
            counts["unknown"] += 1
        if recomputed is not None and recomputed > 0:
            counts["recomputed"] += 1
        if preemptions is not None and preemptions > 0:
            counts["preempted"] += 1
    evicted_requests: set[str] = set()
    for event in block_events:
        evicted = numeric_or_none(event.get("evicted_block_count"))
        if evicted is not None and evicted > 0:
            evicted_requests.add(request_key(event))
    counts["evicted"] = len(evicted_requests)
    return counts


def summarize_p2_npu_attribution(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_count": len(events),
        "request_count": len({request_key(event) for event in events}) if events else 0,
        "quality_counts": count_values_from_payloads(events, "attribution_quality", missing="unavailable"),
        "npu_utilization_integral": p2_total_stats(
            [numeric_or_none(event.get("npu_utilization_integral")) for event in events],
            len(events),
        ),
        "npu_power_integral": p2_total_stats(
            [numeric_or_none(event.get("npu_power_integral")) for event in events],
            len(events),
        ),
        "sample_count": p2_total_stats(
            [numeric_or_none(event.get("sample_count")) for event in events],
            len(events),
        ),
    }


def p2_latency_stats(values: list[float | None]) -> dict[str, Any]:
    clean = sorted(value for value in values if value is not None and math.isfinite(float(value)))
    return {
        "total": sum(clean) if clean else None,
        "mean": (sum(clean) / len(clean)) if clean else None,
        "p50": percentile(clean, 50),
        "p95": percentile(clean, 95),
        "max": max(clean) if clean else None,
        "sample_count": len(clean),
    }


def p2_total_stats(values: list[float | int | None], denominator: int) -> dict[str, Any]:
    clean = [value for value in values if value is not None and math.isfinite(float(value))]
    total = sum(clean) if clean else None
    return {
        "total": total,
        "mean": (total / len(clean)) if clean else None,
        "sample_count": len(clean),
        "coverage": (len(clean) / denominator) if denominator else None,
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    rank = (float(pct) / 100.0) * (len(values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(values[int(rank)])
    lower_value = values[lower]
    upper_value = values[upper]
    return float(lower_value + (upper_value - lower_value) * (rank - lower))


def ns_delta_s(start: Any, end: Any) -> float | None:
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    delta = end - start
    if delta < 0:
        return None
    return delta / 1_000_000_000


def ns_to_seconds(value: Any) -> float | None:
    number = numeric_or_none(value)
    if number is None or number < 0:
        return None
    return float(number) / 1_000_000_000


def sum_numeric_payloads(rows: list[dict[str, Any]], key: str) -> float | int | None:
    return sum_optional([numeric_or_none(row.get(key)) for row in rows])


def sum_optional(values: list[float | int | None]) -> float | int | None:
    clean = [value for value in values if value is not None]
    return sum(clean) if clean else None


def max_or_none(values: list[float | int | None]) -> float | int | None:
    clean = [value for value in values if value is not None]
    return max(clean) if clean else None


def min_or_none(values: list[float | int | None]) -> float | int | None:
    clean = [value for value in values if value is not None]
    return min(clean) if clean else None


def numeric_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    return None


def ratio_or_none(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return float(numerator) / float(denominator)


def sum_values(rows: list[dict[str, Any]], key: str) -> float | int | None:
    values = [numeric_or_none(row.get(key)) for row in rows]
    clean = [value for value in values if value is not None]
    return sum(clean) if clean else None


def count_values_from_payloads(rows: list[dict[str, Any]], key: str, *, missing: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        label = str(value) if value not in {None, ""} else missing
        counts[label] = counts.get(label, 0) + 1
    return counts


def summarize_kv_cache(manifest: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    prefix_status = manifest.get("prefix_caching_status") or "unknown"
    kv_metric_values: list[float] = []
    for event in events:
        if event.get("source") != "vllm" or event.get("event_type") != "sample":
            continue
        semantic_matches = event.get("payload", {}).get("semantic_matches", {})
        kv_metric = semantic_matches.get("kv_cache_usage_ratio")
        if not kv_metric:
            continue
        for sample in event.get("payload", {}).get("samples", []):
            if isinstance(sample, dict) and sample.get("name") == kv_metric and isinstance(sample.get("value"), (int, float)):
                kv_metric_values.append(float(sample["value"]))
    return {
        "prefix_cache_status": prefix_status,
        "prefix_cache_hit_rate": None if prefix_status == "disabled" else "unavailable",
        "prefix_cache_hit_rate_quality": "disabled" if prefix_status == "disabled" else "unavailable",
        "kv_cache_usage_ratio_max": max(kv_metric_values) if kv_metric_values else None,
        "kv_cache_usage_quality": "sampled_ratio" if kv_metric_values else "unavailable",
        "note": "P0 samples vLLM KV usage ratio and NPU memory; it does not derive exact KV cache bytes.",
    }


def flatten_metric_samples(event: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for sample in event.get("payload", {}).get("samples", []):
        if not isinstance(sample, dict):
            continue
        labels = sample.get("labels") or {}
        label_suffix = ",".join(f"{key}={labels[key]}" for key in sorted(labels))
        name = sample.get("name")
        if not name:
            continue
        key = f"{name}{{{label_suffix}}}" if label_suffix else str(name)
        value = sample.get("value")
        if isinstance(value, (int, float)):
            result[key] = float(value)
    return result


def summarize_ascend_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [event for event in events if event.get("source") == "ascend" and event.get("event_type") == "sample"]
    if not samples:
        caps = [event for event in events if event.get("source") == "ascend" and event.get("event_type") == "capability"]
        return {"status": "missing" if not caps else caps[-1].get("payload", {}).get("status", "missing")}
    devices: dict[str, dict[str, Any]] = {}
    parser_confidence = 0.0
    hbm_sample_count = 0
    aicore_sample_count = 0
    for event in samples:
        payload = event.get("payload", {})
        parser_confidence = max(parser_confidence, float(payload.get("parser_confidence") or 0.0))
        for index, device in enumerate(payload.get("devices") or []):
            if not isinstance(device, dict):
                continue
            device_id = str(device.get("device_id", index))
            row = devices.setdefault(device_id, {"peak_hbm_used_bytes": None, "max_aicore_util_pct": None})
            if isinstance(device.get("hbm_used_bytes"), int):
                hbm_sample_count += 1
                row["peak_hbm_used_bytes"] = max(row["peak_hbm_used_bytes"] or 0, device["hbm_used_bytes"])
            if isinstance(device.get("aicore_util_pct"), (int, float)):
                aicore_sample_count += 1
                row["max_aicore_util_pct"] = max(row["max_aicore_util_pct"] or 0.0, float(device["aicore_util_pct"]))
    hbm_quality = "sampled" if hbm_sample_count else "unavailable"
    aicore_quality = "sampled" if aicore_sample_count else "unavailable"
    status = "complete" if devices and hbm_sample_count and aicore_sample_count else "partial"
    result = {
        "status": status,
        "sample_count": len(samples),
        "parser_confidence": parser_confidence,
        "hbm_quality": hbm_quality,
        "aicore_quality": aicore_quality,
        "hbm_sample_count": hbm_sample_count,
        "aicore_sample_count": aicore_sample_count,
        "devices": devices,
    }
    result["device_summary"] = summarize_ascend_devices(result)
    return result


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize_errors(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = [
        event
        for event in events
        if event.get("event_type") == "error" or str(event.get("event_type", "")).endswith(".error")
    ]
    return [
        {
            "event_type": event.get("event_type"),
            "source": event.get("source"),
            "wall_time_ns": event.get("wall_time_ns"),
            "payload": event.get("payload"),
        }
        for event in errors[:50]
    ]


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "AgentMon Run Report",
        "===================",
        f"Run: {report.get('run_id')} status={report.get('status')} exit={report.get('exit_code')}",
        f"Duration: {report.get('duration_s')} s",
        "",
        "Data quality",
        safe_json_dumps(report.get("data_quality", {})),
        "",
        "Event Log",
        safe_json_dumps(report.get("event_log", {})),
        "",
        "Agent",
        safe_json_dumps(report.get("agent", {})),
        "",
        "P1 Spans",
        safe_json_dumps(report.get("p1_spans", {})),
        "",
        "Tool Attribution",
        safe_json_dumps(report.get("tool_attribution", {})),
        "",
        "vLLM",
        safe_json_dumps(report.get("vllm", {})),
        "",
        "vLLM P2",
        safe_json_dumps(report.get("vllm_p2", {})),
        "",
        "Client / Server Stages",
        safe_json_dumps(report.get("client_server", {})),
        "",
        "KV Cache",
        safe_json_dumps(report.get("kv_cache", {})),
        "",
        "Proc",
        safe_json_dumps(report.get("proc", {})),
        "",
        "Ascend",
        safe_json_dumps(report.get("ascend", {})),
        "",
        "Evidence",
        safe_json_dumps(report.get("evidence", {})),
        "",
    ]
    return "\n".join(lines)


def write_csv_report(report: dict[str, Any], path: str | Path) -> None:
    rows = [
        ("run_id", report.get("run_id")),
        ("status", report.get("status")),
        ("exit_code", report.get("exit_code")),
        ("duration_s", report.get("duration_s")),
        ("event_count", report.get("event_count")),
        ("proc.agent_peak_pss_bytes", report.get("proc", {}).get("agent_peak_pss_bytes")),
        ("proc.agent_peak_rss_bytes", report.get("proc", {}).get("agent_peak_rss_bytes")),
        ("vllm.sample_count", report.get("vllm", {}).get("sample_count")),
        ("ascend.sample_count", report.get("ascend", {}).get("sample_count")),
    ]
    with open_private_text(path, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(rows)


def export_run(run_dir: str | Path, *, fmt: str) -> str:
    run_path = Path(run_dir)
    if fmt == "json":
        report_path = run_path / "report.json"
        if report_path.exists():
            return report_path.read_text(encoding="utf-8")
        return json.dumps(build_report(run_path), ensure_ascii=False, indent=2, sort_keys=True)
    if fmt == "csv":
        report_path = run_path / "report.csv"
        if not report_path.exists():
            write_reports(run_path)
        return report_path.read_text(encoding="utf-8")
    raise ValueError(f"unsupported export format: {fmt}")


def compare_reports(run_a: str | Path, run_b: str | Path) -> dict[str, Any]:
    report_a = read_json(Path(run_a) / "report.json") or build_report(run_a)
    report_b = read_json(Path(run_b) / "report.json") or build_report(run_b)
    metrics = ["duration_s", "event_count"]
    comparison: dict[str, Any] = {
        "run_a": report_a.get("run_id"),
        "run_b": report_b.get("run_id"),
        "metrics": {},
        "warnings": [],
    }
    for metric in metrics:
        a = report_a.get(metric)
        b = report_b.get(metric)
        comparison["metrics"][metric] = delta(a, b)
    comparison["metrics"]["proc.agent_peak_pss_bytes"] = delta(
        report_a.get("proc", {}).get("agent_peak_pss_bytes"),
        report_b.get("proc", {}).get("agent_peak_pss_bytes"),
    )
    comparison["metrics"]["proc.agent_peak_rss_bytes"] = delta(
        report_a.get("proc", {}).get("agent_peak_rss_bytes"),
        report_b.get("proc", {}).get("agent_peak_rss_bytes"),
    )
    return comparison


def delta(a: Any, b: Any) -> dict[str, Any]:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return {"a": a, "b": b, "delta": None, "relative_delta": None}
    relative = None if a == 0 else (b - a) / a
    return {"a": a, "b": b, "delta": b - a, "relative_delta": relative}
