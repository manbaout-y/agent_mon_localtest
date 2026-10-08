from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
from typing import Any

from agentmon import __version__ as AGENTMON_VERSION
from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.io import (
    file_sha256,
    inspect_jsonl,
    read_json,
    stable_metadata_hash,
    stable_source_hash,
)


FIELD_EXPLICIT = "EXPLICIT_MANIFEST"
FIELD_CONFIG = "CONFIG"
FIELD_LOG = "LOG"
FIELD_ENVIRONMENT = "ENVIRONMENT"
FIELD_SOURCE = "SOURCE"
FIELD_INFERRED = "INFERRED"
FIELD_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DiscoveryResult:
    input_root: Path
    manifest: dict[str, Any]
    file_inventory: list[dict[str, Any]]
    experiment_inventory: list[dict[str, Any]]
    trial_inventory: list[dict[str, Any]]
    batch_inventory: list[dict[str, Any]]
    run_inventory: list[dict[str, Any]]
    missing_files: list[dict[str, Any]]
    checksum_validation: list[dict[str, Any]]
    jsonl_integrity: list[dict[str, Any]]
    schema_versions: list[dict[str, Any]]


def discover_experiment(input_root: str | Path, cfg: AnalysisConfig) -> DiscoveryResult:
    root = Path(input_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    file_inventory = [file_inventory_row(root, path, full_checksum=cfg.runtime.full_file_checksums) for path in files]
    run_dirs = discover_run_dirs(root)
    batch_dirs = discover_batch_dirs(root)
    p2_streams = discover_p2_streams(root, files)
    missing_files: list[dict[str, Any]] = []
    checksum_validation: list[dict[str, Any]] = []
    jsonl_paths = sorted(p2_streams | {path for run in run_dirs if (path := first_existing(run / "events.jsonl", run / "events.jsonl.gz", run / "events.jsonl.zst")) is not None})
    jsonl_integrity = [
        inspect_jsonl(path) if cfg.runtime.full_jsonl_integrity else lightweight_jsonl_row(path)
        for path in jsonl_paths
        if path.exists()
    ]
    schema_versions = schema_version_rows(files)

    run_inventory = [run_inventory_row(root, run_dir, missing_files, full_jsonl_integrity=cfg.runtime.full_jsonl_integrity) for run_dir in run_dirs]
    batch_inventory = [batch_inventory_row(root, batch_dir, run_inventory, missing_files) for batch_dir in batch_dirs]
    if not batch_inventory and run_inventory:
        batch_inventory.append(synthetic_batch_row(root, run_inventory))
    experiment_id = infer_experiment_id(root, batch_inventory, run_inventory)
    trial_inventory = build_trial_inventory(batch_inventory, experiment_id)
    experiment_inventory = [
        {
            "schema_version": "1.0",
            "experiment_id": experiment_id,
            "experiment_group": infer_common_value(batch_inventory, "experiment_group") or root.name,
            "input_root": str(root),
            "batch_count": len(batch_inventory),
            "trial_count": len(trial_inventory),
            "run_count": len(run_inventory),
            "p2_stream_count": len(p2_streams),
            "source_snapshot_hash": source_snapshot_hash(root, files, cfg),
            "status": inventory_status(missing_files, jsonl_integrity),
        }
    ]
    manifest = build_manifest(
        root=root,
        cfg=cfg,
        experiment_id=experiment_id,
        files=files,
        run_inventory=run_inventory,
        batch_inventory=batch_inventory,
        p2_streams=p2_streams,
        jsonl_integrity=jsonl_integrity,
    )
    return DiscoveryResult(
        input_root=root,
        manifest=manifest,
        file_inventory=file_inventory,
        experiment_inventory=experiment_inventory,
        trial_inventory=trial_inventory,
        batch_inventory=batch_inventory,
        run_inventory=run_inventory,
        missing_files=missing_files,
        checksum_validation=checksum_validation,
        jsonl_integrity=jsonl_integrity,
        schema_versions=schema_versions,
    )


def discover_run_dirs(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for manifest in root.rglob("manifest.json"):
        parent = manifest.parent
        if any((parent / name).exists() for name in ("events.jsonl", "events.jsonl.gz", "events.jsonl.zst")):
            candidates.add(parent)
    for events in list(root.rglob("events.jsonl")) + list(root.rglob("events.jsonl.gz")) + list(root.rglob("events.jsonl.zst")):
        if (events.parent / "manifest.json").exists() or (events.parent / "report.json").exists():
            candidates.add(events.parent)
    return sorted(candidates)


def discover_batch_dirs(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for name in ("batch-manifest.json", "batch-results.tsv", "batch-summary.json"):
        for path in root.rglob(name):
            candidates.add(path.parent)
    return sorted(candidates)


def discover_p2_streams(root: Path, files: list[Path]) -> set[Path]:
    streams: set[Path] = set()
    for path in files:
        lower = path.name.lower()
        if not (lower.endswith(".jsonl") or lower.endswith(".jsonl.gz") or lower.endswith(".jsonl.zst")):
            continue
        relative = str(path.relative_to(root)).lower()
        if "p2" in relative or "vllm" in relative or "service" in relative:
            streams.add(path)
    return streams


def file_inventory_row(root: Path, path: Path, *, full_checksum: bool = False) -> dict[str, Any]:
    stat = path.stat()
    return {
        "schema_version": "1.0",
        "path": str(path),
        "relative_path": str(path.relative_to(root)),
        "size_bytes": stat.st_size,
        "sha256": file_sha256(path) if full_checksum else None,
        "metadata_fingerprint": f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}",
        "checksum_mode": "content_sha256" if full_checksum else "metadata_only",
        "suffix": "".join(path.suffixes),
        "role": classify_file(path),
    }


def classify_file(path: Path) -> str:
    name = path.name
    if name in {"events.jsonl", "events.jsonl.gz", "events.jsonl.zst"}:
        return "run_events"
    if name == "manifest.json":
        return "run_manifest"
    if name == "report.json":
        return "run_report"
    if name == "summary.json":
        return "run_summary"
    if name == "batch-manifest.json":
        return "batch_manifest"
    if name == "batch-results.tsv":
        return "batch_results"
    if name == "batch-phase-events.jsonl":
        return "batch_phase_events"
    if name == "p2-index-metrics.json":
        return "p2_index_metrics"
    if name == "npu-process-mapping.json":
        return "npu_process_mapping"
    if name == "npu-mapping-evidence.csv":
        return "npu_mapping_evidence"
    if name.endswith(".jsonl") or name.endswith(".jsonl.gz") or name.endswith(".jsonl.zst"):
        return "jsonl_stream"
    if "trajectory" in str(path).lower():
        return "trajectory"
    return "other"


def run_inventory_row(
    root: Path,
    run_dir: Path,
    missing_files: list[dict[str, Any]],
    *,
    full_jsonl_integrity: bool = False,
) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    report_path = run_dir / "report.json"
    summary_path = run_dir / "summary.json"
    events_path = first_existing(run_dir / "events.jsonl", run_dir / "events.jsonl.gz", run_dir / "events.jsonl.zst")
    manifest = read_json(manifest_path, default={}) if manifest_path.exists() else {}
    report = read_json(report_path, default={}) if report_path.exists() else {}
    summary = read_json(summary_path, default={}) if summary_path.exists() else {}
    for label, path in (
        ("events", events_path),
        ("manifest", manifest_path),
    ):
        if path is None or not Path(path).exists():
            missing_files.append({"scope": "run", "run_dir": str(run_dir), "missing": label, "status": "FAIL"})
    run_id = str(manifest.get("run_id") or report.get("run_id") or run_dir.name)
    return {
        "schema_version": "1.0",
        "experiment_id": None,
        "trial_id": infer_trial_id(run_dir),
        "batch_id": infer_batch_id(run_dir),
        "configured_concurrency": integer_from_many(
            manifest.get("configured_concurrency"),
            manifest.get("concurrency"),
            summary.get("configured_concurrency"),
        ),
        "task_set_name": text_or_none(manifest.get("task_set_name") or manifest.get("task_set")),
        "task_id": text_or_none(manifest.get("task_id") or manifest.get("label")),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "relative_run_dir": str(run_dir.relative_to(root)),
        "events_path": str(events_path) if events_path else None,
        "events_line_count": line_count_if_requested(events_path, full_jsonl_integrity),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "report_path": str(report_path) if report_path.exists() else None,
        "summary_path": str(summary_path) if summary_path.exists() else None,
        "trajectory_exists": any("trajectory" in str(path).lower() for path in run_dir.rglob("*") if path.is_file()),
        "step_sequence_exists": any(path.name.endswith(".csv") and "step" in path.name.lower() for path in run_dir.rglob("*.csv")),
        "status": manifest.get("status") or report.get("status"),
        "exit_code": manifest.get("exit_code") if manifest.get("exit_code") is not None else report.get("exit_code"),
        "workload_start_mono_ns": integer_from_many(manifest.get("workload_start_mono_ns"), manifest.get("start_mono_ns")),
        "workload_end_mono_ns": integer_from_many(manifest.get("workload_end_mono_ns"), manifest.get("end_mono_ns")),
        "start_wall_ns": integer_from_many(manifest.get("start_wall_ns")),
        "end_wall_ns": integer_from_many(manifest.get("end_wall_ns")),
        "agentmon_version": manifest.get("agentmon_version"),
        "source": FIELD_SOURCE,
    }


def batch_inventory_row(
    root: Path,
    batch_dir: Path,
    run_inventory: list[dict[str, Any]],
    missing_files: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest_path = batch_dir / "batch-manifest.json"
    summary_path = batch_dir / "batch-summary.json"
    results_path = batch_dir / "batch-results.tsv"
    manifest = read_json(manifest_path, default={}) if manifest_path.exists() else {}
    summary = read_json(summary_path, default={}) if summary_path.exists() else {}
    results = read_tsv(results_path) if results_path.exists() else []
    phase_events_path = batch_dir / "batch-phase-events.jsonl"
    direct_phase = phase_timings_from_events(phase_events_path)
    if not results_path.exists():
        missing_files.append({"scope": "batch", "batch_dir": str(batch_dir), "missing": "batch-results.tsv", "status": "PARTIAL"})
    run_dirs = set()
    for row in results:
        raw = row.get("run_dir")
        if raw:
            run_dirs.add(str(Path(raw).expanduser().resolve()))
    observed_runs = [
        row for row in run_inventory if is_relative_to(Path(str(row["run_dir"])), batch_dir) or str(row["run_dir"]) in run_dirs
    ]
    configured = integer_from_many(
        manifest.get("configured_concurrency"),
        manifest.get("concurrency"),
        summary.get("configured_concurrency"),
        infer_concurrency_from_path(batch_dir),
    )
    task_ids = sorted({str(row.get("task_id")) for row in results if row.get("task_id")})
    return {
        "schema_version": "1.0",
        "experiment_id": None,
        "trial_id": str(manifest.get("trial_id") or infer_trial_id(batch_dir)),
        "batch_id": str(manifest.get("batch_id") or batch_dir.name),
        "batch_dir": str(batch_dir),
        "relative_batch_dir": str(batch_dir.relative_to(root)),
        "configured_concurrency": configured,
        "configured_concurrency_source": FIELD_EXPLICIT if manifest.get("concurrency") is not None else FIELD_INFERRED,
        "task_set_name": manifest.get("task_set_name") or summary.get("task_set_name"),
        "task_count": integer_from_many(manifest.get("task_count"), summary.get("task_count"), len(task_ids) or None),
        "expected_run_count": integer_from_many(manifest.get("expected_run_count"), manifest.get("task_count"), summary.get("task_count")),
        "observed_run_count": len(observed_runs) if observed_runs else len(results),
        "result_row_count": len(results),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "summary_path": str(summary_path) if summary_path.exists() else None,
        "results_path": str(results_path) if results_path.exists() else None,
        "batch_phase_events_path": str(phase_events_path)
        if phase_events_path.exists()
        else None,
        "p2_index_metrics_path": str(batch_dir / "p2-index-metrics.json")
        if (batch_dir / "p2-index-metrics.json").exists()
        else None,
        "npu_process_mapping_path": str(batch_dir / "npu-process-mapping.json")
        if (batch_dir / "npu-process-mapping.json").exists()
        else None,
        "npu_mapping_evidence_path": str(batch_dir / "npu-mapping-evidence.csv")
        if (batch_dir / "npu-mapping-evidence.csv").exists()
        else None,
        "experiment_start": first_non_null(manifest.get("start_time"), summary.get("started_at")),
        "experiment_finish": first_non_null(manifest.get("finish_time"), summary.get("finished_at")),
        "batch_wall_s": first_non_null(manifest.get("batch_wall_s"), summary.get("batch_wall_s")),
        "phase_timings": first_non_null(direct_phase.get("phase_timings"), manifest.get("phase_timings"), summary.get("phase_timings")),
        "phase_timestamps": first_non_null(direct_phase.get("phase_timestamps"), manifest.get("phase_timestamps"), summary.get("phase_timestamps")),
        "pipeline_phase_validation": first_non_null(
            direct_phase.get("validation"),
            manifest.get("pipeline_phase_validation"),
            summary.get("pipeline_phase_validation"),
        ),
        "p2_index_metrics": first_non_null(manifest.get("p2_index_metrics"), summary.get("p2_index_metrics")),
        "npu_process_mapping": first_non_null(manifest.get("npu_process_mapping"), summary.get("npu_process_mapping")),
        "task_set_sha256": task_set_sha256(results),
        "agentmon_version": manifest.get("agentmon_version") or summary.get("agentmon_version"),
        "serving_framework": manifest.get("serving_framework"),
        "serving_framework_version": manifest.get("serving_framework_version"),
        "model_name": manifest.get("model_name") or summary.get("model_name"),
        "device_visibility": manifest.get("device_visibility") or manifest.get("ASCEND_RT_VISIBLE_DEVICES"),
        "status": "PASS" if results_path.exists() else "PARTIAL",
    }


def phase_timings_from_events(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    timestamps: dict[str, dict[str, Any]] = {}
    try:
        for item in iter_jsonl_simple(path):
            phase = str(item.get("phase") or item.get("stage") or "")
            state = str(item.get("state") or item.get("event") or "")
            if phase and state in {"started", "finished"}:
                timestamps[f"{phase}_{state}"] = {
                    "wall_time_ns": integer_from_many(item.get("wall_time_ns")),
                    "monotonic_time_ns": integer_from_many(item.get("monotonic_time_ns")),
                }
    except Exception:  # noqa: BLE001 - invalid phase data is handled as unavailable
        return {}
    names = {
        "preprocessing_s": "preprocessing",
        "workload_phase_s": "workload_phase",
        "p2_index_s": "p2_index",
        "analysis_phase_s": "analysis_phase",
        "evidence_build_s": "evidence_build",
        "model_pack_s": "model_pack",
        "finalization_s": "finalization",
        "pipeline_wall_s": "pipeline",
    }
    timings = {field: phase_duration(timestamps, phase) for field, phase in names.items()}
    components = [timings[key] for key in names if key != "pipeline_wall_s"]
    wall = timings.get("pipeline_wall_s")
    if wall is None or any(value is None for value in components):
        validation = {"status": "PARTIAL", "evidence_source": "DIRECT", "reason": "incomplete_direct_phase_timestamps"}
    else:
        component_sum = sum(value for value in components if value is not None)
        residual = wall - component_sum
        relative = abs(residual) / max(wall, component_sum, 1e-12)
        validation = {
            "status": "PASS" if abs(residual) <= max(1.0, wall * 0.001) else "PARTIAL" if relative <= 0.02 else "FAIL",
            "evidence_source": "DIRECT",
            "absolute_error_s": abs(residual),
            "relative_error": relative,
        }
    return {"phase_timings": timings, "phase_timestamps": timestamps, "validation": validation}


def iter_jsonl_simple(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                yield item


def phase_duration(timestamps: dict[str, dict[str, Any]], phase: str) -> float | None:
    start = integer_from_many((timestamps.get(f"{phase}_started") or {}).get("monotonic_time_ns"))
    finish = integer_from_many((timestamps.get(f"{phase}_finished") or {}).get("monotonic_time_ns"))
    if start is None or finish is None or finish < start:
        return None
    return (finish - start) / 1_000_000_000


def synthetic_batch_row(root: Path, run_inventory: list[dict[str, Any]]) -> dict[str, Any]:
    task_ids = sorted({str(row.get("task_id")) for row in run_inventory if row.get("task_id")})
    configured = sorted({row.get("configured_concurrency") for row in run_inventory if row.get("configured_concurrency") is not None})
    return {
        "schema_version": "1.0",
        "experiment_id": None,
        "trial_id": "trial-0",
        "batch_id": root.name,
        "batch_dir": str(root),
        "relative_batch_dir": ".",
        "configured_concurrency": configured[0] if len(configured) == 1 else None,
        "configured_concurrency_source": FIELD_INFERRED if len(configured) == 1 else FIELD_UNKNOWN,
        "task_set_name": None,
        "task_count": len(task_ids) or len(run_inventory),
        "expected_run_count": len(run_inventory),
        "observed_run_count": len(run_inventory),
        "result_row_count": 0,
        "manifest_path": None,
        "summary_path": None,
        "results_path": None,
        "experiment_start": None,
        "experiment_finish": None,
        "task_set_sha256": task_set_sha256(run_inventory),
        "agentmon_version": infer_common_value(run_inventory, "agentmon_version"),
        "serving_framework": None,
        "serving_framework_version": None,
        "model_name": None,
        "device_visibility": None,
        "status": "PARTIAL",
    }


def build_trial_inventory(batch_inventory: list[dict[str, Any]], experiment_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in batch_inventory:
        trial_id = str(batch.get("trial_id") or "trial-0")
        if trial_id in seen:
            continue
        seen.add(trial_id)
        trial_batches = [item for item in batch_inventory if str(item.get("trial_id") or "trial-0") == trial_id]
        rows.append(
            {
                "schema_version": "1.0",
                "experiment_id": experiment_id,
                "trial_id": trial_id,
                "batch_count": len(trial_batches),
                "observed_run_count": sum(int(item.get("observed_run_count") or 0) for item in trial_batches),
                "configured_concurrency_values": sorted(
                    {item.get("configured_concurrency") for item in trial_batches if item.get("configured_concurrency") is not None}
                ),
            }
        )
    return rows


def build_manifest(
    *,
    root: Path,
    cfg: AnalysisConfig,
    experiment_id: str,
    files: list[Path],
    run_inventory: list[dict[str, Any]],
    batch_inventory: list[dict[str, Any]],
    p2_streams: set[Path],
    jsonl_integrity: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_run_count = sum(int(row.get("expected_run_count") or 0) for row in batch_inventory) or None
    observed_run_count = len(run_inventory)
    field_sources = {
        "configured_concurrency": source_for_values(batch_inventory, "configured_concurrency"),
        "task_count": source_for_values(batch_inventory, "task_count"),
        "model_name": source_for_values(batch_inventory, "model_name"),
        "serving_framework_version": source_for_values(batch_inventory, "serving_framework_version"),
    }
    return {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "experiment_group": root.name,
        "trial_id": None,
        "task_set_name": infer_common_value(batch_inventory, "task_set_name"),
        "task_set_version": None,
        "task_set_sha256": infer_common_value(batch_inventory, "task_set_sha256"),
        "agentmon_version": infer_common_value(batch_inventory, "agentmon_version") or infer_common_value(run_inventory, "agentmon_version") or AGENTMON_VERSION,
        "agent_runtime_version": None,
        "model_name": infer_common_value(batch_inventory, "model_name"),
        "model_path_hash": None,
        "serving_framework": infer_common_value(batch_inventory, "serving_framework"),
        "serving_framework_version": infer_common_value(batch_inventory, "serving_framework_version"),
        "tensor_parallel": None,
        "pipeline_parallel": None,
        "max_model_len": None,
        "scheduler_mode": None,
        "prefix_cache_enabled": None,
        "temperature": None,
        "seed": None,
        "device_visibility": infer_common_value(batch_inventory, "device_visibility"),
        "expected_physical_devices": None,
        "configured_concurrency": sorted(
            {row.get("configured_concurrency") for row in batch_inventory if row.get("configured_concurrency") is not None}
        ),
        "task_count": sum(int(row.get("task_count") or 0) for row in batch_inventory) or None,
        "expected_run_count": expected_run_count,
        "observed_run_count": observed_run_count,
        "analysis_worker_count": cfg.runtime.jobs,
        "experiment_start": min_text(row.get("experiment_start") for row in batch_inventory),
        "experiment_finish": max_text(row.get("experiment_finish") for row in batch_inventory),
        "source_snapshot_hash": source_snapshot_hash(root, files, cfg),
        "config_snapshot_hash": stable_source_hash(
            [path for path in files if "config" in path.name.lower() and path.stat().st_size <= 16 * 1024 * 1024]
        ),
        "raw_data_locations": [str(root)],
        "p2_stream_locations": [str(path) for path in sorted(p2_streams)],
        "inferred_fields": {key: value for key, value in field_sources.items() if value == FIELD_INFERRED},
        "unknown_fields": [key for key, value in field_sources.items() if value == FIELD_UNKNOWN],
        "field_sources": field_sources,
        "jsonl_status": inventory_status([], jsonl_integrity),
    }


def schema_version_rows(files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        if path.suffix != ".json":
            continue
        try:
            data = read_json(path, default={})
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("schema_version") is not None:
            rows.append({"path": str(path), "schema_version": data.get("schema_version"), "role": classify_file(path)})
    return rows


def lightweight_jsonl_row(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "sha256": None,
        "total_lines": None,
        "valid_lines": None,
        "invalid_lines": 0,
        "duplicate_event_ids": 0,
        "sequence_regressions": 0,
        "timestamp_regressions": 0,
        "missing_required_fields": 0,
        "last_line_missing_newline": False,
        "status": "PARTIAL",
        "integrity_mode": "metadata_only",
        "error": None,
    }


def source_snapshot_hash(root: Path, files: list[Path], cfg: AnalysisConfig) -> str:
    if cfg.runtime.full_file_checksums:
        return stable_source_hash(files)
    return stable_metadata_hash(files, root=root)


def line_count_if_requested(path: Path | None, enabled: bool) -> int | None:
    if not enabled or path is None or not path.exists():
        return None
    from agentmon.analysis.io import line_count

    return line_count(path)


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def task_set_sha256(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    payload = json.dumps(
        sorted({str(row.get("task_id") or row.get("run_id") or "") for row in rows}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def infer_experiment_id(root: Path, batch_inventory: list[dict[str, Any]], run_inventory: list[dict[str, Any]]) -> str:
    common = infer_common_value(batch_inventory, "experiment_id") or infer_common_value(run_inventory, "experiment_id")
    return str(common or root.name)


def infer_trial_id(path: Path) -> str:
    for part in reversed(path.parts):
        lower = part.lower()
        if lower.startswith("trial") or lower.startswith("repeat") or lower.startswith("rep"):
            return part
    return "trial-0"


def infer_batch_id(path: Path) -> str:
    for part in reversed(path.parts):
        lower = part.lower()
        if "batch" in lower or "concurrency" in lower or lower.startswith("c"):
            return part
    return path.name


def infer_concurrency_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        lower = part.lower().replace("-", "_")
        for prefix in ("concurrency_", "concurrency", "conc_", "c"):
            if lower.startswith(prefix):
                suffix = lower[len(prefix):].strip("_")
                try:
                    return int(suffix)
                except ValueError:
                    continue
    return None


def inventory_status(missing_files: list[dict[str, Any]], jsonl_integrity: list[dict[str, Any]]) -> str:
    if any(row.get("status") == "FAIL" for row in missing_files):
        return "FAIL"
    if any(row.get("status") == "FAIL" for row in jsonl_integrity):
        return "PARTIAL"
    if missing_files:
        return "PARTIAL"
    return "PASS"


def source_for_values(rows: list[dict[str, Any]], key: str) -> str:
    values = [row.get(key) for row in rows if row.get(key) not in {None, ""}]
    if values:
        sources = [row.get(f"{key}_source") for row in rows if row.get(key) not in {None, ""}]
        if any(source == FIELD_INFERRED for source in sources):
            return FIELD_INFERRED
        return FIELD_EXPLICIT
    return FIELD_UNKNOWN


def infer_common_value(rows: list[dict[str, Any]], key: str) -> Any:
    values = {json.dumps(row.get(key), sort_keys=True, ensure_ascii=False) for row in rows if not missing_value(row.get(key))}
    if len(values) == 1:
        return json.loads(next(iter(values)))
    return None


def missing_value(value: Any) -> bool:
    return value is None or value == "" or value == []


def first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def first_non_null(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def min_text(values: Any) -> str | None:
    candidates = sorted(str(value) for value in values if value not in {None, ""})
    return candidates[0] if candidates else None


def max_text(values: Any) -> str | None:
    candidates = sorted(str(value) for value in values if value not in {None, ""})
    return candidates[-1] if candidates else None


def text_or_none(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def integer_from_many(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool) or value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
