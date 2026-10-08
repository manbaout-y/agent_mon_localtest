from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sys
from typing import Any
from collections.abc import Callable

from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.discovery import DiscoveryResult, read_tsv
from agentmon.analysis.io import float_or_none, integer_or_none, read_bundle_table, read_json, write_plain_csv, write_table, write_table_manifest
from agentmon.analysis.io import write_json
from agentmon.analysis.profiles import SchemaProfile, available_profiles, is_event_type, resolve_profile, values_for_keys
from agentmon.analysis.p2_index import event_aliases, query_p2_events
from agentmon.analysis.statistics import interval_union, overlap_duration
from agentmon.reporting.canonical import build_canonical_tables, build_span_rows
from agentmon.reporting.report import build_report, read_event_log
from agentmon.reporting.summary import build_run_summary


NORMALIZED_TABLES = (
    "experiments",
    "trials",
    "batches",
    "workers",
    "worker_intervals",
    "runs",
    "steps",
    "spans",
    "requests",
    "request_lifecycle",
    "request_batch_membership",
    "scheduler_iterations",
    "kv_block_events",
    "resource_samples",
    "tool_calls",
    "error_events",
    "trajectories",
    "p1_request_attempts",
    "p2_requests",
    "request_id_aliases",
    "profile_detection_audit",
    "request_parse_audit",
    "request_parse_conservation",
    "request_identity_audit",
    "p2_index_query_audit",
    "task_results",
    "event_inventory",
)


@dataclass(frozen=True)
class NormalizedDataset:
    tables: dict[str, list[dict[str, Any]]]
    artifacts: dict[str, dict[str, Any]]
    skipped_runs: list[dict[str, Any]]
    execution_metadata: dict[str, Any]


def normalize_experiment(
    discovery: DiscoveryResult,
    bundle_root: str | Path,
    cfg: AnalysisConfig,
    *,
    output_format: str,
    strict_profile: bool = False,
    p2_index_path: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> NormalizedDataset:
    table_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in NORMALIZED_TABLES}
    table_rows["experiments"] = [dict(row) for row in discovery.experiment_inventory]
    table_rows["trials"] = [dict(row) for row in discovery.trial_inventory]
    table_rows["batches"] = [dict(row) for row in discovery.batch_inventory]
    batch_context = build_run_batch_context(discovery)
    skipped_runs: list[dict[str, Any]] = []
    omitted_tables = normalized_omitted_tables(cfg)

    for batch in discovery.batch_inventory:
        append_task_results(table_rows, batch)
        append_workers(table_rows, batch)
        append_worker_intervals_from_batch(table_rows, batch)

    tasks = []
    for run_row in discovery.run_inventory:
        run_dir = Path(str(run_row["run_dir"]))
        tasks.append(
            {
                "experiment_id": discovery.manifest["experiment_id"],
                "run_row": dict(run_row),
                "context": batch_context.get(str(run_dir), default_run_context(discovery, run_row)),
                "omitted_tables": sorted(omitted_tables),
                "cfg": cfg,
                "p2_index_path": p2_index_path,
            }
        )

    largest_event_file = max((event_file_size(task["run_row"].get("events_path")) for task in tasks), default=0)
    jobs = normalize_jobs(
        cfg.runtime.jobs,
        len(tasks),
        memory_limit_bytes=cfg.runtime.memory_limit_bytes,
        estimated_worker_bytes=max(largest_event_file * 3, 1),
    )
    execution_metadata: dict[str, Any] = {
        "schema_version": "1.0",
        "requested_jobs": cfg.runtime.jobs,
        "effective_jobs": jobs,
        "parallel_status": "NOT_REQUESTED" if jobs == 1 else "RUNNING",
        "fallback_reason": None,
    }
    if jobs > 1:
        parallel_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in NORMALIZED_TABLES}
        parallel_skipped: list[dict[str, Any]] = []
        try:
            with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
                for result in pool.map(normalize_one_run, tasks, chunksize=1):
                    merge_table_rows(parallel_rows, result["tables"])
                    parallel_skipped.extend(result["skipped_runs"])
                    enforce_memory_limit(cfg.runtime.memory_limit_bytes)
                    if progress_callback:
                        progress_callback(result.get("progress", {}))
        except Exception as exc:  # noqa: BLE001 - preserve correctness if a launch context cannot spawn
            execution_metadata.update(
                {
                    "effective_jobs": 1,
                    "parallel_status": "FALLBACK_SERIAL",
                    "fallback_reason": f"{type(exc).__name__}: {exc}",
                }
            )
            for result in map(normalize_one_run, tasks):
                merge_table_rows(table_rows, result["tables"])
                skipped_runs.extend(result["skipped_runs"])
                enforce_memory_limit(cfg.runtime.memory_limit_bytes)
                if progress_callback:
                    progress_callback(result.get("progress", {}))
        else:
            execution_metadata["parallel_status"] = "PASS"
            merge_table_rows(table_rows, parallel_rows)
            skipped_runs.extend(parallel_skipped)
    else:
        for result in map(normalize_one_run, tasks):
            merge_table_rows(table_rows, result["tables"])
            skipped_runs.extend(result["skipped_runs"])
            enforce_memory_limit(cfg.runtime.memory_limit_bytes)
            if progress_callback:
                progress_callback(result.get("progress", {}))

    add_cross_run_identity_audit(table_rows)
    mixed_policy = str(cfg.profile_detection.mixed_schema_policy or "per_run").lower()
    if mixed_policy not in {"per_run", "reject"}:
        raise ValueError(f"unsupported mixed_schema_policy: {cfg.profile_detection.mixed_schema_policy}")
    detected_profiles = {
        str(row.get("detected_profile"))
        for row in table_rows["profile_detection_audit"]
        if row.get("status") == "PASS" and row.get("detected_profile")
    }
    if mixed_policy == "reject" and len(detected_profiles) > 1:
        raise ValueError(f"mixed schema profiles rejected by configuration: {','.join(sorted(detected_profiles))}")
    if strict_profile and any(row.get("status") == "AMBIGUOUS" for row in table_rows["profile_detection_audit"]):
        ambiguous = [str(row.get("run_id")) for row in table_rows["profile_detection_audit"] if row.get("status") == "AMBIGUOUS"]
        raise ValueError(f"profile auto detection ambiguous for runs: {','.join(ambiguous[:20])}")
    artifacts: dict[str, dict[str, Any]] = {}
    normalized_dir = Path(bundle_root) / "01_normalized"
    for name in NORMALIZED_TABLES:
        artifacts[name] = write_table(normalized_dir, name, table_rows[name], output_format=output_format)
    for name in ("request_parse_audit", "request_parse_conservation", "request_identity_audit"):
        write_plain_csv(normalized_dir / f"{name}.csv", table_rows[name])
    write_table_manifest(normalized_dir / "normalized_manifest.json", artifacts)
    write_json(normalized_dir / "request_parse_audit_summary.json", request_audit_summary(table_rows))
    write_json(normalized_dir / "normalization_execution.json", execution_metadata)
    return NormalizedDataset(
        tables=table_rows,
        artifacts=artifacts,
        skipped_runs=skipped_runs,
        execution_metadata=execution_metadata,
    )


def normalized_omitted_tables(cfg: AnalysisConfig) -> set[str]:
    omitted = {str(name) for name in (cfg.output.omit_normalized_tables or ()) if str(name)}
    if cfg.output.fast_smoke:
        omitted.add("event_inventory")
    return omitted & set(NORMALIZED_TABLES)


def normalize_jobs(
    configured_jobs: int,
    task_count: int,
    *,
    memory_limit_bytes: int | None = None,
    estimated_worker_bytes: int = 1,
) -> int:
    try:
        jobs = int(configured_jobs)
    except (TypeError, ValueError):
        jobs = 1
    if task_count <= 1:
        return 1
    if jobs > 1 and not process_pool_launch_safe():
        return 1
    cpu_limit = os.cpu_count() or 1
    memory_jobs = max(1, memory_limit_bytes // max(estimated_worker_bytes, 1)) if memory_limit_bytes else jobs
    return max(1, min(jobs, task_count, cpu_limit, memory_jobs))


def event_file_size(value: Any) -> int:
    if value in {None, ""}:
        return 0
    try:
        return Path(str(value)).stat().st_size
    except OSError:
        return 0


def process_pool_launch_safe() -> bool:
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main_file or str(main_file) == "<stdin>":
        return False
    return Path(str(main_file)).is_file()


def enforce_memory_limit(limit_bytes: int | None) -> None:
    if not limit_bytes:
        return
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform != "darwin":
        peak *= 1024
    if peak > int(limit_bytes):
        raise MemoryError(f"analysis peak RSS {peak} exceeded configured memory limit {int(limit_bytes)}")


def normalize_one_run(task: dict[str, Any]) -> dict[str, Any]:
    table_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in NORMALIZED_TABLES}
    skipped_runs: list[dict[str, Any]] = []
    run_row = dict(task["run_row"])
    context = dict(task["context"])
    cfg = task["cfg"]
    experiment_id = str(task["experiment_id"])
    omitted_tables = set(task.get("omitted_tables") or [])
    run_dir = Path(str(run_row["run_dir"]))
    try:
        event_log = read_event_log(run_dir)
        local_events = list(event_log["events"])
        shared_p2, query_metrics = query_p2_events(
            task.get("p2_index_path"),
            run_id=str(run_row.get("run_id") or run_dir.name),
            task_id=str(context.get("task_id")) if context.get("task_id") not in {None, ""} else None,
            aliases=event_aliases(local_events, cfg),
        )
        events, duplicate_events = deduplicate_events(local_events + shared_p2, run_id=str(run_row.get("run_id") or run_dir.name))
        event_log = {**event_log, "events": events, "valid_lines": len(events), "shared_p2_events_selected": len(shared_p2)}
        table_rows["p2_index_query_audit"].append(
            {
                "schema_version": "1.0",
                "run_id": str(run_row.get("run_id") or run_dir.name),
                "task_id": context.get("task_id"),
                "index_path": task.get("p2_index_path"),
                "selected_event_count": len(shared_p2),
                **query_metrics,
            }
        )
        profile, profile_audit = resolve_profile(cfg, events, run_id=str(run_row.get("run_id") or run_dir.name))
        table_rows["profile_detection_audit"].append(profile_audit)
        effective_profile = profile or available_profiles(cfg)["agentmon"]
        report = build_report(run_dir, event_log=event_log)
        canonical = build_canonical_tables(run_dir, report=report, events=events)
        summary = build_run_summary(report)
    except Exception as exc:  # noqa: BLE001 - one bad run must not stop a bundle
        skipped_runs.append({"run_dir": str(run_dir), "error": f"{type(exc).__name__}: {exc}"})
        table_rows["runs"].append(minimal_run_row(experiment_id, run_row, context, error=str(exc)))
        return {
            "tables": table_rows,
            "skipped_runs": skipped_runs,
            "progress": run_progress(run_row, context, table_rows, raw_events=0),
        }
    append_run_tables(table_rows, experiment_id, run_row, context, report, summary, canonical, events, omitted_tables, effective_profile)
    append_request_parse_audit(
        table_rows,
        run_row=run_row,
        context=context,
        event_log=event_log,
        events=events,
        profile=effective_profile,
        profile_audit=profile_audit,
        canonical=canonical,
        duplicate_events=duplicate_events,
    )
    return {
        "tables": table_rows,
        "skipped_runs": skipped_runs,
        "progress": run_progress(run_row, context, table_rows, raw_events=len(events)),
    }


def merge_table_rows(target: dict[str, list[dict[str, Any]]], source: dict[str, list[dict[str, Any]]]) -> None:
    for name in NORMALIZED_TABLES:
        target[name].extend(source.get(name, []))


def run_progress(
    run_row: dict[str, Any],
    context: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
    *,
    raw_events: int,
) -> dict[str, Any]:
    events_path = Path(str(run_row.get("events_path") or ""))
    try:
        event_bytes = events_path.stat().st_size if events_path.is_file() else 0
    except OSError:
        event_bytes = 0
    return {
        "run_id": run_row.get("run_id"),
        "task_id": context.get("task_id"),
        "event_bytes": event_bytes,
        "raw_events": raw_events,
        "requests": len(tables.get("requests", [])),
        "p2_requests": len(tables.get("p2_requests", [])),
        "failed": bool(tables.get("runs") and tables["runs"][0].get("observability_result") == "FAIL"),
    }


def load_normalized_dataset(bundle_root: str | Path) -> NormalizedDataset:
    bundle = Path(bundle_root)
    tables = {name: read_bundle_table(bundle, "01_normalized", name) for name in NORMALIZED_TABLES}
    execution_metadata = read_json(bundle / "01_normalized" / "normalization_execution.json", default={})
    return NormalizedDataset(tables=tables, artifacts={}, skipped_runs=[], execution_metadata=execution_metadata)


def build_run_batch_context(discovery: DiscoveryResult) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    run_by_id = {str(row.get("run_id")): row for row in discovery.run_inventory}
    for batch in discovery.batch_inventory:
        results_path = batch.get("results_path")
        rows = read_tsv(Path(str(results_path))) if results_path else []
        for item in rows:
            raw_run_dir = item.get("run_dir")
            run_id = item.get("run_id")
            candidates: list[str] = []
            if raw_run_dir:
                candidates.append(str(Path(raw_run_dir).expanduser().resolve()))
            if run_id and run_id in run_by_id:
                candidates.append(str(run_by_id[run_id]["run_dir"]))
            for key in candidates:
                result[key] = {
                    "experiment_id": discovery.manifest["experiment_id"],
                    "trial_id": batch.get("trial_id"),
                    "batch_id": batch.get("batch_id"),
                    "configured_concurrency": batch.get("configured_concurrency"),
                    "task_set": batch.get("task_set_name"),
                    "task_id": item.get("task_id") or run_by_id.get(str(run_id), {}).get("task_id"),
                    "worker_id": item.get("worker_id"),
                    "task_result": item.get("task_result") or item.get("status"),
                    "execution_success": parse_bool(item.get("execution_success")),
                    "public_tests_pass": parse_bool(item.get("public_tests_pass")),
                    "final_judge_pass": parse_bool(item.get("final_judge_pass") or item.get("judge_success")),
                    "result_row": dict(item),
                }
    return result


def default_run_context(discovery: DiscoveryResult, run_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": discovery.manifest["experiment_id"],
        "trial_id": run_row.get("trial_id") or "trial-0",
        "batch_id": run_row.get("batch_id") or discovery.manifest["experiment_id"],
        "configured_concurrency": run_row.get("configured_concurrency"),
        "task_set": run_row.get("task_set_name"),
        "task_id": run_row.get("task_id"),
        "worker_id": None,
        "task_result": run_row.get("status"),
        "execution_success": run_row.get("status") == "success",
        "public_tests_pass": None,
        "final_judge_pass": None,
        "result_row": {},
    }


def append_task_results(table_rows: dict[str, list[dict[str, Any]]], batch: dict[str, Any]) -> None:
    results_path = batch.get("results_path")
    if not results_path:
        return
    for index, item in enumerate(read_tsv(Path(str(results_path)))):
        table_rows["task_results"].append(
            {
                "schema_version": "1.0",
                "experiment_id": batch.get("experiment_id"),
                "trial_id": batch.get("trial_id"),
                "batch_id": batch.get("batch_id"),
                "configured_concurrency": batch.get("configured_concurrency"),
                "row_index": index,
                "task_id": item.get("task_id"),
                "run_id": item.get("run_id"),
                "worker_id": item.get("worker_id"),
                "task_result": item.get("task_result") or item.get("status"),
                "execution_success": parse_bool(item.get("execution_success")),
                "monitoring_success": parse_bool(item.get("monitoring_success")),
                "analysis_pipeline_success": parse_bool(item.get("analysis_pipeline_success")),
                "raw_json": json.dumps(item, sort_keys=True, ensure_ascii=False),
            }
        )


def append_workers(table_rows: dict[str, list[dict[str, Any]]], batch: dict[str, Any]) -> None:
    results_path = batch.get("results_path")
    if not results_path:
        return
    workers = sorted({row.get("worker_id") for row in read_tsv(Path(str(results_path))) if row.get("worker_id")})
    for worker_id in workers:
        table_rows["workers"].append(
            {
                "schema_version": "1.0",
                "experiment_id": batch.get("experiment_id"),
                "trial_id": batch.get("trial_id"),
                "batch_id": batch.get("batch_id"),
                "configured_concurrency": batch.get("configured_concurrency"),
                "worker_id": worker_id,
            }
        )


def append_worker_intervals_from_batch(table_rows: dict[str, list[dict[str, Any]]], batch: dict[str, Any]) -> None:
    results_path = batch.get("results_path")
    if not results_path:
        return
    for item in read_tsv(Path(str(results_path))):
        start = integer_or_none(item.get("workload_start_mono_ns"))
        end = integer_or_none(item.get("workload_end_mono_ns") or item.get("workload_finish_mono_ns"))
        if start is None or end is None or end <= start:
            continue
        table_rows["worker_intervals"].append(
            {
                "schema_version": "1.0",
                "experiment_id": batch.get("experiment_id"),
                "trial_id": batch.get("trial_id"),
                "batch_id": batch.get("batch_id"),
                "worker_id": item.get("worker_id"),
                "task_id": item.get("task_id"),
                "run_id": item.get("run_id"),
                "state": "AGENT_WORKLOAD",
                "start_time": start,
                "end_time": end,
                "duration_s": ns_delta_s(start, end),
                "evidence_source": "batch-results.tsv",
                "evidence_confidence": "high",
            }
        )


def append_run_tables(
    table_rows: dict[str, list[dict[str, Any]]],
    experiment_id: str,
    run_inventory_row: dict[str, Any],
    context: dict[str, Any],
    report: dict[str, Any],
    summary: dict[str, Any],
    canonical: dict[str, list[dict[str, Any]]],
    events: list[dict[str, Any]],
    omitted_tables: set[str] | None = None,
    profile: SchemaProfile | None = None,
) -> None:
    omitted = omitted_tables or set()
    run_id = str(run_inventory_row.get("run_id") or report.get("run_id"))
    request_rows = canonical.get("requests", [])
    tool_rows = canonical.get("tools", [])
    span_rows = build_span_rows(events, run_id=run_id)
    model_intervals = [
        ns_interval(row.get("client_request_start_mono_ns"), row.get("client_response_received_mono_ns"))
        for row in request_rows
    ]
    model_intervals = [item for item in model_intervals if item is not None]
    tool_intervals = [ns_interval(row.get("start_mono_ns"), row.get("end_mono_ns")) for row in tool_rows]
    tool_intervals = [item for item in tool_intervals if item is not None]
    _, model_union_s = interval_union(model_intervals)
    _, tool_union_s = interval_union(tool_intervals)
    overlap_s = overlap_duration(model_intervals, tool_intervals)
    run_wall_s = float_or_none(report.get("duration_s")) or ns_delta_s(
        run_inventory_row.get("workload_start_mono_ns"),
        run_inventory_row.get("workload_end_mono_ns"),
    )
    explained = model_union_s + tool_union_s - overlap_s
    unexplained = max(run_wall_s - explained, 0.0) if run_wall_s is not None else None
    p2_eligible = [row for row in request_rows if p2_eligibility(row, profile)[0]]
    p2_matched = [row for row in p2_eligible if row.get("match_status") == "exact"]
    lifecycle_complete = [row for row in p2_matched if row.get("lifecycle_status") == "complete"]
    publishable = summary.get("publishability_status") == "PUBLISHABLE" or summary.get("data_integrity_pass") is True
    table_rows["runs"].append(
        {
            "schema_version": "1.0",
            "experiment_id": context.get("experiment_id") or experiment_id,
            "trial_id": context.get("trial_id"),
            "batch_id": context.get("batch_id"),
            "configured_concurrency": context.get("configured_concurrency"),
            "task_set": context.get("task_set"),
            "task_id": context.get("task_id"),
            "run_id": run_id,
            "worker_id": context.get("worker_id"),
            "task_result": context.get("task_result"),
            "execution_success": context.get("execution_success"),
            "agent_exit_code": report.get("exit_code"),
            "public_tests_pass": context.get("public_tests_pass"),
            "final_judge_pass": context.get("final_judge_pass"),
            "observability_result": summary.get("publishability_status") or summary.get("data_quality_status"),
            "publishable": publishable,
            "workload_start": run_inventory_row.get("workload_start_mono_ns"),
            "workload_finish": run_inventory_row.get("workload_end_mono_ns"),
            "run_wall_s": run_wall_s,
            "model_request_attempt_count": len(request_rows),
            "model_request_success_count": len([row for row in request_rows if row.get("status") in {"success", "observed"}]),
            "model_request_error_count": len([row for row in request_rows if row.get("status") in {"error", "failed"}]),
            "tool_call_count": len(tool_rows),
            "prompt_tokens_sum": sum_int(row.get("prompt_tokens") for row in request_rows),
            "generation_tokens_sum": sum_int(row.get("output_tokens") or row.get("generation_tokens") for row in request_rows),
            "model_union_s": model_union_s,
            "tool_union_s": tool_union_s,
            "model_tool_overlap_s": overlap_s,
            "idle_or_transition_s": unexplained,
            "unexplained_time_s": unexplained,
            "workload_drift": None,
            "p2_match_rate": len(p2_matched) / len(p2_eligible) if p2_eligible else None,
            "p2_lifecycle_rate": len(lifecycle_complete) / len(p2_matched) if p2_matched else None,
            "source_run_dir": run_inventory_row.get("run_dir"),
        }
    )
    append_request_tables(table_rows, context, run_id, request_rows, profile=profile)
    append_tool_and_span_tables(table_rows, context, run_id, span_rows, tool_rows)
    append_event_tables(table_rows, context, run_id, events, omitted)
    append_scheduler_and_resource_tables(table_rows, context, run_id, events, canonical, omitted)


def append_request_tables(
    table_rows: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
    run_id: str,
    request_rows: list[dict[str, Any]],
    *,
    profile: SchemaProfile | None = None,
) -> None:
    for ordinal, row in enumerate(request_rows, start=1):
        raw_request_id = first_text(row.get("model_call_id"), row.get("client_request_id"), row.get("server_request_id"), row.get("engine_request_id"), str(ordinal))
        request_id = f"{run_id}:{raw_request_id}"
        status = row.get("status")
        p2_eligible, p2_reason, p2_evidence = p2_eligibility(row, profile)
        p2_matched = row.get("match_status") == "exact"
        common = {
            "schema_version": "1.0",
            "request_attempt_id": request_id,
            "raw_request_attempt_id": raw_request_id,
            "experiment_id": context.get("experiment_id"),
            "trial_id": context.get("trial_id"),
            "batch_id": context.get("batch_id"),
            "configured_concurrency": context.get("configured_concurrency"),
            "task_id": context.get("task_id"),
            "run_id": run_id,
            "agent_id": row.get("agent_id"),
            "step_id": row.get("step_id"),
            "request_purpose": row.get("request_purpose") or "agent_primary",
            "parent_model_call_id": row.get("parent_model_call_id"),
            "request_ordinal": ordinal,
            "p1_event_id": None,
            "p1_span_id": row.get("span_id"),
            "trajectory_response_id": row.get("server_request_id"),
            "api_request_id": row.get("api_request_id") or row.get("server_request_id"),
            "engine_request_id": row.get("engine_request_id"),
            "alias_ids": aliases_json(row),
            "request_status": status,
            "error_type": None,
            "error_category": None,
            "error_message_hash": None,
            "prompt_tokens": row.get("prompt_tokens"),
            "cached_tokens": row.get("cached_tokens"),
            "uncached_prompt_tokens": row.get("uncached_prompt_tokens"),
            "engine_recomputed_tokens": row.get("engine_recomputed_tokens"),
            "recomputed_token_quality": row.get("recomputed_token_quality"),
            "generation_tokens": row.get("output_tokens"),
            "request_start": row.get("client_request_start_mono_ns") or row.get("server_received_mono_ns"),
            "queued": row.get("scheduler_enqueued_mono_ns"),
            "first_scheduled": row.get("first_scheduled_mono_ns"),
            "prefill_start": row.get("prefill_start_mono_ns"),
            "prefill_end": row.get("prefill_end_mono_ns"),
            "first_token": row.get("first_token_mono_ns"),
            "engine_finished": row.get("engine_finished_mono_ns"),
            "api_finished": row.get("server_finished_mono_ns") or row.get("client_response_received_mono_ns"),
            "queue_s": row.get("queue_s"),
            "scheduled_to_first_token_s": row.get("exact_prefill_s"),
            "prefill_s": row.get("exact_prefill_s"),
            "ttft_s": row.get("exact_ttft_s"),
            "tpot_s": row.get("exact_tpot_s"),
            "first_token_to_engine_finished_s": row.get("exact_decode_s"),
            "decode_s": row.get("exact_decode_s"),
            "engine_residence_s": row.get("engine_residence_s"),
            "engine_finished_to_api_finished_s": ns_delta_s(row.get("engine_finished_mono_ns"), row.get("server_finished_mono_ns")),
            "server_e2e_s": row.get("server_e2e_s"),
            "lifecycle_complete": row.get("lifecycle_status") == "complete",
            "p2_eligible": p2_eligible,
            "p2_eligibility_reason": p2_reason,
            "p2_eligibility_evidence": p2_evidence,
            "p2_matched": p2_matched,
            "match_status": row.get("match_status"),
            "lifecycle_status": row.get("lifecycle_status"),
            "p2_observation_scope": row.get("p2_observation_scope"),
            "p2_source_path": row.get("p2_source_path"),
            "p2_source_line": row.get("p2_source_line"),
            "p2_source_event_ids": row.get("p2_source_event_ids"),
            "p2_entity_id": p2_entity_id(row),
            "running_requests": row.get("running_requests"),
            "waiting_requests": row.get("waiting_requests"),
            "scheduler_batch_request_count": row.get("scheduled_batch_size"),
            "scheduled_prompt_tokens": row.get("scheduled_prompt_tokens"),
            "scheduled_decode_tokens": row.get("scheduled_decode_tokens"),
            "scheduler_iteration_count": row.get("scheduler_iteration_count"),
            "prefill_iteration_count": row.get("prefill_iteration_count"),
            "decode_iteration_count": row.get("decode_iteration_count"),
            "first_scheduler_iteration_id": row.get("first_scheduler_iteration_id"),
            "last_scheduler_iteration_id": row.get("last_scheduler_iteration_id"),
            "first_batch_enter": row.get("first_batch_enter_mono_ns"),
            "last_batch_exit": row.get("last_batch_exit_mono_ns"),
            "batch_residence_s_total": row.get("batch_residence_s_total"),
            "kv_allocated_blocks": row.get("kv_allocated_blocks"),
            "kv_cached_blocks": row.get("kv_cached_blocks"),
            "kv_cache_hit_blocks": row.get("kv_cache_hit_blocks"),
            "kv_released_blocks": row.get("kv_released_blocks"),
            "kv_evicted_blocks": row.get("kv_evicted_blocks"),
            "kv_residency_s": row.get("kv_residency_s"),
            "context_compression_status": row.get("context_compression_status"),
            "context_compression_strategy": row.get("context_compression_strategy"),
            "context_compression_applied": row.get("context_compression_applied"),
            "context_compression_original_tokens": row.get("context_compression_original_tokens"),
            "context_compression_compressed_tokens": row.get("context_compression_compressed_tokens"),
            "context_compression_removed_tokens": row.get("context_compression_removed_tokens"),
            "context_compression_target_tokens": row.get("context_compression_target_tokens"),
            "context_compression_target_achieved": row.get("context_compression_target_achieved"),
            "context_compression_duration_s": row.get("context_compression_duration_s"),
            "context_summary_status": row.get("context_summary_status"),
            "context_summary_request_id": row.get("context_summary_request_id"),
            "context_summary_model": row.get("context_summary_model"),
            "context_summary_input_tokens": row.get("context_summary_input_tokens"),
            "context_summary_output_tokens": row.get("context_summary_output_tokens"),
            "context_summary_cached_tokens": row.get("context_summary_cached_tokens"),
            "context_summary_uncached_prompt_tokens": row.get("context_summary_uncached_prompt_tokens"),
            "context_summary_duration_s": row.get("context_summary_duration_s"),
            "context_summary_token_quality": row.get("context_summary_token_quality"),
            "metric_quality_json": row.get("metric_quality_json"),
            "finish_reason": row.get("finish_reason"),
            "preemption_count": row.get("preemption_count"),
        }
        table_rows["requests"].append(common)
        table_rows["request_identity_audit"].append(request_identity_audit_row(common, row, context, run_id))
        table_rows["request_lifecycle"].append(
            {
                **{key: common[key] for key in ("schema_version", "request_attempt_id", "experiment_id", "trial_id", "batch_id", "run_id")},
                "server_e2e_s": common["server_e2e_s"],
                "queue_s": common["queue_s"],
                "scheduled_to_first_token_s": common["scheduled_to_first_token_s"],
                "prefill_s": common["prefill_s"],
                "ttft_s": common["ttft_s"],
                "tpot_s": common["tpot_s"],
                "first_token_to_engine_finished_s": common["first_token_to_engine_finished_s"],
                "decode_s": common["decode_s"],
                "engine_residence_s": common["engine_residence_s"],
                "engine_finished_to_api_finished_s": common["engine_finished_to_api_finished_s"],
                "residual_s": lifecycle_residual(common),
                "clock_domain_status": "verified_or_unavailable",
            }
        )
        table_rows["p1_request_attempts"].append(
            {
                "schema_version": "1.0",
                "request_attempt_id": request_id,
                "run_id": run_id,
                "task_id": context.get("task_id"),
                "status": status,
                "p2_eligible": p2_eligible,
                "p2_eligibility_reason": p2_reason,
                "p2_eligibility_evidence": p2_evidence,
                "p2_matched": p2_matched,
                "api_request_id": common["api_request_id"],
                "engine_request_id": common["engine_request_id"],
                "trajectory_response_id": common["trajectory_response_id"],
                "p1_span_id": common["p1_span_id"],
                "match_status": common["match_status"],
                "lifecycle_status": common["lifecycle_status"],
                "lifecycle_complete": common["lifecycle_complete"],
            }
        )
        if row.get("engine_request_id") or row.get("match_status") in {"exact", "p2_without_p1"}:
            table_rows["p2_requests"].append(
                {
                    "schema_version": "1.0",
                    "request_attempt_id": request_id,
                    "run_id": run_id,
                    "api_request_id": common["api_request_id"],
                    "engine_request_id": common["engine_request_id"],
                    "lifecycle_status": common["lifecycle_status"],
                    "match_status": common["match_status"],
                    "p2_observation_scope": common["p2_observation_scope"],
                    "p2_entity_id": common["p2_entity_id"],
                    "p2_source_path": common["p2_source_path"],
                    "prompt_tokens": common["prompt_tokens"],
                    "cached_tokens": common["cached_tokens"],
                    "uncached_prompt_tokens": common["uncached_prompt_tokens"],
                    "engine_recomputed_tokens": common["engine_recomputed_tokens"],
                    "generation_tokens": common["generation_tokens"],
                    "prefill_s": common["prefill_s"],
                    "ttft_s": common["ttft_s"],
                    "tpot_s": common["tpot_s"],
                    "engine_residence_s": common["engine_residence_s"],
                    "server_e2e_s": common["server_e2e_s"],
                }
            )
        for alias_type, alias_value in aliases(row).items():
            if alias_value:
                table_rows["request_id_aliases"].append(
                    {
                        "schema_version": "1.0",
                        "request_attempt_id": request_id,
                        "run_id": run_id,
                        "alias_type": alias_type,
                        "alias_value": alias_value,
                        "source": "canonical_requests",
                    }
                )


def append_tool_and_span_tables(
    table_rows: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
    run_id: str,
    span_rows: dict[str, list[dict[str, Any]]],
    tool_rows: list[dict[str, Any]],
) -> None:
    for span_type, rows in span_rows.items():
        for row in rows:
            table_rows["spans"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "run_id": run_id,
                    "task_id": context.get("task_id"),
                    "span_id": row.get("span_id"),
                    "span_type": span_type,
                    "step_id": row.get("step_id"),
                    "status": row.get("status"),
                    "start_mono_ns": row.get("start_mono_ns"),
                    "end_mono_ns": row.get("end_mono_ns"),
                    "duration_s": row.get("duration_s"),
                }
            )
            if row.get("step_id"):
                table_rows["steps"].append(
                    {
                        "schema_version": "1.0",
                        "experiment_id": context.get("experiment_id"),
                        "trial_id": context.get("trial_id"),
                        "batch_id": context.get("batch_id"),
                        "run_id": run_id,
                        "task_id": context.get("task_id"),
                        "step_id": row.get("step_id"),
                        "span_id": row.get("span_id"),
                        "span_type": span_type,
                        "start_mono_ns": row.get("start_mono_ns"),
                        "end_mono_ns": row.get("end_mono_ns"),
                    }
                )
    for row in tool_rows:
        normalized = {
            "schema_version": "1.0",
            "experiment_id": context.get("experiment_id"),
            "trial_id": context.get("trial_id"),
            "batch_id": context.get("batch_id"),
            "run_id": run_id,
            "task_id": context.get("task_id"),
            "agent_id": row.get("agent_id"),
            "step_id": row.get("step_id"),
            "tool_call_id": row.get("tool_call_id"),
            "tool_name": row.get("tool_name"),
            "tool_category": row.get("tool_category"),
            "executable": row.get("executable"),
            "command_hash": row.get("command_hash"),
            "status": row.get("status"),
            "returncode": row.get("returncode"),
            "timed_out": row.get("timed_out"),
            "duration_s": row.get("duration_s"),
            "stdout_bytes": row.get("stdout_bytes"),
            "stderr_bytes": row.get("stderr_bytes"),
            "output_bytes": row.get("output_bytes"),
            "stdout_sha256": row.get("stdout_sha256"),
            "stderr_sha256": row.get("stderr_sha256"),
            "output_sha256": row.get("output_sha256"),
            "error_fingerprint": row.get("error_fingerprint"),
            "code_diff_observed": row.get("code_diff_observed"),
            "code_diff_sha256": row.get("code_diff_sha256"),
            "code_diff_bytes": row.get("code_diff_bytes"),
            "code_diff_changed_file_count": row.get(
                "code_diff_changed_file_count"
            ),
            "test_passed_count": row.get("test_passed_count"),
            "test_failed_count": row.get("test_failed_count"),
            "test_error_count": row.get("test_error_count"),
            "test_skipped_count": row.get("test_skipped_count"),
            "quality": row.get("quality"),
            "source": row.get("source"),
        }
        table_rows["tool_calls"].append(normalized)
        if (
            str(row.get("status") or "").lower() in {"error", "failed"}
            or row.get("timed_out") is True
            or integer_or_none(row.get("returncode")) not in {None, 0}
        ):
            table_rows["error_events"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "run_id": run_id,
                    "task_id": context.get("task_id"),
                    "agent_id": row.get("agent_id"),
                    "step_id": row.get("step_id"),
                    "source_type": "tool_call",
                    "source_id": row.get("tool_call_id"),
                    "error_type": row.get("exception_type"),
                    "error_category": (
                        "timeout"
                        if row.get("timed_out") is True
                        else "nonzero_return"
                        if integer_or_none(row.get("returncode")) not in {None, 0}
                        else "tool_error"
                    ),
                    "error_fingerprint": row.get("error_fingerprint"),
                    "returncode": row.get("returncode"),
                    "evidence_source": row.get("source"),
                }
            )


def append_event_tables(
    table_rows: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
    run_id: str,
    events: list[dict[str, Any]],
    omitted_tables: set[str],
) -> None:
    include_event_inventory = "event_inventory" not in omitted_tables
    include_trajectories = "trajectories" not in omitted_tables
    include_errors = "error_events" not in omitted_tables
    if not include_event_inventory and not include_trajectories and not include_errors:
        return
    for index, event in enumerate(events, start=1):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if include_event_inventory:
            table_rows["event_inventory"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "run_id": run_id,
                    "row_index": index,
                    "event_id": event.get("event_id"),
                    "source": event.get("source"),
                    "event_type": event.get("event_type"),
                    "wall_time_ns": event.get("wall_time_ns"),
                    "monotonic_time_ns": event.get("monotonic_time_ns"),
                    "payload_keys": sorted(payload),
                }
            )
        if include_trajectories and (
            event.get("source") == "trajectory" or "trajectory" in str(event.get("event_type") or "")
        ):
            table_rows["trajectories"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "run_id": run_id,
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "source_path": event.get("_source_path"),
                    "source_line": event.get("_source_line"),
                    "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                }
            )
        if include_errors and (
            str(event.get("event_type") or "").endswith(".error")
            or str(event.get("event_type") or "")
            in {"error", "mini_swe.adapter.error"}
        ):
            table_rows["error_events"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "run_id": run_id,
                    "task_id": context.get("task_id"),
                    "agent_id": payload.get("agent_id"),
                    "step_id": payload.get("step_id"),
                    "source_type": event.get("source"),
                    "source_id": event.get("event_id"),
                    "error_type": first_payload_value(
                        payload,
                        "error_type",
                        "exception_type",
                    ),
                    "error_category": payload.get("where") or event.get("event_type"),
                    "error_fingerprint": first_payload_value(
                        payload,
                        "error_fingerprint",
                        "fingerprint",
                        "message_hash",
                    ),
                    "returncode": payload.get("returncode"),
                    "evidence_source": event.get("_source_path") or "events.jsonl",
                    "evidence_line": event.get("_source_line"),
                }
            )


def append_scheduler_and_resource_tables(
    table_rows: dict[str, list[dict[str, Any]]],
    context: dict[str, Any],
    run_id: str,
    events: list[dict[str, Any]],
    canonical: dict[str, list[dict[str, Any]]],
    omitted_tables: set[str],
) -> None:
    include_scheduler = "scheduler_iterations" not in omitted_tables
    include_request_batch = "request_batch_membership" not in omitted_tables
    include_kv = "kv_block_events" not in omitted_tables
    include_resources = "resource_samples" not in omitted_tables
    if not any((include_scheduler, include_request_batch, include_kv, include_resources)):
        return
    for row in canonical.get("system_samples", []):
        payload = parse_json(row.get("payload_json"))
        source = row.get("source")
        event_type = row.get("event_type")
        target_table = "scheduler_iterations" if "scheduler" in str(event_type) else "resource_samples"
        if target_table == "scheduler_iterations" and not include_scheduler:
            continue
        if target_table == "resource_samples" and not include_resources:
            continue
        base = {
            "schema_version": "1.0",
            "experiment_id": context.get("experiment_id"),
            "trial_id": context.get("trial_id"),
            "batch_id": context.get("batch_id"),
            "configured_concurrency": context.get("configured_concurrency"),
            "run_id": run_id,
            "event_id": row.get("event_id"),
            "source": source,
            "event_type": event_type,
            "sample_time": row.get("monotonic_time_ns") or row.get("wall_time_ns"),
            "payload_json": row.get("payload_json"),
        }
        if target_table == "scheduler_iterations":
            table_rows[target_table].append(
                {
                    **base,
                    "running_requests": first_payload_value(payload, "running_requests", "running_request_count"),
                    "waiting_requests": first_payload_value(payload, "waiting_requests", "waiting_request_count"),
                    "batch_request_count": first_payload_value(payload, "batch_request_count", "scheduled_request_count"),
                    "batch_sequence_count": first_payload_value(payload, "batch_sequence_count"),
                    "scheduled_token_count": first_payload_value(payload, "scheduled_token_count", "scheduled_tokens"),
                    "scheduled_token_budget": first_payload_value(payload, "scheduled_token_budget", "max_scheduled_tokens", "token_budget"),
                    "actual_generated_tokens_delta": first_payload_value(
                        payload,
                        "actual_generated_tokens_delta",
                        "generated_tokens_delta",
                        "generation_tokens_delta",
                        "output_tokens_delta",
                        "num_generation_tokens_delta",
                    ),
                    "prompt_tokens_processed_delta": first_payload_value(
                        payload,
                        "prompt_tokens_processed_delta",
                        "prompt_tokens_delta",
                        "prefill_tokens_delta",
                    ),
                }
            )
        else:
            table_rows[target_table].append(
                {
                    **base,
                    "sample_scope": row.get("observation_scope"),
                    "logical_device_id": first_payload_value(payload, "device_id", "logical_device_id"),
                    "physical_device_id": first_payload_value(payload, "physical_device_id"),
                    "device_id_source": first_payload_value(payload, "device_id_source"),
                    "mapping_confidence": first_payload_value(payload, "mapping_confidence"),
                    "cpu_percent": first_payload_value(payload, "cpu_percent", "process_cpu_percent"),
                    "rss_bytes": first_payload_value(payload, "rss_bytes", "resident_bytes"),
                    "aicore_util_pct": first_payload_value(payload, "aicore_util_pct", "ai_core_utilization_pct"),
                    "hbm_bytes": first_payload_value(payload, "hbm_bytes", "memory_used_bytes"),
                    "hbm_total_bytes": first_payload_value(payload, "hbm_total_bytes", "memory_total_bytes"),
                }
            )

    alias_map = {
        str(row.get("alias_value")): row.get("request_attempt_id")
        for row in table_rows.get("request_id_aliases", [])
        if row.get("run_id") == run_id and row.get("alias_value") not in {None, ""}
    }
    scheduler_by_id: dict[Any, dict[str, Any]] = {}
    membership_by_key: dict[tuple[Any, str], dict[str, Any]] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type in {
            "vllm.scheduler.iteration.scheduled",
            "vllm.scheduler.iteration.completed",
        }:
            iteration_id = payload.get("scheduler_iteration_id")
            target = scheduler_by_id.setdefault(
                iteration_id,
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "configured_concurrency": context.get("configured_concurrency"),
                    "run_id": run_id,
                    "scheduler_iteration_id": iteration_id,
                    "source": event.get("source"),
                    "event_type": "vllm.scheduler.iteration",
                    "event_id_scheduled": None,
                    "event_id_completed": None,
                    "observation_quality": "engine_execution_observed",
                },
            )
            update_present(
                target,
                {
                    "running_requests": first_payload_value(
                        payload,
                        "running_requests_after_schedule",
                        "running_requests",
                    ),
                    "waiting_requests": first_payload_value(
                        payload,
                        "waiting_requests_after_schedule",
                        "waiting_requests",
                    ),
                    "batch_request_count": payload.get("batch_request_count"),
                    "scheduled_token_count": first_payload_value(
                        payload,
                        "scheduled_tokens",
                        "scheduled_token_count",
                    ),
                    "scheduled_prefill_tokens": first_payload_value(
                        payload,
                        "scheduled_prefill_tokens",
                    ),
                    "scheduled_decode_tokens": first_payload_value(
                        payload,
                        "scheduled_decode_tokens",
                    ),
                    "prefill_request_count": payload.get("prefill_request_count"),
                    "decode_request_count": payload.get("decode_request_count"),
                    "mixed_prefill_decode_batch": payload.get(
                        "mixed_prefill_decode_batch"
                    ),
                    "schedule_start": payload.get("schedule_start_ns"),
                    "schedule_end": payload.get("schedule_end_ns"),
                    "scheduler_duration_s": ns_value_s(
                        payload.get("scheduler_duration_ns")
                    ),
                    "execution_completed": payload.get("execution_completed_ns"),
                    "execution_duration_s": ns_value_s(
                        payload.get("schedule_to_execution_complete_ns")
                    ),
                    "sample_time": first_payload_value(
                        payload,
                        "schedule_end_ns",
                        "execution_completed_ns",
                    )
                    or event.get("monotonic_time_ns"),
                    "payload_json": json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
            target[
                "event_id_completed"
                if event_type.endswith(".completed")
                else "event_id_scheduled"
            ] = event.get("event_id")
        elif event_type in {
            "vllm.request.scheduler_iteration",
            "vllm.request.scheduler_iteration_completed",
        }:
            raw_request_id = first_payload_value(
                payload,
                "model_call_id",
                "client_request_id",
                "agentmon_model_request_id",
                "engine_request_id",
                "vllm_request_id",
                "request_id",
            )
            key = (payload.get("scheduler_iteration_id"), str(raw_request_id or ""))
            target = membership_by_key.setdefault(
                key,
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "configured_concurrency": context.get("configured_concurrency"),
                    "run_id": run_id,
                    "task_id": context.get("task_id"),
                    "request_attempt_id": alias_map.get(str(raw_request_id)),
                    "raw_request_id": raw_request_id,
                    "scheduler_iteration_id": payload.get("scheduler_iteration_id"),
                    "observation_quality": "engine_execution_observed",
                    "event_id_scheduled": None,
                    "event_id_completed": None,
                },
            )
            update_present(
                target,
                {
                    "scheduler_batch_position": payload.get(
                        "scheduler_batch_position"
                    ),
                    "scheduler_request_kind": payload.get(
                        "scheduler_request_kind"
                    ),
                    "scheduler_phase": payload.get("scheduler_phase"),
                    "scheduled_token_count": payload.get("scheduled_token_count"),
                    "computed_tokens_before": payload.get(
                        "num_computed_tokens_before"
                    ),
                    "computed_tokens_after": payload.get(
                        "num_computed_tokens_after"
                    ),
                    "batch_request_count": payload.get("batch_request_count"),
                    "batch_prefill_request_count": payload.get(
                        "batch_prefill_request_count"
                    ),
                    "batch_decode_request_count": payload.get(
                        "batch_decode_request_count"
                    ),
                    "running_requests": payload.get(
                        "running_requests_after_schedule"
                    ),
                    "waiting_requests": payload.get(
                        "waiting_requests_after_schedule"
                    ),
                    "schedule_start": payload.get("schedule_start_ns"),
                    "schedule_end": payload.get("schedule_end_ns"),
                    "batch_enter": payload.get("batch_enter_ns"),
                    "batch_exit": payload.get("batch_exit_ns"),
                    "batch_residence_s": ns_value_s(
                        payload.get("batch_residence_ns")
                    ),
                    "mixed_prefill_decode_batch": payload.get(
                        "mixed_prefill_decode_batch"
                    ),
                    "chunked_prefill": payload.get("chunked_prefill"),
                }
            )
            target[
                "event_id_completed"
                if event_type.endswith("_completed")
                else "event_id_scheduled"
            ] = event.get("event_id")
        elif event_type == "vllm.kv.block_event" and include_kv:
            raw_request_id = first_payload_value(
                payload,
                "model_call_id",
                "client_request_id",
                "agentmon_model_request_id",
                "engine_request_id",
                "vllm_request_id",
                "request_id",
            )
            table_rows["kv_block_events"].append(
                {
                    "schema_version": "1.0",
                    "experiment_id": context.get("experiment_id"),
                    "trial_id": context.get("trial_id"),
                    "batch_id": context.get("batch_id"),
                    "configured_concurrency": context.get("configured_concurrency"),
                    "run_id": run_id,
                    "task_id": context.get("task_id"),
                    "request_attempt_id": alias_map.get(str(raw_request_id)),
                    "raw_request_id": raw_request_id,
                    "event_id": event.get("event_id"),
                    "sample_time": event.get("monotonic_time_ns"),
                    "operation": payload.get("operation"),
                    "allocated_block_count": payload.get("allocated_block_count"),
                    "cached_block_count": payload.get("cached_block_count"),
                    "cache_hit_block_count": payload.get("cache_hit_block_count"),
                    "freed_block_count": payload.get("freed_block_count"),
                    "evicted_block_count": payload.get("evicted_block_count"),
                    "kv_usage_before": payload.get("kv_usage_before"),
                    "kv_usage_after": payload.get("kv_usage_after"),
                    "quality": payload.get("kv_quality") or event.get("quality"),
                    "source_path": event.get("_source_path"),
                    "source_line": event.get("_source_line"),
                }
            )
    if include_scheduler:
        table_rows["scheduler_iterations"].extend(scheduler_by_id.values())
    if include_request_batch:
        table_rows["request_batch_membership"].extend(
            membership_by_key.values()
        )


def minimal_run_row(
    experiment_id: str,
    run_inventory_row: dict[str, Any],
    context: dict[str, Any],
    *,
    error: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "experiment_id": context.get("experiment_id") or experiment_id,
        "trial_id": context.get("trial_id"),
        "batch_id": context.get("batch_id"),
        "configured_concurrency": context.get("configured_concurrency"),
        "task_set": context.get("task_set"),
        "task_id": context.get("task_id"),
        "run_id": run_inventory_row.get("run_id"),
        "worker_id": context.get("worker_id"),
        "task_result": context.get("task_result"),
        "execution_success": False,
        "agent_exit_code": run_inventory_row.get("exit_code"),
        "observability_result": "FAIL",
        "publishable": False,
        "run_wall_s": ns_delta_s(run_inventory_row.get("workload_start_mono_ns"), run_inventory_row.get("workload_end_mono_ns")),
        "model_request_attempt_count": 0,
        "model_request_success_count": 0,
        "model_request_error_count": 0,
        "tool_call_count": 0,
        "prompt_tokens_sum": None,
        "generation_tokens_sum": None,
        "model_union_s": None,
        "tool_union_s": None,
        "model_tool_overlap_s": None,
        "idle_or_transition_s": None,
        "unexplained_time_s": None,
        "p2_match_rate": None,
        "p2_lifecycle_rate": None,
        "normalization_error": error,
        "source_run_dir": run_inventory_row.get("run_dir"),
    }


def lifecycle_residual(row: dict[str, Any]) -> float | None:
    server = float_or_none(row.get("server_e2e_s"))
    parts = [
        float_or_none(row.get("queue_s")),
        float_or_none(row.get("scheduled_to_first_token_s")),
        float_or_none(row.get("first_token_to_engine_finished_s")),
        float_or_none(row.get("engine_finished_to_api_finished_s")),
    ]
    if server is None or any(item is None for item in parts):
        return None
    return server - sum(item for item in parts if item is not None)


def ns_interval(start: Any, end: Any) -> tuple[float, float] | None:
    start_int = integer_or_none(start)
    end_int = integer_or_none(end)
    if start_int is None or end_int is None or end_int <= start_int:
        return None
    return start_int / 1_000_000_000, end_int / 1_000_000_000


def ns_delta_s(start: Any, end: Any) -> float | None:
    interval = ns_interval(start, end)
    if interval is None:
        return None
    return interval[1] - interval[0]


def ns_value_s(value: Any) -> float | None:
    parsed = integer_or_none(value)
    return parsed / 1_000_000_000 if parsed is not None else None


def sum_int(values: Any) -> int | None:
    total = 0
    count = 0
    for value in values:
        parsed = integer_or_none(value)
        if parsed is None:
            continue
        total += parsed
        count += 1
    return total if count else None


def aliases(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_call_id": row.get("model_call_id"),
        "client_request_id": row.get("client_request_id"),
        "server_request_id": row.get("server_request_id"),
        "api_request_id": row.get("api_request_id"),
        "engine_request_id": row.get("engine_request_id"),
        "vllm_request_id": row.get("vllm_request_id"),
    }


def aliases_json(row: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in aliases(row).items() if value}, sort_keys=True, ensure_ascii=False)


def p2_eligibility(row: dict[str, Any], profile: SchemaProfile | None = None) -> tuple[bool, str, str]:
    evidence: list[str] = []
    fields = profile.p2_eligibility_fields if profile else ()
    for field in fields:
        if row.get(field) not in {None, ""}:
            evidence.append(field)
    if row.get("match_status") in {"exact", "p2_without_p1"}:
        evidence.append(f"match_status:{row.get('match_status')}")
    status = str(row.get("status") or "").lower()
    if evidence:
        return True, "HAS_REQUEST_ID_OR_SERVICE_EVIDENCE", ",".join(evidence)
    if status in {"error", "failed"}:
        return False, "REQUEST_FAILED_BEFORE_P2_IDENTITY", ""
    return False, "NO_P2_ELIGIBILITY_EVIDENCE", ""


def request_identity_audit_row(
    common: dict[str, Any],
    canonical: dict[str, Any],
    context: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    status = str(common.get("request_status") or "").lower()
    match_status = str(common.get("match_status") or "")
    eligible = bool(common.get("p2_eligible"))
    complete = bool(common.get("lifecycle_complete"))
    if status in {"error", "failed"}:
        classification = "ERROR_AFTER_API_ACCEPTED" if eligible else "ERROR_BEFORE_API"
    elif match_status == "multiple":
        classification = "P2_ATTRIBUTION_FAILURE"
    elif match_status == "p2_without_p1":
        classification = "P1_CAPTURE_FAILURE"
    elif eligible and match_status == "p1_without_p2":
        classification = "P2_CAPTURE_FAILURE"
    elif match_status == "exact" and complete:
        classification = "SUCCESS_COMPLETE"
    elif match_status == "exact":
        classification = "P2_CAPTURE_FAILURE"
    else:
        classification = "UNRESOLVED"
    denominator = eligible
    numerator = eligible and match_status == "exact" and complete
    return {
        "schema_version": "1.0",
        "experiment_id": common.get("experiment_id"),
        "trial_id": common.get("trial_id"),
        "batch_id": common.get("batch_id"),
        "configured_concurrency": common.get("configured_concurrency"),
        "task_id": common.get("task_id"),
        "run_id": run_id,
        "request_attempt_id": common.get("request_attempt_id"),
        "request_ordinal": common.get("request_ordinal"),
        "trajectory_record_id": None,
        "trajectory_response_id": common.get("trajectory_response_id"),
        "p1_span_id": common.get("p1_span_id"),
        "p1_start_event_id": None,
        "p1_end_event_id": None,
        "p1_error_event_id": None,
        "tool_span_id": None,
        "api_request_id": common.get("api_request_id"),
        "engine_request_id": common.get("engine_request_id"),
        "alias_ids": common.get("alias_ids"),
        "request_status": common.get("request_status"),
        "error_category": common.get("error_category"),
        "api_boundary_crossed": eligible,
        "p2_eligible": eligible,
        "p2_received_exists": canonical.get("server_received_mono_ns") is not None,
        "p2_first_scheduled_exists": canonical.get("first_scheduled_mono_ns") is not None,
        "p2_first_token_exists": canonical.get("first_token_mono_ns") is not None,
        "p2_engine_finished_exists": canonical.get("engine_finished_mono_ns") is not None,
        "p2_finished_exists": canonical.get("server_finished_mono_ns") is not None,
        "p2_present": match_status in {"exact", "multiple", "p2_without_p1"},
        "p2_observation_scope": canonical.get("p2_observation_scope"),
        "p2_lifecycle_complete": complete,
        "publishability_denominator_member": denominator,
        "publishability_numerator_member": numerator,
        "produces_gap": denominator and not numerator,
        "causes_run_gate_failure": denominator and not numerator,
        "classification": classification,
        "evidence_file": canonical.get("p2_source_path"),
        "evidence_line": canonical.get("p2_source_line"),
        "confidence": "high" if classification in {"SUCCESS_COMPLETE", "ERROR_BEFORE_API", "ERROR_AFTER_API_ACCEPTED"} else "medium",
    }


def append_request_parse_audit(
    tables: dict[str, list[dict[str, Any]]],
    *,
    run_row: dict[str, Any],
    context: dict[str, Any],
    event_log: dict[str, Any],
    events: list[dict[str, Any]],
    profile: SchemaProfile,
    profile_audit: dict[str, Any],
    canonical: dict[str, list[dict[str, Any]]],
    duplicate_events: list[dict[str, Any]],
) -> None:
    event_types = [str(event.get("event_type") or "") for event in events]
    raw_model = sum(event_type in profile.model_request_event_types for event_type in event_types)
    raw_p1 = sum(event_type in profile.p1_event_types for event_type in event_types)
    raw_p2 = sum(is_event_type(event_type, profile.p2_event_types, profile.p2_event_prefixes) for event_type in event_types)
    unsupported = sum(
        1
        for event_type in event_types
        if "request" in event_type.lower()
        and not is_event_type(event_type, profile.model_request_event_types + profile.p1_event_types + profile.p2_event_types, profile.p2_event_prefixes)
    )
    requests = canonical.get("requests", [])
    parsed_p2 = sum(row.get("match_status") in {"exact", "multiple", "p2_without_p1"} for row in requests)
    unmatched_p1 = sum(row.get("match_status") == "p1_without_p2" for row in requests)
    unmatched_p2 = sum(row.get("match_status") == "p2_without_p1" for row in requests)
    incomplete = sum(row.get("lifecycle_status") not in {"complete", None} for row in requests)
    malformed = int(event_log.get("invalid_lines") or 0)
    parsed = len(requests)
    duplicate_candidate_ids = request_candidate_identities(duplicate_events, profile)
    unsupported_events = [
        event
        for event in events
        if "request" in str(event.get("event_type") or "").lower()
        and not is_event_type(
            str(event.get("event_type") or ""),
            profile.model_request_event_types + profile.p1_event_types + profile.p2_event_types,
            profile.p2_event_prefixes,
        )
    ]
    unsupported_candidate_ids = request_candidate_identities(unsupported_events, profile, include_event_id=True)
    duplicate_candidates = len(duplicate_candidate_ids)
    unsupported_candidates = len(unsupported_candidate_ids)
    # Invalid JSON cannot be typed safely. Conservatively retain each invalid
    # line as a malformed unresolved candidate instead of silently losing it.
    raw_candidates = parsed + duplicate_candidates + unsupported_candidates + malformed
    reasons = ["PARSED"] if parsed else []
    if duplicate_candidates:
        reasons.append("DUPLICATE_WITHIN_RUN")
    if unsupported:
        reasons.append("UNSUPPORTED_EVENT_TYPE")
    if malformed:
        reasons.append("MALFORMED_EVENT")
    if unmatched_p1:
        reasons.append("P1_UNMATCHED")
    if unmatched_p2:
        reasons.append("P2_UNMATCHED")
    if incomplete:
        reasons.append("INCOMPLETE_REQUEST")
    run_id = str(run_row.get("run_id") or "")
    audit = {
        "schema_version": "1.0",
        "experiment_id": context.get("experiment_id"),
        "trial_id": context.get("trial_id"),
        "batch_id": context.get("batch_id"),
        "run_id": run_id,
        "input_events_file": event_log.get("path") or run_row.get("events_path"),
        "profile_selected": profile.name,
        "profile_detection_status": profile_audit.get("status"),
        "profile_detection_signals": profile_audit.get("detection_signals"),
        "profile_confidence": profile_audit.get("confidence"),
        "input_schema_versions": profile_audit.get("schema_versions"),
        "raw_events_count": int(event_log.get("total_lines") or len(events)),
        "raw_model_request_events": raw_model,
        "raw_request_attempt_candidates": raw_candidates,
        "raw_p1_events": raw_p1,
        "raw_p2_events": raw_p2,
        "parsed_request_attempts": parsed,
        "parsed_p2_requests": parsed_p2,
        "duplicate_event_count": len(duplicate_events),
        "duplicate_candidates": duplicate_candidates,
        "duplicates_removed": duplicate_candidates,
        "duplicate_cross_run_prevented": 0,
        "filtered_events": 0,
        "unsupported_events": unsupported,
        "unsupported_request_candidates": unsupported_candidates,
        "parse_errors": malformed,
        "unmatched_p1": unmatched_p1,
        "unmatched_p2": unmatched_p2,
        "incomplete_requests": incomplete,
        "unknown_loss_count": 0,
        "final_status": "PASS" if profile_audit.get("status") == "PASS" and malformed == 0 else "PARTIAL",
        "reason_codes": json.dumps(sorted(set(reasons)), ensure_ascii=False),
    }
    tables["request_parse_audit"].append(audit)
    for reason, count in (
        ("PARSED", parsed),
        ("DUPLICATE_WITHIN_RUN", duplicate_candidates),
        ("FILTERED_BY_PROFILE", 0),
        ("UNSUPPORTED_EVENT_TYPE", unsupported_candidates),
        ("MALFORMED_EVENT", malformed),
        ("UNKNOWN", 0),
    ):
        tables["request_parse_conservation"].append(
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "raw_request_attempt_candidates": raw_candidates,
                "reason_code": reason,
                "count": count,
                "equation_status": "PASS",
            }
        )


def deduplicate_events(events: list[dict[str, Any]], *, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicates: list[dict[str, Any]] = []
    for event in events:
        event_id = event.get("event_id")
        if event_id in {None, ""}:
            output.append(event)
            continue
        key = (str(event.get("run_id") or run_id), str(event_id))
        if key in seen:
            duplicates.append(event)
            continue
        seen.add(key)
        output.append(event)
    return output, duplicates


def request_candidate_identities(
    events: list[dict[str, Any]],
    profile: SchemaProfile,
    *,
    include_event_id: bool = False,
) -> set[str]:
    identities: set[str] = set()
    keys = tuple(dict.fromkeys(profile.request_id_keys + ("span_id", "model_call_id", "request_id")))
    for event in events:
        values = values_for_keys(event, keys)
        if values:
            identities.add(values[0])
        elif include_event_id and event.get("event_id") not in {None, ""}:
            identities.add(f"event:{event['event_id']}")
    return identities


def add_cross_run_identity_audit(tables: dict[str, list[dict[str, Any]]]) -> None:
    runs_by_alias: dict[tuple[str, str], set[str]] = {}
    for row in tables.get("request_id_aliases", []):
        key = (str(row.get("alias_type") or ""), str(row.get("alias_value") or ""))
        runs_by_alias.setdefault(key, set()).add(str(row.get("run_id") or ""))
    shared_aliases = {key for key, run_ids in runs_by_alias.items() if len(run_ids) > 1}
    shared_runs = {run_id for key, run_ids in runs_by_alias.items() if key in shared_aliases for run_id in run_ids}
    ambiguous_attempts = {
        str(row.get("request_attempt_id"))
        for row in tables.get("request_id_aliases", [])
        if (str(row.get("alias_type") or ""), str(row.get("alias_value") or "")) in shared_aliases
    }
    attribution_failures_by_run: dict[str, int] = {}
    ambiguous_global_attempts: set[str] = set()
    for row in tables.get("requests", []):
        attempt_id = str(row.get("request_attempt_id") or "")
        if attempt_id not in ambiguous_attempts:
            continue
        if row.get("p2_observation_scope") != "GLOBAL_UNSCOPED" or not row.get("p2_matched"):
            continue
        row["p2_matched"] = False
        row["match_status"] = "ambiguous_cross_run"
        row["lifecycle_complete"] = False
        row["lifecycle_status"] = "ambiguous_attribution"
        row["p2_attribution_status"] = "AMBIGUOUS_CROSS_RUN_ALIAS"
        ambiguous_global_attempts.add(attempt_id)
        for field in (
            "queued", "first_scheduled", "first_token", "engine_finished", "api_finished",
            "queue_s", "scheduled_to_first_token_s", "first_token_to_engine_finished_s",
            "engine_finished_to_api_finished_s", "server_e2e_s", "running_requests",
            "waiting_requests", "scheduler_batch_request_count",
        ):
            row[field] = None
        run_id = str(row.get("run_id") or "")
        attribution_failures_by_run[run_id] = attribution_failures_by_run.get(run_id, 0) + 1
    for table_name in ("p1_request_attempts", "request_identity_audit"):
        for row in tables.get(table_name, []):
            if str(row.get("request_attempt_id") or "") not in ambiguous_global_attempts:
                continue
            if table_name == "p1_request_attempts" and row.get("p2_matched"):
                row["p2_matched"] = False
                row["match_status"] = "ambiguous_cross_run"
                row["lifecycle_complete"] = False
                row["lifecycle_status"] = "ambiguous_attribution"
            elif table_name == "request_identity_audit" and row.get("p2_observation_scope") == "GLOBAL_UNSCOPED":
                row["p2_lifecycle_complete"] = False
                row["publishability_numerator_member"] = False
                row["produces_gap"] = bool(row.get("publishability_denominator_member"))
                row["causes_run_gate_failure"] = bool(row.get("publishability_denominator_member"))
                row["classification"] = "P2_ATTRIBUTION_FAILURE"
                row["confidence"] = "high"
    for row in tables.get("request_lifecycle", []):
        if str(row.get("request_attempt_id") or "") not in ambiguous_global_attempts:
            continue
        for field in (
            "server_e2e_s",
            "queue_s",
            "scheduled_to_first_token_s",
            "first_token_to_engine_finished_s",
            "engine_finished_to_api_finished_s",
            "residual_s",
        ):
            row[field] = None
        row["clock_domain_status"] = "ambiguous_p2_attribution"
        row["p2_attribution_status"] = "AMBIGUOUS_CROSS_RUN_ALIAS"
    deduplicated_p2: list[dict[str, Any]] = []
    seen_ambiguous_entities: set[str] = set()
    for row in tables.get("p2_requests", []):
        if str(row.get("request_attempt_id") or "") not in ambiguous_global_attempts or row.get("p2_observation_scope") != "GLOBAL_UNSCOPED":
            deduplicated_p2.append(row)
            continue
        entity_id = str(row.get("p2_entity_id") or f"{row.get('api_request_id')}:{row.get('engine_request_id')}")
        if entity_id in seen_ambiguous_entities:
            continue
        seen_ambiguous_entities.add(entity_id)
        deduplicated_p2.append({**row, "run_id": None, "request_attempt_id": None, "match_status": "ambiguous_cross_run", "attribution_status": "AMBIGUOUS_CROSS_RUN_ALIAS"})
    tables["p2_requests"] = deduplicated_p2
    for row in tables.get("request_parse_audit", []):
        if str(row.get("run_id")) in shared_runs:
            row["duplicate_cross_run_prevented"] = 1
            reasons = set(json.loads(str(row.get("reason_codes") or "[]")))
            reasons.add("DUPLICATE_CROSS_RUN_PREVENTED")
            failures = attribution_failures_by_run.get(str(row.get("run_id") or ""), 0)
            if failures:
                row["p2_attribution_failures"] = failures
                row["parsed_p2_requests"] = max(0, int(row.get("parsed_p2_requests") or 0) - failures)
                row["final_status"] = "PARTIAL"
                reasons.add("P2_ATTRIBUTION_FAILURE")
            row["reason_codes"] = json.dumps(sorted(reasons), ensure_ascii=False)
    refresh_run_p2_coverage(tables)


def refresh_run_p2_coverage(tables: dict[str, list[dict[str, Any]]]) -> None:
    requests_by_run: dict[str, list[dict[str, Any]]] = {}
    for row in tables.get("requests", []):
        requests_by_run.setdefault(str(row.get("run_id") or ""), []).append(row)
    for run in tables.get("runs", []):
        requests = requests_by_run.get(str(run.get("run_id") or ""), [])
        eligible = [row for row in requests if row.get("p2_eligible") is True]
        matched = [row for row in eligible if row.get("p2_matched") is True]
        complete = [row for row in matched if row.get("lifecycle_complete") is True]
        run["p2_match_rate"] = len(matched) / len(eligible) if eligible else None
        run["p2_lifecycle_rate"] = len(complete) / len(matched) if matched else None


def p2_entity_id(row: dict[str, Any]) -> str | None:
    source = row.get("p2_source_path")
    event_ids = row.get("p2_source_event_ids")
    api_id = row.get("api_request_id") or row.get("server_request_id")
    engine_id = row.get("engine_request_id") or row.get("vllm_request_id")
    if all(value in {None, "", "[]"} for value in (source, event_ids, api_id, engine_id)):
        return None
    return json.dumps([source, event_ids, api_id, engine_id], ensure_ascii=False, separators=(",", ":"))


def request_audit_summary(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    audits = tables.get("request_parse_audit", [])
    unknown = sum(int(row.get("unknown_loss_count") or 0) for row in audits)
    return {
        "schema_version": "1.0",
        "run_count": len(audits),
        "raw_request_attempt_candidates": sum(int(row.get("raw_request_attempt_candidates") or 0) for row in audits),
        "parsed_request_attempts": sum(int(row.get("parsed_request_attempts") or 0) for row in audits),
        "parsed_p2_requests": sum(int(row.get("parsed_p2_requests") or 0) for row in audits),
        "duplicates_removed": sum(int(row.get("duplicates_removed") or 0) for row in audits),
        "unsupported_events": sum(int(row.get("unsupported_events") or 0) for row in audits),
        "unsupported_request_candidates": sum(int(row.get("unsupported_request_candidates") or 0) for row in audits),
        "parse_errors": sum(int(row.get("parse_errors") or 0) for row in audits),
        "unknown_loss_count": unknown,
        "request_conservation_status": "PASS" if audits and unknown == 0 else "PARTIAL" if not audits else "FAIL",
    }


def first_text(*values: Any) -> str | None:
    for value in values:
        if value not in {None, ""}:
            return str(value)
    return None


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in {None, ""}:
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "passed", "success"}


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_payload_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def update_present(target: dict[str, Any], values: dict[str, Any]) -> None:
    target.update({key: value for key, value in values.items() if value is not None})
