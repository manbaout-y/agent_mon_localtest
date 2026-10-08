from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Iterable

from agentmon.paths import make_private_dir, open_private_text, write_private_text


CANONICAL_SCHEMA_VERSION = "1.0"
TABLE_NAMES = ("runs", "requests", "tools", "events", "system_samples")
SYSTEM_SAMPLE_SOURCES = {"host", "proc", "ascend", "vllm"}
REQUEST_ID_KEYS = (
    "model_call_id",
    "client_request_id",
    "agentmon_model_request_id",
    "agent_request_id",
    "server_request_id",
    "api_request_id",
    "engine_request_id",
    "vllm_request_id",
    "request_id",
)


METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "client_e2e_s": {
        "quality": "exact",
        "source": "mini_swe.model_request span",
        "clock_domain": "agent process monotonic",
        "measurement_boundary": "client request start to client response received",
    },
    "server_e2e_s": {
        "quality": "exact_when_verified",
        "source": "vllm.request.received/finished",
        "clock_domain": "API process monotonic",
        "measurement_boundary": "server received to API response finished",
    },
    "queue_s": {
        "quality": "exact_when_verified",
        "source": "vllm.request.queued/first_scheduled",
        "clock_domain": "EngineCore process monotonic",
        "measurement_boundary": "scheduler enqueue to first schedule",
    },
    "exact_prefill_s": {
        "quality": "exact_when_execution_observed",
        "source": "vllm.request.first_scheduled/prefill_completed",
        "clock_domain": "EngineCore process monotonic",
        "measurement_boundary": "first schedule to execution-observed prefill completion",
    },
    "exact_ttft_s": {
        "quality": "exact_when_engine_first_token_observed",
        "source": "vllm.request.received/first_token",
        "clock_domain": "same-host monotonic",
        "measurement_boundary": "server received to EngineCore first token",
    },
    "exact_decode_s": {
        "quality": "exact_when_engine_finished_observed",
        "source": "vllm.request.first_token/engine_finished",
        "clock_domain": "EngineCore process monotonic",
        "measurement_boundary": "EngineCore first token to EngineCore completion",
    },
    "exact_tpot_s": {
        "quality": "exact_when_engine_token_boundaries_observed",
        "source": "vllm.request.first_token/engine_finished.last_token_ns",
        "clock_domain": "EngineCore process monotonic",
        "measurement_boundary": "first generated token to last generated token divided by generation token intervals",
    },
    "engine_residence_s": {
        "quality": "exact_when_engine_boundaries_observed",
        "source": "vllm.request.first_scheduled/engine_finished",
        "clock_domain": "EngineCore process monotonic",
        "measurement_boundary": "first scheduler selection to EngineCore request completion",
    },
    "proxy_ttft_s": {
        "quality": "proxy",
        "source": "vllm.request.received/first_output",
        "clock_domain": "API process monotonic",
        "measurement_boundary": "server received to first API output observed",
    },
    "proxy_decode_s": {
        "quality": "proxy",
        "source": "vllm.request.first_output/finished",
        "clock_domain": "API process monotonic",
        "measurement_boundary": "first API output observed to API response finished",
    },
}


def build_canonical_tables(
    run_dir: str | Path,
    *,
    report: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    run_path = Path(run_dir)
    manifest = as_dict(report.get("manifest"))
    run_id = text_or_none(report.get("run_id") or manifest.get("run_id"))
    span_rows = build_span_rows(events, run_id=run_id)
    request_rows = build_request_rows(report, span_rows.get("model_request", []), run_id=run_id)
    tool_rows = [canonical_tool_row(row, run_id=run_id) for row in span_rows.get("tool_call", [])]
    publication = as_dict(report.get("analysis_publication"))
    run_row = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "run_id": run_id,
        "label": manifest.get("label"),
        "status": report.get("status"),
        "exit_code": report.get("exit_code"),
        "start_wall_ns": manifest.get("start_wall_ns"),
        "end_wall_ns": manifest.get("end_wall_ns"),
        "start_mono_ns": manifest.get("start_mono_ns"),
        "end_mono_ns": manifest.get("end_mono_ns"),
        "duration_s": report.get("duration_s"),
        "host_id": manifest.get("host_id"),
        "host_boot_id": manifest.get("host_boot_id"),
        "clock_domain_id": manifest.get("clock_domain_id"),
        "agentmon_version": manifest.get("agentmon_version"),
        "request_count": len(request_rows),
        "tool_count": len(tool_rows),
        "event_count": len(events),
        "publishability_status": publication.get("status"),
        "source_run_dir": str(run_path),
    }
    event_rows = [canonical_event_row(event) for event in events]
    system_rows = [
        canonical_system_sample_row(event)
        for event in events
        if event.get("source") in SYSTEM_SAMPLE_SOURCES
        and event.get("event_type") in {"sample", "counter_snapshot", "vllm.kv.snapshot", "vllm.scheduler.snapshot"}
    ]
    return {
        "runs": [run_row],
        "requests": request_rows,
        "tools": tool_rows,
        "events": event_rows,
        "system_samples": system_rows,
    }


def build_span_rows(
    events: Iterable[dict[str, Any]],
    *,
    run_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    starts: dict[str, dict[str, Any]] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        payload = as_dict(event.get("payload"))
        span_id = text_or_none(payload.get("span_id"))
        span_type = text_or_none(payload.get("span_type"))
        event_type = str(event.get("event_type") or "")
        if not span_id or not span_type:
            continue
        if event_type.endswith(".start"):
            starts[span_id] = {"event": event, "payload": payload}
            continue
        if not (event_type.endswith(".end") or event_type.endswith(".error")):
            continue
        start = starts.get(span_id, {})
        start_event = as_dict(start.get("event"))
        start_payload = as_dict(start.get("payload"))
        merged = dict(start_payload)
        merged.update(payload)
        rows.setdefault(span_type, []).append(
            {
                "schema_version": CANONICAL_SCHEMA_VERSION,
                "run_id": run_id or event.get("run_id"),
                "span_id": span_id,
                "parent_span_id": merged.get("parent_span_id"),
                "step_id": merged.get("step_id"),
                "span_type": span_type,
                "name": merged.get("name"),
                "status": merged.get("status") or ("error" if event_type.endswith(".error") else "success"),
                "start_wall_ns": start_payload.get("start_wall_ns") or start_event.get("wall_time_ns"),
                "end_wall_ns": payload.get("end_wall_ns") or event.get("wall_time_ns"),
                "start_mono_ns": start_payload.get("start_mono_ns") or start_event.get("monotonic_time_ns"),
                "end_mono_ns": payload.get("end_mono_ns") or event.get("monotonic_time_ns"),
                "duration_s": ns_to_s(payload.get("duration_ns")),
                "clock_domain_id": merged.get("clock_domain"),
                "host_boot_id": merged.get("host_boot_id"),
                "source_pid": merged.get("source_pid"),
                "payload": merged,
            }
        )
    return rows


def build_request_rows(
    report: dict[str, Any],
    model_spans: list[dict[str, Any]],
    *,
    run_id: str | None,
) -> list[dict[str, Any]]:
    p2_rows = [row for row in as_dict(report.get("vllm_p2")).get("requests", []) if isinstance(row, dict)]
    p2_by_identity: dict[str, list[dict[str, Any]]] = {}
    for row in p2_rows:
        for scope in request_scopes(row):
            for value in request_identity_values(row):
                bucket = p2_by_identity.setdefault(scoped_identity(scope, value), [])
                if all(candidate is not row for candidate in bucket):
                    bucket.append(row)
    output: list[dict[str, Any]] = []
    used_p2: set[int] = set()
    for span in model_spans:
        payload = as_dict(span.get("payload"))
        candidates: list[dict[str, Any]] = []
        scopes = unique_text(request_scopes(payload) + request_scopes(span, fallback_run_id=run_id))
        # Unscoped P2 streams are queryable, but duplicate IDs remain ambiguous
        # instead of being silently attributed across runs.
        if "GLOBAL_UNSCOPED" not in scopes:
            scopes.append("GLOBAL_UNSCOPED")
        for scope in scopes:
            for value in request_identity_values(payload) + request_identity_values(span):
                candidates.extend(p2_by_identity.get(scoped_identity(scope, value), []))
        unique = unique_dicts(candidates)
        p2 = unique[0] if len(unique) == 1 else {}
        if p2:
            used_p2.add(id(p2))
        output.append(canonical_request_row(span, p2, run_id=run_id, candidate_count=len(unique)))
    for p2 in p2_rows:
        if id(p2) in used_p2:
            continue
        output.append(canonical_request_row({}, p2, run_id=run_id, candidate_count=0))
    return output


def canonical_request_row(
    span: dict[str, Any],
    p2: dict[str, Any],
    *,
    run_id: str | None,
    candidate_count: int,
) -> dict[str, Any]:
    payload = as_dict(span.get("payload"))
    model_call_id = first_text(
        payload.get("model_call_id"),
        payload.get("agentmon_model_request_id"),
        payload.get("request_id"),
        span.get("span_id"),
        p2.get("model_call_id"),
        p2.get("agentmon_model_request_id"),
    )
    client_request_id = first_text(payload.get("client_request_id"), model_call_id)
    server_request_id = first_text(
        payload.get("server_request_id"),
        payload.get("api_request_id"),
        payload.get("response_id"),
        p2.get("server_request_id"),
        p2.get("api_request_id"),
    )
    engine_request_id = first_text(
        p2.get("engine_request_id"),
        p2.get("vllm_request_id"),
        p2.get("request_id"),
    )
    client_start = integer_or_none(span.get("start_mono_ns"))
    client_end = integer_or_none(span.get("end_mono_ns"))
    received = integer_or_none(p2.get("received_ns"))
    queued = integer_or_none(p2.get("queued_ns"))
    first_scheduled = integer_or_none(p2.get("first_scheduled_ns"))
    prefill_completed = integer_or_none(p2.get("prefill_completed_ns"))
    first_token = integer_or_none(p2.get("first_token_ns"))
    last_token = integer_or_none(p2.get("last_token_ns"))
    first_output = integer_or_none(p2.get("first_output_ns"))
    engine_finished = integer_or_none(p2.get("engine_finished_ns"))
    finished = integer_or_none(p2.get("finished_ns"))
    exact_first_token = first_token if is_engine_first_token(p2) else None
    exact_engine_finished = engine_finished if p2.get("engine_finished_timestamp_source") not in {"api_output", "api_finished"} else None
    exact_prefill = (
        delta_ns_s(first_scheduled, prefill_completed)
        if p2.get("prefill_boundary_quality") in {
            "exact",
            "execution_observed",
            "derived_execution_observed",
            "engine_execution_observed",
        }
        else None
    )
    match_status = (
        "exact"
        if span and p2 and candidate_count == 1
        else "multiple"
        if span and candidate_count > 1
        else "p1_without_p2"
        if span
        else "p2_without_p1"
    )
    p2_scopes = request_scopes(p2) if p2 else []
    p2_source_paths = p2.get("_source_paths") if isinstance(p2.get("_source_paths"), list) else []
    p2_source_lines = p2.get("_source_lines") if isinstance(p2.get("_source_lines"), list) else []
    p2_event_ids = p2.get("_source_event_ids") if isinstance(p2.get("_source_event_ids"), list) else []
    prompt = integer_or_none(p2.get("prompt_tokens")) or integer_or_none(as_dict(payload.get("usage")).get("prompt_tokens"))
    cached = integer_or_none(p2.get("cached_prompt_tokens"))
    generation = integer_or_none(p2.get("generation_tokens")) or integer_or_none(
        as_dict(payload.get("usage")).get("completion_tokens")
    )
    uncached = max(prompt - cached, 0) if prompt is not None and cached is not None else None
    recomputed_quality = text_or_none(p2.get("recomputed_token_quality"))
    recomputed = integer_or_none(p2.get("recomputed_prompt_tokens")) if recomputed_quality == "exact" else None
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "run_id": run_id,
        "agent_id": payload.get("agent_id") or p2.get("agent_id"),
        "request_purpose": payload.get("request_purpose") or "agent_primary",
        "parent_model_call_id": payload.get("parent_model_call_id"),
        "step_id": span.get("step_id"),
        "span_id": span.get("span_id"),
        "model_call_id": model_call_id,
        "client_request_id": client_request_id,
        "server_request_id": server_request_id,
        "engine_request_id": engine_request_id,
        "api_request_id": server_request_id,
        "vllm_request_id": engine_request_id,
        "match_status": match_status,
        "match_confidence": "exact" if match_status == "exact" else "unavailable",
        "correlation_quality": p2.get("correlation_quality") or ("unavailable" if not p2 else "server_generated"),
        "p2_observation_scope": ",".join(p2_scopes) if p2_scopes else None,
        "p2_source_path": p2_source_paths[0] if p2_source_paths else None,
        "p2_source_line": min(p2_source_lines) if p2_source_lines else None,
        "p2_source_event_ids": json_text(p2_event_ids),
        "lifecycle_status": p2.get("lifecycle_status") or "missing",
        "lifecycle_missing_events_json": json_text(p2.get("lifecycle_missing_events") or []),
        "model_name": p2.get("model_name") or payload.get("model_name"),
        "status": span.get("status") or ("observed" if p2 else "missing"),
        "client_request_start_mono_ns": client_start,
        "server_received_mono_ns": received,
        "scheduler_enqueued_mono_ns": queued,
        "first_scheduled_mono_ns": first_scheduled,
        "prefill_start_mono_ns": first_scheduled,
        "prefill_end_mono_ns": prefill_completed,
        "first_token_mono_ns": exact_first_token,
        "decode_start_mono_ns": exact_first_token,
        "last_token_mono_ns": last_token,
        "engine_finished_mono_ns": exact_engine_finished,
        "server_finished_mono_ns": finished,
        "client_response_received_mono_ns": client_end,
        "client_e2e_s": delta_ns_s(client_start, client_end),
        "client_network_in_s": verified_cross_delta(span, p2, client_start, received, "received"),
        "queue_s": verified_p2_delta(p2, queued or received, first_scheduled, "queued", "first_scheduled"),
        "exact_prefill_s": exact_prefill,
        "exact_ttft_s": verified_p2_delta(p2, received, exact_first_token, "received", "first_token"),
        "exact_decode_s": verified_p2_delta(p2, exact_first_token, exact_engine_finished, "first_token", "engine_finished"),
        "exact_tpot_s": token_period_seconds(p2, exact_first_token, last_token, generation),
        "engine_residence_s": verified_p2_delta(
            p2,
            first_scheduled,
            exact_engine_finished,
            "first_scheduled",
            "engine_finished",
        ),
        "server_e2e_s": verified_p2_delta(p2, received, finished, "received", "finished"),
        "response_transfer_s": verified_cross_delta(span, p2, finished, client_end, "finished"),
        "proxy_ttft_s": verified_p2_delta(p2, received, first_output, "received", "first_output"),
        "proxy_decode_s": verified_p2_delta(p2, first_output, finished, "first_output", "finished"),
        "api_first_output_observed_mono_ns": first_output,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "new_prompt_tokens": uncached,
        "uncached_prompt_tokens": uncached,
        "engine_recomputed_tokens": recomputed,
        "recomputed_token_quality": recomputed_quality or "unavailable_not_observed",
        "output_tokens": generation,
        "context_length": prompt,
        "context_compression_status": payload.get("context_compression_status"),
        "context_compression_trigger_strategy": payload.get(
            "context_compression_trigger_strategy"
        ),
        "context_compression_trigger_reason": payload.get(
            "context_compression_trigger_reason"
        ),
        "context_compression_strategy": payload.get("context_compression_strategy"),
        "context_compression_applied": payload.get("context_compression_applied"),
        "context_compression_original_tokens": payload.get(
            "context_compression_original_tokens"
        ),
        "context_compression_compressed_tokens": payload.get(
            "context_compression_compressed_tokens"
        ),
        "context_compression_removed_tokens": payload.get(
            "context_compression_removed_tokens"
        ),
        "context_compression_target_tokens": payload.get(
            "context_compression_target_tokens"
        ),
        "context_compression_target_achieved": payload.get(
            "context_compression_target_achieved"
        ),
        "context_compression_measurement_quality": payload.get(
            "context_compression_measurement_quality"
        ),
        "context_compression_started_mono_ns": payload.get(
            "context_compression_started_mono_ns"
        ),
        "context_compression_ended_mono_ns": payload.get(
            "context_compression_ended_mono_ns"
        ),
        "context_compression_duration_s": payload.get(
            "context_compression_duration_s"
        ),
        "context_summary_status": payload.get("context_summary_status"),
        "context_summary_request_id": payload.get("context_summary_request_id"),
        "context_summary_model": payload.get("context_summary_model"),
        "context_summary_input_tokens": payload.get(
            "context_summary_input_tokens"
        ),
        "context_summary_output_tokens": payload.get(
            "context_summary_output_tokens"
        ),
        "context_summary_total_tokens": payload.get(
            "context_summary_total_tokens"
        ),
        "context_summary_cached_tokens": payload.get(
            "context_summary_cached_tokens"
        ),
        "context_summary_uncached_prompt_tokens": payload.get(
            "context_summary_uncached_prompt_tokens"
        ),
        "context_summary_duration_s": payload.get("context_summary_duration_s"),
        "context_summary_token_quality": payload.get(
            "context_summary_token_quality"
        ),
        "context_compression_limitations_json": json_text(
            payload.get("context_compression_limitations") or []
        ),
        "finish_reason": p2.get("finish_reason"),
        "preemption_count": p2.get("preemption_count"),
        "queue_depth_on_arrival": p2.get("queue_depth_on_arrival"),
        "running_requests": p2.get("running_requests_after_schedule"),
        "waiting_requests": p2.get("waiting_requests_after_schedule"),
        "scheduled_batch_size": p2.get("batch_request_count"),
        "scheduled_prompt_tokens": p2.get("scheduled_prompt_tokens"),
        "scheduled_decode_tokens": p2.get("scheduled_decode_tokens"),
        "scheduler_iteration_id": p2.get("scheduler_iteration_id"),
        "scheduler_iteration_count": p2.get("scheduler_iteration_count"),
        "prefill_iteration_count": p2.get("prefill_iteration_count"),
        "decode_iteration_count": p2.get("decode_iteration_count"),
        "first_scheduler_iteration_id": p2.get("first_scheduler_iteration_id"),
        "last_scheduler_iteration_id": p2.get("last_scheduler_iteration_id"),
        "first_batch_enter_mono_ns": p2.get("first_batch_enter_ns"),
        "last_batch_exit_mono_ns": p2.get("last_batch_exit_ns"),
        "batch_residence_s_total": p2.get("batch_residence_s_total"),
        "kv_allocated_blocks": p2.get("allocated_block_count"),
        "kv_cached_blocks": p2.get("cached_block_count"),
        "kv_cache_hit_blocks": p2.get("cache_hit_block_count"),
        "kv_released_blocks": p2.get("freed_block_count"),
        "kv_evicted_blocks": p2.get("evicted_block_count"),
        "kv_residency_s": p2.get("kv_residency_s"),
        "moe_routing_status": p2.get("moe_routing_status"),
        "moe_routing_mode": p2.get("moe_routing_mode"),
        "moe_routed_token_count": p2.get("moe_routed_token_count"),
        "moe_assignment_count": p2.get("moe_assignment_count"),
        "moe_max_expert_share": p2.get("moe_max_expert_share"),
        "moe_min_normalized_entropy": p2.get("moe_min_normalized_entropy"),
        "moe_max_load_coefficient_of_variation": p2.get(
            "moe_max_load_coefficient_of_variation"
        ),
        "moe_token_alignment_counts_json": json_text(
            p2.get("moe_token_alignment_counts") or {}
        ),
        "moe_routing_limitations_json": json_text(
            p2.get("moe_routing_limitations") or []
        ),
        "metric_quality_json": json_text(request_metric_quality(p2, span)),
        "source_json": json_text(request_metric_source()),
        "clock_domain_json": json_text(request_clock_domains(p2, span)),
        "measurement_boundary_json": json_text(
            {key: value["measurement_boundary"] for key, value in METRIC_DEFINITIONS.items()}
        ),
    }


def canonical_tool_row(row: dict[str, Any], *, run_id: str | None) -> dict[str, Any]:
    payload = as_dict(row.get("payload"))
    result = as_dict(payload.get("result_summary"))
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "run_id": run_id,
        "agent_id": payload.get("agent_id"),
        "step_id": row.get("step_id"),
        "span_id": row.get("span_id"),
        "tool_call_id": payload.get("tool_call_id") or row.get("span_id"),
        "tool_name": payload.get("tool_name") or row.get("name"),
        "executable": payload.get("executable"),
        "command_hash": payload.get("command_hash"),
        "status": row.get("status"),
        "returncode": first_present(payload.get("returncode"), result.get("returncode")),
        "timed_out": first_present(payload.get("timed_out"), result.get("timed_out")),
        "timeout_limit_s": payload.get("timeout_limit_s"),
        "termination_signal": payload.get("termination_signal"),
        "exception_type": payload.get("exception_type") or payload.get("error_type"),
        "start_mono_ns": row.get("start_mono_ns"),
        "end_mono_ns": row.get("end_mono_ns"),
        "duration_s": row.get("duration_s"),
        "pid": payload.get("pid"),
        "pgid": payload.get("pgid"),
        "cpu_time_s": payload.get("cpu_time_s"),
        "peak_rss_bytes": payload.get("peak_rss_bytes"),
        "peak_pss_bytes": payload.get("peak_pss_bytes"),
        "read_bytes_delta": payload.get("read_bytes_delta"),
        "write_bytes_delta": payload.get("write_bytes_delta"),
        "stdout_bytes": first_present(payload.get("stdout_bytes"), result.get("stdout_bytes")),
        "stderr_bytes": first_present(payload.get("stderr_bytes"), result.get("stderr_bytes")),
        "output_bytes": payload.get("output_bytes"),
        "tool_category": first_present(payload.get("tool_category"), result.get("tool_category")),
        "stdout_sha256": first_present(payload.get("stdout_sha256"), result.get("stdout_sha256")),
        "stderr_sha256": first_present(payload.get("stderr_sha256"), result.get("stderr_sha256")),
        "output_sha256": first_present(payload.get("output_sha256"), result.get("output_sha256")),
        "error_fingerprint": first_present(payload.get("error_fingerprint"), result.get("error_fingerprint")),
        "code_diff_observed": first_present(payload.get("code_diff_observed"), result.get("code_diff_observed")),
        "code_diff_sha256": first_present(payload.get("code_diff_sha256"), result.get("code_diff_sha256")),
        "code_diff_bytes": first_present(payload.get("code_diff_bytes"), result.get("code_diff_bytes")),
        "code_diff_changed_file_count": first_present(
            payload.get("code_diff_changed_file_count"),
            result.get("code_diff_changed_file_count"),
        ),
        "test_passed_count": first_present(payload.get("test_passed_count"), result.get("test_passed_count")),
        "test_failed_count": first_present(payload.get("test_failed_count"), result.get("test_failed_count")),
        "test_error_count": first_present(payload.get("test_error_count"), result.get("test_error_count")),
        "test_skipped_count": first_present(payload.get("test_skipped_count"), result.get("test_skipped_count")),
        "quality": "exact" if row.get("start_mono_ns") is not None and row.get("end_mono_ns") is not None else "partial",
        "source": "mini_swe.tool_call span",
        "clock_domain_id": row.get("clock_domain_id"),
    }


def canonical_event_row(event: dict[str, Any]) -> dict[str, Any]:
    payload = as_dict(event.get("payload"))
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "event_id": event.get("event_id"),
        "run_id": event.get("run_id"),
        "agent_id": payload.get("agent_id"),
        "source": event.get("source"),
        "event_type": event.get("event_type"),
        "wall_time_ns": event.get("wall_time_ns"),
        "monotonic_time_ns": event.get("monotonic_time_ns"),
        "quality": event.get("quality"),
        "clock_domain_id": payload.get("clock_domain"),
        "host_boot_id": payload.get("host_boot_id"),
        "source_pid": payload.get("source_pid"),
        "span_id": payload.get("span_id"),
        "parent_span_id": payload.get("parent_span_id"),
        "step_id": payload.get("step_id"),
        "model_call_id": first_text(payload.get("model_call_id"), payload.get("agentmon_model_request_id")),
        "server_request_id": first_text(payload.get("server_request_id"), payload.get("api_request_id")),
        "engine_request_id": first_text(payload.get("engine_request_id"), payload.get("vllm_request_id")),
        "labels_json": json_text(event.get("labels") or {}),
        "payload_json": json_text(payload),
    }


def canonical_system_sample_row(event: dict[str, Any]) -> dict[str, Any]:
    payload = as_dict(event.get("payload"))
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "event_id": event.get("event_id"),
        "run_id": event.get("run_id"),
        "source": event.get("source"),
        "event_type": event.get("event_type"),
        "wall_time_ns": event.get("wall_time_ns"),
        "monotonic_time_ns": event.get("monotonic_time_ns"),
        "clock_domain_id": payload.get("clock_domain"),
        "host_boot_id": payload.get("host_boot_id"),
        "observation_scope": payload.get("observation_scope") or "run_window_observation",
        "request_attribution": payload.get("request_attribution") or "not_request_attributed",
        "payload_json": json_text(payload),
    }


def write_canonical_dataset(
    run_dirs: Iterable[str | Path],
    output_dir: str | Path,
    *,
    formats: Iterable[str] = ("jsonl", "csv", "parquet-if-available"),
) -> dict[str, Any]:
    from agentmon.reporting.report import build_report, load_events

    combined = {name: [] for name in TABLE_NAMES}
    source_runs: list[str] = []
    for run_dir in run_dirs:
        run_path = Path(run_dir)
        source_runs.append(str(run_path))
        tables = build_canonical_tables(run_path, report=build_report(run_path), events=load_events(run_path))
        for name in TABLE_NAMES:
            combined[name].extend(tables[name])
    return write_canonical_tables(combined, output_dir, formats=formats, source_runs=source_runs)


def write_run_canonical_tables(
    run_dir: str | Path,
    *,
    report: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    run_path = Path(run_dir)
    tables = build_canonical_tables(run_path, report=report, events=events)
    return write_canonical_tables(
        tables,
        run_path / "canonical",
        formats=("jsonl", "csv", "parquet-if-available"),
        source_runs=[str(run_path)],
    )


def write_canonical_tables(
    tables: dict[str, list[dict[str, Any]]],
    output_dir: str | Path,
    *,
    formats: Iterable[str],
    source_runs: list[str],
) -> dict[str, Any]:
    output = Path(output_dir)
    make_private_dir(output)
    requested = list(dict.fromkeys(formats))
    invalid = set(requested) - {"jsonl", "csv", "parquet", "parquet-if-available"}
    if invalid:
        raise ValueError(f"unsupported canonical formats: {sorted(invalid)}")
    artifacts: dict[str, dict[str, str]] = {name: {} for name in TABLE_NAMES}
    for name in TABLE_NAMES:
        rows = tables.get(name, [])
        if "jsonl" in requested:
            path = output / f"{name}.jsonl"
            write_jsonl(path, rows)
            artifacts[name]["jsonl"] = str(path)
        if "csv" in requested:
            path = output / f"{name}.csv"
            write_csv(path, rows)
            artifacts[name]["csv"] = str(path)
    parquet_requested = "parquet" in requested or "parquet-if-available" in requested
    parquet_status = "not_requested"
    parquet_reason = None
    if parquet_requested:
        try:
            write_parquet_tables(tables, output, artifacts)
            parquet_status = "complete"
        except ModuleNotFoundError as exc:
            parquet_status = "unavailable"
            parquet_reason = "pyarrow_not_installed"
            if "parquet" in requested:
                raise RuntimeError("canonical Parquet export requires pyarrow") from exc
        except Exception as exc:
            parquet_status = "error"
            parquet_reason = f"{type(exc).__name__}: {exc}"
            if "parquet" in requested:
                raise
    manifest = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "dataset_type": "agentmon.canonical",
        "source_runs": source_runs,
        "table_row_counts": {name: len(tables.get(name, [])) for name in TABLE_NAMES},
        "formats_requested": requested,
        "artifacts": artifacts,
        "parquet_status": parquet_status,
        "parquet_missing_reason": parquet_reason,
        "metric_definitions": METRIC_DEFINITIONS,
    }
    write_private_text(output / "canonical_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open_private_text(path) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with open_private_text(path, newline="") as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_scalar(row.get(key)) for key in fields})


def write_parquet_tables(
    tables: dict[str, list[dict[str, Any]]],
    output: Path,
    artifacts: dict[str, dict[str, str]],
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    for name in TABLE_NAMES:
        rows = [{key: csv_scalar(value) for key, value in row.items()} for row in tables.get(name, [])]
        table = pa.Table.from_pylist(rows) if rows else pa.table({"schema_version": pa.array([], type=pa.string())})
        path = output / f"{name}.parquet"
        pq.write_table(table, path)
        os.chmod(path, 0o600)
        artifacts[name]["parquet"] = str(path)


def request_identity_values(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in REQUEST_ID_KEYS:
        value = text_or_none(row.get(key))
        if value and value not in values:
            values.append(value)
    return values


def request_scopes(row: dict[str, Any], *, fallback_run_id: str | None = None) -> list[str]:
    scopes: list[str] = []
    for key in ("agentmon_run_id", "run_id", "task_id"):
        value = text_or_none(row.get(key))
        if value:
            namespace = "run" if key in {"agentmon_run_id", "run_id"} else "task"
            scopes.append(f"{namespace}:{value}")
    if not scopes and fallback_run_id:
        scopes.append(f"run:{fallback_run_id}")
    return scopes or ["GLOBAL_UNSCOPED"]


def scoped_identity(scope: str, value: Any) -> str:
    return f"{scope}|{value}"


def unique_text(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def unique_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        if id(row) not in seen:
            seen.add(id(row))
            output.append(row)
    return output


def request_metric_quality(p2: dict[str, Any], span: dict[str, Any]) -> dict[str, str]:
    return {
        "client_e2e_s": "exact" if span.get("start_mono_ns") is not None and span.get("end_mono_ns") is not None else "unavailable",
        "server_e2e_s": pair_quality(p2, "received", "finished"),
        "queue_s": pair_quality(p2, "queued", "first_scheduled"),
        "exact_prefill_s": text_or_none(p2.get("prefill_boundary_quality")) or "unavailable",
        "exact_ttft_s": pair_quality(p2, "received", "first_token"),
        "exact_decode_s": pair_quality(p2, "first_token", "engine_finished"),
        "exact_tpot_s": pair_quality(p2, "first_token", "last_token"),
        "proxy_ttft_s": pair_quality(p2, "received", "first_output"),
        "proxy_decode_s": pair_quality(p2, "first_output", "finished"),
    }


def request_metric_source() -> dict[str, str]:
    return {key: value["source"] for key, value in METRIC_DEFINITIONS.items()}


def request_clock_domains(p2: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "client": span.get("clock_domain_id"),
        "server_received": p2.get("received_clock_domain"),
        "scheduler": p2.get("first_scheduled_clock_domain"),
        "first_token": p2.get("first_token_clock_domain"),
        "last_token": p2.get("last_token_clock_domain"),
        "engine_finished": p2.get("engine_finished_clock_domain"),
        "server_finished": p2.get("finished_clock_domain"),
    }


def verified_p2_delta(
    p2: dict[str, Any],
    start: int | None,
    end: int | None,
    start_prefix: str,
    end_prefix: str,
) -> float | None:
    if pair_quality(p2, start_prefix, end_prefix) in {"clock_domain_mismatch", "unavailable", "legacy_unverified"}:
        return None
    return delta_ns_s(start, end)


def verified_cross_delta(
    span: dict[str, Any],
    p2: dict[str, Any],
    start: int | None,
    end: int | None,
    p2_prefix: str,
) -> float | None:
    client_domain = text_or_none(span.get("clock_domain_id"))
    server_domain = text_or_none(p2.get(f"{p2_prefix}_clock_domain"))
    if not client_domain or not server_domain or client_domain != server_domain:
        return None
    return delta_ns_s(start, end)


def pair_quality(p2: dict[str, Any], start_prefix: str, end_prefix: str) -> str:
    start = integer_or_none(p2.get(f"{start_prefix}_ns"))
    end = integer_or_none(p2.get(f"{end_prefix}_ns"))
    if start is None or end is None:
        return "unavailable"
    start_domain = text_or_none(p2.get(f"{start_prefix}_clock_domain"))
    end_domain = text_or_none(p2.get(f"{end_prefix}_clock_domain"))
    if start_domain and end_domain and start_domain != end_domain:
        return "clock_domain_mismatch"
    start_host = text_or_none(p2.get(f"{start_prefix}_source_host"))
    end_host = text_or_none(p2.get(f"{end_prefix}_source_host"))
    if start_host and end_host and start_host != end_host:
        return "clock_domain_mismatch"
    if start_domain and start_domain == end_domain:
        return "exact_same_clock_domain"
    return "legacy_unverified"


def token_period_seconds(
    p2: dict[str, Any],
    first_token_ns: int | None,
    last_token_ns: int | None,
    generation_tokens: int | None,
) -> float | None:
    if generation_tokens is None or generation_tokens <= 1:
        return None
    duration = verified_p2_delta(
        p2,
        first_token_ns,
        last_token_ns,
        "first_token",
        "last_token",
    )
    return duration / (generation_tokens - 1) if duration is not None else None


def is_engine_first_token(p2: dict[str, Any]) -> bool:
    source = text_or_none(p2.get("first_token_timestamp_source"))
    return source in {
        None,
        "engine_core",
        "engine_core_output",
        "engine_output",
        "engine_observed",
    } and p2.get("first_token_ns") is not None


def csv_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json_text(value)
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text_or_none(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def first_text(*values: Any) -> str | None:
    return next((str(value) for value in values if value not in {None, ""}), None)


def first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def ns_to_s(value: Any) -> float | None:
    parsed = integer_or_none(value)
    return parsed / 1_000_000_000 if parsed is not None else None


def delta_ns_s(start: int | None, end: int | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start) / 1_000_000_000
