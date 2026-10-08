from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from agentmon.optimizer.harness.schemas import (
    COMPLETE,
    DEFAULT_STEP_CSV_PROFILE,
    FORBIDDEN_CONCLUSIONS,
    PARTIAL,
    SCHEMA_VERSION,
    UNKNOWN,
    UNRESOLVED,
    merge_schema_profile,
)


STEP_CSV_PROFILE = DEFAULT_STEP_CSV_PROFILE


@dataclass
class RunEventData:
    run_id: str
    run_dir: Path | None
    events_path: Path | None
    # Compatibility only. load_batch_experiment deliberately leaves it empty;
    # events.jsonl remains authoritative and is consumed through iter_run_events.
    events: list[dict[str, Any]] = field(default_factory=list)
    invalid_lines: int = 0
    status: str = UNKNOWN
    resolution_source: str = UNKNOWN
    resolution_candidates: tuple[Path, ...] = ()


@dataclass
class BatchExperimentData:
    batch_root: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]
    results: list[dict[str, str]]
    step_rows: list[dict[str, str]]
    step_paths: list[Path]
    run_events: dict[str, RunEventData]
    source_status: dict[str, Any]
    # The index describes authoritative source locations.  M1 may use the
    # compatibility row list, while M2/M3 can re-read CSV rows on demand.
    index: "BatchExperimentIndex | None" = None
    step_rows_materialized: bool = True

    def iter_results(self):
        yield from self.results

    def iter_step_rows(self):
        if self.step_rows_materialized:
            yield from self.step_rows
            return
        for path in self.step_paths:
            for row in _read_delimited(path):
                row["_evidence_source"] = str(path)
                yield row


@dataclass(frozen=True)
class BatchExperimentIndex:
    """Source-only batch index for bounded replay input reads.

    It is derived from the batch root and contains no telemetry payloads.  The
    original TSV/CSV/JSONL files remain the authority.
    """

    batch_root: Path
    manifest_path: Path
    summary_path: Path
    results_path: Path
    step_paths: tuple[Path, ...]
    run_event_paths: dict[str, Path]


def load_batch_experiment(
    batch_root: str | Path,
    *,
    run_search_roots: list[str | Path] | tuple[str | Path, ...] = (),
    materialize_step_rows: bool = True,
) -> BatchExperimentData:
    root = Path(batch_root).expanduser().resolve()
    manifest_path = root / "batch-manifest.json"
    summary_path = root / "batch-summary.json"
    results_path = root / "batch-results.tsv"
    manifest = _read_optional_json(manifest_path)
    summary = _read_optional_json(summary_path)
    results = _read_delimited(results_path, delimiter="\t") if results_path.is_file() else []
    step_paths = locate_step_csvs(root, results)
    step_rows: list[dict[str, str]] = []
    step_row_count = 0
    for path in step_paths:
        for row in _read_delimited(path):
            step_row_count += 1
            if materialize_step_rows:
                row["_evidence_source"] = str(path)
                step_rows.append(row)
    run_events = load_run_events(root, results, run_search_roots=run_search_roots)
    source_status = build_source_status(
        root,
        manifest_path=manifest_path,
        summary_path=summary_path,
        results_path=results_path,
        step_paths=step_paths,
        results=results,
        step_rows=step_rows,
        step_row_count=step_row_count,
        run_events=run_events,
    )
    return BatchExperimentData(
        batch_root=root,
        manifest=manifest,
        summary=summary,
        results=results,
        step_rows=step_rows,
        step_paths=step_paths,
        run_events=run_events,
        source_status=source_status,
        index=BatchExperimentIndex(
            batch_root=root,
            manifest_path=manifest_path,
            summary_path=summary_path,
            results_path=results_path,
            step_paths=tuple(step_paths),
            run_event_paths={
                run_id: item.events_path
                for run_id, item in run_events.items()
                if item.events_path is not None
            },
        ),
        step_rows_materialized=materialize_step_rows,
    )


def iter_batch_results(data: BatchExperimentData):
    """Iterate observed batch result rows without exposing storage choice."""
    yield from data.iter_results()


def iter_step_rows(data: BatchExperimentData):
    """Iterate step CSV rows from the compatibility list or source files."""
    yield from data.iter_step_rows()


def build_evidence_outputs(data: BatchExperimentData, schema_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_profile = merge_schema_profile(schema_profile)
    step_profile = resolved_profile["step_csv_profile"]
    graph = build_evidence_graph(data, step_profile)
    critical_path = build_critical_path_rows(data, step_profile)
    bottlenecks = build_bottleneck_attribution(data, critical_path, step_profile)
    metrics = build_workload_normalized_metrics(data, step_profile)
    quality = build_data_quality(data, resolved_profile)
    manifest = build_experiment_manifest(data, graph, critical_path, metrics, resolved_profile)
    return {
        "experiment_manifest": manifest,
        "evidence_graph": graph,
        "critical_path": critical_path,
        "bottleneck_attribution": bottlenecks,
        "workload_normalized_metrics": metrics,
        "data_quality": quality,
    }


def write_evidence_outputs(outputs: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "experiment_manifest": root / "experiment_manifest.json",
        "evidence_graph": root / "evidence_graph.json",
        "critical_path": root / "critical_path.csv",
        "bottleneck_attribution": root / "bottleneck_attribution.json",
        "workload_normalized_metrics": root / "workload_normalized_metrics.csv",
        "data_quality": root / "data_quality.json",
    }
    _write_json(paths["experiment_manifest"], outputs["experiment_manifest"])
    _write_json(paths["evidence_graph"], outputs["evidence_graph"])
    _write_csv(paths["critical_path"], outputs["critical_path"])
    _write_json(paths["bottleneck_attribution"], outputs["bottleneck_attribution"])
    _write_csv(paths["workload_normalized_metrics"], outputs["workload_normalized_metrics"])
    _write_json(paths["data_quality"], outputs["data_quality"])
    return paths


def build_experiment_manifest(
    data: BatchExperimentData,
    graph: dict[str, Any],
    critical_path: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    manifest_status = COMPLETE if data.manifest else PARTIAL
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.manifest",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_root": str(data.batch_root),
        "analysis_sop_phase": "04_evidence",
        "status": manifest_status,
        "source_files": source_file_inventory(data),
        "input_summary": {
            "result_rows": len(data.results),
            "step_rows": len(data.step_rows),
            "run_event_files": len([item for item in data.run_events.values() if item.events_path]),
            "graph_nodes": len(graph.get("nodes", [])),
            "graph_edges": len(graph.get("edges", [])),
            "critical_path_rows": len(critical_path),
            "workload_metric_rows": len(metrics),
        },
        "batch_metadata": {
            "manifest_type": data.manifest.get("manifest_type") or UNKNOWN,
            "task_count": data.manifest.get("task_count", data.summary.get("task_count", UNKNOWN)),
            "concurrency": first_value(data.manifest.get("concurrency"), data.summary.get("configured_concurrency"), UNKNOWN),
            "configuration_fingerprint": data.manifest.get("configuration_fingerprint") or UNKNOWN,
        },
        "privacy_defaults": {
            "prompts_included": False,
            "summaries_included": False,
            "raw_tool_outputs_included": False,
            "full_code_diffs_included": False,
        },
        "schema_profile": {
            "step_csv_profile_keys": sorted(schema_profile.get("step_csv_profile", {}).keys()),
            "p2_service_event_profile_keys": sorted(schema_profile.get("p2_service_event_profile", {}).keys()),
        },
        "forbidden_conclusions": list(FORBIDDEN_CONCLUSIONS),
    }


def build_evidence_graph(data: BatchExperimentData, step_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    step_profile = step_profile or STEP_CSV_PROFILE
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    batch_id = data.batch_root.name or "batch"
    nodes.append(
        {
            "id": f"batch:{batch_id}",
            "type": "batch",
            "label": batch_id,
            "evidence_source": "batch-manifest.json" if data.manifest else UNKNOWN,
            "data_quality": COMPLETE if data.manifest else PARTIAL,
        }
    )
    results_by_run = {str(row.get("run_id") or ""): row for row in data.results if row.get("run_id")}
    step_rows_by_run: dict[str, list[dict[str, str]]] = {}
    for row in data.step_rows:
        run_id = str(row.get("run_id") or "")
        if run_id:
            step_rows_by_run.setdefault(run_id, []).append(row)
    run_ids = sorted(set(results_by_run) | set(step_rows_by_run) | set(data.run_events))
    for run_id in run_ids:
        result = results_by_run.get(run_id, {})
        node_id = f"run:{run_id}"
        nodes.append(
            {
                "id": node_id,
                "type": "run",
                "run_id": run_id,
                "task_id": result.get("task_id") or UNKNOWN,
                "status": first_value(result.get("status"), result.get("execution_success"), UNKNOWN),
                "start_mono_ns": first_int(*(result.get(field) for field in step_profile["run_start_fields"])),
                "end_mono_ns": first_int(*(result.get(field) for field in step_profile["run_end_fields"])),
                "evidence_source": "batch-results.tsv" if result else "step_csv_or_events",
                "data_quality": COMPLETE if result else PARTIAL,
            }
        )
        edges.append(
            {
                "source": f"batch:{batch_id}",
                "target": node_id,
                "type": "contains_run",
                "evidence_source": "batch-results.tsv" if result else "runtime_discovery",
                "data_quality": COMPLETE if result else PARTIAL,
            }
        )
        for index, row in enumerate(sorted(step_rows_by_run.get(run_id, []), key=step_row_sort_key), start=1):
            item_id = row.get("span_id") or row.get("model_call_id") or row.get("tool_call_id") or f"item-{index}"
            item_node = f"item:{run_id}:{item_id}"
            item_type = row.get("item_type") or UNKNOWN
            nodes.append(
                {
                    "id": item_node,
                    "type": "workflow_item",
                    "run_id": run_id,
                    "step_id": row.get("step_id") or UNKNOWN,
                    "step_index": int_or_none(row.get("step_index")),
                    "item_type": item_type,
                    "start_mono_ns": int_or_none(row.get("item_start_mono_ns")),
                    "end_mono_ns": int_or_none(row.get("item_end_mono_ns")),
                    "duration_s": duration_from_row(row),
                    "evidence_source": row.get("_evidence_source") or "step_csv",
                    "data_quality": COMPLETE if row.get("item_start_mono_ns") and row.get("item_end_mono_ns") else PARTIAL,
                }
            )
            edges.append(
                {
                    "source": node_id,
                    "target": item_node,
                    "type": "contains_workflow_item",
                    "evidence_source": row.get("_evidence_source") or "step_csv",
                    "data_quality": COMPLETE,
                }
            )
            if item_type == "model":
                _append_model_phase_edges(edges, item_node, row, step_profile)
    phase_node_ids = sorted(
        {
            str(edge["target"])
            for edge in edges
            if str(edge.get("target") or "").startswith("phase:")
        }
    )
    for phase_node_id in phase_node_ids:
        nodes.append(
            {
                "id": phase_node_id,
                "type": "model_phase",
                "label": phase_node_id.split(":", 1)[1],
                "evidence_source": "step_csv_schema_profile",
                "data_quality": COMPLETE,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.evidence_graph",
        "nodes": nodes,
        "edges": edges,
        "limitations": list(FORBIDDEN_CONCLUSIONS),
    }


def build_critical_path_rows(
    data: BatchExperimentData,
    step_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    step_profile = step_profile or STEP_CSV_PROFILE
    results_by_run = {str(row.get("run_id") or ""): row for row in data.results if row.get("run_id")}
    rows: list[dict[str, Any]] = []
    for row in data.step_rows:
        start = int_or_none(row.get("item_start_mono_ns"))
        end = int_or_none(row.get("item_end_mono_ns"))
        if start is None or end is None or end < start:
            continue
        run_id = str(row.get("run_id") or UNKNOWN)
        result = results_by_run.get(run_id, {})
        run_wall_s = float_or_none(result.get("workload_wall_s")) or run_wall_from_result(result, step_profile)
        duration_s = (end - start) / 1_000_000_000
        rows.append(
            {
                "run_id": run_id,
                "task_id": result.get("task_id") or row.get("task_id") or UNKNOWN,
                "step_id": row.get("step_id") or UNKNOWN,
                "step_index": int_or_none(row.get("step_index")),
                "item_type": row.get("item_type") or UNKNOWN,
                "span_id": row.get("span_id") or row.get("model_call_id") or row.get("tool_call_id") or UNKNOWN,
                "start_mono_ns": start,
                "end_mono_ns": end,
                "duration_s": duration_s,
                "run_wall_s": run_wall_s,
                "contribution_pct_of_run_wall": (
                    duration_s / run_wall_s * 100.0 if run_wall_s and run_wall_s > 0 else None
                ),
                "critical_path_method": "observed_item_interval_order",
                "evidence_source": row.get("_evidence_source") or "step_csv",
                "data_quality": COMPLETE,
            }
        )
    rows.sort(key=lambda item: (str(item["run_id"]), float(item["start_mono_ns"]), float(item["end_mono_ns"])))
    ranks: dict[str, int] = {}
    for row in sorted(rows, key=lambda item: (str(item["run_id"]), -float(item["duration_s"]), str(item["span_id"]))):
        run_id = str(row["run_id"])
        ranks[run_id] = ranks.get(run_id, 0) + 1
        row["duration_rank_within_run"] = ranks[run_id]
    return rows


def build_bottleneck_attribution(
    data: BatchExperimentData,
    critical_path: list[dict[str, Any]],
    step_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step_profile = step_profile or STEP_CSV_PROFILE
    component_rows: dict[str, dict[str, Any]] = {}
    for row in data.step_rows:
        item_type = row.get("item_type")
        if item_type == "model":
            _add_component(
                component_rows,
                "model_e2e",
                first_float(*(row.get(field) for field in step_profile["model_e2e_duration_fields"])),
                row,
                "step_csv_model_duration",
            )
            for component, fields in step_profile["model_phase_duration_fields"].items():
                _add_component(
                    component_rows,
                    component,
                    first_float(*(row.get(field) for field in fields)),
                    row,
                    "step_csv_phase_duration",
                )
        elif item_type == "tool":
            _add_component(
                component_rows,
                "tool_exec",
                first_float(*(row.get(field) for field in step_profile["tool_duration_fields"])),
                row,
                "step_csv_tool_duration",
            )
        else:
            _add_component(component_rows, "unclassified_item", duration_from_row(row), row, "step_csv_item_duration")
    observed_item_total = sum(float(row["duration_s"]) for row in critical_path if row.get("duration_s") is not None)
    run_wall_total = sum(value for value in (run_wall_from_result(row, step_profile) for row in data.results) if value is not None)
    if run_wall_total and run_wall_total > observed_item_total:
        component_rows["run_unattributed"] = {
            "component": "run_unattributed",
            "observed_duration_s": run_wall_total - observed_item_total,
            "observation_count": len(data.results),
            "evidence_sources": ["batch-results.tsv"],
            "data_quality": PARTIAL,
            "limitations": ["Run wall time exceeded observed item intervals; missing step/resource evidence is unresolved."],
        }
    components = sorted(component_rows.values(), key=lambda item: (-float(item["observed_duration_s"] or 0.0), str(item["component"])))
    total = sum(float(row.get("observed_duration_s") or 0.0) for row in components)
    for row in components:
        row["share_of_observed_duration_pct"] = (
            float(row.get("observed_duration_s") or 0.0) / total * 100.0 if total > 0 else None
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.bottleneck_attribution",
        "attribution_method": "observed_duration_rollup",
        "dominant_observed_component": components[0]["component"] if components else UNKNOWN,
        "components": components,
        "data_quality": COMPLETE if components else UNKNOWN,
        "limitations": [
            "Duration attribution is observational and not causal.",
            "Missing P2 or resource fields remain UNKNOWN/PARTIAL.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def build_workload_normalized_metrics(
    data: BatchExperimentData,
    step_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    step_profile = step_profile or STEP_CSV_PROFILE
    step_rows_by_run: dict[str, list[dict[str, str]]] = {}
    for row in data.step_rows:
        run_id = str(row.get("run_id") or "")
        if run_id:
            step_rows_by_run.setdefault(run_id, []).append(row)
    run_ids = sorted(set(step_rows_by_run) | {str(row.get("run_id") or "") for row in data.results if row.get("run_id")})
    results_by_run = {str(row.get("run_id") or ""): row for row in data.results if row.get("run_id")}
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        result = results_by_run.get(run_id, {})
        steps = step_rows_by_run.get(run_id, [])
        model_rows = [row for row in steps if row.get("item_type") == "model"]
        tool_rows = [row for row in steps if row.get("item_type") == "tool"]
        computed_prefill_tokens = sum_first_numeric(model_rows, step_profile["actual_prefill_token_fields"])
        generation_tokens = sum_first_numeric(model_rows, step_profile["generation_token_fields"])
        model_time_s = sum_first_numeric(model_rows, step_profile["model_e2e_duration_fields"])
        tool_time_s = sum_first_numeric(tool_rows, step_profile["tool_duration_fields"])
        decode_s = sum_first_numeric(model_rows, step_profile["model_phase_duration_fields"]["model_decode"])
        queue_s = sum_first_numeric(model_rows, step_profile["model_phase_duration_fields"]["model_queue"])
        run_wall_s = float_or_none(result.get("workload_wall_s")) or run_wall_from_result(result, step_profile)
        rows.append(
            {
                "run_id": run_id or UNKNOWN,
                "task_id": result.get("task_id") or UNKNOWN,
                "status": first_value(result.get("status"), result.get("execution_success"), UNKNOWN),
                "run_wall_s": run_wall_s,
                "model_call_count": len(model_rows),
                "tool_call_count": len(tool_rows),
                "observed_model_time_s": model_time_s,
                "observed_tool_time_s": tool_time_s,
                "observed_p2_queue_s": queue_s,
                "observed_p2_decode_s": decode_s,
                "actual_prefill_tokens": computed_prefill_tokens,
                "actual_prefill_tokens_status": COMPLETE if computed_prefill_tokens is not None else UNKNOWN,
                "generation_tokens": generation_tokens,
                "generation_tokens_status": COMPLETE if generation_tokens is not None else UNKNOWN,
                "model_time_per_call_s": model_time_s / len(model_rows) if model_rows and model_time_s is not None else None,
                "decode_s_per_generation_token": (
                    decode_s / generation_tokens
                    if decode_s is not None and generation_tokens and generation_tokens > 0
                    else None
                ),
                "workload_normalization_basis": "run_and_observed_model_tool_counts",
                "evidence_source": "batch-results.tsv;step_csv",
                "data_quality": COMPLETE if result and steps else PARTIAL,
            }
        )
    return rows


def build_data_quality(
    data: BatchExperimentData,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_profile = schema_profile or merge_schema_profile()
    p2_sources = set(resolved_profile.get("p2_service_event_profile", {}).get("event_sources") or ())
    p2_event_count = 0
    resource_event_count = 0
    for _, event, _ in iter_run_events(data):
        if event.get("source") in p2_sources:
            p2_event_count += 1
        if event.get("source") in {"host", "proc", "ascend", "resource"}:
            resource_event_count += 1
    model_rows = [row for row in data.step_rows if row.get("item_type") == "model"]
    matched_model_rows = [
        row for row in model_rows if str(row.get("p2_match_status") or "").lower() in {"matched", "exact", "complete"}
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.data_quality",
        "overall_status": overall_quality_status(data),
        "sources": data.source_status,
        "coverage": {
            "result_rows": len(data.results),
            "step_rows": len(data.step_rows),
            "model_step_rows": len(model_rows),
            "model_step_rows_with_p2_match_status": len([row for row in model_rows if row.get("p2_match_status")]),
            "model_step_rows_matched_to_p2": len(matched_model_rows),
            "raw_p2_event_count": p2_event_count,
            "resource_event_count": resource_event_count,
        },
        "privacy": {
            "content_fields_excluded": [
                "prompt",
                "messages",
                "summary_text",
                "raw_tool_output",
                "full_code_diff",
            ],
            "default_content_retention": "excluded",
        },
        "limitations": list(FORBIDDEN_CONCLUSIONS),
    }


def build_source_status(
    root: Path,
    *,
    manifest_path: Path,
    summary_path: Path,
    results_path: Path,
    step_paths: list[Path],
    results: list[dict[str, str]],
    step_rows: list[dict[str, str]],
    step_row_count: int | None = None,
    run_events: dict[str, RunEventData],
) -> dict[str, Any]:
    event_files = [item for item in run_events.values() if item.events_path and item.events_path.is_file()]
    invalid_lines = sum(item.invalid_lines for item in run_events.values())
    missing_run_event_count = len([item for item in run_events.values() if not item.events_path or not item.events_path.is_file()])
    return {
        "batch_root": {"path": str(root), "status": COMPLETE if root.is_dir() else UNKNOWN},
        "batch_manifest": _file_status(manifest_path),
        "batch_summary": _file_status(summary_path),
        "batch_results": {**_file_status(results_path), "row_count": len(results)},
        "step_csv": {
            "status": COMPLETE if step_paths and (step_row_count if step_row_count is not None else len(step_rows)) else PARTIAL if step_paths else UNKNOWN,
            "paths": [str(path) for path in step_paths],
            "row_count": step_row_count if step_row_count is not None else len(step_rows),
        },
        "run_events": {
            "status": (
                UNRESOLVED
                if any(item.status == UNRESOLVED for item in run_events.values())
                else COMPLETE
                if event_files and invalid_lines == 0 and missing_run_event_count == 0
                else PARTIAL
                if event_files
                else UNKNOWN
            ),
            "event_file_count": len(event_files),
            "invalid_line_count": invalid_lines,
            "missing_run_event_count": missing_run_event_count,
            "unresolved_run_directory_count": len(
                [item for item in run_events.values() if item.status == UNRESOLVED]
            ),
            "resolution_source_counts": count_values(
                item.resolution_source for item in run_events.values()
            ),
        },
    }


def locate_step_csvs(batch_path: Path, results: list[dict[str, str]]) -> list[Path]:
    preferred = [
        batch_path / "step-sequence" / "all-steps.csv",
        batch_path / "step-sequence" / "all-tasks-step-sequence.csv",
        batch_path / "step-sequence" / "all-tasks-step-sequence-fixed.csv",
        batch_path / "all-steps.csv",
    ]
    for path in preferred:
        if path.is_file():
            return [path]
    paths: list[Path] = []
    seen: set[Path] = set()
    for row in results:
        raw = row.get("step_sequence_csv")
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (batch_path / path).resolve()
        if path.is_file() and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def load_run_events(
    batch_root: Path,
    results: list[dict[str, str]],
    *,
    run_search_roots: list[str | Path] | tuple[str | Path, ...] = (),
) -> dict[str, RunEventData]:
    run_events: dict[str, RunEventData] = {}
    search_index = build_run_search_index(run_search_roots)
    for row in results:
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        run_dir, resolution_source, resolution_candidates = resolve_run_directory(
            batch_root,
            row,
            search_index=search_index,
        )
        events_path = run_dir / "events.jsonl" if run_dir else None
        if events_path and events_path.is_file():
            parsed = _scan_jsonl(events_path)
            run_events[run_id] = RunEventData(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                invalid_lines=parsed["invalid_lines"],
                status=COMPLETE if parsed["invalid_lines"] == 0 else PARTIAL,
                resolution_source=resolution_source,
                resolution_candidates=resolution_candidates,
            )
        else:
            run_events[run_id] = RunEventData(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                status=UNRESOLVED if len(resolution_candidates) > 1 else UNKNOWN,
                resolution_source=resolution_source,
                resolution_candidates=resolution_candidates,
            )
    return run_events


def iter_run_events(data: BatchExperimentData):
    """Yield parsed JSONL records without retaining the corpus in memory."""
    for run_data in data.run_events.values():
        events_path = run_data.events_path
        if events_path is None or not events_path.is_file():
            continue
        with events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield run_data, value, line_number


def resolve_run_dir(batch_root: Path, row: dict[str, str]) -> Path | None:
    run_dir, _, _ = resolve_run_directory(batch_root, row)
    return run_dir


def resolve_run_directory(
    batch_root: Path,
    row: dict[str, str],
    *,
    search_index: dict[str, tuple[Path, ...]] | None = None,
) -> tuple[Path | None, str, tuple[Path, ...]]:
    raw = row.get("run_dir")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (batch_root / path).resolve()
        if (path / "events.jsonl").is_file():
            return path, "batch_results_run_dir", (path,)
    run_id = str(row.get("run_id") or "")
    if not run_id:
        return None, UNKNOWN, ()
    for candidate in (batch_root / run_id, batch_root / "runs" / run_id):
        if (candidate / "events.jsonl").is_file():
            return candidate, "batch_root_fallback", (candidate,)
    candidates = (search_index or {}).get(run_id, ())
    if len(candidates) == 1:
        return candidates[0], "explicit_run_search_root", candidates
    if len(candidates) > 1:
        return None, "ambiguous_explicit_run_search_root", candidates
    if raw:
        return path, "stale_batch_results_run_dir", (path,)
    return None, UNKNOWN, ()


def build_run_search_index(
    run_search_roots: list[str | Path] | tuple[str | Path, ...],
) -> dict[str, tuple[Path, ...]]:
    found: dict[str, set[Path]] = {}
    for raw_root in run_search_roots:
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            continue
        for events_path in sorted(root.rglob("events.jsonl")):
            run_dir = events_path.parent.resolve()
            found.setdefault(run_dir.name, set()).add(run_dir)
    return {
        run_id: tuple(sorted(paths))
        for run_id, paths in sorted(found.items())
    }


def source_file_inventory(data: BatchExperimentData) -> dict[str, Any]:
    files = {
        "batch_manifest": data.batch_root / "batch-manifest.json",
        "batch_summary": data.batch_root / "batch-summary.json",
        "batch_results": data.batch_root / "batch-results.tsv",
    }
    return {
        **{
            key: {
                "path": str(path),
                "exists": path.is_file(),
                "sha256": file_sha256(path) if path.is_file() else None,
            }
            for key, path in files.items()
        },
        "step_csv": [
            {"path": str(path), "sha256": file_sha256(path)}
            for path in data.step_paths
            if path.is_file()
        ],
        "run_events": [
            {"run_id": item.run_id, "path": str(item.events_path), "sha256": file_sha256(item.events_path)}
            for item in data.run_events.values()
            if item.events_path and item.events_path.is_file()
        ],
    }


def overall_quality_status(data: BatchExperimentData) -> str:
    if not data.results or not data.step_rows:
        return PARTIAL
    if data.source_status.get("run_events", {}).get("status") != COMPLETE:
        return PARTIAL
    if any(item.invalid_lines for item in data.run_events.values()):
        return PARTIAL
    return COMPLETE


def _append_model_phase_edges(
    edges: list[dict[str, Any]],
    item_node: str,
    row: dict[str, str],
    step_profile: dict[str, Any],
) -> None:
    for phase, field_names in step_profile["model_phase_duration_fields"].items():
        duration_s = first_float(*(row.get(field) for field in field_names))
        if duration_s is None:
            continue
        edges.append(
            {
                "source": item_node,
                "target": f"phase:{phase}",
                "type": "has_observed_phase_duration",
                "metric": ";".join(field_names),
                "duration_s": duration_s,
                "evidence_source": row.get("_evidence_source") or "step_csv",
                "data_quality": COMPLETE,
            }
        )


def _add_component(
    components: dict[str, dict[str, Any]],
    component: str,
    duration_s: float | None,
    row: dict[str, str],
    source: str,
) -> None:
    if duration_s is None:
        return
    current = components.setdefault(
        component,
        {
            "component": component,
            "observed_duration_s": 0.0,
            "observation_count": 0,
            "evidence_sources": [],
            "data_quality": COMPLETE,
            "limitations": [],
        },
    )
    current["observed_duration_s"] += duration_s
    current["observation_count"] += 1
    evidence_source = f"{source}:{row.get('_evidence_source') or 'step_csv'}"
    if evidence_source not in current["evidence_sources"]:
        current["evidence_sources"].append(evidence_source)


def _file_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "status": COMPLETE if path.is_file() else UNKNOWN,
        "sha256": file_sha256(path) if path.is_file() else None,
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_delimited(path: Path, *, delimiter: str | None = None) -> list[dict[str, str]]:
    actual_delimiter = delimiter or ("\t" if path.suffix in {".tsv", ".tab"} else ",")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [
            {str(key): "" if value is None else str(value) for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter=actual_delimiter)
        ]


def _scan_jsonl(path: Path) -> dict[str, Any]:
    invalid_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
    return {"invalid_lines": invalid_lines}


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def step_row_sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    return (
        int_or_none(row.get("item_start_mono_ns")) or -1,
        int_or_none(row.get("item_end_mono_ns")) or -1,
        row.get("span_id") or row.get("model_call_id") or row.get("tool_call_id") or "",
    )


def run_wall_from_result(
    row: dict[str, str],
    step_profile: dict[str, Any] | None = None,
) -> float | None:
    step_profile = step_profile or STEP_CSV_PROFILE
    start = first_int(*(row.get(field) for field in step_profile["run_start_fields"]))
    end = first_int(*(row.get(field) for field in step_profile["run_end_fields"]))
    if start is None or end is None or end < start:
        return None
    return (end - start) / 1_000_000_000


def duration_from_row(row: dict[str, str]) -> float | None:
    direct = first_float(row.get("duration_s"), row.get("model_call_e2e_s"), row.get("tool_call_duration_s"))
    if direct is not None:
        return direct
    start = int_or_none(row.get("item_start_mono_ns"))
    end = int_or_none(row.get("item_end_mono_ns"))
    if start is None or end is None or end < start:
        return None
    return (end - start) / 1_000_000_000


def sum_numeric(rows: list[dict[str, str]], key: str) -> float | None:
    values = [float_or_none(row.get(key)) for row in rows]
    filtered = [value for value in values if value is not None]
    return sum(filtered) if filtered else None


def sum_first_numeric(rows: list[dict[str, str]], keys: tuple[str, ...]) -> float | None:
    values = [first_float(*(row.get(key) for key in keys)) for row in rows]
    filtered = [value for value in values if value is not None]
    return sum(filtered) if filtered else None


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def first_int(*values: Any) -> int | None:
    for value in values:
        result = int_or_none(value)
        if result is not None:
            return result
    return None


def first_float(*values: Any) -> float | None:
    for value in values:
        result = float_or_none(value)
        if result is not None:
            return result
    return None


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
