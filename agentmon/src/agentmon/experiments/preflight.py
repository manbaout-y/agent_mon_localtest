from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentmon.optimizer.harness.schemas import (
    COMPLETE,
    FORBIDDEN_CONCLUSIONS,
    PARTIAL,
    SCHEMA_VERSION,
    UNRESOLVED,
    merge_schema_profile,
)

from .evidence import BatchExperimentData, iter_batch_results, iter_step_rows
from .snapshots import build_run_intervals, iter_model_service_event_samples, iter_step_item_timepoints


def build_optimizer_experiment_preflight(
    data: BatchExperimentData,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_profile = merge_schema_profile(schema_profile)
    intervals = build_run_intervals(data, resolved_profile["step_csv_profile"])
    valid_intervals = [
        interval
        for interval in intervals
        if interval.start_mono_ns is not None
        and interval.end_mono_ns is not None
        and interval.end_mono_ns >= interval.start_mono_ns
    ]
    step_timepoint_count = sum(1 for _ in iter_step_item_timepoints(data))
    service_sample_count = 0
    service_conflicts = []
    service_ids = set()
    for sample in iter_model_service_event_samples(data, resolved_profile["p2_service_event_profile"]):
        service_sample_count += 1
        if sample.get("service_id"):
            service_ids.add(str(sample["service_id"]))
        if sample.get("conflicted_fields"):
            service_conflicts.append(
                {
                    "timestamp_mono_ns": sample.get("timestamp_mono_ns"),
                    "event_type": sample.get("event_type"),
                    "conflicted_fields": list(sample.get("conflicted_fields", ())),
                }
            )
    result_count = sum(1 for _ in iter_batch_results(data))
    step_row_count = sum(1 for _ in iter_step_rows(data))
    blocking_reasons: list[str] = []
    if not data.batch_root.is_dir():
        blocking_reasons.append("batch_root_not_directory")
    if not valid_intervals and not step_timepoint_count and not service_sample_count:
        blocking_reasons.append("no_observed_timeline_timepoints")
    unresolved_run_dirs = [
        {
            "run_id": item.run_id,
            "resolution_source": item.resolution_source,
            "candidates": [str(path) for path in item.resolution_candidates],
        }
        for item in data.run_events.values()
        if item.status == UNRESOLVED
    ]
    if blocking_reasons:
        status = UNRESOLVED
    elif (
        not result_count
        or not step_row_count
        or data.source_status.get("run_events", {}).get("status") != COMPLETE
        or unresolved_run_dirs
        or service_conflicts
        or len(service_ids) > 1
    ):
        status = PARTIAL
    else:
        status = COMPLETE
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.preflight",
        "status": status,
        "batch_root": str(data.batch_root),
        "blocking_reasons": blocking_reasons,
        "timeline_evidence": {
            "valid_run_interval_count": len(valid_intervals),
            "step_timepoint_count": step_timepoint_count,
            "model_service_sample_count": service_sample_count,
            "model_service_ids": sorted(service_ids),
            "conflicting_model_service_sample_count": len(service_conflicts),
            "conflicting_model_service_samples": service_conflicts,
            "evidence_sources": [
                source
                for source, present in (
                    ("batch_results_or_step_interval", bool(valid_intervals)),
                    ("step_csv", bool(step_timepoint_count)),
                    ("raw_p2_events", bool(service_sample_count)),
                )
                if present
            ],
        },
        "input_coverage": {
            "batch_result_rows": result_count,
            "step_rows": step_row_count,
            "run_event_records": len(data.run_events),
            "resolved_run_event_files": len(
                [item for item in data.run_events.values() if item.events_path and item.events_path.is_file()]
            ),
            "unresolved_run_directories": unresolved_run_dirs,
        },
        "source_status": data.source_status,
        "limitations": [
            "Preflight establishes input and timeline availability; it does not establish causal optimizer benefit.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def write_optimizer_experiment_preflight(payload: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir).expanduser().resolve() / "optimizer_preflight.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
