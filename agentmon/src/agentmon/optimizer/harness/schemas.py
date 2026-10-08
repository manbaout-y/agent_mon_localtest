from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
UNKNOWN = "UNKNOWN"
PARTIAL = "PARTIAL"
UNRESOLVED = "UNRESOLVED"
COMPLETE = "COMPLETE"

EVIDENCE_OUTPUT_FILES = {
    "optimizer_preflight": "optimizer_preflight.json",
    "experiment_manifest": "experiment_manifest.json",
    "evidence_graph": "evidence_graph.json",
    "critical_path": "critical_path.csv",
    "bottleneck_attribution": "bottleneck_attribution.json",
    "workload_normalized_metrics": "workload_normalized_metrics.csv",
    "data_quality": "data_quality.json",
}

REPLAY_OUTPUT_FILES = {
    "optimizer_decision_timeline": "optimizer_decision_timeline.csv",
    "optimizer_decisions": "optimizer_decisions.jsonl",
    "optimizer_strategy_summary": "optimizer_strategy_summary.json",
    "optimizer_risk_audit": "optimizer_risk_audit.json",
    "prediction_variable_validation": "prediction_variable_validation.json",
    "prediction_variable_validation_table": "prediction_variable_validation.csv",
    "optimizer_replay_report": "optimizer_replay_report.md",
}

EVALUATION_OUTPUT_FILES = {
    "mechanism_comparison": "mechanism_comparison.json",
    "ablation_table": "ablation_table.csv",
    "contribution_decomposition": "contribution_decomposition.csv",
    "conflict_audit": "conflict_audit.json",
    "evaluation_report": "evaluation_report.md",
}


def output_paths(output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir).expanduser().resolve()
    return {
        key: root / filename
        for key, filename in {
            **EVIDENCE_OUTPUT_FILES,
            **REPLAY_OUTPUT_FILES,
            **EVALUATION_OUTPUT_FILES,
        }.items()
    }


FORBIDDEN_CONCLUSIONS = [
    "Do not treat API first output as EngineCore first token unless deep engine evidence is present.",
    "Do not treat total prompt tokens as actual Prefill tokens.",
    "Do not infer exclusive resource ownership from request Batch membership.",
    "Do not infer causal optimizer benefit from advice replay alone.",
    "Do not include prompts, summaries, raw tool output, or full code diffs in harness outputs by default.",
]

DEFAULT_STEP_CSV_PROFILE = {
    "run_start_fields": (
        "workload_start_mono_ns",
        "run_start_mono_ns",
        "start_mono_ns",
    ),
    "run_end_fields": (
        "workload_end_mono_ns",
        "run_end_mono_ns",
        "end_mono_ns",
    ),
    "model_e2e_duration_fields": (
        "model_call_e2e_s",
        "p2_e2e_s",
        "p1_model_span_duration_s",
    ),
    "tool_duration_fields": (
        "tool_call_duration_s",
        "duration_s",
    ),
    "model_phase_duration_fields": {
        "model_queue": ("p2_queue_s",),
        "model_prefill": ("p2_exact_prefill_s", "p2_prefill_s"),
        "model_ttft": ("p2_exact_ttft_s", "p2_engine_ttft_s", "p2_ttft_s"),
        "model_decode": ("p2_exact_decode_s", "p2_decode_s"),
    },
    "scheduler_running_request_fields": (
        "model_scheduler_running_requests_max",
        "step_scheduler_running_requests_max",
        "scheduler_running_requests",
        "running_requests",
    ),
    "scheduler_waiting_request_fields": (
        "model_scheduler_waiting_requests_max",
        "step_scheduler_waiting_requests_max",
        "scheduler_waiting_requests",
        "waiting_requests",
    ),
    "kv_usage_ratio_fields": (
        "model_kv_usage_ratio",
        "step_kv_usage_ratio",
        "p2_kv_usage_ratio",
        "kv_usage_ratio",
    ),
    "actual_prefill_token_fields": (
        "p2_computed_prefill_tokens",
    ),
    "generation_token_fields": (
        "p2_generation_tokens",
    ),
    "request_ttft_fields": (
        "p2_exact_ttft_s",
        "p2_engine_ttft_s",
        "p2_ttft_s",
    ),
    "request_tpot_fields": (
        "p2_exact_tpot_s",
    ),
    "resource_utilization_fields": (
        "step_target_avg_aicore_util_pct",
        "step_target_max_aicore_util_pct",
    ),
    "resource_memory_fields": (
        "step_target_peak_hbm_used_bytes",
        "step_target_avg_hbm_used_bytes",
    ),
    "tool_timeout_fields": (
        "timed_out",
        "timeout",
    ),
    "tool_error_fields": (
        "returncode",
        "status",
        "exception_type",
    ),
}

DEFAULT_MECHANISM_PROFILE = {
    "fusion_policy": "max_pressure_advice",
    "pressure_order": {
        "none": 0,
        "moderate": 1,
        "high": 2,
    },
    "risk_flag_component_map": {
        "scheduler_capacity": "runtime_admission_capacity",
        "model_waiting_requests": "model_service_queue",
        "model_queue_latency": "model_service_queue",
        "model_kv_usage": "model_kv_pressure",
        "stalled_runtime_tasks": "agent_task_progress",
        "low_progress_runtime_tasks": "agent_task_progress",
    },
}

DEFAULT_P2_SERVICE_EVENT_PROFILE = {
    "event_sources": (
        "vllm_p2",
    ),
    "scheduler_event_types": (
        "vllm.scheduler.snapshot",
        "vllm.scheduler.iteration.scheduled",
        "vllm.scheduler.iteration.completed",
    ),
    "kv_event_types": (
        "vllm.kv.snapshot",
    ),
    "running_request_fields": (
        "running_requests",
        "running_requests_after_schedule",
    ),
    "waiting_request_fields": (
        "waiting_requests",
        "waiting_requests_after_schedule",
        "waiting",
    ),
    "kv_usage_ratio_fields": (
        "kv_usage_ratio",
        "kv_usage_after",
        "kv_usage_before",
    ),
}

DEFAULT_SCHEMA_PROFILE = {
    "step_csv_profile": DEFAULT_STEP_CSV_PROFILE,
    "p2_service_event_profile": DEFAULT_P2_SERVICE_EVENT_PROFILE,
}


def merge_schema_profile(schema_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_SCHEMA_PROFILE)
    if schema_profile:
        merged = merge_profile_dict(merged, schema_profile)
    return merged


def merge_profile_dict(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = merge_profile_dict(current, value)
        else:
            merged[key] = value
    return merged
