from __future__ import annotations

from typing import Any

from agentmon.batch.constants import BATCH_SCHEMA_VERSION
from agentmon.batch.metrics import (
    aggregate_optional_metric,
    count_result_values,
    number,
    optional_number,
    ratio,
    steady_state_completion_rate,
    throughput_metric,
)


INVALID_EXECUTION_STATUSES = {
    "agentmon_run_id_missing",
    "runtime_source_integrity_violation",
    "worker_error",
    "workspace_policy_rejected",
}


def build_batch_completion(
    results: list[dict[str, Any]],
    *,
    expected_task_count: int | None,
    runtime_source_integrity_status: str,
    analysis_complete: bool,
) -> dict[str, Any]:
    expected = len(results) if expected_task_count is None else expected_task_count
    result_table_complete = len(results) == expected
    run_id_missing = sum(
        row.get("status") == "agentmon_run_id_missing"
        or (row.get("run_id_required") is True and not row.get("run_id"))
        for row in results
    )
    workspace_policy_failures = sum(
        row.get("workspace_policy_status") == "FAIL"
        or row.get("status") == "workspace_policy_rejected"
        for row in results
    )
    controller_failures = sum(row.get("status") in INVALID_EXECUTION_STATUSES for row in results)
    terminal_count = sum(row.get("status") not in {None, "", "running"} for row in results)
    workload_attempt_complete = result_table_complete and terminal_count == len(results)

    invalid_reasons: list[str] = []
    unresolved_reasons: list[str] = []
    if not result_table_complete:
        invalid_reasons.append("result_count_mismatch")
    if run_id_missing:
        invalid_reasons.append("agentmon_run_id_missing")
    if workspace_policy_failures:
        invalid_reasons.append("workspace_policy_failure")
    if controller_failures:
        invalid_reasons.append("workload_controller_failure")
    if runtime_source_integrity_status == "CHANGED":
        invalid_reasons.append("runtime_source_integrity_changed")
    elif runtime_source_integrity_status not in {"PASS", "NOT_APPLICABLE"}:
        unresolved_reasons.append("runtime_source_integrity_unresolved")

    if invalid_reasons:
        experiment_valid: bool | None = False
        validity_status = "INVALID"
    elif unresolved_reasons:
        experiment_valid = None
        validity_status = "UNRESOLVED"
    else:
        experiment_valid = True
        validity_status = "VALID"

    return {
        "schema_version": "1.0",
        "controller_finished": True,
        "expected_task_count": expected,
        "result_count": len(results),
        "result_table_complete": result_table_complete,
        "workload_terminal_count": terminal_count,
        "workload_attempt_complete": workload_attempt_complete,
        "agentmon_run_id_missing": run_id_missing,
        "workspace_policy_failures": workspace_policy_failures,
        "controller_failures": controller_failures,
        "runtime_source_integrity_status": runtime_source_integrity_status,
        "analysis_complete": analysis_complete,
        "experiment_valid": experiment_valid,
        "validity_status": validity_status,
        "invalid_reasons": sorted(set(invalid_reasons)),
        "unresolved_reasons": sorted(set(unresolved_reasons)),
        "data_quality": "COMPLETE" if experiment_valid is not None else "UNRESOLVED",
        "forbidden_conclusions": (
            []
            if experiment_valid is True
            else ["Do not use this batch for A/B, throughput, latency, or optimizer-effect conclusions."]
        ),
    }

def build_batch_summary(
    results: list[dict[str, Any]],
    *,
    batch_wall_s: float | None,
    phase_timings: dict[str, float] | None = None,
    configured_concurrency: int = 1,
    expected_task_count: int | None = None,
    runtime_source_integrity_status: str = "UNKNOWN",
    analysis_complete: bool = True,
) -> dict[str, Any]:
    prompt_metric = aggregate_optional_metric(results, "prompt_tokens")
    generation_metric = aggregate_optional_metric(results, "generation_tokens")
    model_time_metric = aggregate_optional_metric(results, "run_model_e2e_sum_s", alias="model_time_s")
    tool_time_metric = aggregate_optional_metric(results, "run_tool_duration_sum_s", alias="tool_time_s")
    total_generation_tokens = generation_metric["total"]
    total_prompt_tokens = prompt_metric["total"]
    total_model_requests = sum(number(row.get("model_request_count")) for row in results)
    total_tool_calls = sum(number(row.get("tool_call_count")) for row in results)
    total_model_e2e = model_time_metric["total"]
    total_tool_duration = tool_time_metric["total"]
    stage_counts = {
        "attempted": len(results),
        "execution_success": sum(row.get("execution_success") is True for row in results),
        "execution_failure": sum(row.get("execution_success") is False for row in results),
        "monitoring_success": sum(row.get("monitoring_success") is True for row in results),
        "monitoring_failure": sum(row.get("monitoring_success") is False for row in results),
        "trajectory_import_success": sum(row.get("trajectory_import_success") is True for row in results),
        "trajectory_import_failure": sum(row.get("trajectory_import_success") is False for row in results),
        "report_generation_success": sum(row.get("report_generation_success") is True for row in results),
        "report_generation_failure": sum(row.get("report_generation_success") is False for row in results),
        "report_publishable": sum(row.get("report_publishability_status") == "PUBLISHABLE" for row in results),
        "report_unpublishable": sum(row.get("report_publishability_status") == "UNPUBLISHABLE" for row in results),
        "p1_analysis_success": sum(row.get("p1_analysis_success") is True for row in results),
        "p1_analysis_failure": sum(row.get("p1_analysis_success") is False for row in results),
        "p1_native_complete": sum(row.get("p1_native_status") == "complete" for row in results),
        "p1_native_partial": sum(row.get("p1_native_status") == "partial" for row in results),
        "p1_native_missing": sum(row.get("p1_native_status") == "missing" for row in results),
        "p2_import_success": sum(row.get("p2_import_success") is True for row in results),
        "p2_import_failure": sum(row.get("p2_import_success") is False for row in results),
        "p2_not_configured": sum(row.get("p2_import_success") is None for row in results),
        "p2_analysis_success": sum(row.get("p2_analysis_success") is True for row in results),
        "p2_analysis_failure": sum(row.get("p2_analysis_success") is False for row in results),
        "judge_success": sum(row.get("judge_success") is True for row in results),
        "judge_failure": sum(row.get("judge_success") is False for row in results),
        "judge_not_configured": sum(row.get("judge_success") is None for row in results),
        "pipeline_success": sum(row.get("pipeline_success") is True for row in results),
        "pipeline_failure": sum(row.get("pipeline_success") is False for row in results),
        "analysis_success": sum(row.get("analysis_pipeline_success") is True for row in results),
        "analysis_failure": sum(row.get("analysis_pipeline_success") is False for row in results),
        "usable_run": sum(row.get("usable_run") is True for row in results),
        "overall_pipeline_success": sum(row.get("overall_pipeline_success") is True for row in results),
        "overall_pipeline_failure": sum(row.get("overall_pipeline_success") is False for row in results),
        "publishable_task": sum(row.get("publishability_status") == "PUBLISHABLE" for row in results),
    }
    timings = dict(phase_timings or {})
    pipeline_window_s = optional_number(timings.get("pipeline_wall_s")) or batch_wall_s
    execution_window_s = optional_number(timings.get("workload_phase_s")) or batch_wall_s
    successful_work_s = sum(
        optional_number(row.get("workload_wall_s")) or 0.0
        for row in results
        if row.get("execution_success") is True
    )
    packed_workers = max(1, min(configured_concurrency, stage_counts["execution_success"] or 1))
    counterfactual_window_s = successful_work_s / packed_workers if successful_work_s > 0 else None
    steady_state = steady_state_completion_rate(results)
    throughput_definitions = {
        "workload_task_throughput": throughput_metric(
            len(results), execution_window_s, "attempted_runs / workload_phase_s", "workload_phase", "observed"
        ),
        "pipeline_task_throughput": throughput_metric(
            len(results), pipeline_window_s, "attempted_runs / pipeline_wall_s", "full_pipeline", "observed"
        ),
        "successful_workload_task_throughput": throughput_metric(
            stage_counts["execution_success"],
            execution_window_s,
            "execution_success_runs / workload_phase_s",
            "workload_phase",
            "observed",
        ),
        "publishable_pipeline_task_throughput": throughput_metric(
            stage_counts["publishable_task"],
            pipeline_window_s,
            "publishable_runs / pipeline_wall_s",
            "full_pipeline",
            "observed",
        ),
        "pipeline_attempt_throughput": throughput_metric(
            len(results), pipeline_window_s, "attempted_runs / pipeline_wall_s", "full_pipeline", "observed"
        ),
        "execution_attempt_throughput": throughput_metric(
            len(results), execution_window_s, "attempted_runs / workload_phase_s", "workload_phase", "observed"
        ),
        "observed_success_throughput": throughput_metric(
            stage_counts["execution_success"],
            execution_window_s,
            "execution_success_runs / workload_phase_s",
            "workload_phase",
            "observed",
        ),
        "counterfactual_success_only_throughput": throughput_metric(
            stage_counts["execution_success"],
            counterfactual_window_s,
            "execution_success_runs / (sum_success_workload_s / effective_concurrency)",
            "perfectly_packed_successful_work",
            "counterfactual",
            confidence="estimated",
        ),
        "steady_state_completion_rate": steady_state,
    }
    completion = build_batch_completion(
        results,
        expected_task_count=expected_task_count,
        runtime_source_integrity_status=runtime_source_integrity_status,
        analysis_complete=analysis_complete,
    )
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "summary_type": "agentmon.batch_summary",
        "run_count": len(results),
        "batch_wall_s": batch_wall_s,
        "phase_timings": timings,
        "configured_concurrency": configured_concurrency,
        "completion": completion,
        "experiment_valid": completion["experiment_valid"],
        "validity_status": completion["validity_status"],
        "stage_counts": stage_counts,
        "status_counts": count_result_values(results, "status"),
        "p1_status_counts": count_result_values(results, "p1_native_status"),
        "p2_import_mode_counts": count_result_values(results, "p2_import_mode"),
        "totals": {
            "model_requests": total_model_requests,
            "tool_calls": total_tool_calls,
            "prompt_tokens": total_prompt_tokens,
            "generation_tokens": total_generation_tokens,
            "run_model_e2e_sum_s": total_model_e2e,
            "run_tool_duration_sum_s": total_tool_duration,
            "p2_requests": sum(number(row.get("p2_request_count")) for row in results),
            "p2_request_events": sum(number(row.get("p2_request_event_count")) for row in results),
            "p2_global_events_copied": sum(number(row.get("p2_global_event_count")) for row in results),
            "p2_sample_events_copied": sum(number(row.get("p2_sample_event_count")) for row in results),
            "unmatched_trajectory_ids": sum(
                number(row.get("unmatched_trajectory_id_count")) for row in results
            ),
            "multiple_p2_api_request_ids": sum(
                number(row.get("multiple_p2_api_request_id_count")) for row in results
            ),
            "p2_requests_in_task_windows_not_in_trajectory": sum(
                number(row.get("p2_requests_in_task_window_not_in_trajectory_count")) for row in results
            ),
            "trajectory_tool_calls": sum(number(row.get("trajectory_tool_call_count")) for row in results),
            "p1_tool_spans": sum(number(row.get("p1_tool_span_count")) for row in results),
            "tool_resource_complete": sum(number(row.get("tool_resource_complete_count")) for row in results),
            "analyzer_tool_rows": sum(number(row.get("analyzer_tool_row_count")) for row in results),
        },
        "metric_coverage": {
            "prompt_tokens": prompt_metric,
            "generation_tokens": generation_metric,
            "run_model_e2e_sum_s": model_time_metric,
            "run_tool_duration_sum_s": tool_time_metric,
        },
        "throughput": {
            "tasks_per_second": ratio(len(results), batch_wall_s),
            "workload_task_throughput": throughput_definitions["workload_task_throughput"]["value_per_second"],
            "pipeline_task_throughput": throughput_definitions["pipeline_task_throughput"]["value_per_second"],
            "successful_workload_task_throughput": throughput_definitions["successful_workload_task_throughput"]["value_per_second"],
            "publishable_pipeline_task_throughput": throughput_definitions["publishable_pipeline_task_throughput"]["value_per_second"],
            "successful_tasks_per_hour": ratio(stage_counts["execution_success"] * 3600.0, batch_wall_s),
            "model_requests_per_second": ratio(total_model_requests, batch_wall_s),
            "prompt_tokens_per_second": ratio(total_prompt_tokens, batch_wall_s),
            "generation_tokens_per_second": ratio(total_generation_tokens, batch_wall_s),
            "standard_definitions": throughput_definitions,
        },
        "publishability_status_counts": count_result_values(results, "publishability_status"),
    }


def restore_execution_measurements(
    summary: dict[str, Any], manifest: dict[str, Any], original: dict[str, Any],
) -> None:
    """Analysis can enrich results, but cannot replace the execution clock."""
    for key in (
        "batch_wall_s", "configured_concurrency", "phase_timings", "phase_timestamps",
        "phase_resource_profiles", "pipeline_phase_validation", "npu_process_mapping",
    ):
        summary[key] = original.get(key)
        manifest[key] = original.get(key)
    for key in ("start_wall_ns", "start_mono_ns", "end_wall_ns", "end_mono_ns"):
        if key in original:
            manifest[key] = original[key]
        else:
            manifest.pop(key, None)
