from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any


PHASE_TIMESTAMP_KEYS = {
    ("pipeline", "started"): "pipeline_started_at",
    ("pipeline", "finished"): "pipeline_finished_at",
    ("preprocessing", "started"): "preprocessing_started_at",
    ("preprocessing", "finished"): "preprocessing_finished_at",
    ("workload_phase", "started"): "workload_phase_started_at",
    ("workload_phase", "finished"): "workload_phase_finished_at",
    ("p2_index", "started"): "p2_index_started_at",
    ("p2_index", "finished"): "p2_index_finished_at",
    ("analysis_phase", "started"): "analysis_phase_started_at",
    ("analysis_phase", "finished"): "analysis_phase_finished_at",
    ("evidence_build", "started"): "evidence_build_started_at",
    ("evidence_build", "finished"): "evidence_build_finished_at",
    ("model_pack", "started"): "model_pack_started_at",
    ("model_pack", "finished"): "model_pack_finished_at",
    ("finalization", "started"): "finalization_started_at",
    ("finalization", "finished"): "finalization_finished_at",
}


class BatchPhaseRecorder:
    def __init__(self, path: Path, *, worker_id: str | None = None) -> None:
        self.path = path
        self.sequence = 0
        self.timestamps: dict[str, dict[str, Any]] = {}
        self.worker_id = worker_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def mark(self, phase: str, state: str) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "schema_version": "1.0",
            "event_type": f"batch.phase.{state}",
            "phase": phase,
            "state": state,
            "sequence": self.sequence,
            "wall_time_ns": time.time_ns(),
            "monotonic_time_ns": time.monotonic_ns(),
            "process_id": os.getpid(),
            "worker_id": self.worker_id,
            "stage": phase,
            "event": state,
            "status": "RUNNING" if state == "started" else "PASS",
        }
        key = PHASE_TIMESTAMP_KEYS.get((phase, state))
        if key:
            self.timestamps[key] = {
                "wall_time_ns": record["wall_time_ns"],
                "monotonic_time_ns": record["monotonic_time_ns"],
            }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def duration_s(self, start_key: str, end_key: str) -> float | None:
        start = integer_or_none(as_dict(self.timestamps.get(start_key)).get("monotonic_time_ns"))
        end = integer_or_none(as_dict(self.timestamps.get(end_key)).get("monotonic_time_ns"))
        if start is None or end is None or end < start:
            return None
        return (end - start) / 1_000_000_000

    def phase_timings(self) -> dict[str, float | None]:
        preprocessing = self.duration_s("preprocessing_started_at", "preprocessing_finished_at")
        workload = self.duration_s("workload_phase_started_at", "workload_phase_finished_at")
        p2_index = self.duration_s("p2_index_started_at", "p2_index_finished_at")
        analysis = self.duration_s("analysis_phase_started_at", "analysis_phase_finished_at")
        evidence_build = self.duration_s("evidence_build_started_at", "evidence_build_finished_at")
        model_pack = self.duration_s("model_pack_started_at", "model_pack_finished_at")
        finalization = self.duration_s("finalization_started_at", "finalization_finished_at")
        pipeline = self.duration_s("pipeline_started_at", "pipeline_finished_at")
        return {
            "preprocessing_s": preprocessing,
            "workload_phase_s": workload,
            "p2_index_s": p2_index,
            "analysis_phase_s": analysis,
            "evidence_build_s": evidence_build,
            "model_pack_s": model_pack,
            "finalization_s": finalization,
            "pipeline_wall_s": pipeline,
            "postprocessing_s": (
                (p2_index or 0.0) + (analysis or 0.0) + (evidence_build or 0.0) + (model_pack or 0.0) + (finalization or 0.0)
                if any(value is not None for value in (p2_index, analysis, evidence_build, model_pack, finalization))
                else None
            ),
        }


def validate_phase_timings(
    phase_timings: dict[str, Any],
    *,
    pass_absolute_seconds: float = 1.0,
    pass_relative_error: float = 0.001,
    partial_relative_error: float = 0.02,
) -> dict[str, Any]:
    pipeline_wall = optional_number(phase_timings.get("pipeline_wall_s"))
    required_components = [
        optional_number(phase_timings.get("preprocessing_s")),
        optional_number(phase_timings.get("workload_phase_s")),
        optional_number(phase_timings.get("p2_index_s")),
        optional_number(phase_timings.get("analysis_phase_s")),
        optional_number(phase_timings.get("finalization_s")),
    ]
    optional_components = [
        optional_number(phase_timings.get("evidence_build_s")),
        optional_number(phase_timings.get("model_pack_s")),
    ]
    if pipeline_wall is None or any(value is None for value in required_components):
        return {
            "status": "PARTIAL",
            "pipeline_wall_s": pipeline_wall,
            "component_sum_s": None,
            "absolute_error_s": None,
            "relative_error": None,
            "threshold_s": None,
        }
    component_sum = sum(value for value in required_components + optional_components if value is not None)
    absolute_error = abs(pipeline_wall - component_sum)
    relative_error = absolute_error / max(pipeline_wall, component_sum, 1e-12)
    pass_threshold = max(pass_absolute_seconds, pipeline_wall * pass_relative_error)
    status = "PASS" if absolute_error <= pass_threshold else "PARTIAL" if relative_error <= partial_relative_error else "FAIL"
    return {
        "status": status,
        "pipeline_wall_s": pipeline_wall,
        "component_sum_s": component_sum,
        "absolute_error_s": absolute_error,
        "relative_error": relative_error,
        "threshold_s": pass_threshold,
        "partial_relative_error": partial_relative_error,
        "evidence_source": "DIRECT",
    }


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "null", "nan", "n/a"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None
