from __future__ import annotations

import csv
import io
import json
from typing import Any

from agentmon.step_sequence.utils import as_dict, fmt_bytes, fmt_na, fmt_pct, fmt_pct_value, fmt_s, ns_to_s
from agentmon.step_sequence.utils import *  # noqa: F403 - shared internal formatting helpers

def format_output(payload: dict[str, Any] | list[dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    analyses = payload if isinstance(payload, list) else [payload]
    if fmt == "csv":
        return format_csv(analyses)
    return "\n\n".join(format_table_one(analysis) for analysis in analyses)


def format_table_one(analysis: dict[str, Any]) -> str:
    totals = analysis["totals"]
    lines = [
        f"Run {analysis['run_id']} | steps={totals['step_count']} "
        f"models={totals['model_request_count']} tools={totals['tool_call_count']} "
        f"model_time={fmt_s(totals['model_time_s'])} tool_time={fmt_s(totals['tool_time_s'])}",
    ]
    for step in analysis["steps"]:
        snapshot = as_dict(step.get("kv_snapshot"))
        agent_proc = as_dict(step.get("agent_proc"))
        vllm_proc = as_dict(step.get("vllm_proc"))
        npu = as_dict(step.get("npu"))
        lines.append(
            f"Step {step['step_index']:>2} {step['step_id']} "
            f"type={step.get('step_type') or 'agent_step'} "
            f"status={step.get('status') or 'N/A'} duration={fmt_s(step.get('duration_s'))} "
            f"align={step.get('alignment_status') or 'N/A'}/{step.get('alignment_quality') or 'N/A'} "
            f"model={fmt_s(step.get('model_time_s'))} tool={fmt_s(step.get('tool_time_s'))} "
            f"agent_cpu={fmt_s(agent_proc.get('cpu_time_s'))} "
            f"agent_cpu/wall={fmt_pct(agent_proc.get('cpu_wall_ratio'))} "
            f"agent_pss={fmt_bytes(agent_proc.get('peak_pss_bytes'))} "
            f"vllm_cpu={fmt_s(vllm_proc.get('cpu_time_s'))} "
            f"hbm={fmt_bytes(first_value(npu.get('target_peak_hbm_used_bytes'), npu.get('host_peak_hbm_used_bytes')))} "
            f"aicore={fmt_pct_value(first_value(npu.get('target_max_aicore_util_pct'), npu.get('host_max_aicore_util_pct')))} "
            f"reuse={fmt_pct(step.get('kv_cache_reuse_rate'))} "
            f"kv_usage={fmt_pct(first_value(step.get('kv_usage_ratio_after_max'), snapshot.get('kv_usage_ratio')))} "
            f"pool/rank={fmt_bytes(snapshot.get('kv_pool_allocated_bytes_per_rank'))}"
        )
        for item in step["items"]:
            if item["item_type"] == "model":
                p2 = item.get("p2") or {}
                kv_snapshot = as_dict(p2.get("kv_snapshot"))
                vllm_proc = as_dict(item.get("vllm_proc"))
                npu = as_dict(item.get("npu"))
                lines.append(
                    "  "
                    f"{item['item_index']:>2}. model duration={fmt_s(item.get('duration_s'))} "
                    f"p2_e2e={fmt_s(p2.get('e2e_s'))} "
                    f"vllm_cpu={fmt_s(vllm_proc.get('cpu_time_s'))} "
                    f"hbm={fmt_bytes(first_value(npu.get('target_peak_hbm_used_bytes'), npu.get('host_peak_hbm_used_bytes')))} "
                    f"tokens={fmt_na(item.get('prompt_tokens'))}/{fmt_na(item.get('completion_tokens'))} "
                    f"cached={fmt_na(item.get('cached_tokens'))} "
                    f"reuse={fmt_pct(p2.get('cache_reuse_rate'))} "
                    f"kv={fmt_pct(first_value(p2.get('kv_usage_after'), kv_snapshot.get('kv_usage_ratio')))} "
                    f"api={item.get('api_request_id') or p2.get('api_request_id') or 'N/A'} "
                    f"resp={item.get('response_id') or 'N/A'} "
                    f"finish={p2.get('finish_reason') or 'N/A'} "
                    f"corr={p2.get('correlation_quality') or 'N/A'}"
                )
            else:
                output_bytes = first_value(item.get("output_bytes"), item.get("stdout_bytes"))
                proc = as_dict(item.get("proc"))
                lines.append(
                    "  "
                    f"{item['item_index']:>2}. tool {item.get('tool_name') or item.get('executable') or 'N/A'} "
                    f"duration={fmt_s(item.get('duration_s'))} cpu={fmt_s(proc.get('cpu_time_s'))} "
                    f"pss={fmt_bytes(proc.get('peak_pss_bytes'))} rc={fmt_na(item.get('returncode'))} "
                    f"timeout={item.get('timed_out') is True} output_bytes={fmt_na(output_bytes)} "
                    f"cmd={item.get('command') or 'N/A'}"
                )
    if analysis.get("unassigned_items"):
        lines.append(f"Unassigned items: {len(analysis['unassigned_items'])}")
    if analysis.get("warnings"):
        lines.append("Warnings: " + ", ".join(analysis["warnings"]))
    return "\n".join(lines)


def format_csv(analyses: list[dict[str, Any]]) -> str:
    fields = [
        "step_sequence_schema_version",
        "run_id",
        "run_wall_s",
        "run_wall_source",
        "run_start_wall_ns",
        "run_start_mono_ns",
        "run_end_mono_ns",
        "source_host",
        "host_boot_id",
        "clock_domain_id",
        "step_index",
        "step_id",
        "step_type",
        "step_start_mono_ns",
        "step_end_mono_ns",
        "step_start_offset_from_run_s",
        "step_end_offset_from_run_s",
        "step_semantic_phase",
        "step_action_count",
        "path_expansion_count",
        "path_expansion_observed",
        "format_retry_observed",
        "step_messages_before",
        "step_messages_after",
        "step_messages_added",
        "agent_path_depth_before",
        "response_id",
        "api_request_id",
        "model_call_id",
        "client_request_id",
        "server_request_id",
        "engine_request_id",
        "alignment_status",
        "alignment_quality",
        "p2_match_status",
        "p2_match_method",
        "p2_match_confidence",
        "p2_missing_reason",
        "item_index",
        "item_type",
        "span_id",
        "status",
        "item_start_mono_ns",
        "item_end_mono_ns",
        "item_start_offset_from_step_s",
        "item_start_offset_from_run_s",
        "item_end_offset_from_run_s",
        "start_offset_s",
        "duration_s",
        "step_duration_s",
        "p1_model_span_duration_s",
        "model_call_e2e_s",
        "tool_call_duration_s",
        "model_name",
        "context_message_count",
        "context_message_delta",
        "context_input_chars",
        "context_input_chars_delta",
        "context_token_estimate",
        "context_token_estimate_delta",
        "context_token_estimate_quality",
        "context_prompt_tokens",
        "context_prompt_tokens_delta",
        "context_change_type",
        "context_truncation_detected",
        "context_rewrite_detected",
        "context_summarization_detected",
        "context_removed_message_count",
        "context_compression_status",
        "context_compression_trigger_strategy",
        "context_compression_trigger_reason",
        "context_compression_strategy",
        "context_compression_applied",
        "context_compression_original_tokens",
        "context_compression_compressed_tokens",
        "context_compression_removed_tokens",
        "context_compression_target_tokens",
        "context_compression_target_achieved",
        "context_compression_measurement_quality",
        "tool_result_message_count",
        "tool_result_chars",
        "tool_result_tokens_estimated",
        "tool_result_token_quality",
        "agent_semantic_phase",
        "model_request_sequence",
        "model_call_index_in_step",
        "transport_attempt_count",
        "retry_count",
        "retry_observation_quality",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "p2_e2e_s",
        "p2_queue_s",
        "p2_ttft_s",
        "p2_engine_ttft_s",
        "p2_prefill_s",
        "p2_prefill_to_first_token_s",
        "p2_decode_s",
        "p2_first_output_s",
        "p2_exact_ttft_s",
        "p2_exact_prefill_s",
        "p2_exact_decode_s",
        "p2_exact_tpot_s",
        "p2_proxy_ttft_s",
        "p2_proxy_decode_s",
        "p2_queue_quality",
        "p2_ttft_quality",
        "p2_engine_ttft_quality",
        "p2_prefill_quality",
        "p2_prefill_boundary_source",
        "p2_prefill_boundary_quality",
        "p2_decode_quality",
        "p2_e2e_quality",
        "client_model_call_to_server_received_s",
        "server_finished_to_client_return_s",
        "client_observed_model_e2e_s",
        "client_non_server_overhead_s",
        "client_to_server_quality",
        "server_to_client_quality",
        "p2_correlation_quality",
        "p2_lifecycle_status",
        "p2_lifecycle_missing_events",
        "p2_agentmon_run_id",
        "p2_agentmon_model_request_id",
        "p2_finish_reason",
        "p2_finished_wall_time_s",
        "p2_prompt_tokens",
        "p2_cached_prompt_tokens",
        "p2_computed_prefill_tokens",
        "p2_recomputed_prompt_tokens",
        "p2_recomputed_token_quality",
        "p2_generation_tokens",
        "p2_cache_reuse_rate",
        "p2_recomputed_prompt_rate",
        "p2_scheduler_iteration_count",
        "p2_prefill_iteration_count",
        "p2_decode_iteration_count",
        "p2_chunked_prefill_iteration_count",
        "p2_mixed_batch_iteration_count",
        "p2_max_batch_request_count",
        "p2_total_scheduled_tokens",
        "p2_scheduled_prompt_tokens",
        "p2_scheduled_decode_tokens",
        "p2_queue_depth_on_arrival",
        "p2_running_requests_on_arrival",
        "p2_running_requests_after_schedule",
        "p2_waiting_requests_after_schedule",
        "p2_prompt_token_accounting_quality",
        "p2_preemption_count",
        "p2_kv_usage_before",
        "p2_kv_usage_after",
        "p2_kv_usage_delta",
        "p2_allocated_block_count",
        "p2_cached_block_count",
        "p2_cache_hit_block_count",
        "p2_freed_block_count",
        "p2_evicted_block_count",
        "p2_kv_quality",
        "p2_kv_lifecycle_event_count",
        "p2_kv_lifecycle_operation_counts",
        "p2_kv_residency_s",
        "p2_kv_residency_quality",
        "p2_prefix_cache_status",
        "p2_prefix_cache_hit_rate",
        "p2_kv_snapshot_usage_ratio",
        "p2_kv_pool_allocated_bytes_per_rank",
        "p2_kv_observation_scope",
        "p2_kv_request_attribution",
        "step_kv_cache_reuse_rate",
        "step_kv_usage_ratio_after_max",
        "step_kv_snapshot_usage_ratio",
        "step_kv_pool_allocated_bytes_per_rank",
        "step_agent_cpu_time_s",
        "step_agent_cpu_wall_ratio",
        "step_agent_peak_rss_bytes",
        "step_agent_peak_pss_bytes",
        "step_vllm_cpu_time_s",
        "step_vllm_cpu_wall_ratio",
        "step_vllm_peak_rss_bytes",
        "step_vllm_peak_pss_bytes",
        "step_scheduler_running_requests_max",
        "step_scheduler_running_requests_mean",
        "step_scheduler_waiting_requests_max",
        "step_scheduler_waiting_requests_mean",
        "step_scheduler_scheduled_tokens_max",
        "step_scheduler_batch_request_count_max",
        "step_scheduler_prefill_request_total",
        "step_scheduler_decode_request_total",
        "step_scheduler_mixed_iteration_count",
        "step_scheduler_continuous_batching_iteration_count",
        "step_scheduler_observation_scope",
        "step_target_peak_hbm_used_bytes",
        "step_target_avg_hbm_used_bytes",
        "step_target_max_aicore_util_pct",
        "step_target_avg_aicore_util_pct",
        "step_target_max_temperature_c",
        "step_target_avg_power_w",
        "step_target_min_aicore_frequency_mhz",
        "step_target_max_hbm_bandwidth_bytes_per_s",
        "step_npu_observation_scope",
        "step_npu_request_attribution",
        "step_npu_device_metrics_json",
        "step_host_peak_hbm_used_bytes",
        "step_host_avg_hbm_used_bytes",
        "step_host_max_aicore_util_pct",
        "step_host_avg_aicore_util_pct",
        "model_vllm_cpu_time_s",
        "model_vllm_peak_pss_bytes",
        "model_scheduler_running_requests_max",
        "model_scheduler_waiting_requests_max",
        "model_scheduler_scheduled_tokens_max",
        "model_scheduler_observation_scope",
        "model_target_peak_hbm_used_bytes",
        "model_target_max_aicore_util_pct",
        "model_target_max_temperature_c",
        "model_target_avg_power_w",
        "model_target_min_aicore_frequency_mhz",
        "model_target_max_hbm_bandwidth_bytes_per_s",
        "model_npu_observation_scope",
        "model_npu_request_attribution",
        "model_npu_device_metrics_json",
        "tool_cpu_time_s",
        "tool_cpu_user_time_s",
        "tool_cpu_system_time_s",
        "tool_cpu_time_quality",
        "tool_sampled_cpu_time_s",
        "tool_cpu_wall_ratio",
        "tool_peak_rss_bytes",
        "tool_peak_pss_bytes",
        "tool_resource_sample_count",
        "tool_resource_sample_interval_s",
        "tool_resource_quality",
        "tool_pss_quality",
        "tool_read_bytes_delta",
        "tool_write_bytes_delta",
        "tool_minor_faults_delta",
        "tool_major_faults_delta",
        "tool_input_blocks_delta",
        "tool_output_blocks_delta",
        "tool_voluntary_context_switches_delta",
        "tool_involuntary_context_switches_delta",
        "vllm_request_id",
        "tool_call_id",
        "tool_name",
        "command",
        "approx_tool_duration_s",
        "executable",
        "returncode",
        "tool_status",
        "timed_out",
        "timeout_limit_s",
        "termination_signal",
        "exception_type",
        "output_bytes",
        "stdout_bytes",
        "stderr_bytes",
        "command_hash",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for analysis in analyses:
        for step in analysis["steps"]:
            for item in step["items"]:
                p2 = item.get("p2") or {}
                kv_snapshot = as_dict(p2.get("kv_snapshot"))
                step_snapshot = as_dict(step.get("kv_snapshot"))
                step_agent_proc = as_dict(step.get("agent_proc"))
                step_vllm_proc = as_dict(step.get("vllm_proc"))
                step_scheduler = as_dict(step.get("scheduler"))
                step_npu = as_dict(step.get("npu"))
                model_vllm_proc = as_dict(item.get("vllm_proc"))
                model_scheduler = as_dict(item.get("scheduler"))
                model_npu = as_dict(item.get("npu"))
                tool_proc = as_dict(item.get("proc"))
                row = {
                    **item,
                    "step_sequence_schema_version": analysis.get("schema_version"),
                    "run_id": analysis["run_id"],
                    "run_wall_s": analysis.get("run_wall_s"),
                    "run_wall_source": analysis.get("run_wall_source"),
                    "run_start_wall_ns": analysis.get("run_start_wall_ns"),
                    "run_start_mono_ns": analysis.get("run_start_mono_ns"),
                    "run_end_mono_ns": analysis.get("run_end_mono_ns"),
                    "source_host": first_value(item.get("source_host"), step.get("source_host"), analysis.get("source_host")),
                    "host_boot_id": first_value(item.get("host_boot_id"), step.get("host_boot_id"), analysis.get("host_boot_id")),
                    "clock_domain_id": first_value(item.get("clock_domain_id"), step.get("clock_domain_id"), analysis.get("clock_domain_id")),
                    "step_index": step["step_index"],
                    "step_id": step["step_id"],
                    "step_type": step.get("step_type"),
                    "step_start_mono_ns": step.get("start_mono_ns"),
                    "step_end_mono_ns": step.get("end_mono_ns"),
                    "step_start_offset_from_run_s": step.get("step_start_offset_from_run_s"),
                    "step_end_offset_from_run_s": step.get("step_end_offset_from_run_s"),
                    "step_semantic_phase": step.get("step_semantic_phase"),
                    "step_action_count": step.get("step_action_count"),
                    "path_expansion_count": step.get("path_expansion_count"),
                    "path_expansion_observed": step.get("path_expansion_observed"),
                    "format_retry_observed": step.get("format_retry_observed"),
                    "step_messages_before": step.get("messages_before"),
                    "step_messages_after": step.get("messages_after"),
                    "step_messages_added": step.get("messages_added"),
                    "agent_path_depth_before": step.get("agent_path_depth_before"),
                    "response_id": first_value(item.get("response_id"), step.get("response_id")),
                    "api_request_id": first_value(item.get("api_request_id"), step.get("api_request_id"), p2.get("api_request_id")),
                    "model_call_id": first_value(
                        item.get("model_call_id"),
                        item.get("span_id") if item.get("item_type") == "model" else None,
                        p2.get("model_call_id"),
                    ),
                    "client_request_id": first_value(item.get("client_request_id"), p2.get("client_request_id")),
                    "server_request_id": first_value(item.get("server_request_id"), p2.get("server_request_id")),
                    "engine_request_id": first_value(item.get("engine_request_id"), p2.get("engine_request_id")),
                    "alignment_status": step.get("alignment_status"),
                    "alignment_quality": step.get("alignment_quality"),
                    "step_duration_s": step.get("duration_s"),
                    "p1_model_span_duration_s": item.get("duration_s") if item.get("item_type") == "model" and item.get("span_id") else None,
                    "model_call_e2e_s": p2.get("e2e_s") if item.get("item_type") == "model" else None,
                    "tool_call_duration_s": (
                        first_value(item.get("approx_tool_duration_s"), item.get("duration_s"))
                        if item.get("item_type") == "tool"
                        else None
                    ),
                    "p2_e2e_s": p2.get("e2e_s"),
                    "p2_queue_s": p2.get("queue_s"),
                    "p2_ttft_s": p2.get("ttft_s"),
                    "p2_engine_ttft_s": p2.get("engine_ttft_s"),
                    "p2_prefill_s": p2.get("prefill_s"),
                    "p2_prefill_to_first_token_s": p2.get("prefill_to_first_token_s"),
                    "p2_decode_s": p2.get("decode_s"),
                    "p2_first_output_s": p2.get("first_output_s"),
                    "p2_exact_ttft_s": p2.get("exact_ttft_s"),
                    "p2_exact_prefill_s": p2.get("exact_prefill_s"),
                    "p2_exact_decode_s": p2.get("exact_decode_s"),
                    "p2_exact_tpot_s": p2.get("exact_tpot_s"),
                    "p2_proxy_ttft_s": p2.get("proxy_ttft_s"),
                    "p2_proxy_decode_s": p2.get("proxy_decode_s"),
                    "p2_queue_quality": p2.get("queue_quality"),
                    "p2_ttft_quality": p2.get("ttft_quality"),
                    "p2_engine_ttft_quality": p2.get("engine_ttft_quality"),
                    "p2_prefill_quality": p2.get("prefill_quality"),
                    "p2_prefill_boundary_source": p2.get("prefill_boundary_source"),
                    "p2_prefill_boundary_quality": p2.get("prefill_boundary_quality"),
                    "p2_decode_quality": p2.get("decode_quality"),
                    "p2_e2e_quality": p2.get("e2e_quality"),
                    "client_model_call_to_server_received_s": p2.get(
                        "client_model_call_to_server_received_s"
                    ),
                    "server_finished_to_client_return_s": p2.get(
                        "server_finished_to_client_return_s"
                    ),
                    "client_observed_model_e2e_s": p2.get("client_observed_e2e_s"),
                    "client_non_server_overhead_s": p2.get("client_non_server_overhead_s"),
                    "client_to_server_quality": p2.get("client_to_server_quality"),
                    "server_to_client_quality": p2.get("server_to_client_quality"),
                    "p2_correlation_quality": p2.get("correlation_quality"),
                    "p2_lifecycle_status": p2.get("lifecycle_status"),
                    "p2_lifecycle_missing_events": json.dumps(p2.get("lifecycle_missing_events") or [], separators=(",", ":")),
                    "p2_agentmon_run_id": p2.get("agentmon_run_id"),
                    "p2_agentmon_model_request_id": p2.get("agentmon_model_request_id"),
                    "p2_finish_reason": p2.get("finish_reason"),
                    "p2_finished_wall_time_s": p2.get("finished_wall_time_s"),
                    "p2_prompt_tokens": p2.get("prompt_tokens"),
                    "p2_cached_prompt_tokens": p2.get("cached_prompt_tokens"),
                    "p2_computed_prefill_tokens": p2.get("computed_prefill_tokens"),
                    "p2_recomputed_prompt_tokens": p2.get("recomputed_prompt_tokens"),
                    "p2_recomputed_token_quality": p2.get("recomputed_token_quality"),
                    "p2_generation_tokens": p2.get("generation_tokens"),
                    "p2_cache_reuse_rate": p2.get("cache_reuse_rate"),
                    "p2_recomputed_prompt_rate": p2.get("recomputed_prompt_rate"),
                    "p2_scheduler_iteration_count": p2.get("scheduler_iteration_count"),
                    "p2_prefill_iteration_count": p2.get("prefill_iteration_count"),
                    "p2_decode_iteration_count": p2.get("decode_iteration_count"),
                    "p2_chunked_prefill_iteration_count": p2.get("chunked_prefill_iteration_count"),
                    "p2_mixed_batch_iteration_count": p2.get("mixed_batch_iteration_count"),
                    "p2_max_batch_request_count": p2.get("max_batch_request_count"),
                    "p2_total_scheduled_tokens": p2.get("total_scheduled_tokens"),
                    "p2_scheduled_prompt_tokens": p2.get("scheduled_prompt_tokens"),
                    "p2_scheduled_decode_tokens": p2.get("scheduled_decode_tokens"),
                    "p2_queue_depth_on_arrival": p2.get("queue_depth_on_arrival"),
                    "p2_running_requests_on_arrival": p2.get("running_requests_on_arrival"),
                    "p2_running_requests_after_schedule": p2.get("running_requests_after_schedule"),
                    "p2_waiting_requests_after_schedule": p2.get("waiting_requests_after_schedule"),
                    "p2_prompt_token_accounting_quality": p2.get("prompt_token_accounting_quality"),
                    "p2_preemption_count": p2.get("preemption_count"),
                    "p2_kv_usage_before": p2.get("kv_usage_before"),
                    "p2_kv_usage_after": p2.get("kv_usage_after"),
                    "p2_kv_usage_delta": p2.get("kv_usage_delta"),
                    "p2_allocated_block_count": p2.get("allocated_block_count"),
                    "p2_cached_block_count": p2.get("cached_block_count"),
                    "p2_cache_hit_block_count": p2.get("cache_hit_block_count"),
                    "p2_freed_block_count": p2.get("freed_block_count"),
                    "p2_evicted_block_count": p2.get("evicted_block_count"),
                    "p2_kv_quality": p2.get("kv_quality"),
                    "p2_kv_lifecycle_event_count": p2.get("kv_lifecycle_event_count"),
                    "p2_kv_lifecycle_operation_counts": json.dumps(
                        p2.get("kv_lifecycle_operation_counts") or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "p2_kv_residency_s": p2.get("kv_residency_s"),
                    "p2_kv_residency_quality": p2.get("kv_residency_quality"),
                    "p2_prefix_cache_status": kv_snapshot.get("prefix_cache_status"),
                    "p2_prefix_cache_hit_rate": kv_snapshot.get("prefix_cache_hit_rate"),
                    "p2_kv_snapshot_usage_ratio": kv_snapshot.get("kv_usage_ratio"),
                    "p2_kv_pool_allocated_bytes_per_rank": kv_snapshot.get("kv_pool_allocated_bytes_per_rank"),
                    "p2_kv_observation_scope": kv_snapshot.get("observation_scope"),
                    "p2_kv_request_attribution": kv_snapshot.get("request_attribution"),
                    "step_kv_cache_reuse_rate": step.get("kv_cache_reuse_rate"),
                    "step_kv_usage_ratio_after_max": step.get("kv_usage_ratio_after_max"),
                    "step_kv_snapshot_usage_ratio": step_snapshot.get("kv_usage_ratio"),
                    "step_kv_pool_allocated_bytes_per_rank": step_snapshot.get("kv_pool_allocated_bytes_per_rank"),
                    "step_agent_cpu_time_s": step_agent_proc.get("cpu_time_s"),
                    "step_agent_cpu_wall_ratio": step_agent_proc.get("cpu_wall_ratio"),
                    "step_agent_peak_rss_bytes": step_agent_proc.get("peak_rss_bytes"),
                    "step_agent_peak_pss_bytes": step_agent_proc.get("peak_pss_bytes"),
                    "step_vllm_cpu_time_s": step_vllm_proc.get("cpu_time_s"),
                    "step_vllm_cpu_wall_ratio": step_vllm_proc.get("cpu_wall_ratio"),
                    "step_vllm_peak_rss_bytes": step_vllm_proc.get("peak_rss_bytes"),
                    "step_vllm_peak_pss_bytes": step_vllm_proc.get("peak_pss_bytes"),
                    "step_scheduler_running_requests_max": step_scheduler.get("running_requests_max"),
                    "step_scheduler_running_requests_mean": step_scheduler.get("running_requests_mean"),
                    "step_scheduler_waiting_requests_max": step_scheduler.get("waiting_requests_max"),
                    "step_scheduler_waiting_requests_mean": step_scheduler.get("waiting_requests_mean"),
                    "step_scheduler_scheduled_tokens_max": step_scheduler.get("scheduled_tokens_max"),
                    "step_scheduler_batch_request_count_max": step_scheduler.get("batch_request_count_max"),
                    "step_scheduler_prefill_request_total": step_scheduler.get("prefill_request_total"),
                    "step_scheduler_decode_request_total": step_scheduler.get("decode_request_total"),
                    "step_scheduler_mixed_iteration_count": step_scheduler.get(
                        "mixed_prefill_decode_iteration_count"
                    ),
                    "step_scheduler_continuous_batching_iteration_count": step_scheduler.get(
                        "continuous_batching_iteration_count"
                    ),
                    "step_scheduler_observation_scope": step_scheduler.get("observation_scope"),
                    "step_target_peak_hbm_used_bytes": step_npu.get("target_peak_hbm_used_bytes"),
                    "step_target_avg_hbm_used_bytes": step_npu.get("target_avg_hbm_used_bytes"),
                    "step_target_max_aicore_util_pct": step_npu.get("target_max_aicore_util_pct"),
                    "step_target_avg_aicore_util_pct": step_npu.get("target_avg_aicore_util_pct"),
                    "step_target_max_temperature_c": step_npu.get("target_max_temperature_c"),
                    "step_target_avg_power_w": step_npu.get("target_avg_power_w"),
                    "step_target_min_aicore_frequency_mhz": step_npu.get("target_min_aicore_frequency_mhz"),
                    "step_target_max_hbm_bandwidth_bytes_per_s": step_npu.get("target_max_hbm_bandwidth_bytes_per_s"),
                    "step_npu_observation_scope": step_npu.get("observation_scope"),
                    "step_npu_request_attribution": step_npu.get("request_attribution"),
                    "step_npu_device_metrics_json": json.dumps(step_npu.get("device_metrics") or {}, sort_keys=True, separators=(",", ":")),
                    "step_host_peak_hbm_used_bytes": step_npu.get("host_peak_hbm_used_bytes"),
                    "step_host_avg_hbm_used_bytes": step_npu.get("host_avg_hbm_used_bytes"),
                    "step_host_max_aicore_util_pct": step_npu.get("host_max_aicore_util_pct"),
                    "step_host_avg_aicore_util_pct": step_npu.get("host_avg_aicore_util_pct"),
                    "model_vllm_cpu_time_s": model_vllm_proc.get("cpu_time_s"),
                    "model_vllm_peak_pss_bytes": model_vllm_proc.get("peak_pss_bytes"),
                    "model_scheduler_running_requests_max": model_scheduler.get("running_requests_max"),
                    "model_scheduler_waiting_requests_max": model_scheduler.get("waiting_requests_max"),
                    "model_scheduler_scheduled_tokens_max": model_scheduler.get("scheduled_tokens_max"),
                    "model_scheduler_observation_scope": model_scheduler.get("observation_scope"),
                    "model_target_peak_hbm_used_bytes": model_npu.get("target_peak_hbm_used_bytes"),
                    "model_target_max_aicore_util_pct": model_npu.get("target_max_aicore_util_pct"),
                    "model_target_max_temperature_c": model_npu.get("target_max_temperature_c"),
                    "model_target_avg_power_w": model_npu.get("target_avg_power_w"),
                    "model_target_min_aicore_frequency_mhz": model_npu.get("target_min_aicore_frequency_mhz"),
                    "model_target_max_hbm_bandwidth_bytes_per_s": model_npu.get("target_max_hbm_bandwidth_bytes_per_s"),
                    "model_npu_observation_scope": model_npu.get("observation_scope"),
                    "model_npu_request_attribution": model_npu.get("request_attribution"),
                    "model_npu_device_metrics_json": json.dumps(model_npu.get("device_metrics") or {}, sort_keys=True, separators=(",", ":")),
                    "tool_cpu_time_s": tool_proc.get("cpu_time_s"),
                    "tool_cpu_user_time_s": tool_proc.get("cpu_user_time_s"),
                    "tool_cpu_system_time_s": tool_proc.get("cpu_system_time_s"),
                    "tool_cpu_time_quality": tool_proc.get("cpu_time_quality"),
                    "tool_sampled_cpu_time_s": tool_proc.get("sampled_cpu_time_s"),
                    "tool_cpu_wall_ratio": tool_proc.get("cpu_wall_ratio"),
                    "tool_peak_rss_bytes": tool_proc.get("peak_rss_bytes"),
                    "tool_peak_pss_bytes": tool_proc.get("peak_pss_bytes"),
                    "tool_resource_sample_count": tool_proc.get("tool_resource_sample_count"),
                    "tool_resource_sample_interval_s": tool_proc.get("tool_resource_sample_interval_s"),
                    "tool_resource_quality": tool_proc.get("tool_resource_quality"),
                    "tool_pss_quality": tool_proc.get("tool_pss_quality"),
                    "tool_read_bytes_delta": tool_proc.get("read_bytes_delta"),
                    "tool_write_bytes_delta": tool_proc.get("write_bytes_delta"),
                    "tool_minor_faults_delta": tool_proc.get("minor_faults_delta"),
                    "tool_major_faults_delta": tool_proc.get("major_faults_delta"),
                    "tool_input_blocks_delta": tool_proc.get("input_blocks_delta"),
                    "tool_output_blocks_delta": tool_proc.get("output_blocks_delta"),
                    "tool_voluntary_context_switches_delta": tool_proc.get(
                        "voluntary_context_switches_delta"
                    ),
                    "tool_involuntary_context_switches_delta": tool_proc.get(
                        "involuntary_context_switches_delta"
                    ),
                    "vllm_request_id": p2.get("vllm_request_id"),
                }
                writer.writerow(row)
    return output.getvalue().rstrip("\n")
