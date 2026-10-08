from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable

from agentmon.reporting.ascend import resolve_ascend_target_devices, summarize_ascend_devices


SUMMARY_SCHEMA_VERSION = "1.0"
AGGREGATE_SCHEMA_VERSION = "1.0"


SUMMARY_FIELDS = [
    "run_id",
    "label",
    "task_success",
    "data_integrity_pass",
    "run_status",
    "exit_code",
    "agent_exit_status",
    "duration_seconds",
    "event_count",
    "event_file_size_bytes",
    "finalization_completed",
    "mini_swe_version",
    "data_quality_status",
    "api_calls",
    "assistant_turns",
    "p1_status",
    "agent_steps",
    "model_requests",
    "tool_calls",
    "context_message_count_max",
    "context_input_chars_max",
    "context_prompt_tokens_max",
    "tool_result_chars_max",
    "tool_result_tokens_estimated_max",
    "context_truncation_count",
    "context_rewrite_count",
    "context_summarization_count",
    "context_compression_status",
    "context_compression_request_count",
    "context_compression_applied_count",
    "context_compression_target_achieved_count",
    "context_compression_removed_tokens_total",
    "context_compression_duration_s_total",
    "context_summary_request_count",
    "context_summary_input_tokens_total",
    "context_summary_output_tokens_total",
    "context_summary_cached_tokens_total",
    "context_summary_uncached_prompt_tokens_total",
    "context_summary_duration_s_total",
    "context_summary_status_counts",
    "context_summary_token_quality_counts",
    "context_compression_status_counts",
    "context_compression_trigger_strategy_counts",
    "context_compression_trigger_reason_counts",
    "context_compression_strategy_counts",
    "context_compression_measurement_quality_counts",
    "model_transport_attempt_total",
    "model_retry_total",
    "retried_model_request_count",
    "path_expansion_step_count",
    "path_expansion_total",
    "instrumentation_error_count",
    "command_nonzero_count",
    "expected_test_failure_count",
    "submission_control_flow_count",
    "tool_error_count",
    "tool_timeout_count",
    "command_nonzero_return_count",
    "prompt_tokens",
    "generation_tokens",
    "cached_tokens",
    "token_source",
    "token_quality",
    "token_missing_reason",
    "instance_cost",
    "model_latency.total",
    "model_latency.mean",
    "model_latency.p50",
    "model_latency.p95",
    "model_latency.max",
    "tool_latency.total",
    "tool_latency.mean",
    "tool_latency.p50",
    "tool_latency.p95",
    "tool_latency.max",
    "invalid_json_lines",
    "duplicate_event_ids",
    "p0_jsonl_sqlite_difference",
    "event_count_mismatch",
    "open_spans",
    "abandoned_spans",
    "orphan_end_spans",
    "negative_durations",
    "peak_pss_bytes",
    "peak_rss_bytes",
    "proc_agent_process_count_max",
    "proc_agent_process_count_active_last",
    "kv_cache_usage_ratio_max",
    "prefix_cache_status",
    "prefix_cache_hit_rate",
    "prefix_cache_hit_rate_quality",
    "vllm_request_count",
    "preemption_count",
    "p2_status",
    "p2_request_count",
    "p2_direct_correlation_count",
    "p2_server_generated_correlation_count",
    "p2_heuristic_correlation_count",
    "p2_unavailable_correlation_count",
    "p2_adapter_error_count",
    "p2_scheduler_iteration_count",
    "p2_scheduler_iteration_completion_rate",
    "p2_mixed_prefill_decode_iteration_count",
    "p2_continuous_batching_iteration_count",
    "p2_queue_latency.mean",
    "p2_queue_latency.p50",
    "p2_queue_latency.p95",
    "p2_prefill_latency.mean",
    "p2_prefill_latency.p50",
    "p2_prefill_latency.p95",
    "p2_prefill_to_first_token_latency.mean",
    "p2_prefill_to_first_token_latency.p50",
    "p2_prefill_to_first_token_latency.p95",
    "p2_decode_latency.mean",
    "p2_decode_latency.p50",
    "p2_decode_latency.p95",
    "p2_ttft_latency.mean",
    "p2_ttft_latency.p50",
    "p2_ttft_latency.p95",
    "p2_engine_ttft_latency.mean",
    "p2_engine_ttft_latency.p50",
    "p2_engine_ttft_latency.p95",
    "p2_latency_quality",
    "p2_latency_quality_coverage",
    "p2_e2e_latency.mean",
    "p2_e2e_latency.p50",
    "p2_e2e_latency.p95",
    "p2_prompt_tokens",
    "p2_cached_prompt_tokens",
    "p2_computed_prefill_tokens",
    "p2_recomputed_prompt_tokens",
    "p2_generation_tokens",
    "p2_prompt_token_accounting_quality_counts",
    "p2_preemption_count",
    "p2_kv_prefix_cache_status",
    "p2_kv_prefix_cache_hit_rate",
    "p2_kv_cached_prompt_ratio",
    "p2_kv_recomputed_prompt_ratio",
    "p2_kv_state_counts",
    "p2_kv_pool_bytes_per_rank",
    "p2_kv_pool_quality",
    "p2_npu_attribution_quality_counts",
    "p2_moe_routing_status",
    "p2_moe_routing_mode",
    "p2_moe_request_count",
    "p2_moe_routed_token_count",
    "p2_moe_assignment_count",
    "p2_moe_max_expert_share",
    "p2_moe_min_normalized_entropy",
    "p2_moe_max_load_coefficient_of_variation",
    "p2_moe_token_alignment_counts",
    "client_server_status",
    "client_server_match_rate",
    "client_model_call_to_server_received_latency.mean",
    "server_finished_to_client_return_latency.mean",
    "client_non_server_overhead_latency.mean",
    "npu_peak_hbm_bytes",
    "npu_max_aicore_util_pct",
    "npu_host_peak_hbm_bytes",
    "npu_host_max_aicore_util_pct",
    "npu_collection_scope",
    "npu_collected_devices",
    "npu_target_devices",
    "npu_target_attribution_status",
    "npu_target_device_source",
    "npu_target_device_mapping_quality",
    "npu_parser_confidence",
    "npu_quality",
    "npu_hbm_quality",
    "npu_aicore_quality",
]


AGGREGATE_FIELDS = [
    "run_count",
    "success_count",
    "failure_count",
    "success_rate",
    "task_success_count",
    "data_integrity_pass_count",
    "duration.p50",
    "duration.p90",
    "duration.p95",
    "duration.max",
    "model_latency.p50",
    "model_latency.p90",
    "model_latency.p95",
    "model_latency.max",
    "tool_latency.p50",
    "tool_latency.p90",
    "tool_latency.p95",
    "tool_latency.max",
    "api_calls.total",
    "api_calls.mean",
    "model_requests.total",
    "model_requests.mean",
    "tool_calls.total",
    "tool_calls.mean",
    "model_retry_total.total",
    "context_truncation_count.total",
    "context_summarization_count.total",
    "context_compression_applied_count.total",
    "context_compression_removed_tokens_total.total",
    "path_expansion_total.total",
    "instrumentation_error_count.total",
    "command_nonzero_count.total",
    "tool_timeout_count.total",
    "submission_control_flow_count.total",
    "prompt_tokens.total",
    "prompt_tokens.mean",
    "generation_tokens.total",
    "generation_tokens.mean",
    "peak_pss_bytes.p50",
    "peak_pss_bytes.p95",
    "peak_pss_bytes.max",
    "peak_rss_bytes.p50",
    "peak_rss_bytes.p95",
    "peak_rss_bytes.max",
    "kv_cache_usage_ratio_max.p50",
    "kv_cache_usage_ratio_max.p95",
    "kv_cache_usage_ratio_max.max",
    "prefix_cache_hit_rate.mean",
    "p2_request_count.total",
    "p2_request_count.mean",
    "p2_direct_correlation_count.total",
    "p2_heuristic_correlation_count.total",
    "p2_unavailable_correlation_count.total",
    "p2_adapter_error_count.total",
    "p2_scheduler_iteration_count.total",
    "p2_mixed_prefill_decode_iteration_count.total",
    "p2_continuous_batching_iteration_count.total",
    "p2_queue_latency.p50",
    "p2_queue_latency.p90",
    "p2_queue_latency.p95",
    "p2_prefill_latency.p50",
    "p2_prefill_latency.p90",
    "p2_prefill_latency.p95",
    "p2_prefill_to_first_token_latency.p50",
    "p2_prefill_to_first_token_latency.p90",
    "p2_prefill_to_first_token_latency.p95",
    "p2_decode_latency.p50",
    "p2_decode_latency.p90",
    "p2_decode_latency.p95",
    "p2_ttft_latency.p50",
    "p2_ttft_latency.p90",
    "p2_ttft_latency.p95",
    "p2_engine_ttft_latency.p50",
    "p2_engine_ttft_latency.p90",
    "p2_engine_ttft_latency.p95",
    "p2_e2e_latency.p50",
    "p2_e2e_latency.p90",
    "p2_e2e_latency.p95",
    "p2_prompt_tokens.total",
    "p2_cached_prompt_tokens.total",
    "p2_computed_prefill_tokens.total",
    "p2_recomputed_prompt_tokens.total",
    "p2_generation_tokens.total",
    "p2_preemption_count.total",
    "p2_kv_pool_bytes_per_rank.p50",
    "p2_kv_pool_bytes_per_rank.p95",
    "p2_kv_pool_bytes_per_rank.max",
    "p2_moe_request_count.total",
    "p2_moe_routed_token_count.total",
    "p2_moe_assignment_count.total",
    "p2_moe_max_expert_share.max",
    "p2_moe_min_normalized_entropy.p50",
    "p2_moe_max_load_coefficient_of_variation.max",
]


def build_run_summary(report: dict[str, Any]) -> dict[str, Any]:
    manifest = as_dict(report.get("manifest"))
    agent = as_dict(report.get("agent"))
    event_log = as_dict(report.get("event_log"))
    p1 = as_dict(report.get("p1_spans"))
    p1_workload = as_dict(p1.get("workload"))
    p1_context = as_dict(p1_workload.get("context"))
    p1_compression = as_dict(p1_context.get("compression"))
    p1_retry = as_dict(p1_workload.get("retry"))
    p1_semantic = as_dict(p1_workload.get("semantic_phase"))
    tool_attr = as_dict(report.get("tool_attribution"))
    proc = as_dict(report.get("proc"))
    kv_cache = as_dict(report.get("kv_cache"))
    vllm = as_dict(report.get("vllm"))
    vllm_p2 = as_dict(report.get("vllm_p2"))
    client_server = as_dict(report.get("client_server"))
    ascend = as_dict(report.get("ascend"))
    event_snapshot = as_dict(report.get("event_snapshot"))
    finalization = as_dict(report.get("finalization"))
    analysis_state = as_dict(report.get("analysis_state"))
    analysis_trajectory = as_dict(analysis_state.get("trajectory"))

    model_durations = span_durations(report, "model_request")
    tool_durations = span_durations(report, "tool_call")
    tool_error_count = count_spans(report, span_type="tool_call", statuses={"error"})
    command_nonzero_count = count_nonzero_commands(tool_attr)
    tool_timeout_count = count_tool_timeouts(tool_attr)
    instrumentation_error_count = count_instrumentation_errors(report)
    submission_control_flow_count = count_submission_control_flow(report)
    target_resolution = resolve_ascend_target_devices(p2_events=capability_events_from_report(vllm_p2))
    npu_devices = summarize_ascend_devices(
        ascend,
        target_devices=target_resolution["devices"],
        target_device_source=target_resolution["source"],
        visible_devices_raw=target_resolution["raw"],
        mapping_quality=target_resolution["mapping_quality"],
    )
    prefix_status = none_if_unavailable(kv_cache.get("prefix_cache_status"))
    prefix_hit_rate = numeric_or_none(kv_cache.get("prefix_cache_hit_rate"))
    prefix_hit_quality = none_if_unavailable(kv_cache.get("prefix_cache_hit_rate_quality"))
    p2_status = vllm_p2.get("status") or "missing"
    p2_correlation = as_dict(vllm_p2.get("correlation"))
    p2_latency = as_dict(vllm_p2.get("latency"))
    p2_tokens = as_dict(vllm_p2.get("tokens"))
    p2_kv = as_dict(vllm_p2.get("kv_cache"))
    p2_kv_pool = as_dict(p2_kv.get("kv_pool"))
    p2_npu = as_dict(vllm_p2.get("npu_attribution"))
    p2_scheduler = as_dict(vllm_p2.get("scheduler"))
    p2_scheduler_iterations = as_dict(p2_scheduler.get("iterations"))
    p2_moe = as_dict(vllm_p2.get("moe_routing"))
    run_status = report.get("status") or manifest.get("status")
    exit_code = report.get("exit_code") if report.get("exit_code") is not None else manifest.get("exit_code")
    agent_exit_status = agent.get("exit_status")
    agent_prompt_tokens = numeric_or_none(agent.get("prompt_tokens"))
    agent_generation_tokens = numeric_or_none(agent.get("completion_tokens"))
    agent_cached_tokens = numeric_or_none(agent.get("cached_tokens"))
    agent_token_source = agent.get("token_source")
    agent_token_quality = agent.get("token_quality")
    agent_token_missing_reason = agent.get("token_missing_reason")
    if not any(value is not None for value in (agent_prompt_tokens, agent_generation_tokens, agent_cached_tokens)):
        agent_token_source = agent_token_source or "unavailable"
        agent_token_quality = agent_token_quality or "unavailable"
        agent_token_missing_reason = (
            agent_token_missing_reason
            or "trajectory_usage_fields_absent;p1_usage_fields_absent;p2_attribution_not_exclusive"
        )
    elif not agent_token_source:
        agent_token_source = "unknown"
        agent_token_quality = agent_token_quality or "unavailable"

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_type": "agentmon.run_summary",
        "source_report_schema_version": report.get("schema_version"),
        "run_id": report.get("run_id") or manifest.get("run_id"),
        "label": manifest.get("label"),
        "run_status": run_status,
        "exit_code": exit_code,
        "agent_exit_status": agent_exit_status,
        "duration_seconds": numeric_or_none(report.get("duration_s")),
        "event_count": numeric_or_none(event_snapshot.get("valid_event_count", report.get("event_count"))),
        "event_file_size_bytes": numeric_or_none(event_snapshot.get("file_size_bytes")),
        "finalization_completed": finalization.get("completed") is True,
        "analysis_trajectory_status": analysis_trajectory.get("status"),
        "runtime_finalization_trajectory_status": analysis_trajectory.get("runtime_finalization_status"),
        "post_run_trajectory_import_status": analysis_trajectory.get("post_run_import_status"),
        "mini_swe_version": agent.get("mini_version"),
        "data_quality_status": summarize_data_quality_status(as_dict(report.get("data_quality"))),
        "data_quality": as_dict(report.get("data_quality")),
        "api_calls": numeric_or_none(agent.get("api_calls")),
        "assistant_turns": numeric_or_none(agent.get("assistant_turns")),
        "p1_status": p1.get("status"),
        "agent_steps": numeric_or_none(p1.get("agent_steps")),
        "model_requests": numeric_or_none(p1.get("model_requests")),
        "tool_calls": numeric_or_none(p1.get("tool_calls")),
        "context_message_count_max": numeric_or_none(p1_context.get("message_count_max")),
        "context_input_chars_max": numeric_or_none(p1_context.get("input_chars_max")),
        "context_prompt_tokens_max": numeric_or_none(p1_context.get("prompt_tokens_max")),
        "tool_result_chars_max": numeric_or_none(p1_context.get("tool_result_chars_max")),
        "tool_result_tokens_estimated_max": numeric_or_none(
            p1_context.get("tool_result_tokens_estimated_max")
        ),
        "context_truncation_count": numeric_or_none(p1_context.get("truncation_count")),
        "context_rewrite_count": numeric_or_none(p1_context.get("rewrite_count")),
        "context_summarization_count": numeric_or_none(p1_context.get("summarization_count")),
        "context_compression_status": p1_compression.get("status"),
        "context_compression_request_count": numeric_or_none(
            p1_compression.get("request_count")
        ),
        "context_compression_applied_count": numeric_or_none(
            p1_compression.get("applied_request_count")
        ),
        "context_compression_target_achieved_count": numeric_or_none(
            p1_compression.get("target_achieved_count")
        ),
        "context_compression_removed_tokens_total": numeric_or_none(
            p1_compression.get("removed_tokens_total")
        ),
        "context_compression_duration_s_total": numeric_or_none(
            as_dict(p1_compression.get("compression_duration_s")).get("total")
        ),
        "context_summary_request_count": numeric_or_none(
            p1_compression.get("summary_request_count")
        ),
        "context_summary_input_tokens_total": numeric_or_none(
            p1_compression.get("summary_input_tokens_total")
        ),
        "context_summary_output_tokens_total": numeric_or_none(
            p1_compression.get("summary_output_tokens_total")
        ),
        "context_summary_cached_tokens_total": numeric_or_none(
            p1_compression.get("summary_cached_tokens_total")
        ),
        "context_summary_uncached_prompt_tokens_total": numeric_or_none(
            p1_compression.get("summary_uncached_prompt_tokens_total")
        ),
        "context_summary_duration_s_total": numeric_or_none(
            as_dict(p1_compression.get("summary_duration_s")).get("total")
        ),
        "context_summary_status_counts": as_dict(
            p1_compression.get("summary_status_counts")
        ),
        "context_summary_token_quality_counts": as_dict(
            p1_compression.get("summary_token_quality_counts")
        ),
        "context_compression_status_counts": as_dict(
            p1_compression.get("status_counts")
        ),
        "context_compression_trigger_strategy_counts": as_dict(
            p1_compression.get("trigger_strategy_counts")
        ),
        "context_compression_trigger_reason_counts": as_dict(
            p1_compression.get("trigger_reason_counts")
        ),
        "context_compression_strategy_counts": as_dict(
            p1_compression.get("strategy_counts")
        ),
        "context_compression_measurement_quality_counts": as_dict(
            p1_compression.get("measurement_quality_counts")
        ),
        "model_transport_attempt_total": numeric_or_none(p1_retry.get("transport_attempt_total")),
        "model_retry_total": numeric_or_none(p1_retry.get("retry_total")),
        "retried_model_request_count": numeric_or_none(p1_retry.get("retried_model_request_count")),
        "path_expansion_step_count": numeric_or_none(p1_semantic.get("path_expansion_step_count")),
        "path_expansion_total": numeric_or_none(p1_semantic.get("path_expansion_total")),
        "instrumentation_error_count": instrumentation_error_count,
        "command_nonzero_count": command_nonzero_count,
        "expected_test_failure_count": numeric_or_none(manifest.get("expected_test_failure_count")),
        "submission_control_flow_count": submission_control_flow_count,
        "tool_error_count": tool_error_count,
        "tool_timeout_count": tool_timeout_count,
        "command_nonzero_return_count": command_nonzero_count,
        "prompt_tokens": agent_prompt_tokens,
        "generation_tokens": agent_generation_tokens,
        "cached_tokens": agent_cached_tokens,
        "token_source": agent_token_source,
        "token_quality": agent_token_quality,
        "token_missing_reason": agent_token_missing_reason,
        "instance_cost": numeric_or_none(agent.get("instance_cost")),
        "model_latency": latency_stats(model_durations),
        "tool_latency": latency_stats(tool_durations),
        "invalid_json_lines": numeric_or_none(event_log.get("jsonl_invalid_count", event_log.get("jsonl_invalid_lines"))),
        "duplicate_event_ids": numeric_or_none(event_log.get("duplicate_event_ids")),
        "p0_jsonl_sqlite_difference": numeric_or_none(event_log.get("p0_jsonl_sqlite_difference")),
        "event_count_mismatch": bool(event_log.get("event_count_mismatch") or event_log.get("p0_jsonl_sqlite_mismatch")),
        "open_spans": numeric_or_none(p1.get("open_span_count")),
        "abandoned_spans": numeric_or_none(p1.get("abandoned_span_count")),
        "orphan_end_spans": numeric_or_none(p1.get("orphan_end_count")),
        "negative_durations": numeric_or_none(p1.get("negative_duration_count")),
        "peak_pss_bytes": numeric_or_none(proc.get("agent_peak_pss_bytes")),
        "peak_rss_bytes": numeric_or_none(proc.get("agent_peak_rss_bytes")),
        "proc_agent_process_count_max": numeric_or_none(proc.get("agent_process_count_max")),
        "proc_agent_process_count_active_last": numeric_or_none(proc.get("agent_process_count_active_last")),
        "kv_cache_usage_ratio_max": numeric_or_none(kv_cache.get("kv_cache_usage_ratio_max")),
        "prefix_cache_status": prefix_status,
        "prefix_cache_hit_rate": None if prefix_status == "disabled" else prefix_hit_rate,
        "prefix_cache_hit_rate_quality": "disabled" if prefix_status == "disabled" else prefix_hit_quality,
        "vllm_request_count": find_counter_delta(vllm, ["request_success_total", "requests_total"], ["request"]),
        "preemption_count": find_counter_delta(vllm, ["preemption_total", "preemptions_total"], ["preemption"]),
        "p2_status": p2_status,
        "p2_request_count": p2_metric(vllm_p2, "request_count", p2_status),
        "p2_direct_correlation_count": p2_metric(p2_correlation, "direct", p2_status),
        "p2_server_generated_correlation_count": p2_metric(p2_correlation, "server_generated", p2_status),
        "p2_heuristic_correlation_count": p2_metric(p2_correlation, "heuristic", p2_status),
        "p2_unavailable_correlation_count": p2_metric(p2_correlation, "unavailable", p2_status),
        "p2_adapter_error_count": p2_metric(vllm_p2, "adapter_error_count", p2_status),
        "p2_scheduler_iteration_count": p2_metric(
            p2_scheduler_iterations,
            "scheduled_count",
            p2_status,
        ),
        "p2_scheduler_iteration_completion_rate": p2_metric(
            p2_scheduler_iterations,
            "completion_rate",
            p2_status,
        ),
        "p2_mixed_prefill_decode_iteration_count": p2_metric(
            p2_scheduler_iterations,
            "mixed_prefill_decode_iteration_count",
            p2_status,
        ),
        "p2_continuous_batching_iteration_count": p2_metric(
            p2_scheduler_iterations,
            "continuous_batching_iteration_count",
            p2_status,
        ),
        "p2_queue_latency": p2_latency_stats(p2_latency, "queue", p2_status),
        "p2_prefill_latency": p2_latency_stats(p2_latency, "prefill", p2_status),
        "p2_prefill_to_first_token_latency": p2_latency_stats(
            p2_latency,
            "prefill_to_first_token",
            p2_status,
        ),
        "p2_decode_latency": p2_latency_stats(p2_latency, "decode", p2_status),
        "p2_ttft_latency": p2_latency_stats(p2_latency, "ttft", p2_status),
        "p2_engine_ttft_latency": p2_latency_stats(p2_latency, "engine_ttft", p2_status),
        "p2_latency_quality": as_dict(p2_latency.get("quality")) if p2_status != "missing" else {},
        "p2_latency_quality_coverage": (
            as_dict(p2_latency.get("quality_coverage")) if p2_status != "missing" else {}
        ),
        "p2_e2e_latency": p2_latency_stats(p2_latency, "e2e", p2_status),
        "p2_prompt_tokens": p2_token_total(p2_tokens, "prompt_tokens", p2_status),
        "p2_cached_prompt_tokens": p2_token_total(p2_tokens, "cached_prompt_tokens", p2_status),
        "p2_computed_prefill_tokens": p2_token_total(p2_tokens, "computed_prefill_tokens", p2_status),
        "p2_recomputed_prompt_tokens": p2_token_total(p2_tokens, "recomputed_prompt_tokens", p2_status),
        "p2_generation_tokens": p2_token_total(p2_tokens, "generation_tokens", p2_status),
        "p2_prompt_token_accounting_quality_counts": (
            as_dict(p2_tokens.get("prompt_token_accounting_quality_counts"))
            if p2_status != "missing"
            else {}
        ),
        "p2_preemption_count": p2_token_total(p2_tokens, "preemption_count", p2_status),
        "p2_kv_prefix_cache_status": None if p2_status == "missing" else p2_kv.get("prefix_cache_status"),
        "p2_kv_prefix_cache_hit_rate": p2_metric(p2_kv, "prefix_cache_hit_rate", p2_status),
        "p2_kv_cached_prompt_ratio": p2_metric(p2_kv, "cached_prompt_ratio", p2_status),
        "p2_kv_recomputed_prompt_ratio": p2_metric(p2_kv, "recomputed_prompt_ratio", p2_status),
        "p2_kv_state_counts": as_dict(p2_kv.get("state_counts")) if p2_status != "missing" else {},
        "p2_kv_pool_bytes_per_rank": p2_metric(p2_kv_pool, "allocated_pool_bytes_per_rank", p2_status),
        "p2_kv_pool_quality": None if p2_status == "missing" else p2_kv_pool.get("source_quality"),
        "p2_npu_attribution_quality_counts": as_dict(p2_npu.get("quality_counts")) if p2_status != "missing" else {},
        "p2_moe_routing_status": p2_moe.get("status") or "missing",
        "p2_moe_routing_mode": p2_moe.get("mode"),
        "p2_moe_request_count": numeric_or_none(p2_moe.get("request_count")),
        "p2_moe_routed_token_count": numeric_or_none(p2_moe.get("routed_token_count")),
        "p2_moe_assignment_count": numeric_or_none(p2_moe.get("assignment_count")),
        "p2_moe_max_expert_share": numeric_or_none(p2_moe.get("max_expert_share")),
        "p2_moe_min_normalized_entropy": numeric_or_none(
            p2_moe.get("min_normalized_entropy")
        ),
        "p2_moe_max_load_coefficient_of_variation": numeric_or_none(
            p2_moe.get("max_load_coefficient_of_variation")
        ),
        "p2_moe_token_alignment_counts": as_dict(
            p2_moe.get("token_alignment_counts")
        ),
        "client_server_status": client_server.get("status"),
        "client_server_match_rate": numeric_or_none(client_server.get("match_rate")),
        "client_model_call_to_server_received_latency": as_dict(
            client_server.get("client_model_call_to_server_received")
        ),
        "server_finished_to_client_return_latency": as_dict(
            client_server.get("server_finished_to_client_return")
        ),
        "client_non_server_overhead_latency": as_dict(
            client_server.get("client_non_server_overhead")
        ),
        "npu_peak_hbm_bytes": npu_devices.get("target_peak_hbm_used_bytes"),
        "npu_max_aicore_util_pct": npu_devices.get("target_max_aicore_util_pct"),
        "npu_host_peak_hbm_bytes": npu_devices.get("host_peak_hbm_used_bytes"),
        "npu_host_max_aicore_util_pct": npu_devices.get("host_max_aicore_util_pct"),
        "npu_collection_scope": npu_devices.get("collection_scope"),
        "npu_collected_devices": npu_devices.get("collected_devices"),
        "npu_target_devices": npu_devices.get("target_devices"),
        "npu_target_attribution_status": npu_devices.get("target_attribution_status"),
        "npu_target_device_source": npu_devices.get("target_device_source"),
        "npu_target_device_mapping_quality": npu_devices.get("target_device_mapping_quality"),
        "npu_parser_confidence": numeric_or_none(ascend.get("parser_confidence")),
        "npu_quality": ascend.get("status"),
        "npu_hbm_quality": ascend.get("hbm_quality"),
        "npu_aicore_quality": ascend.get("aicore_quality"),
    }
    summary["data_integrity_pass"] = data_integrity_pass(summary)
    summary["task_success"] = (
        run_status == "success"
        and exit_code == 0
        and str(agent_exit_status).lower() == "submitted"
        and summary["data_integrity_pass"]
    )
    summary["strict_failures"] = strict_failures(summary)
    return summary


def build_aggregate(
    summaries: list[dict[str, Any]],
    *,
    reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reports = reports or []
    run_count = len(summaries)
    success_count = sum(1 for item in summaries if item.get("run_status") == "success")
    failure_count = run_count - success_count
    model_durations = flatten(span_durations(report, "model_request") for report in reports)
    tool_durations = flatten(span_durations(report, "tool_call") for report in reports)
    model_latency_denominator: int | None = int(sum(values(summaries, "model_requests"))) or None
    tool_latency_denominator: int | None = int(sum(values(summaries, "tool_calls"))) or None
    if not model_durations:
        model_durations = values(summaries, "model_latency.mean")
        model_latency_denominator = run_count
    if not tool_durations:
        tool_durations = values(summaries, "tool_latency.mean")
        tool_latency_denominator = run_count

    aggregate = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "summary_type": "agentmon.aggregate_summary",
        "run_count": run_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": (success_count / run_count) if run_count else None,
        "task_success_count": sum(1 for item in summaries if item.get("task_success") is True),
        "data_integrity_pass_count": sum(1 for item in summaries if item.get("data_integrity_pass") is True),
        "failure_by_exit_status": count_failures_by_exit_status(summaries),
        "duration": percentile_stats(values(summaries, "duration_seconds"), [50, 90, 95], denominator=run_count),
        "model_latency": percentile_stats(model_durations, [50, 90, 95], denominator=model_latency_denominator),
        "tool_latency": percentile_stats(tool_durations, [50, 90, 95], denominator=tool_latency_denominator),
        "api_calls": total_mean(values(summaries, "api_calls"), run_count),
        "model_requests": total_mean(values(summaries, "model_requests"), run_count),
        "tool_calls": total_mean(values(summaries, "tool_calls"), run_count),
        "model_retry_total": total_mean(values(summaries, "model_retry_total"), run_count),
        "context_truncation_count": total_mean(
            values(summaries, "context_truncation_count"),
            run_count,
        ),
        "context_summarization_count": total_mean(
            values(summaries, "context_summarization_count"),
            run_count,
        ),
        "context_compression_status_counts": count_values(
            summaries,
            "context_compression_status",
            missing="missing",
        ),
        "context_compression_applied_count": total_mean(
            values(summaries, "context_compression_applied_count"),
            run_count,
        ),
        "context_compression_removed_tokens_total": total_mean(
            values(summaries, "context_compression_removed_tokens_total"),
            run_count,
        ),
        "path_expansion_total": total_mean(values(summaries, "path_expansion_total"), run_count),
        "instrumentation_error_count": total_mean(values(summaries, "instrumentation_error_count"), run_count),
        "command_nonzero_count": total_mean(values(summaries, "command_nonzero_count"), run_count),
        "tool_timeout_count": total_mean(values(summaries, "tool_timeout_count"), run_count),
        "submission_control_flow_count": total_mean(values(summaries, "submission_control_flow_count"), run_count),
        "prompt_tokens": total_mean(values(summaries, "prompt_tokens"), run_count),
        "generation_tokens": total_mean(values(summaries, "generation_tokens"), run_count),
        "peak_pss_bytes": percentile_stats(values(summaries, "peak_pss_bytes"), [50, 95], denominator=run_count),
        "peak_rss_bytes": percentile_stats(values(summaries, "peak_rss_bytes"), [50, 95], denominator=run_count),
        "kv_cache_usage_ratio_max": percentile_stats(
            values(summaries, "kv_cache_usage_ratio_max"),
            [50, 95],
            denominator=run_count,
        ),
        "p2_status_counts": count_values(summaries, "p2_status", missing="missing"),
        "p2_request_count": total_mean(values(summaries, "p2_request_count"), run_count),
        "p2_direct_correlation_count": total_mean(values(summaries, "p2_direct_correlation_count"), run_count),
        "p2_heuristic_correlation_count": total_mean(values(summaries, "p2_heuristic_correlation_count"), run_count),
        "p2_unavailable_correlation_count": total_mean(
            values(summaries, "p2_unavailable_correlation_count"),
            run_count,
        ),
        "p2_adapter_error_count": total_mean(values(summaries, "p2_adapter_error_count"), run_count),
        "p2_scheduler_iteration_count": total_mean(
            values(summaries, "p2_scheduler_iteration_count"),
            run_count,
        ),
        "p2_mixed_prefill_decode_iteration_count": total_mean(
            values(summaries, "p2_mixed_prefill_decode_iteration_count"),
            run_count,
        ),
        "p2_continuous_batching_iteration_count": total_mean(
            values(summaries, "p2_continuous_batching_iteration_count"),
            run_count,
        ),
        "p2_queue_latency": percentile_stats(values(summaries, "p2_queue_latency.mean"), [50, 90, 95], denominator=run_count),
        "p2_prefill_latency": percentile_stats(
            values(summaries, "p2_prefill_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_prefill_to_first_token_latency": percentile_stats(
            values(summaries, "p2_prefill_to_first_token_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_decode_latency": percentile_stats(
            values(summaries, "p2_decode_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_ttft_latency": percentile_stats(
            values(summaries, "p2_ttft_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_engine_ttft_latency": percentile_stats(
            values(summaries, "p2_engine_ttft_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_e2e_latency": percentile_stats(
            values(summaries, "p2_e2e_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "p2_prompt_tokens": total_mean(values(summaries, "p2_prompt_tokens"), run_count),
        "p2_cached_prompt_tokens": total_mean(values(summaries, "p2_cached_prompt_tokens"), run_count),
        "p2_computed_prefill_tokens": total_mean(values(summaries, "p2_computed_prefill_tokens"), run_count),
        "p2_recomputed_prompt_tokens": total_mean(values(summaries, "p2_recomputed_prompt_tokens"), run_count),
        "p2_generation_tokens": total_mean(values(summaries, "p2_generation_tokens"), run_count),
        "p2_preemption_count": total_mean(values(summaries, "p2_preemption_count"), run_count),
        "p2_kv_prefix_cache_status_counts": count_values(
            summaries,
            "p2_kv_prefix_cache_status",
            missing="missing",
        ),
        "p2_kv_state_counts": sum_count_maps(summaries, "p2_kv_state_counts"),
        "p2_kv_pool_quality_counts": count_values(summaries, "p2_kv_pool_quality", missing="missing"),
        "p2_kv_pool_bytes_per_rank": percentile_stats(
            values(summaries, "p2_kv_pool_bytes_per_rank"),
            [50, 95],
            denominator=run_count,
        ),
        "p2_npu_attribution_quality_counts": sum_count_maps(summaries, "p2_npu_attribution_quality_counts"),
        "p2_moe_routing_status_counts": count_values(
            summaries,
            "p2_moe_routing_status",
            missing="missing",
        ),
        "p2_moe_request_count": total_mean(
            values(summaries, "p2_moe_request_count"),
            run_count,
        ),
        "p2_moe_routed_token_count": total_mean(
            values(summaries, "p2_moe_routed_token_count"),
            run_count,
        ),
        "p2_moe_assignment_count": total_mean(
            values(summaries, "p2_moe_assignment_count"),
            run_count,
        ),
        "p2_moe_max_expert_share": percentile_stats(
            values(summaries, "p2_moe_max_expert_share"),
            [50, 95],
            denominator=run_count,
        ),
        "p2_moe_min_normalized_entropy": percentile_stats(
            values(summaries, "p2_moe_min_normalized_entropy"),
            [50, 95],
            denominator=run_count,
        ),
        "p2_moe_max_load_coefficient_of_variation": percentile_stats(
            values(summaries, "p2_moe_max_load_coefficient_of_variation"),
            [50, 95],
            denominator=run_count,
        ),
        "p2_moe_token_alignment_counts": sum_count_maps(
            summaries,
            "p2_moe_token_alignment_counts",
        ),
        "client_server_status_counts": count_values(summaries, "client_server_status", missing="missing"),
        "client_server_match_rate": percentile_stats(
            values(summaries, "client_server_match_rate"),
            [50, 95],
            denominator=run_count,
        ),
        "client_model_call_to_server_received_latency": percentile_stats(
            values(summaries, "client_model_call_to_server_received_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "server_finished_to_client_return_latency": percentile_stats(
            values(summaries, "server_finished_to_client_return_latency.mean"),
            [50, 90, 95],
            denominator=run_count,
        ),
        "data_quality_failures": count_data_quality_failures(summaries),
        "span_issue_run_counts": {
            "open_spans": count_runs_with_positive(summaries, "open_spans"),
            "abandoned_spans": count_runs_with_positive(summaries, "abandoned_spans"),
            "negative_durations": count_runs_with_positive(summaries, "negative_durations"),
            "orphan_end_spans": count_runs_with_positive(summaries, "orphan_end_spans"),
        },
        "prefix_cache_status_counts": count_values(summaries, "prefix_cache_status", missing="unknown"),
        "prefix_cache_hit_rate": prefix_cache_hit_rate_aggregate(summaries),
        "strict_failure_run_count": sum(1 for item in summaries if item.get("strict_failures")),
        "runs": summaries,
    }
    return aggregate


def strict_failures(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if summary.get("run_status") not in {None, "success"}:
        failures.append("run_status")
    if positive(summary.get("invalid_json_lines")):
        failures.append("invalid_json")
    if positive(summary.get("duplicate_event_ids")):
        failures.append("duplicate_event_ids")
    if positive(summary.get("open_spans")):
        failures.append("open_spans")
    if positive(summary.get("abandoned_spans")):
        failures.append("abandoned_spans")
    if positive(summary.get("orphan_end_spans")):
        failures.append("orphan_end_spans")
    if positive(summary.get("negative_durations")):
        failures.append("negative_durations")
    if summary.get("event_count_mismatch"):
        failures.append("p0_jsonl_sqlite_mismatch")
    return failures


def data_integrity_pass(summary: dict[str, Any]) -> bool:
    return (
        not positive(summary.get("invalid_json_lines"))
        and not positive(summary.get("duplicate_event_ids"))
        and not positive(summary.get("open_spans"))
        and not positive(summary.get("abandoned_spans"))
        and not positive(summary.get("orphan_end_spans"))
        and not positive(summary.get("negative_durations"))
        and not bool(summary.get("event_count_mismatch"))
    )


def passes_filters(
    summary: dict[str, Any],
    *,
    label: str | None = None,
    status: str | None = None,
    started_after_ns: int | None = None,
    started_before_ns: int | None = None,
    report: dict[str, Any] | None = None,
) -> bool:
    if label is not None and summary.get("label") != label:
        return False
    if status is not None and summary.get("run_status") != status:
        return False
    manifest = as_dict((report or {}).get("manifest"))
    start_wall_ns = manifest.get("start_wall_ns") or manifest.get("created_wall_ns")
    if started_after_ns is not None and (not isinstance(start_wall_ns, int) or start_wall_ns < started_after_ns):
        return False
    if started_before_ns is not None and (not isinstance(start_wall_ns, int) or start_wall_ns > started_before_ns):
        return False
    return True


def format_summary_table(summary: dict[str, Any]) -> str:
    rows = [(field, display_value(get_nested(summary, field))) for field in SUMMARY_FIELDS]
    return render_table(["metric", "value"], rows)


def format_aggregate_table(aggregate: dict[str, Any]) -> str:
    rows = [(field, display_value(get_nested(aggregate, field))) for field in AGGREGATE_FIELDS]
    rows.append(("strict_failure_run_count", display_value(aggregate.get("strict_failure_run_count"))))
    return render_table(["metric", "value"], rows)


def summaries_to_csv(summaries: list[dict[str, Any]]) -> str:
    return rows_to_csv(summaries, SUMMARY_FIELDS)


def aggregate_to_csv(aggregate: dict[str, Any]) -> str:
    row = {field: get_nested(aggregate, field) for field in AGGREGATE_FIELDS}
    row["strict_failure_run_count"] = aggregate.get("strict_failure_run_count")
    return rows_to_csv([row], [*AGGREGATE_FIELDS, "strict_failure_run_count"], already_flat=True)


def rows_to_csv(rows: list[dict[str, Any]], fields: list[str], *, already_flat: bool = False) -> str:
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if already_flat:
            flat = {key: csv_value(row.get(key)) for key in fields}
        else:
            flat = {field: csv_value(get_nested(row, field)) for field in fields}
        writer.writerow(flat)
    return handle.getvalue()


def render_table(headers: list[str], rows: list[tuple[str, str]]) -> str:
    widths = [
        max(len(headers[0]), *(len(str(row[0])) for row in rows)) if rows else len(headers[0]),
        max(len(headers[1]), *(len(str(row[1])) for row in rows)) if rows else len(headers[1]),
    ]
    lines = [
        f"{headers[0].ljust(widths[0])}  {headers[1].ljust(widths[1])}",
        f"{'-' * widths[0]}  {'-' * widths[1]}",
    ]
    lines.extend(f"{str(key).ljust(widths[0])}  {str(value).ljust(widths[1])}" for key, value in rows)
    return "\n".join(lines)


def span_durations(report: dict[str, Any], span_type: str) -> list[float]:
    spans = as_dict(report.get("p1_spans")).get("spans") or []
    result: list[float] = []
    for span in spans:
        if not isinstance(span, dict) or span.get("span_type") != span_type:
            continue
        value = numeric_or_none(span.get("duration_s"))
        if value is not None:
            result.append(float(value))
    return result


def latency_stats(values_: list[float]) -> dict[str, Any]:
    return {
        "total": sum(values_) if values_ else None,
        "mean": (sum(values_) / len(values_)) if values_ else None,
        "p50": percentile(values_, 50),
        "p95": percentile(values_, 95),
        "max": max(values_) if values_ else None,
        "sample_count": len(values_),
    }


def percentile_stats(values_: list[float], percentiles: list[int], *, denominator: int | None = None) -> dict[str, Any]:
    stats = {f"p{item}": percentile(values_, item) for item in percentiles}
    stats["max"] = max(values_) if values_ else None
    stats["sample_count"] = len(values_)
    stats["coverage"] = (len(values_) / denominator) if denominator else None
    return stats


def percentile(values_: list[float], pct: float) -> float | None:
    clean = sorted(value for value in values_ if isinstance(value, (int, float)) and math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return float(clean[0])
    rank = (float(pct) / 100.0) * (len(clean) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(clean[int(rank)])
    lower_value = clean[lower]
    upper_value = clean[upper]
    return float(lower_value + (upper_value - lower_value) * (rank - lower))


def total_mean(values_: list[float], run_count: int) -> dict[str, Any]:
    total = sum(values_) if values_ else None
    return {
        "total": total,
        "mean": (total / len(values_)) if values_ else None,
        "sample_count": len(values_),
        "coverage": (len(values_) / run_count) if run_count else None,
    }


def p2_metric(data: dict[str, Any], key: str, status: Any) -> float | int | None:
    if status in {None, "missing"}:
        return None
    return numeric_or_none(data.get(key))


def p2_latency_stats(latency: dict[str, Any], key: str, status: Any) -> dict[str, Any]:
    empty = latency_stats([])
    if status in {None, "missing"}:
        return empty
    section = as_dict(latency.get(key))
    if not section:
        return empty
    return {
        "total": numeric_or_none(section.get("total")),
        "mean": numeric_or_none(section.get("mean")),
        "p50": numeric_or_none(section.get("p50")),
        "p95": numeric_or_none(section.get("p95")),
        "max": numeric_or_none(section.get("max")),
        "sample_count": numeric_or_none(section.get("sample_count")) or 0,
    }


def p2_token_total(tokens: dict[str, Any], key: str, status: Any) -> float | int | None:
    if status in {None, "missing"}:
        return None
    return numeric_or_none(as_dict(tokens.get(key)).get("total"))


def prefix_cache_hit_rate_aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    values_: list[float] = []
    enabled_count = 0
    for summary in summaries:
        if summary.get("prefix_cache_status") != "enabled":
            continue
        enabled_count += 1
        value = numeric_or_none(summary.get("prefix_cache_hit_rate"))
        if value is not None:
            values_.append(value)
    return {
        "mean": (sum(values_) / len(values_)) if values_ else None,
        "sample_count": len(values_),
        "enabled_run_count": enabled_count,
        "coverage": (len(values_) / enabled_count) if enabled_count else None,
    }


def count_spans(report: dict[str, Any], *, span_type: str, statuses: set[str]) -> int | None:
    spans = as_dict(report.get("p1_spans")).get("spans")
    if not isinstance(spans, list):
        return None
    count = 0
    for span in spans:
        if isinstance(span, dict) and span.get("span_type") == span_type and span.get("status") in statuses:
            count += 1
    return count


def count_nonzero_commands(tool_attr: dict[str, Any]) -> int | None:
    tools = tool_attr.get("tools")
    if not isinstance(tools, list):
        return None
    count = 0
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        returncode = tool.get("returncode")
        if isinstance(returncode, int) and returncode != 0:
            count += 1
    return count


def count_tool_timeouts(tool_attr: dict[str, Any]) -> int | None:
    explicit = numeric_or_none(tool_attr.get("tool_timeout_count"))
    if explicit is not None:
        return int(explicit)
    tools = tool_attr.get("tools")
    if not isinstance(tools, list):
        return None
    return sum(1 for tool in tools if isinstance(tool, dict) and tool.get("timed_out") is True)


def count_instrumentation_errors(report: dict[str, Any]) -> int | None:
    spans = as_dict(report.get("p1_spans")).get("spans")
    if isinstance(spans, list):
        return sum(
            1
            for span in spans
            if isinstance(span, dict)
            and span.get("status") == "error"
            and span.get("terminal_reason") != "submitted"
            and not span.get("submission_control_flow")
        )
    errors = report.get("errors")
    if not isinstance(errors, list):
        return None
    return sum(1 for error in errors if isinstance(error, dict) and str(error.get("event_type", "")).endswith(".error"))


def count_submission_control_flow(report: dict[str, Any]) -> int | None:
    spans = as_dict(report.get("p1_spans")).get("spans")
    if not isinstance(spans, list):
        return None
    return sum(
        1
        for span in spans
        if isinstance(span, dict)
        and (span.get("terminal_reason") == "submitted" or span.get("submission_control_flow") is True)
    )


def summarize_npu_devices(ascend: dict[str, Any]) -> tuple[int | None, float | None]:
    hbm_values: list[int] = []
    aicore_values: list[float] = []
    for device in as_dict(ascend.get("devices")).values():
        if not isinstance(device, dict):
            continue
        hbm = numeric_or_none(device.get("peak_hbm_used_bytes"))
        aicore = numeric_or_none(device.get("max_aicore_util_pct"))
        if hbm is not None:
            hbm_values.append(int(hbm))
        if aicore is not None:
            aicore_values.append(float(aicore))
    return (max(hbm_values) if hbm_values else None, max(aicore_values) if aicore_values else None)


def capability_events_from_report(vllm_p2: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for payload in vllm_p2.get("capabilities") or []:
        if not isinstance(payload, dict):
            continue
        events.append({"source": "vllm_p2", "event_type": "vllm.adapter.capability", "payload": payload})
    return events


def summarize_data_quality_status(data_quality: dict[str, Any]) -> str:
    if not data_quality:
        return "missing"
    values_ = [str(value) for value in data_quality.values()]
    if all(value == "complete" for value in values_):
        return "complete"
    if any(value == "complete" for value in values_):
        return "partial"
    return "missing"


def count_failures_by_exit_status(summaries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        if summary.get("run_status") == "success":
            continue
        key = str(summary.get("agent_exit_status") or "N/A")
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_data_quality_failures(summaries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        for key, value in as_dict(summary.get("data_quality")).items():
            if value != "complete":
                counts[key] = counts.get(key, 0) + 1
    return counts


def count_runs_with_positive(summaries: list[dict[str, Any]], key: str) -> int:
    return sum(1 for summary in summaries if positive(summary.get(key)))


def count_values(summaries: list[dict[str, Any]], key: str, *, missing: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        value = summary.get(key)
        label = str(value) if value not in {None, ""} else missing
        counts[label] = counts.get(label, 0) + 1
    return counts


def sum_count_maps(summaries: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        for label, value in as_dict(summary.get(key)).items():
            numeric = numeric_or_none(value)
            if numeric is None:
                continue
            counts[str(label)] = counts.get(str(label), 0) + int(numeric)
    return counts


def find_counter_delta(vllm: dict[str, Any], exact_needles: list[str], contains_needles: list[str]) -> float | None:
    deltas = as_dict(vllm.get("counter_deltas"))
    if not deltas:
        return None
    for name in sorted(deltas):
        lowered = name.lower()
        if any(needle in lowered for needle in exact_needles):
            return numeric_or_none(deltas[name])
    for name in sorted(deltas):
        lowered = name.lower()
        if any(needle in lowered for needle in contains_needles):
            return numeric_or_none(deltas[name])
    return None


def values(items: list[dict[str, Any]], dotted_key: str) -> list[float]:
    result: list[float] = []
    for item in items:
        value = numeric_or_none(get_nested(item, dotted_key))
        if value is not None:
            result.append(float(value))
    return result


def flatten(groups: Iterable[list[float]]) -> list[float]:
    result: list[float] = []
    for group in groups:
        result.extend(group)
    return result


def get_nested(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def numeric_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    return None


def none_if_unavailable(value: Any) -> Any:
    return None if value in {"unavailable", "N/A", ""} else value


def positive(value: Any) -> bool:
    numeric = numeric_or_none(value)
    return numeric is not None and numeric > 0


def display_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def csv_value(value: Any) -> Any:
    if value is None:
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value
