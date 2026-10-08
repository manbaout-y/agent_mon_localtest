from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from agentmon.batch.io import as_dict, make_private_dir, write_json_private
from agentmon.batch.metrics import optional_number

def read_step_totals(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    totals = as_dict(as_dict(payload).get("totals"))
    return {
        "step_count": totals.get("step_count", 0),
        "model_request_count": totals.get("model_request_count", 0),
        "tool_call_count": totals.get("tool_call_count", 0),
        "model_time_s": totals.get("model_time_s", 0.0),
        "tool_time_s": totals.get("tool_time_s", 0.0),
        "run_model_e2e_sum_s": totals.get("model_time_s", 0.0),
        "run_tool_duration_sum_s": totals.get("tool_time_s", 0.0),
        "prompt_tokens": totals.get("prompt_tokens", 0),
        "generation_tokens": totals.get("completion_tokens", 0),
        "p2_expected_model_request_count": totals.get("p2_expected_model_request_count", 0),
        "p2_matched_model_request_count": totals.get("p2_matched_model_request_count", 0),
        "p2_match_rate": totals.get("p2_match_rate"),
        "p2_lifecycle_complete_count": totals.get("p2_lifecycle_complete_count", 0),
        "p2_lifecycle_complete_rate": totals.get("p2_lifecycle_complete_rate"),
        "trajectory_tool_call_count": totals.get("trajectory_tool_call_count", 0),
        "p1_tool_span_count": totals.get("p1_tool_span_count", 0),
        "tool_resource_complete_count": totals.get("tool_resource_complete_count", 0),
        "analyzer_tool_row_count": totals.get("analyzer_tool_row_count", totals.get("tool_call_count", 0)),
    }


def read_report_validation(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "validation_checks.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        return {
            "publishability_status": "UNPUBLISHABLE",
            "blocking_failures": ["missing_or_invalid_run_validation"],
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "publishability_status": "UNPUBLISHABLE",
            "blocking_failures": ["invalid_run_validation_shape"],
        }
    return payload


def build_task_validation(result: dict[str, Any]) -> dict[str, Any]:
    p2_configured = bool(result.get("p2_source_path"))
    checks = [
        validation_check("workload_execution", result.get("execution_success") is True, blocking=False),
        validation_check("report_generation", result.get("report_generation_success") is True),
        validation_check(
            "run_report_publishable",
            result.get("report_publishability_status") == "PUBLISHABLE",
            details={"blocking_failures": result.get("report_blocking_failures") or []},
        ),
        validation_check("step_analysis", result.get("p1_analysis_success") is True),
        validation_check("trajectory_import", result.get("trajectory_import_success") is True),
        validation_check(
            "p2_import",
            not p2_configured or result.get("p2_import_success") is True,
            details={"configured": p2_configured},
        ),
        validation_check(
            "p2_request_match_rate",
            not p2_configured or result.get("p2_analysis_success") is True,
            details={
                "match_rate": result.get("p2_match_rate"),
                "lifecycle_complete_rate": result.get("p2_lifecycle_complete_rate"),
            },
        ),
    ]
    blocking_failures = [check["name"] for check in checks if check["blocking"] and not check["passed"]]
    return {
        "schema_version": "1.0",
        "validation_type": "agentmon.run_analysis",
        "run_id": result.get("run_id"),
        "task_id": result.get("task_id"),
        "publishability_status": "PUBLISHABLE" if not blocking_failures else "UNPUBLISHABLE",
        "blocking_failures": blocking_failures,
        "checks": checks,
    }


def validation_check(
    name: str,
    passed: bool,
    *,
    blocking: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "blocking": blocking,
        "details": details or {},
    }


def write_task_validation(result: dict[str, Any], path: Path) -> dict[str, Any]:
    validation = build_task_validation(result)
    write_json_private(path, validation)
    return validation


def write_analysis_quality_bundle(result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    validation = build_task_validation(result)
    write_json_private(output_dir / "validation_checks.json", validation)
    write_json_private(
        output_dir / "metric_definition.json",
        {
            "schema_version": "1.0",
            "time": {
                "workload_wall_s": "monotonic workload process envelope",
                "run_model_e2e_sum_s": "sum of matched per-call P2 E2E, with P1 span fallback where declared",
                "run_tool_duration_sum_s": "sum of analyzer tool-call durations",
                "p2_prefill_s": "explicit post-prefill boundary only; null when unavailable",
                "p2_prefill_to_first_token_s": "first-scheduled to first-token proxy, not pure prefill",
            },
            "counts": {
                "trajectory_tool_call_count": "tool calls represented by mini-SWE trajectory",
                "p1_tool_span_count": "native P1 tool spans",
                "tool_resource_complete_count": "tool rows with CPU, RSS and PSS observations",
                "analyzer_tool_row_count": "tool rows emitted by step analyzer",
            },
            "attribution": {
                "npu": "shared_service_observation unless explicitly marked exact",
                "p2": "request-id correlated; window-only extras remain diagnostics",
            },
        },
    )
    write_json_private(
        output_dir / "data_quality.json",
        {
            "schema_version": "1.0",
            "status": validation["publishability_status"],
            "p1_native_status": result.get("p1_native_status"),
            "p2_match_rate": result.get("p2_match_rate"),
            "p2_lifecycle_complete_rate": result.get("p2_lifecycle_complete_rate"),
            "trajectory_import_success": result.get("trajectory_import_success"),
            "report_generation_success": result.get("report_generation_success"),
            "report_publishability_status": result.get("report_publishability_status"),
            "report_blocking_failures": result.get("report_blocking_failures") or [],
            "blocking_failures": validation["blocking_failures"],
        },
    )
    p2_quality = "exact_request_id" if result.get("p2_match_rate") == 1.0 else (
        "partial" if result.get("p2_match_rate") is not None else "unavailable"
    )
    write_json_private(
        output_dir / "confidence.json",
        {
            "schema_version": "1.0",
            "p2_request_attribution": p2_quality,
            "p1_event_quality": result.get("p1_native_status"),
            "npu_attribution": "shared_service_observation_not_request_attributed",
            "judge": "exact" if result.get("judge_success") is not None else "not_configured",
        },
    )
    limitations: list[dict[str, str]] = []
    if result.get("p1_native_status") != "complete":
        limitations.append({"code": "p1_native_incomplete", "message": "Some steps may use trajectory reconstruction."})
    if result.get("p2_source_path") and result.get("p2_match_rate") != 1.0:
        limitations.append({"code": "p2_partial_match", "message": "Not every expected model request has exact P2 data."})
    if result.get("report_publishability_status") != "PUBLISHABLE":
        limitations.append(
            {
                "code": "run_report_unpublishable",
                "message": "Run-level event/span integrity checks failed; deterministic conclusions must not be published.",
            }
        )
    limitations.append(
        {
            "code": "shared_npu_observation",
            "message": "NPU/HBM windows describe the shared vLLM service and are not request-exclusive attribution.",
        }
    )
    if result.get("judge_success") is None:
        limitations.append({"code": "judge_not_configured", "message": "Execution success is not benchmark correctness."})
    write_json_private(
        output_dir / "limitations.json",
        {"schema_version": "1.0", "limitations": limitations},
    )
    return validation


def write_results_tsv(path: Path, results: list[dict[str, Any]]) -> None:
    make_private_dir(path.parent)
    fields = [
        "task_id",
        "status",
        "pipeline_success",
        "analysis_pipeline_success",
        "analysis_deferred",
        "analysis_deferred_reason",
        "overall_pipeline_success",
        "usable_run",
        "publishability_status",
        "execution_success",
        "monitoring_success",
        "report_generation_success",
        "report_publishability_status",
        "report_blocking_failures",
        "p1_analysis_success",
        "trajectory_import_success",
        "p1_native_status",
        "p1_injection_status",
        "p2_import_success",
        "p2_analysis_success",
        "judge_success",
        "judge_status",
        "agentmon_exit_code",
        "run_id",
        "run_dir",
        "workspace",
        "cwd",
        "workspace_policy",
        "workspace_policy_status",
        "workspace_contract_path",
        "run_id_required",
        "runtime_source_integrity_status",
        "source_integrity_violation",
        "trajectory_path",
        "trajectory_response_id_count",
        "matched_p2_request_count",
        "multiple_p2_api_request_id_count",
        "multiple_p2_api_request_ids",
        "unmatched_trajectory_id_count",
        "p2_requests_in_task_window_not_in_trajectory_count",
        "p2_source_path",
        "p2_import_mode",
        "p2_imported_event_count",
        "p2_request_count",
        "p2_request_event_count",
        "p2_request_keyed_event_count",
        "p2_global_event_count",
        "p2_sample_event_count",
        "p2_total_event_count",
        "p2_query_wall_s",
        "p2_bytes_scanned_per_run",
        "p2_events_scanned_per_run",
        "p2_window_candidate_event_count",
        "p2_window_time_index_used",
        "p2_service_global_filter_mode",
        "p2_service_global_source_event_count",
        "p2_service_global_written_event_count",
        "p2_service_global_events_scanned",
        "p2_service_global_time_index_used",
        "p2_service_global_path",
        "p2_index_cache_hit",
        "p2_index_source",
        "p2_expected_model_request_count",
        "p2_matched_model_request_count",
        "p2_match_rate",
        "p2_lifecycle_complete_count",
        "p2_lifecycle_complete_rate",
        "step_count",
        "model_request_count",
        "tool_call_count",
        "model_time_s",
        "tool_time_s",
        "run_model_e2e_sum_s",
        "run_tool_duration_sum_s",
        "prompt_tokens",
        "generation_tokens",
        "trajectory_tool_call_count",
        "p1_tool_span_count",
        "tool_resource_complete_count",
        "analyzer_tool_row_count",
        "workload_start_wall_ns",
        "workload_end_wall_ns",
        "workload_start_mono_ns",
        "workload_end_mono_ns",
        "workload_wall_s",
        "analysis_wall_s",
        "analysis_trajectory_import_s",
        "analysis_p2_import_s",
        "analysis_report_generation_s",
        "analysis_step_analysis_s",
        "analysis_judge_s",
        "analysis_pipeline_profile_path",
        "validation_checks_path",
        "step_sequence_json",
        "step_sequence_csv",
        "step_sequence_txt",
        "stdout_log",
        "stderr_log",
        "judge_stdout_log",
        "judge_stderr_log",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = dict(result)
            row["report_blocking_failures"] = json.dumps(
                result.get("report_blocking_failures") or [],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            row["multiple_p2_api_request_ids"] = json.dumps(
                result.get("multiple_p2_api_request_ids") or [],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            writer.writerow(row)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_results_tsv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle, delimiter="\t")]
    boolean_fields = {
        "pipeline_success",
        "analysis_pipeline_success",
        "analysis_deferred",
        "overall_pipeline_success",
        "usable_run",
        "execution_success",
        "run_id_required",
        "source_integrity_violation",
        "monitoring_success",
        "report_generation_success",
        "p1_analysis_success",
        "trajectory_import_success",
        "p2_import_success",
        "p2_analysis_success",
        "judge_success",
        "p2_index_cache_hit",
        "p2_window_time_index_used",
        "p2_service_global_time_index_used",
    }
    numeric_fields = {
        "agentmon_exit_code",
        "trajectory_response_id_count",
        "matched_p2_request_count",
        "multiple_p2_api_request_id_count",
        "unmatched_trajectory_id_count",
        "p2_requests_in_task_window_not_in_trajectory_count",
        "p2_imported_event_count",
        "p2_request_count",
        "p2_request_event_count",
        "p2_request_keyed_event_count",
        "p2_global_event_count",
        "p2_sample_event_count",
        "p2_total_event_count",
        "p2_query_wall_s",
        "p2_bytes_scanned_per_run",
        "p2_events_scanned_per_run",
        "p2_window_candidate_event_count",
        "p2_service_global_source_event_count",
        "p2_service_global_written_event_count",
        "p2_service_global_events_scanned",
        "p2_expected_model_request_count",
        "p2_matched_model_request_count",
        "p2_match_rate",
        "p2_lifecycle_complete_count",
        "p2_lifecycle_complete_rate",
        "step_count",
        "model_request_count",
        "tool_call_count",
        "model_time_s",
        "tool_time_s",
        "run_model_e2e_sum_s",
        "run_tool_duration_sum_s",
        "prompt_tokens",
        "generation_tokens",
        "trajectory_tool_call_count",
        "p1_tool_span_count",
        "tool_resource_complete_count",
        "analyzer_tool_row_count",
        "workload_start_wall_ns",
        "workload_end_wall_ns",
        "workload_start_mono_ns",
        "workload_end_mono_ns",
        "workload_wall_s",
        "analysis_wall_s",
        "analysis_trajectory_import_s",
        "analysis_p2_import_s",
        "analysis_report_generation_s",
        "analysis_step_analysis_s",
        "analysis_judge_s",
    }
    for row in rows:
        for field in boolean_fields:
            value = str(row.get(field) or "").strip().lower()
            row[field] = True if value in {"true", "1", "yes"} else False if value in {"false", "0", "no"} else None
        for field in numeric_fields:
            value = optional_number(row.get(field))
            if value is not None:
                row[field] = int(value) if field.endswith("_ns") or field == "agentmon_exit_code" else value
    return rows


def combine_step_csvs(results: list[dict[str, Any]], output: Path) -> Path | None:
    sources = [
        Path(str(row["step_sequence_csv"]))
        for row in results
        if row.get("step_sequence_csv") and Path(str(row["step_sequence_csv"])).is_file()
    ]
    if not sources:
        return None
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for source in sources:
        with source.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for field in reader.fieldnames or []:
                if field not in seen_fields:
                    seen_fields.add(field)
                    fieldnames.append(field)
            rows.extend(dict(row) for row in reader)
    make_private_dir(output.parent)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        output.chmod(0o600)
    except OSError:
        pass
    return output
