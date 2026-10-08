from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.io import (
    float_or_none,
    integer_or_none,
    read_bundle_table,
    read_json,
    write_markdown,
    write_table,
    write_table_manifest,
)
from agentmon.analysis.normalize import NormalizedDataset
from agentmon.analysis.statistics import concurrency_series, iqr, mean, median, percentile, safe_div, time_weighted_summary


ANALYSIS_TABLES = (
    "throughput_summary",
    "throughput_by_trial",
    "steady_state_throughput",
    "concurrency_timeseries",
    "concurrency_summary",
    "request_stage_summary",
    "token_normalized_performance",
    "token_timeseries",
    "workload_service_decomposition",
    "task_paired_analysis",
    "task_fixed_effects",
    "workload_drift",
    "configured_concurrency_decay",
    "effective_capacity_drain",
    "completion_tail",
    "tail_task_audit",
    "pipeline_summary",
    "pipeline_phase_summary",
    "pipeline_conservation",
    "worker_utilization",
    "agentmon_overhead",
    "scheduler_summary",
    "engine_state_classification",
    "scheduler_threshold_sensitivity",
    "batching_efficiency",
    "kv_analysis",
    "resource_summary",
    "resource_timeseries",
    "npu_mapping_evidence",
    "npu_balance",
    "p1_request_attempts",
    "p1_capture_audit",
    "p1_capture_summary",
    "p2_match_audit",
    "publishability_gap_mapping",
)


def analyze_bundle(
    *,
    bundle_root: str | Path,
    cfg: AnalysisConfig,
    normalized: NormalizedDataset | None = None,
    modules: set[str] | None = None,
    output_format: str = "parquet",
    no_figures: bool = False,
    stage_context: Any | None = None,
    terminal_stage: str | None = None,
    stop_callback: Any | None = None,
    cached_stages: set[str] | None = None,
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    tables = normalized.tables if normalized else load_analysis_tables(bundle)
    selected = modules or {
        "throughput",
        "concurrency",
        "lifecycle",
        "workload",
        "tail",
        "pipeline",
        "scheduler",
        "resource",
        "p1",
        "p2",
    }
    outputs: dict[str, list[dict[str, Any]]] = {name: [] for name in ANALYSIS_TABLES}
    module_specs = (
        ("throughput", "throughput_analysis", analyze_throughput),
        ("concurrency", "concurrency_analysis", analyze_concurrency),
        ("lifecycle", "lifecycle_analysis", analyze_request_lifecycle),
        ("workload", "workload_analysis", analyze_workload),
        ("tail", "tail_analysis", analyze_tail),
        ("pipeline", "pipeline_analysis", analyze_pipeline),
        ("scheduler", "scheduler_analysis", analyze_scheduler),
        ("resource", "resource_analysis", analyze_resources),
    )
    module_status: dict[str, str] = {}
    module_failures: list[dict[str, str]] = []
    terminal_stage_completed: str | None = None
    generated_tables: set[str] = set()
    cached = cached_stages or set()
    prior_manifest = read_json(bundle / "03_analysis" / "analysis_manifest.json", default={})
    for module, stage_name, analyzer in module_specs:
        if module not in selected:
            continue
        expected_tables = stage_output_tables(stage_name)
        if stage_name in cached and all(first_existing_analysis_table(bundle, name) for name in expected_tables):
            for name in expected_tables:
                outputs[name] = read_bundle_table(bundle, "03_analysis", name)
            module_status[module] = "CACHED"
            if stage_context:
                stage_context.start_stage(stage_name)
                stage_context.finish_stage(
                    stage_name,
                    status="CACHED",
                    row_counts={name: len(outputs[name]) for name in expected_tables},
                    cache_hit=True,
                )
            if terminal_stage == stage_name:
                terminal_stage_completed = stage_name
                break
            continue
        if stage_context:
            stage_context.start_stage(stage_name)
        try:
            module_outputs = analyzer(tables, cfg)
            outputs.update(module_outputs)
            generated_tables.update(module_outputs)
        except Exception as exc:  # noqa: BLE001 - isolate independent analysis modules
            module_status[module] = "FAIL"
            module_failures.append({"module": module, "exception_type": type(exc).__name__, "exception_message": str(exc)})
            if stage_context:
                stage_context.fail_stage(stage_name, exc)
            continue
        module_status[module] = "PASS"
        if stage_context:
            stage_context.finish_stage(stage_name, status="PASS", row_counts={name: len(rows) for name, rows in module_outputs.items()})
        if terminal_stage == stage_name:
            terminal_stage_completed = stage_name
            break
    if terminal_stage_completed is None and selected & {"p1", "p2"}:
        try:
            module_outputs: dict[str, list[dict[str, Any]]] = {}
            if "p1" in selected:
                module_outputs.update(analyze_p1_capture(tables, cfg))
            if "p2" in selected:
                module_outputs.update(analyze_p2(tables, cfg))
            outputs.update(module_outputs)
            generated_tables.update(module_outputs)
        except Exception as exc:  # noqa: BLE001
            module_status["p1_p2"] = "FAIL"
            module_failures.append({"module": "p1_p2", "exception_type": type(exc).__name__, "exception_message": str(exc)})
        else:
            module_status["p1_p2"] = "PASS"

    analysis_dir = bundle / "03_analysis"
    artifacts: dict[str, dict[str, Any]] = {}
    for name in ANALYSIS_TABLES:
        if name in generated_tables or not first_existing_analysis_table(bundle, name):
            artifacts[name] = write_table(analysis_dir, name, outputs.get(name, []), output_format=output_format)
        else:
            prior = prior_manifest.get("tables", {}).get(name, {}) if isinstance(prior_manifest, dict) else {}
            artifacts[name] = dict(prior) if isinstance(prior, dict) else {}
            if not artifacts[name].get("path"):
                existing = first_existing_analysis_table(bundle, name)
                artifacts[name] = {
                    "format": "parquet" if existing and existing.suffix == ".parquet" else "csv.gz" if existing and existing.name.endswith(".csv.gz") else "csv",
                    "path": str(existing),
                    "row_count": len(read_bundle_table(bundle, "03_analysis", name)),
                }
    write_table_manifest(analysis_dir / "analysis_manifest.json", artifacts)
    if stage_context:
        for _, stage_name, _ in module_specs:
            files = [str(artifacts[name].get("path")) for name in stage_output_tables(stage_name) if artifacts.get(name, {}).get("path")]
            if files and stage_context.records.get(stage_name) and stage_context.records[stage_name].status in {"PASS", "PARTIAL", "CACHED"}:
                stage_context.attach_stage_outputs(stage_name, files)
    write_methodology_docs(analysis_dir)
    if not no_figures:
        maybe_write_figures(bundle, outputs)
    if terminal_stage_completed and stop_callback:
        stop_callback(terminal_stage_completed)
    return {
        "schema_version": "1.0",
        "analysis_modules": sorted(selected),
        "artifacts": artifacts,
        "table_row_counts": {name: len(outputs.get(name, [])) for name in ANALYSIS_TABLES},
        "module_status": module_status,
        "module_failures": module_failures,
        "status": "FAIL" if module_failures else "PASS",
    }


def stage_output_tables(stage_name: str) -> tuple[str, ...]:
    return {
        "throughput_analysis": ("throughput_summary", "throughput_by_trial", "steady_state_throughput"),
        "concurrency_analysis": ("concurrency_timeseries", "concurrency_summary"),
        "lifecycle_analysis": ("request_stage_summary", "token_normalized_performance"),
        "workload_analysis": ("workload_service_decomposition", "task_paired_analysis", "task_fixed_effects", "workload_drift"),
        "tail_analysis": ("configured_concurrency_decay", "effective_capacity_drain", "completion_tail", "tail_task_audit"),
        "pipeline_analysis": ("pipeline_summary", "pipeline_phase_summary", "pipeline_conservation", "worker_utilization", "agentmon_overhead"),
        "scheduler_analysis": ("scheduler_summary", "engine_state_classification", "scheduler_threshold_sensitivity", "batching_efficiency", "kv_analysis", "token_timeseries"),
        "resource_analysis": ("resource_summary", "resource_timeseries", "npu_mapping_evidence", "npu_balance"),
    }.get(stage_name, ())


def first_existing_analysis_table(bundle: Path, name: str) -> Path | None:
    for suffix in (".parquet", ".csv.gz", ".csv"):
        candidate = bundle / "03_analysis" / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_analysis_tables(bundle: Path) -> dict[str, list[dict[str, Any]]]:
    names = (
        "experiments",
        "trials",
        "batches",
        "runs",
        "requests",
        "request_lifecycle",
        "worker_intervals",
        "scheduler_iterations",
        "resource_samples",
        "trajectories",
        "p1_request_attempts",
        "p2_requests",
        "task_results",
    )
    return {name: read_bundle_table(bundle, "01_normalized", name) for name in names}


def analyze_throughput(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for run in tables.get("runs", []):
        grouped[(run.get("trial_id"), run.get("batch_id"), run.get("configured_concurrency"))].append(run)
    batch_by_key = {
        (row.get("trial_id"), row.get("batch_id"), row.get("configured_concurrency")): row
        for row in tables.get("batches", [])
    }
    scheduler_by_key: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("scheduler_iterations", []):
        scheduler_by_key[(row.get("trial_id"), row.get("batch_id"), row.get("configured_concurrency"))].append(row)
    summary: list[dict[str, Any]] = []
    by_trial: list[dict[str, Any]] = []
    steady: list[dict[str, Any]] = []
    for key, runs in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        trial_id, batch_id, concurrency = key
        batch = batch_by_key.get(key, {})
        phase_timings = parse_json_value(batch.get("phase_timings"), {})
        window = float_or_none(phase_timings.get("workload_phase_s")) or workload_window_s(runs)
        pipeline_window = float_or_none(phase_timings.get("pipeline_wall_s")) or float_or_none(batch.get("batch_wall_s")) or window
        task_count = len(runs)
        success_count = sum(1 for run in runs if truthy(run.get("execution_success")))
        publishable_count = sum(1 for run in runs if truthy(run.get("publishable")))
        request_count = sum(integer_or_none(run.get("model_request_attempt_count")) or 0 for run in runs)
        successful_requests = sum(integer_or_none(run.get("model_request_success_count")) or 0 for run in runs)
        prompt_tokens = sum(integer_or_none(run.get("prompt_tokens_sum")) or 0 for run in runs)
        generation_tokens = sum(integer_or_none(run.get("generation_tokens_sum")) or 0 for run in runs)
        row = {
            "schema_version": "1.0",
            "trial_id": trial_id,
            "batch_id": batch_id,
            "configured_concurrency": concurrency,
            "window_s": window,
            "task_count": task_count,
            "successful_task_count": success_count,
            "publishable_task_count": publishable_count,
            "request_count": request_count,
            "successful_request_count": successful_requests,
            "prompt_tokens": prompt_tokens,
            "generation_tokens": generation_tokens,
            "workload_window_s": window,
            "pipeline_window_s": pipeline_window,
            "task_throughput_per_hour": safe_div(task_count * 3600, window),
            "workload_task_throughput": safe_div(task_count, window),
            "pipeline_task_throughput": safe_div(task_count, pipeline_window),
            "successful_task_throughput_per_hour": safe_div(success_count * 3600, window),
            "successful_workload_task_throughput": safe_div(success_count, window),
            "publishable_task_throughput_per_hour": safe_div(publishable_count * 3600, window),
            "publishable_pipeline_task_throughput": safe_div(publishable_count, pipeline_window),
            "request_throughput_per_second": safe_div(request_count, window),
            "successful_request_throughput_per_second": safe_div(successful_requests, window),
            "whole_workload_generation_tok_s": safe_div(generation_tokens, window),
            "prompt_token_throughput": safe_div(prompt_tokens, window),
            "total_token_throughput": safe_div(prompt_tokens + generation_tokens, window),
            "scheduled_token_rate": None,
            "status": "PASS" if window and task_count else "PARTIAL",
        }
        summary.append(row)
        by_trial.append(dict(row))
        steady.append(steady_state_token_row(row, scheduler_by_key.get(key, [])))
    return {"throughput_summary": summary, "throughput_by_trial": by_trial, "steady_state_throughput": steady}


def analyze_concurrency(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    requests = tables.get("requests", [])
    runs = tables.get("runs", [])
    series_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    groups = group_by_batch(runs)
    for key, group_runs in groups.items():
        trial_id, batch_id, concurrency = key
        run_intervals = [(ns_to_s(run.get("workload_start")), ns_to_s(run.get("workload_finish"))) for run in group_runs]
        run_intervals = [(start, end) for start, end in run_intervals if start is not None and end is not None and end > start]
        model_intervals = []
        for req in requests:
            if req.get("trial_id") == trial_id and req.get("batch_id") == batch_id:
                start = ns_to_s(req.get("request_start"))
                end = ns_to_s(req.get("api_finished"))
                if start is not None and end is not None and end > start:
                    model_intervals.append((start, end))
        for metric, intervals in (("active_agent_runs", run_intervals), ("active_model_spans", model_intervals)):
            series = concurrency_series(intervals)
            for item in series:
                series_rows.append(
                    {
                        "trial_id": trial_id,
                        "batch_id": batch_id,
                        "configured_concurrency": concurrency,
                        "metric": metric,
                        **item,
                    }
                )
            tw = time_weighted_summary((item["start_s"], item["end_s"], item["value"]) for item in series)
            summary_rows.append(
                {
                    "trial_id": trial_id,
                    "batch_id": batch_id,
                    "configured_concurrency": concurrency,
                    "metric": metric,
                    "time_weighted_mean": tw["mean"],
                    "median": tw["median"],
                    "p90": tw["p90"],
                    "p95": tw["p95"],
                    "max": tw["max"],
                    "integral": tw["integral"],
                    "steady_state_mean": tw["mean"],
                    "steady_state_p95": tw["p95"],
                    "configured_to_actual_mean_ratio": safe_div(tw["mean"], concurrency),
                    "status": "PASS" if series else "PARTIAL",
                }
            )
    return {"concurrency_timeseries": series_rows, "concurrency_summary": summary_rows}


def analyze_request_lifecycle(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    requests = tables.get("requests", [])
    rows: list[dict[str, Any]] = []
    token_rows: list[dict[str, Any]] = []
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in requests:
        grouped[(row.get("configured_concurrency"), "all")].append(row)
        grouped[
            (
                row.get("configured_concurrency"),
                str(row.get("request_purpose") or "agent_primary"),
            )
        ].append(row)
    for (concurrency, bucket), items in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        for metric in (
            "queue_s",
            "prefill_s",
            "ttft_s",
            "tpot_s",
            "decode_s",
            "engine_residence_s",
            "engine_finished_to_api_finished_s",
            "server_e2e_s",
            "context_compression_duration_s",
            "context_summary_duration_s",
        ):
            values = [item.get(metric) for item in items]
            count = len([item for item in values if float_or_none(item) is not None])
            rows.append(
                {
                    "configured_concurrency": concurrency,
                    "bucket": bucket,
                    "stage": metric,
                    "count": count,
                    "coverage": count / len(items) if items else None,
                    "mean": mean(values),
                    "median": median(values),
                    "p50": percentile(values, 0.50),
                    "p90": percentile(values, 0.90),
                    "p95": percentile(values, 0.95),
                    "p99": percentile(values, 0.99),
                    "iqr": iqr(values),
                    "bootstrap_ci_low": None,
                    "bootstrap_ci_high": None,
                    "residual": None,
                    "status": "PASS" if count else "PARTIAL",
                }
            )
        for item in items:
            gen = float_or_none(item.get("generation_tokens"))
            e2e = float_or_none(item.get("server_e2e_s"))
            prompt = float_or_none(item.get("prompt_tokens"))
            token_rows.append(
                {
                    "request_attempt_id": item.get("request_attempt_id"),
                    "configured_concurrency": concurrency,
                    "request_purpose": item.get("request_purpose"),
                    "prompt_tokens": prompt,
                    "cached_tokens": item.get("cached_tokens"),
                    "uncached_prompt_tokens": item.get("uncached_prompt_tokens"),
                    "engine_recomputed_tokens": item.get("engine_recomputed_tokens"),
                    "generation_tokens": gen,
                    "server_e2e_s": e2e,
                    "per_request_generation_tok_s": safe_div(gen, e2e),
                    "prefill_unit_s_per_1k_prompt_tokens": safe_div(float_or_none(item.get("prefill_s")), (prompt or 0) / 1000) if prompt else None,
                    "generation_unit_s_per_token": safe_div(float_or_none(item.get("decode_s")), gen),
                    "ttft_s": item.get("ttft_s"),
                    "tpot_s": item.get("tpot_s"),
                    "engine_residence_s": item.get("engine_residence_s"),
                }
            )
    return {"request_stage_summary": rows, "token_normalized_performance": token_rows}


def workload_baseline_key(run: dict[str, Any]) -> tuple[float, str]:
    concurrency = float_or_none(run.get("configured_concurrency"))
    return (concurrency if concurrency is not None else float("inf"), str(run.get("run_id") or ""))


def analyze_workload(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    runs = tables.get("runs", [])
    by_task: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_task[run.get("task_id")].append(run)
    paired_rows: list[dict[str, Any]] = []
    fixed_rows: list[dict[str, Any]] = []
    drift_rows: list[dict[str, Any]] = []
    decomposition_rows: list[dict[str, Any]] = []
    for task_id, task_runs in sorted(by_task.items(), key=lambda item: str(item[0])):
        conc_values = sorted({str(run.get("configured_concurrency")) for run in task_runs})
        fixed_rows.append({"task_id": task_id, "run_count": len(task_runs), "configured_concurrency_values": conc_values, "paired": len(conc_values) > 1})
        trials: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run in task_runs:
            trials[str(run.get("trial_id") or "trial-0")].append(run)
        baselines = {trial: min(rows, key=workload_baseline_key) for trial, rows in trials.items()}
        for run in task_runs:
            trial = str(run.get("trial_id") or "trial-0")
            baseline = baselines[trial]
            paired = len({row.get("configured_concurrency") for row in trials[trial]}) > 1
            delta_wall = none_delta(run.get("run_wall_s"), baseline.get("run_wall_s"))
            paired_rows.append(
                {
                    "task_id": task_id,
                    "trial_id": trial,
                    "baseline_concurrency": baseline.get("configured_concurrency"),
                    "target_concurrency": run.get("configured_concurrency"),
                    "run_id": run.get("run_id"),
                    "paired": paired,
                    "delta_run_wall_s": delta_wall,
                    "delta_request_count": none_delta(run.get("model_request_attempt_count"), baseline.get("model_request_attempt_count")),
                    "delta_prompt_tokens": none_delta(run.get("prompt_tokens_sum"), baseline.get("prompt_tokens_sum")),
                    "delta_generation_tokens": none_delta(run.get("generation_tokens_sum"), baseline.get("generation_tokens_sum")),
                    "conclusion_strength": "STATISTICAL_ASSOCIATION" if paired else "UNRESOLVED_HYPOTHESIS",
                }
            )
            decomposition_rows.append(
                {
                    "task_id": task_id,
                    "run_id": run.get("run_id"),
                    "trial_id": trial,
                    "configured_concurrency": run.get("configured_concurrency"),
                    "request_count_effect": none_delta(run.get("model_request_attempt_count"), baseline.get("model_request_attempt_count")),
                    "prompt_volume_effect": none_delta(run.get("prompt_tokens_sum"), baseline.get("prompt_tokens_sum")),
                    "generation_volume_effect": none_delta(run.get("generation_tokens_sum"), baseline.get("generation_tokens_sum")),
                    "conditional_service_time_effect": None,
                    "interaction": None,
                    "residual": delta_wall,
                    "additivity_status": "PARTIAL",
                }
            )
        prompt_values = [float_or_none(run.get("prompt_tokens_sum")) for run in task_runs]
        drift_rows.append(
            {
                "task_id": task_id,
                "run_count": len(task_runs),
                "prompt_tokens_min": min([v for v in prompt_values if v is not None], default=None),
                "prompt_tokens_max": max([v for v in prompt_values if v is not None], default=None),
                "workload_drift_status": "PASS" if len(task_runs) > 1 else "PARTIAL",
            }
        )
    return {
        "workload_service_decomposition": decomposition_rows,
        "task_paired_analysis": paired_rows,
        "task_fixed_effects": fixed_rows,
        "workload_drift": drift_rows,
    }


def analyze_tail(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    runs = sorted(tables.get("runs", []), key=lambda row: float_or_none(row.get("workload_finish")) or float("inf"))
    completion_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    if not runs:
        return {
            "configured_concurrency_decay": [{"status": "PARTIAL", "reason": "no runs"}],
            "effective_capacity_drain": [{"status": "PARTIAL", "reason": "no effective concurrency series"}],
            "completion_tail": [],
            "tail_task_audit": [],
        }
    start = min(float_or_none(row.get("workload_start")) or 0 for row in runs)
    finish_times = [float_or_none(row.get("workload_finish")) for row in runs]
    finish_times = [value for value in finish_times if value is not None]
    for pct in (10, 25, 50, 75, 80, 90, 95, 100):
        value = percentile(finish_times, pct / 100)
        completion_rows.append({"percentile": f"t{pct}", "finish_time_ns": value, "elapsed_s_from_first_start": safe_div((value or 0) - start, 1_000_000_000) if value is not None else None})
    ordered_finishes = sorted(finish_times)
    for tail_pct in (20, 10, 5):
        tail_count = max(1, int(round(len(ordered_finishes) * tail_pct / 100))) if ordered_finishes else 0
        tail_start = ordered_finishes[-tail_count] if tail_count else None
        tail_end = ordered_finishes[-1] if ordered_finishes else None
        completion_rows.append(
            {
                "percentile": f"tail_{tail_pct}",
                "finish_time_ns": tail_end,
                "tail_start_finish_time_ns": tail_start,
                "tail_task_count": tail_count,
                "elapsed_s_from_first_start": safe_div((tail_end or 0) - start, 1_000_000_000) if tail_end is not None else None,
                "tail_duration_s": safe_div((tail_end or 0) - (tail_start or 0), 1_000_000_000)
                if tail_start is not None and tail_end is not None
                else None,
            }
        )
    for label, count in (("last_1", 1), ("last_5", 5), ("last_10", 10)):
        for run in runs[-count:]:
            audit_rows.append(
                {
                    "tail_group": label,
                    "task_id": run.get("task_id"),
                    "run_id": run.get("run_id"),
                    "run_wall_s": run.get("run_wall_s"),
                    "request_count": run.get("model_request_attempt_count"),
                    "model_error_count": run.get("model_request_error_count"),
                    "prompt_tokens": run.get("prompt_tokens_sum"),
                    "generation_tokens": run.get("generation_tokens_sum"),
                    "model_time_s": run.get("model_union_s"),
                    "tool_time_s": run.get("tool_union_s"),
                    "task_result": run.get("task_result"),
                    "observability_result": run.get("observability_result"),
                }
            )
    decay = configured_concurrency_decay_rows(runs, cfg)
    drain = effective_capacity_drain_rows(tables.get("scheduler_iterations", []), cfg)
    return {
        "configured_concurrency_decay": decay,
        "effective_capacity_drain": drain,
        "completion_tail": completion_rows,
        "tail_task_audit": audit_rows,
    }


def analyze_pipeline(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("worker_intervals", []):
        grouped[(row.get("trial_id"), row.get("batch_id"))].append(row)
    summary_rows: list[dict[str, Any]] = []
    util_rows: list[dict[str, Any]] = []
    overhead_rows: list[dict[str, Any]] = []
    phase_keys: set[tuple[Any, Any]] = set()
    for batch in tables.get("batches", []):
        timings = parse_json_value(batch.get("phase_timings"), {})
        validation = parse_json_value(batch.get("pipeline_phase_validation"), {})
        if not timings:
            continue
        key = (batch.get("trial_id"), batch.get("batch_id"))
        phase_keys.add(key)
        summary_rows.append(
            {
                "trial_id": batch.get("trial_id"),
                "batch_id": batch.get("batch_id"),
                "observed_batch_wall_s": float_or_none(timings.get("pipeline_wall_s")) or float_or_none(batch.get("batch_wall_s")),
                "preprocessing_critical_path_s": float_or_none(timings.get("preprocessing_s")),
                "workload_critical_path_s": float_or_none(timings.get("workload_phase_s")),
                "interleaved_analysis_critical_path_s": float_or_none(timings.get("analysis_phase_s")),
                "final_postprocessing_critical_path_s": float_or_none(timings.get("finalization_s")),
                "p2_index_critical_path_s": float_or_none(timings.get("p2_index_s")),
                "postprocessing_s": float_or_none(timings.get("postprocessing_s")),
                "runner_idle_s": None,
                "unknown_s": None,
                "residual_s": validation.get("absolute_error_s"),
                "relative_error": validation.get("relative_error"),
                "status": validation.get("status") or "PASS",
                "evidence_source": "batch_phase_events" if validation or batch.get("batch_phase_events_path") else "estimated_worker_intervals",
                "timestamp_quality": validation.get("evidence_source") or ("DIRECT" if validation or batch.get("batch_phase_events_path") else "ESTIMATED"),
            }
        )
    for (trial_id, batch_id), items in grouped.items():
        if (trial_id, batch_id) in phase_keys:
            continue
        starts = [ns_to_s(row.get("start_time")) for row in items]
        ends = [ns_to_s(row.get("end_time")) for row in items]
        starts = [v for v in starts if v is not None]
        ends = [v for v in ends if v is not None]
        wall = max(ends) - min(starts) if starts and ends else None
        by_state: dict[str, float] = defaultdict(float)
        for row in items:
            by_state[str(row.get("state") or "UNKNOWN")] += float_or_none(row.get("duration_s")) or 0.0
        summary_rows.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "observed_batch_wall_s": wall,
                "preprocessing_critical_path_s": by_state.get("MATERIALIZING"),
                "workload_critical_path_s": by_state.get("AGENT_WORKLOAD"),
                "interleaved_analysis_critical_path_s": by_state.get("IMPORT_TRAJECTORY", 0.0) + by_state.get("IMPORT_P2", 0.0) + by_state.get("REPORT_GENERATION", 0.0),
                "final_postprocessing_critical_path_s": by_state.get("STEP_ANALYSIS", 0.0) + by_state.get("VALIDATION", 0.0),
                "runner_idle_s": by_state.get("IDLE"),
                "unknown_s": by_state.get("UNKNOWN"),
                "residual_s": None,
                "status": "PARTIAL",
                "evidence_source": "estimated_worker_intervals",
                "timestamp_quality": "ESTIMATED",
                "reason": "direct_pipeline_phase_timestamps_unavailable",
            }
        )
        workers = sorted({row.get("worker_id") for row in items if row.get("worker_id")})
        for worker in workers:
            duration = sum(float_or_none(row.get("duration_s")) or 0.0 for row in items if row.get("worker_id") == worker)
            util_rows.append({"trial_id": trial_id, "batch_id": batch_id, "worker_id": worker, "occupied_s": duration, "wall_s": wall, "slot_occupancy": safe_div(duration, wall)})
        overhead_rows.append({"trial_id": trial_id, "batch_id": batch_id, "analysis_cpu_s": None, "event_sink_write_bytes": None, "status": "PARTIAL"})
    conservation = [
        {
            "trial_id": row.get("trial_id"),
            "batch_id": row.get("batch_id"),
            "pipeline_wall_s": row.get("observed_batch_wall_s"),
            "component_sum_s": sum(
                float_or_none(row.get(key)) or 0.0
                for key in ("preprocessing_critical_path_s", "workload_critical_path_s", "p2_index_critical_path_s", "interleaved_analysis_critical_path_s", "final_postprocessing_critical_path_s")
            ),
            "residual_s": row.get("residual_s"),
            "relative_error": row.get("relative_error"),
            "evidence_source": row.get("evidence_source") or "ESTIMATED",
            "timestamp_quality": row.get("timestamp_quality") or "ESTIMATED",
            "status": row.get("status"),
        }
        for row in summary_rows
    ]
    return {
        "pipeline_summary": summary_rows,
        "pipeline_phase_summary": [dict(row) for row in summary_rows],
        "pipeline_conservation": conservation,
        "worker_utilization": util_rows,
        "agentmon_overhead": overhead_rows,
    }


def analyze_scheduler(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    rows = tables.get("scheduler_iterations", [])
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("trial_id"), row.get("batch_id"), row.get("configured_concurrency"))].append(row)
    summary: list[dict[str, Any]] = []
    batching: list[dict[str, Any]] = []
    kv: list[dict[str, Any]] = []
    token_timeseries: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    for key, items in grouped.items():
        trial_id, batch_id, concurrency = key
        token_timeseries.extend(token_timeseries_rows(trial_id, batch_id, concurrency, items))
        engine = engine_window_metrics(items, cfg)
        summary.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "scheduler_iteration_count": len(items),
                "running_requests_mean": mean(item.get("running_requests") for item in items),
                "waiting_requests_mean": mean(item.get("waiting_requests") for item in items),
                "batch_request_count_mean": mean(item.get("batch_request_count") for item in items),
                "scheduled_token_count_total": sum(integer_or_none(item.get("scheduled_token_count")) or 0 for item in items),
                "scheduled_prefill_tokens_total": sum(
                    integer_or_none(item.get("scheduled_prefill_tokens")) or 0
                    for item in items
                ),
                "scheduled_decode_tokens_total": sum(
                    integer_or_none(item.get("scheduled_decode_tokens")) or 0
                    for item in items
                ),
                "prefill_request_count_total": sum(
                    integer_or_none(item.get("prefill_request_count")) or 0
                    for item in items
                ),
                "decode_request_count_total": sum(
                    integer_or_none(item.get("decode_request_count")) or 0
                    for item in items
                ),
                **engine["metrics"],
                "status": "PASS" if items else "PARTIAL",
            }
        )
        state_rows.append({"trial_id": trial_id, "batch_id": batch_id, "configured_concurrency": concurrency, **engine["classification"]})
        sensitivity_rows.extend(
            {"trial_id": trial_id, "batch_id": batch_id, "configured_concurrency": concurrency, **row}
            for row in engine_sensitivity(items, cfg)
        )
        batching.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "batch_request_count_p95": percentile((item.get("batch_request_count") for item in items), 0.95),
                "waiting_backlog_p95": percentile((item.get("waiting_requests") for item in items), 0.95),
                "status": "PASS" if items else "PARTIAL",
            }
        )
    if not summary:
        summary.append({"status": "PARTIAL", "reason": "no scheduler iteration rows"})
        batching.append({"status": "PARTIAL", "reason": "no scheduler iteration rows"})
    kv_rows = tables.get("kv_block_events", [])
    kv_grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in kv_rows:
        kv_grouped[
            (
                row.get("trial_id"),
                row.get("batch_id"),
                row.get("configured_concurrency"),
            )
        ].append(row)
    requests = tables.get("requests", [])
    for key, items in kv_grouped.items():
        trial_id, batch_id, concurrency = key
        request_items = [
            row
            for row in requests
            if row.get("trial_id") == trial_id
            and row.get("batch_id") == batch_id
            and row.get("configured_concurrency") == concurrency
        ]
        attributed = sum(
            1 for item in items if item.get("request_attempt_id") not in {None, ""}
        )
        kv.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "kv_event_count": len(items),
                "allocated_block_count_total": sum(
                    integer_or_none(item.get("allocated_block_count")) or 0
                    for item in items
                ),
                "cached_block_count_total": sum(
                    integer_or_none(item.get("cached_block_count")) or 0
                    for item in items
                ),
                "cache_hit_block_count_total": sum(
                    integer_or_none(item.get("cache_hit_block_count")) or 0
                    for item in items
                ),
                "freed_block_count_total": sum(
                    integer_or_none(item.get("freed_block_count")) or 0
                    for item in items
                ),
                "evicted_block_count_total": sum(
                    integer_or_none(item.get("evicted_block_count")) or 0
                    for item in items
                ),
                "kv_usage_max": max(
                    (
                        value
                        for item in items
                        for value in (
                            float_or_none(item.get("kv_usage_before")),
                            float_or_none(item.get("kv_usage_after")),
                        )
                        if value is not None
                    ),
                    default=None,
                ),
                "request_attribution_coverage": safe_div(attributed, len(items)),
                "preemption_count": sum(
                    integer_or_none(item.get("preemption_count")) or 0
                    for item in request_items
                ),
                "status": "PASS",
                "reason": None,
            }
        )
    if not kv:
        kv.append(
            {
                "status": "PARTIAL",
                "reason": "no vLLM P2 KV block events observed",
                "kv_usage_max": None,
                "preemption_count": None,
            }
        )
    if not token_timeseries:
        token_timeseries.append(
            {
                "status": "UNAVAILABLE",
                "reason": "no scheduler iteration rows with actual generated token deltas",
            }
        )
    return {
        "scheduler_summary": summary,
        "batching_efficiency": batching,
        "kv_analysis": kv,
        "token_timeseries": token_timeseries,
        "engine_state_classification": state_rows or [{"status": "PARTIAL", "state": "TRANSIENT_OR_UNRESOLVED", "reason": "no scheduler iteration rows"}],
        "scheduler_threshold_sensitivity": sensitivity_rows,
    }


def engine_window_metrics(items: list[dict[str, Any]], cfg: AnalysisConfig) -> dict[str, Any]:
    ordered = sorted(items, key=lambda row: integer_or_none(row.get("sample_time")) or 0)
    intervals = sample_intervals(ordered)
    running_values = [float_or_none(row.get("running_requests")) for row in ordered]
    running_values = [value for value in running_values if value is not None]
    waiting_values = [float_or_none(row.get("waiting_requests")) for row in ordered]
    waiting_values = [value for value in waiting_values if value is not None]
    maximum_running = max(running_values, default=None)
    start = intervals[0][0] if intervals else None
    end = intervals[-1][1] if intervals else None
    warm = cfg.windows.steady_state_warmup_fraction
    cool = cfg.windows.steady_state_cooldown_fraction
    steady_start = start + (end - start) * warm if start is not None and end is not None else None
    steady_end = end - (end - start) * cool if start is not None and end is not None else None
    plateau_floor = maximum_running * (1.0 - cfg.engine_metrics.plateau_fraction) if maximum_running is not None else None
    plateau = [value for value in running_values if plateau_floor is not None and value >= plateau_floor]
    pre_drain_end = max((finish for begin, finish, running, _ in intervals if plateau_floor is not None and running is not None and running >= plateau_floor), default=end)
    waiting_threshold = cfg.engine_metrics.waiting_positive_threshold
    waiting_positive_duration = sum(finish - begin for begin, finish, _, waiting in intervals if waiting is not None and waiting > waiting_threshold)
    longest_waiting = longest_matching_interval(intervals, lambda row: row[3] is not None and row[3] > waiting_threshold)
    duration = (end - start) if start is not None and end is not None else 0.0
    token_budget_seen = any(float_or_none(row.get("scheduled_token_budget")) is not None for row in ordered)
    token_budget_near = any(
        (float_or_none(row.get("scheduled_token_count")) or 0.0)
        >= (float_or_none(row.get("scheduled_token_budget")) or float("inf")) * cfg.engine_metrics.token_budget_near_ratio
        for row in ordered
        if float_or_none(row.get("scheduled_token_budget")) is not None
    )
    long_context_values = [
        value
        for row in ordered
        for field in cfg.engine_metrics.long_context_signal_fields
        if (value := float_or_none(row.get(field))) is not None
    ]
    long_context_evidence = bool(
        cfg.engine_metrics.long_context_signal_threshold is not None
        and long_context_values
        and max(long_context_values) >= cfg.engine_metrics.long_context_signal_threshold
    )
    if token_budget_seen and token_budget_near and waiting_positive_duration >= cfg.engine_metrics.minimum_state_duration_s:
        state = "TOKEN_BUDGET_CANDIDATE"
        reason = "configured token budget was observed and scheduled tokens remained near it"
    elif long_context_evidence and waiting_positive_duration >= cfg.engine_metrics.minimum_state_duration_s:
        state = "LONG_CONTEXT_PRESSURE_CANDIDATE"
        reason = "configured long-context signal threshold and sustained waiting evidence were both observed"
    elif waiting_positive_duration >= cfg.engine_metrics.minimum_state_duration_s and plateau:
        state = "ENGINE_CAPACITY_CANDIDATE"
        reason = "running plateau and positive waiting backlog persisted"
    elif duration and waiting_positive_duration == 0 and maximum_running is not None:
        state = "AGENT_SUPPLY_LIMITED"
        reason = "no sustained waiting backlog was observed"
    elif running_values and running_values[-1] < max(running_values) * (1.0 - cfg.engine_metrics.plateau_fraction):
        state = "TASK_POOL_DECAY"
        reason = "running requests declined below the configured plateau band"
    else:
        state = "TRANSIENT_OR_UNRESOLVED"
        reason = "configured persistence and evidence gates were not satisfied"
    metrics = {
        "whole_workload_running_tw_mean": weighted_mean(intervals, 2),
        "pre_drain_running_tw_mean": weighted_mean(intervals, 2, end_s=pre_drain_end),
        "steady_state_running_tw_mean": weighted_mean(intervals, 2, start_s=steady_start, end_s=steady_end),
        "plateau_running_median": median(plateau),
        "plateau_running_p90": percentile(plateau, 0.90),
        "maximum_running": maximum_running,
        "waiting_tw_mean": weighted_mean(intervals, 3),
        "waiting_p90": percentile(waiting_values, 0.90),
        "waiting_p95": percentile(waiting_values, 0.95),
        "waiting_max": max(waiting_values, default=None),
        "waiting_positive_fraction": safe_div(waiting_positive_duration, duration),
        "longest_waiting_positive_interval": longest_waiting,
    }
    classification = {
        "state": state,
        "reason": reason,
        "formula": "state gates use time-weighted running/waiting intervals and configured minimum duration",
        "thresholds_json": json.dumps(cfg.engine_metrics.__dict__, ensure_ascii=False, sort_keys=True),
        "minimum_duration_s": cfg.engine_metrics.minimum_state_duration_s,
        "token_budget_evidence": token_budget_seen,
        "long_context_evidence": long_context_evidence,
        "status": "PASS" if state != "TRANSIENT_OR_UNRESOLVED" else "PARTIAL",
    }
    return {"metrics": metrics, "classification": classification}


def engine_sensitivity(items: list[dict[str, Any]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda row: integer_or_none(row.get("sample_time")) or 0)
    rows: list[dict[str, Any]] = []
    for time_slice in cfg.engine_metrics.sensitivity_time_slices_s:
        intervals = sample_intervals(resample_engine_items(ordered, time_slice))
        maximum = max((row[2] for row in intervals if row[2] is not None), default=None)
        for waiting_threshold in cfg.engine_metrics.sensitivity_waiting_thresholds:
            for plateau_fraction in cfg.engine_metrics.sensitivity_plateau_fractions:
                for minimum_duration in cfg.engine_metrics.sensitivity_minimum_durations_s:
                    plateau_floor = maximum * (1.0 - plateau_fraction) if maximum is not None else None
                    sustained = sum(
                        finish - begin
                        for begin, finish, running, waiting in intervals
                        if running is not None and waiting is not None and plateau_floor is not None and running >= plateau_floor and waiting > waiting_threshold
                    )
                    rows.append(
                        {
                            "time_slice_s": time_slice,
                            "waiting_threshold": waiting_threshold,
                            "plateau_fraction": plateau_fraction,
                            "minimum_duration_s": minimum_duration,
                            "qualifying_duration_s": sustained,
                            "state": "ENGINE_CAPACITY_CANDIDATE" if sustained >= minimum_duration else "TRANSIENT_OR_UNRESOLVED",
                        }
                    )
    return rows


def resample_engine_items(items: list[dict[str, Any]], time_slice_s: float) -> list[dict[str, Any]]:
    if not items or time_slice_s <= 0:
        return items
    timestamps = [integer_or_none(row.get("sample_time")) for row in items]
    valid_timestamps = [value for value in timestamps if value is not None]
    if not valid_timestamps:
        return items
    origin = min(valid_timestamps)
    width_ns = max(int(time_slice_s * 1_000_000_000), 1)
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        timestamp = integer_or_none(row.get("sample_time"))
        if timestamp is None:
            continue
        buckets[(timestamp - origin) // width_ns].append(row)
    return [
        {
            "sample_time": origin + bucket * width_ns,
            "running_requests": median(item.get("running_requests") for item in bucket_rows),
            "waiting_requests": median(item.get("waiting_requests") for item in bucket_rows),
        }
        for bucket, bucket_rows in sorted(buckets.items())
    ]


def sample_intervals(items: list[dict[str, Any]]) -> list[tuple[float, float, float | None, float | None]]:
    rows: list[tuple[float, float, float | None, float | None]] = []
    for current, following in zip(items, items[1:]):
        start = ns_to_s(current.get("sample_time"))
        end = ns_to_s(following.get("sample_time"))
        if start is None or end is None or end <= start:
            continue
        rows.append((start, end, float_or_none(current.get("running_requests")), float_or_none(current.get("waiting_requests"))))
    return rows


def weighted_mean(
    intervals: list[tuple[float, float, float | None, float | None]],
    value_index: int,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in intervals:
        begin = max(row[0], start_s) if start_s is not None else row[0]
        finish = min(row[1], end_s) if end_s is not None else row[1]
        value = row[value_index]
        if value is None or finish <= begin:
            continue
        numerator += value * (finish - begin)
        denominator += finish - begin
    return numerator / denominator if denominator else None


def longest_matching_interval(
    intervals: list[tuple[float, float, float | None, float | None]],
    predicate: Any,
) -> float:
    longest = 0.0
    current_start: float | None = None
    current_end: float | None = None
    for row in intervals:
        if predicate(row):
            if current_start is None or current_end != row[0]:
                current_start = row[0]
            current_end = row[1]
            longest = max(longest, current_end - current_start)
        else:
            current_start = None
            current_end = None
    return longest


def analyze_resources(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    samples = tables.get("resource_samples", [])
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        grouped[(row.get("trial_id"), row.get("batch_id"), row.get("configured_concurrency"))].append(row)
    summary: list[dict[str, Any]] = []
    timeseries: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    balance: list[dict[str, Any]] = []
    for key, items in grouped.items():
        trial_id, batch_id, concurrency = key
        summary.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "sample_count": len(items),
                "cpu_percent_mean": mean(item.get("cpu_percent") for item in items),
                "rss_bytes_max": max([float_or_none(item.get("rss_bytes")) for item in items if float_or_none(item.get("rss_bytes")) is not None], default=None),
                "aicore_util_pct_mean": mean(item.get("aicore_util_pct") for item in items),
                "hbm_bytes_max": max([float_or_none(item.get("hbm_bytes")) for item in items if float_or_none(item.get("hbm_bytes")) is not None], default=None),
                "window": "whole_pipeline",
                "status": "PASS" if items else "PARTIAL",
            }
        )
        timeseries.extend(dict(item) for item in items)
    for sample in samples:
        if sample.get("logical_device_id") or sample.get("physical_device_id"):
            mapping.append(
                {
                    "run_id": sample.get("run_id"),
                    "logical_device_id": sample.get("logical_device_id"),
                    "physical_device_id": sample.get("physical_device_id"),
                    "device_id_source": sample.get("device_id_source"),
                    "mapping_confidence": sample.get("mapping_confidence") or "NONE",
                    "status": "PASS" if sample.get("mapping_confidence") in {"LEVEL_1", "LEVEL_2"} else "PARTIAL",
                }
            )
    for batch in tables.get("batches", []):
        batch_mapping = parse_json_value(batch.get("npu_process_mapping"), {})
        evidence_level = str(batch_mapping.get("evidence_level") or "NONE")
        processes = parse_json_value(batch_mapping.get("processes"), [])
        if isinstance(processes, list) and processes:
            for process in processes:
                if not isinstance(process, dict):
                    continue
                mapping.append(
                    {
                        "trial_id": batch.get("trial_id"),
                        "batch_id": batch.get("batch_id"),
                        "run_id": None,
                        "pid": process.get("pid"),
                        "rank": process.get("rank"),
                        "local_rank": process.get("local_rank"),
                        "logical_device_id": process.get("logical_device"),
                        "physical_device_id": process.get("physical_device"),
                        "visible_devices": process.get("visible_devices") or batch_mapping.get("visible_devices"),
                        "device_id_source": "npu-process-mapping.json",
                        "mapping_confidence": evidence_level,
                        "status": "PASS" if evidence_level in {"LEVEL_1", "LEVEL_2"} else "PARTIAL" if evidence_level == "LEVEL_3" else "UNRESOLVED",
                    }
                )
        elif batch_mapping:
            mapping.append(
                {
                    "trial_id": batch.get("trial_id"),
                    "batch_id": batch.get("batch_id"),
                    "visible_devices": batch_mapping.get("visible_devices"),
                    "device_id_source": "npu-process-mapping.json",
                    "mapping_confidence": evidence_level,
                    "status": "PASS" if evidence_level in {"LEVEL_1", "LEVEL_2"} else "PARTIAL" if evidence_level == "LEVEL_3" else "UNRESOLVED",
                    "reason": batch_mapping.get("status"),
                }
            )
    if not mapping:
        mapping.append({"status": "UNRESOLVED", "mapping_confidence": "NONE", "reason": "no logical-to-physical NPU mapping evidence"})
    balance.append({"status": "PARTIAL", "reason": "requires per-device NPU samples", "imbalance_ratio": None})
    return {
        "resource_summary": summary or [{"status": "PARTIAL", "reason": "no resource samples"}],
        "resource_timeseries": timeseries,
        "npu_mapping_evidence": mapping,
        "npu_balance": balance,
    }


def analyze_p1_capture(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    requests = [dict(row) for row in tables.get("requests", [])]
    trajectory_attempts = trajectory_model_attempts(tables.get("trajectories", []))
    request_by_identity = request_identity_index(requests)
    seen_trajectory_ids: set[str] = set()
    audit: list[dict[str, Any]] = []
    captured = 0
    missing = 0
    for attempt in trajectory_attempts:
        trajectory_id = first_non_empty(attempt.get("trajectory_response_id"), attempt.get("api_request_id"))
        duplicate = bool(trajectory_id and trajectory_id in seen_trajectory_ids)
        if trajectory_id:
            seen_trajectory_ids.add(str(trajectory_id))
        matched_request = request_by_identity.get(str(trajectory_id)) if trajectory_id else None
        if matched_request:
            captured += 1
            status = "CAPTURED"
            missing_reason = None
        else:
            missing += 1
            status = "MISSING"
            missing_reason = "DUPLICATE_TRAJECTORY_RECORD" if duplicate else "REQUEST_ID_MISMATCH" if trajectory_id else "UNKNOWN"
        audit.append(
            {
                "schema_version": "1.0",
                "task_id": attempt.get("task_id"),
                "run_id": attempt.get("run_id"),
                "request_ordinal": attempt.get("request_ordinal"),
                "trajectory_response_id": trajectory_id,
                "p1_span_id": matched_request.get("p1_span_id") if matched_request else None,
                "api_request_id": matched_request.get("api_request_id") if matched_request else attempt.get("api_request_id"),
                "status": status,
                "missing_reason": missing_reason,
                "evidence_file": attempt.get("evidence_file"),
                "evidence_line": attempt.get("evidence_line"),
            }
        )
    if not trajectory_attempts and not requests:
        audit.append({"schema_version": "1.0", "status": "PARTIAL", "missing_reason": "NO_TRAJECTORY_OR_P1_REQUEST_ROWS"})
    summary = [
        {
            "schema_version": "1.0",
            "trajectory_model_request_attempt_count": len(trajectory_attempts),
            "p1_model_request_attempt_count": len(requests),
            "p1_captured_request_count": captured,
            "p1_missing_request_count": missing,
            "p1_capture_rate": safe_div(captured, len(trajectory_attempts)),
            "p1_error_request_count": sum(1 for row in requests if str(row.get("request_status") or "").lower() in {"error", "failed"}),
            "status": "PASS" if trajectory_attempts and missing == 0 else "FAIL" if missing else "PARTIAL",
        }
    ]
    return {"p1_capture_audit": audit, "p1_capture_summary": summary}


def analyze_p2(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> dict[str, list[dict[str, Any]]]:
    attempts = [dict(row) for row in tables.get("requests", [])]
    audit: list[dict[str, Any]] = []
    gap_rows: list[dict[str, Any]] = []
    for row in attempts:
        eligible = truthy(row.get("p2_eligible"))
        matched = truthy(row.get("p2_matched"))
        if eligible and not matched:
            reason = "SUCCESS_REQUEST_MISSING_API_REQUEST_ID" if not row.get("api_request_id") else "REQUEST_REACHED_API_BUT_NO_P2_EVENT"
            audit.append(
                {
                    "request_attempt_id": row.get("request_attempt_id"),
                    "run_id": row.get("run_id"),
                    "task_id": row.get("task_id"),
                    "status": row.get("request_status"),
                    "api_request_id": row.get("api_request_id"),
                    "engine_request_id": row.get("engine_request_id"),
                    "p2_eligibility_reason": row.get("p2_eligibility_reason"),
                    "p2_eligibility_evidence": row.get("p2_eligibility_evidence"),
                    "root_cause": reason,
                    "confidence": "medium",
                    "enters_denominator": True,
                    "enters_numerator": False,
                    "affects_publishability": True,
                }
            )
            gap_rows.append(
                {
                    "request_attempt_id": row.get("request_attempt_id"),
                    "api_request_id": row.get("api_request_id"),
                    "engine_request_id": row.get("engine_request_id"),
                    "expected_identity": row.get("api_request_id") or row.get("engine_request_id") or row.get("request_attempt_id"),
                    "gap_type": reason,
                    "publishability_impact": True,
                }
            )
    gate = {
        "model_request_attempt_count": len(attempts),
        "all_model_request_attempts": len(attempts),
        "successful_model_requests": sum(1 for row in attempts if str(row.get("request_status") or "") not in {"error", "failed"}),
        "error_model_requests": sum(1 for row in attempts if str(row.get("request_status") or "") in {"error", "failed"}),
        "p2_eligible_requests": sum(1 for row in attempts if truthy(row.get("p2_eligible"))),
        "p2_matched_requests": sum(1 for row in attempts if truthy(row.get("p2_matched"))),
        "p2_lifecycle_complete_requests": sum(
            1 for row in attempts if truthy(row.get("p2_matched")) and truthy(row.get("lifecycle_complete"))
        ),
    }
    gate["model_request_error_rate"] = safe_div(gate["error_model_requests"], gate["all_model_request_attempts"])
    gate["p2_eligible_request_match_rate"] = safe_div(gate["p2_matched_requests"], gate["p2_eligible_requests"])
    gate["p2_lifecycle_complete_rate"] = safe_div(gate["p2_lifecycle_complete_requests"], gate["p2_matched_requests"])
    if not audit:
        audit.append({"status": "PASS", "root_cause": "none", **gate})
    return {
        "p1_request_attempts": [dict(row) for row in tables.get("p1_request_attempts", [])],
        "p2_match_audit": audit,
        "publishability_gap_mapping": gap_rows,
    }


def write_methodology_docs(analysis_dir: Path) -> None:
    write_markdown(
        analysis_dir / "throughput_methodology.md",
        "Throughput separates workload task, pipeline task, successful workload task, publishable pipeline task, request, aggregate token, and per-request token levels. Scheduled tokens are never treated as generated tokens. Steady-state generated-token throughput requires actual generated token deltas.",
    )
    write_markdown(
        analysis_dir / "concurrency_reconciliation.md",
        "Configured concurrency is a configuration input. Active agent/model/tool/engine concurrency are reconstructed as separate observed time series when source intervals exist.",
    )
    write_markdown(
        analysis_dir / "request_lifecycle_analysis.md",
        "Request lifecycle rows use same-request stage fields. Cross-clock unavailable fields remain null and limit conclusion strength.",
    )
    write_markdown(
        analysis_dir / "tail_methodology.md",
        "Configured concurrency decay, effective capacity drain, and completion tail are reported as separate concepts.",
    )
    write_markdown(
        analysis_dir / "pipeline_analysis.md",
        "Pipeline analysis uses worker state intervals and critical-path wall time. Parallel worker time is not serially summed.",
    )
    write_markdown(
        analysis_dir / "scheduler_analysis.md",
        "Scheduler and batching analysis reports field coverage first and avoids mechanism claims when P2 scheduler/KV fields are missing. `token_timeseries` marks actual generated-token deltas as PASS and scheduled-only rows as UNAVAILABLE for generated-token conclusions.",
    )
    write_markdown(
        analysis_dir / "resource_analysis.md",
        "Resource means are time-window scoped. Physical NPU mapping requires LEVEL_1 or LEVEL_2 evidence.",
    )
    write_markdown(
        analysis_dir / "p2_analysis.md",
        "P1 capture audit compares trajectory model attempts against normalized P1 request rows. P2 audit distinguishes all model attempts, successful requests, error requests, evidence-eligible requests, matched requests, and lifecycle-complete requests.",
    )


def maybe_write_figures(bundle: Path, outputs: dict[str, list[dict[str, Any]]]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        return
    figures = bundle / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    rows = outputs.get("throughput_summary", [])
    xs = [str(row.get("configured_concurrency")) for row in rows]
    ys = [float_or_none(row.get("whole_workload_generation_tok_s")) or 0.0 for row in rows]
    if xs:
        plt.figure()
        plt.bar(xs, ys)
        plt.title("Whole-workload generation throughput (batch level)")
        plt.xlabel("Configured concurrency")
        plt.ylabel("generated token/s")
        plt.tight_layout()
        plt.savefig(figures / "whole_workload_generation_throughput.png")
        plt.close()


def parse_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if is_missing(value):
        return default
    if not isinstance(value, str):
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return default
    return parsed


def steady_state_token_row(base: dict[str, Any], scheduler_rows: list[dict[str, Any]]) -> dict[str, Any]:
    token_rows = [
        (float_or_none(row.get("sample_time")), float_or_none(row.get("actual_generated_tokens_delta")))
        for row in scheduler_rows
    ]
    token_rows = [(ts, delta) for ts, delta in token_rows if ts is not None and delta is not None and delta >= 0]
    row = dict(base)
    if len(token_rows) < 2:
        row.update(
            {
                "steady_state_generation_tok_s": None,
                "steady_state_window_source": "actual_generated_token_delta_unavailable",
                "token_timeseries_status": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "missing_reason": "actual_generated_tokens_delta_required_for_steady_state_generation_rate",
            }
        )
        return row
    ordered = sorted(token_rows)
    start = percentile([ts for ts, _ in ordered], 0.10)
    end = percentile([ts for ts, _ in ordered], 0.90)
    window_rows = [(ts, delta) for ts, delta in ordered if start <= ts <= end]
    if not window_rows:
        start = ordered[0][0]
        end = ordered[-1][0]
        window_rows = ordered
    denominator_s = (end - start) / 1_000_000_000 if end > start else None
    row.update(
        {
            "steady_state_generation_tok_s": safe_div(sum(delta for _, delta in window_rows), denominator_s),
            "steady_state_window_source": "scheduler_actual_generated_token_delta_t10_t90",
            "token_timeseries_status": "PASS",
            "steady_state_window_start": start,
            "steady_state_window_end": end,
            "steady_state_token_delta_count": len(window_rows),
            "status": "PASS" if denominator_s and window_rows else "PARTIAL",
        }
    )
    return row


def token_timeseries_rows(
    trial_id: Any,
    batch_id: Any,
    concurrency: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: float_or_none(row.get("sample_time")) or 0.0):
        actual_delta = float_or_none(item.get("actual_generated_tokens_delta"))
        rows.append(
            {
                "schema_version": "1.0",
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "run_id": item.get("run_id"),
                "sample_time": item.get("sample_time"),
                "actual_generated_tokens_delta": actual_delta,
                "prompt_tokens_processed_delta": float_or_none(item.get("prompt_tokens_processed_delta")),
                "scheduled_token_count": float_or_none(item.get("scheduled_token_count")),
                "running_requests": float_or_none(item.get("running_requests")),
                "waiting_requests": float_or_none(item.get("waiting_requests")),
                "batch_request_count": float_or_none(item.get("batch_request_count")),
                "batch_sequence_count": float_or_none(item.get("batch_sequence_count")),
                "status": "PASS" if actual_delta is not None else "UNAVAILABLE",
                "missing_reason": None if actual_delta is not None else "actual_generated_tokens_delta_missing",
            }
        )
    return rows


def configured_concurrency_decay_rows(runs: list[dict[str, Any]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    intervals = []
    for run in runs:
        start = ns_to_s(run.get("workload_start"))
        end = ns_to_s(run.get("workload_finish"))
        if start is not None and end is not None and end > start:
            intervals.append((start, end))
    if not intervals:
        return [
            {
                "metric": "configured_concurrency_decay",
                "status": "PARTIAL",
                "definition": "time from peak active task concurrency to sustained drop below configured threshold",
                "decay_time_s": None,
                "reason": "requires active-agent run intervals",
            }
        ]
    series = concurrency_series(intervals)
    peak = max((row["value"] for row in series), default=None)
    configured = max((integer_or_none(run.get("configured_concurrency")) or 0 for run in runs), default=0) or peak
    threshold = max(1.0, float(configured or peak or 1) * cfg.windows.effective_capacity_threshold_ratio)
    peak_time = next((row["start_s"] for row in series if row["value"] == peak), None)
    decay_time = first_sustained_below(series, threshold, cfg.windows.effective_capacity_min_duration_s, after_s=peak_time)
    return [
        {
            "metric": "configured_concurrency_decay",
            "status": "PASS" if decay_time is not None else "PARTIAL",
            "definition": "time from peak active task concurrency to sustained drop below configured threshold",
            "peak_active_tasks": peak,
            "configured_concurrency": configured,
            "threshold_ratio": cfg.windows.effective_capacity_threshold_ratio,
            "threshold_active_tasks": threshold,
            "decay_time_s": decay_time - peak_time if decay_time is not None and peak_time is not None else None,
            "threshold_crossing_s": decay_time,
            "reason": None if decay_time is not None else "no sustained post-peak drop detected",
        }
    ]


def effective_capacity_drain_rows(scheduler_rows: list[dict[str, Any]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    points = [
        (ns_to_s(row.get("sample_time")), float_or_none(row.get("running_requests")))
        for row in scheduler_rows
    ]
    points = [(ts, value) for ts, value in points if ts is not None and value is not None]
    if len(points) < 2:
        return [
            {
                "metric": "effective_capacity_drain",
                "status": "PARTIAL",
                "threshold_ratio": cfg.windows.effective_capacity_threshold_ratio,
                "min_duration_s": cfg.windows.effective_capacity_min_duration_s,
                "drain_time_s": None,
                "reason": "requires scheduler running request series",
            }
        ]
    peak = max(value for _, value in points)
    threshold = max(1.0, peak * cfg.windows.effective_capacity_threshold_ratio)
    peak_time = next(ts for ts, value in points if value == peak)
    drain_time = first_point_sustained_below(points, threshold, cfg.windows.effective_capacity_min_duration_s, peak_time)
    return [
        {
            "metric": "effective_capacity_drain",
            "status": "PASS" if drain_time is not None else "PARTIAL",
            "peak_running_requests": peak,
            "threshold_ratio": cfg.windows.effective_capacity_threshold_ratio,
            "min_duration_s": cfg.windows.effective_capacity_min_duration_s,
            "threshold_running_requests": threshold,
            "drain_time_s": drain_time - peak_time if drain_time is not None else None,
            "threshold_crossing_s": drain_time,
            "reason": None if drain_time is not None else "no sustained post-peak drain detected",
        }
    ]


def first_sustained_below(
    series: list[dict[str, Any]],
    threshold: float,
    min_duration_s: float,
    *,
    after_s: float | None,
) -> float | None:
    for row in series:
        start = float_or_none(row.get("start_s"))
        end = float_or_none(row.get("end_s"))
        value = float_or_none(row.get("value"))
        if start is None or end is None or value is None:
            continue
        if after_s is not None and end < after_s:
            continue
        if value < threshold and (end - max(start, after_s or start)) >= min_duration_s:
            return max(start, after_s or start)
    return None


def first_point_sustained_below(
    points: list[tuple[float, float]],
    threshold: float,
    min_duration_s: float,
    after_s: float,
) -> float | None:
    ordered = sorted(points)
    for index, (ts, value) in enumerate(ordered):
        if ts < after_s or value >= threshold:
            continue
        end = ts
        ok = True
        for next_ts, next_value in ordered[index:]:
            if next_value >= threshold:
                ok = False
                break
            end = next_ts
            if end - ts >= min_duration_s:
                return ts
        if ok and end - ts >= min_duration_s:
            return ts
    return None


def trajectory_model_attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, start=1):
        payload = parse_json_value(row.get("payload_json"), {})
        event_type = str(row.get("event_type") or "").lower()
        if not is_trajectory_model_record(event_type, payload):
            continue
        response_id = first_non_empty(
            deep_first(payload, "response_id", "api_request_id", "server_request_id", "request_id"),
            payload.get("id") if "model" in event_type or "completion" in event_type else None,
        )
        attempts.append(
            {
                "task_id": row.get("task_id"),
                "run_id": row.get("run_id"),
                "request_ordinal": ordinal,
                "trajectory_response_id": response_id,
                "api_request_id": deep_first(payload, "api_request_id", "server_request_id"),
                "evidence_file": row.get("source_path"),
                "evidence_line": row.get("source_line"),
            }
        )
    return attempts


def is_trajectory_model_record(event_type: str, payload: Any) -> bool:
    if any(token in event_type for token in ("model", "llm", "completion", "chat")):
        return True
    if not isinstance(payload, dict):
        return False
    keys = set(flat_keys(payload))
    return bool(keys & {"response_id", "api_request_id", "server_request_id"}) and bool(
        keys & {"messages", "prompt", "completion", "usage", "model"}
    )


def flat_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(flat_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(flat_keys(child))
    return keys


def deep_first(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if not is_missing(value.get(key)):
                return value.get(key)
        for child in value.values():
            found = deep_first(child, *keys)
            if not is_missing(found):
                return found
    elif isinstance(value, list):
        for child in value:
            found = deep_first(child, *keys)
            if not is_missing(found):
                return found
    return None


def request_identity_index(requests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for request in requests:
        values = [
            request.get("request_attempt_id"),
            request.get("trajectory_response_id"),
            request.get("api_request_id"),
            request.get("engine_request_id"),
        ]
        aliases = parse_json_value(request.get("alias_ids"), {})
        if isinstance(aliases, dict):
            values.extend(aliases.values())
        for value in values:
            if not is_missing(value):
                index.setdefault(str(value), request)
    return index


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if not is_missing(value):
            return value
    return None


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def group_by_batch(runs: list[dict[str, Any]]) -> dict[tuple[Any, Any, Any], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[(run.get("trial_id"), run.get("batch_id"), run.get("configured_concurrency"))].append(run)
    return grouped


def workload_window_s(runs: list[dict[str, Any]]) -> float | None:
    starts = [float_or_none(run.get("workload_start")) for run in runs]
    ends = [float_or_none(run.get("workload_finish")) for run in runs]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]
    if starts and ends and max(ends) > min(starts):
        return (max(ends) - min(starts)) / 1_000_000_000
    durations = [float_or_none(run.get("run_wall_s")) for run in runs]
    durations = [value for value in durations if value is not None]
    return max(durations) if durations else None


def ns_to_s(value: Any) -> float | None:
    parsed = integer_or_none(value)
    if parsed is None:
        return None
    return parsed / 1_000_000_000


def none_delta(value: Any, baseline: Any) -> float | None:
    left = float_or_none(value)
    right = float_or_none(baseline)
    if left is None or right is None:
        return None
    return left - right


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "passed", "success"}
