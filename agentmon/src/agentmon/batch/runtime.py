#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import importlib.util
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess
import sys
from string import Formatter
import threading
import time
from types import ModuleType
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.reporting.timeline import generate_batch_timeline
from agentmon.reporting.report import read_event_log as read_run_event_log, write_reports
from agentmon.adapters.inference.vllm_018.importer import (
    is_p2_global_event,
    normalize_imported_p2_event,
    summarize_p2_imported_events,
)
from agentmon.storage.jsonl import JsonlWriter
from agentmon.adapters.agent_frameworks.mini_swe.adapter import (
    MINI_SWE_P1_CLASSES as P1_CLASSES,
    configure_p1_command as configure_mini_swe_p1_command,
)
from agentmon.adapters.inference.vllm_018.p2_index import (
    build_or_load_p2_batch_index,
    build_p2_batch_index,
    filter_p2_events_for_response_ids,
    p2_service_bounds,
    read_jsonl_events,
    select_indexed_p2_events,
    select_indexed_p2_global_events,
    write_jsonl_events,
)
from agentmon.runtime.phases import BatchPhaseRecorder, validate_phase_timings
from agentmon.sampling.npu_mapping import collect_npu_process_mapping as collect_npu_process_mapping_evidence
from agentmon.profile_data import profile_quality_gate
from agentmon.batch.analysis_runner import (
    analyze_one_task,
    copy_p2_result_fields,
    detect_native_p1_status,
    extract_trajectory_response_ids,
    import_p2,
    import_trajectory,
    load_step_sequence_module,
    regenerate_report,
    run_one_task,
    trajectory_messages,
    write_step_sequence_outputs,
)
from agentmon.batch.commands import (
    agentmon_command,
    append_error,
    configure_p1_command,
    parse_run_dir,
    parse_run_id,
    run_judge,
    run_logged,
    task_command,
)
from agentmon.batch.workload import (
    failed_worker_result,
    find_run_trajectory,
    find_trajectory,
    has_trajectory_sample,
    initial_task_result,
    run_workload_task,
)
from agentmon.batch.artifacts import (
    build_task_validation,
    combine_step_csvs,
    read_report_validation,
    read_results_tsv,
    read_step_totals,
    validation_check,
    write_analysis_quality_bundle,
    write_results_tsv,
    write_task_validation,
)
from agentmon.batch.constants import BATCH_SCHEMA_VERSION, RUNNER_VERSION
from agentmon.batch.io import (
    as_dict,
    batch_context,
    integer_or_none,
    is_blank_value,
    make_private_dir,
    read_env_file,
    read_tasks,
    read_text,
    render_template,
    string_or_none,
    write_json_private,
)
from agentmon.batch.manifest import (
    build_batch_manifest,
    detect_cann_version,
    file_hash_if_exists,
    git_commit,
    model_config_hash,
    package_version,
    parse_metadata,
    parse_scalar,
    parse_vllm_launch_settings,
    path_hash_if_exists,
    safe_scheduler_environment,
    update_manifest_p2_metadata,
)
from agentmon.batch.metrics import (
    aggregate_optional_metric,
    count_result_values,
    number,
    optional_number,
    percentile_linear,
    ratio,
    steady_state_completion_rate,
    throughput_metric,
)
from agentmon.batch.policy import (
    analysis_defer_reason,
    p2_index_cache_root,
    p2_service_global_missing_window_policy,
    should_build_combined_step_csv,
    should_build_timeline,
    should_write_p2_service_global_copy,
)
from agentmon.batch.resources import (
    aggregate_task_stage_profiles,
    capture_file_inventory,
    capture_pipeline_resources,
    collect_npu_process_mapping,
    numeric_delta,
    pipeline_resource_delta,
    read_proc_self_io,
)
from agentmon.batch.summary import INVALID_EXECUTION_STATUSES, build_batch_summary, restore_execution_measurements
from agentmon.batch.safety import (
    WorkspacePolicyError,
    build_isolated_runtime_env,
    build_workspace_contracts,
    capture_source_manifest,
    compare_source_manifests,
    prepare_runtime_source_snapshot,
    protected_source_roots,
    verify_runtime_snapshot,
    write_workspace_contracts,
)


DEFAULT_MIN_P2_MATCH_RATE = profile_quality_gate("min_p2_match_rate", profile="legacy_compat")
DEFAULT_MIN_P2_LIFECYCLE_RATE = profile_quality_gate("min_p2_lifecycle_rate", profile="legacy_compat")


def result_is_failure(result: dict[str, Any]) -> bool:
    return bool(
        result.get("overall_pipeline_success") is False
        or result.get("source_integrity_violation") is True
        or (result.get("run_id_required") is True and not result.get("run_id"))
        or result.get("status") in {
            "agentmon_run_id_missing",
            "runtime_source_integrity_violation",
            "worker_error",
            "workspace_policy_rejected",
        }
        or (
            result.get("overall_pipeline_success") is None
            and result.get("execution_success") is False
        )
    )


def evaluate_runtime_source_integrity(
    args: argparse.Namespace,
    batch_root: Path,
    *,
    source_before: dict[str, Any] | None,
    runtime_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if args.runtime_source_integrity == "off":
        report = {
            "schema_version": "1.0",
            "status": "UNKNOWN",
            "mode": "off",
            "data_quality": "UNRESOLVED",
            "limitations": ["Runtime source integrity hashing was explicitly disabled."],
            "forbidden_conclusions": ["Do not claim the runtime source stayed unchanged."],
        }
        write_json_private(batch_root / "runtime-source-integrity.json", report)
        return report

    if source_before is None:
        report = {
            "schema_version": "1.0",
            "status": "UNKNOWN",
            "mode": args.runtime_source_integrity,
            "data_quality": "UNRESOLVED",
            "limitations": ["The before-workload source manifest is missing."],
            "forbidden_conclusions": ["Do not claim the runtime source stayed unchanged."],
        }
        write_json_private(batch_root / "runtime-source-integrity.json", report)
        return report

    source_after = capture_source_manifest(ROOT)
    write_json_private(batch_root / "runtime-source-manifest-after.json", source_after)
    original_comparison = compare_source_manifests(source_before, source_after)
    snapshot_comparison: dict[str, Any] | None = None
    if runtime_snapshot is not None:
        snapshot_after = capture_source_manifest(
            Path(str(runtime_snapshot["snapshot_root"])),
            relative_roots=("src",),
        )
        write_json_private(batch_root / "runtime-snapshot-manifest-after.json", snapshot_after)
        snapshot_comparison = compare_source_manifests(
            runtime_snapshot["manifest"],
            snapshot_after,
        )

    changed = original_comparison["status"] == "CHANGED" or (
        snapshot_comparison is not None and snapshot_comparison["status"] == "CHANGED"
    )
    report = {
        "schema_version": "1.0",
        "status": "CHANGED" if changed else "PASS",
        "mode": args.runtime_source_integrity,
        "data_quality": "COMPLETE",
        "original_source": original_comparison,
        "runtime_snapshot": snapshot_comparison,
        "limitations": [],
        "forbidden_conclusions": (
            ["Do not compare or publish this batch as a reproducible experiment."]
            if changed
            else []
        ),
    }
    write_json_private(batch_root / "runtime-source-integrity.json", report)
    return report


def annotate_integrity_violation(
    results: list[dict[str, Any]],
    integrity: dict[str, Any],
) -> None:
    status = integrity.get("status")
    for result in results:
        result["runtime_source_integrity_status"] = status
        if status != "CHANGED":
            continue
        result["source_integrity_violation"] = True
        result["status"] = "runtime_source_integrity_violation"
        result["pipeline_success"] = False
        result["overall_pipeline_success"] = False
        result["usable_run"] = False
        result["publishability_status"] = "UNPUBLISHABLE"
        failures = list(result.get("report_blocking_failures") or [])
        if "runtime_source_integrity_changed" not in failures:
            failures.append("runtime_source_integrity_changed")
        result["report_blocking_failures"] = failures
        result["error"] = append_error(
            str(result.get("error") or ""),
            "runtime source changed during workload; experiment invalid",
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = run_batch(args)
    except Exception as exc:
        print(f"agentmon batch runtime error: {exc}", file=sys.stderr)
        return 2

    failures = [result for result in results if result_is_failure(result)]
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AgentMon tasks concurrently with isolated mini-SWE trajectories and request-id P2 import",
    )
    parser.add_argument("--batch-root", required=True, help="directory for batch logs, homes, workspaces, and results")
    parser.add_argument("--task-file", required=True, help="TSV/CSV/JSONL task file")
    parser.add_argument("--task-id-column", default="task_id")
    parser.add_argument("--command-column", help="task column containing a shell command")
    parser.add_argument("--command-template", help="shell command template; may reference task columns and batch paths")
    parser.add_argument("--label-template", default="{task_id}")
    parser.add_argument("--workspace-template", default="{batch_root}/workspaces/{task_id}")
    parser.add_argument("--cwd-template", default="{workspace}")
    parser.add_argument(
        "--workspace-policy",
        choices=["isolated", "external"],
        default="isolated",
        help=(
            "isolated requires every task workspace under <batch-root>/workspaces; external must be "
            "selected explicitly for a pre-materialized repository. Both reject AgentMon source overlap"
        ),
    )
    parser.add_argument(
        "--protected-source-root",
        action="append",
        default=[],
        help="additional source root that task workspace/cwd must not overlap; may be repeated",
    )
    parser.add_argument(
        "--runtime-source-isolation",
        choices=["snapshot", "off"],
        default="snapshot",
        help="run AgentMon worker processes from a batch-local read-only source snapshot",
    )
    parser.add_argument(
        "--runtime-source-integrity",
        choices=["strict", "warn", "off"],
        default="strict",
        help="hash protected runtime source before/after workload; strict stops analysis on a change",
    )
    parser.add_argument("--trajectory-template", help="optional explicit trajectory path template")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--analysis-workers",
        type=int,
        default=4,
        help="parallel workers for trajectory/P2 import, report generation, and step analysis",
    )
    parser.add_argument(
        "--analysis-preset",
        choices=["full", "standard", "quick"],
        default="full",
        help=(
            "analysis cost profile: full preserves legacy artifacts; standard avoids large service-global copies; "
            "quick also skips combined step CSV and timeline generation"
        ),
    )
    parser.add_argument(
        "--p1-mode",
        choices=["auto", "required", "off"],
        default="auto",
        help="auto injects AgentMon classes into simple mini-SWE commands; required also fails tasks when native P1 events are absent",
    )
    parser.add_argument("--agentmon-python", default=sys.executable)
    parser.add_argument("--agentmon-config", help="AgentMon config path")
    parser.add_argument("--output-root", help="AgentMon run output root; also used to resolve run dirs")
    parser.add_argument("--env-file", help="KEY=VALUE file loaded for each worker process")
    parser.add_argument("--p2-events", help="service-scoped vLLM P2 events.jsonl")
    parser.add_argument(
        "--p2-index-cache-root",
        help="optional shared P2 index cache directory reused across batch roots for the same service P2 source",
    )
    parser.add_argument(
        "--p2-filter-mode",
        choices=["auto", "run-id", "trajectory"],
        default="auto",
        help="P2 import strategy: run-id uses payload.agentmon_run_id, trajectory uses response ids, auto tries run-id then trajectory",
    )
    parser.add_argument(
        "--p2-quality",
        choices=["direct", "service_exclusive", "service_stream", "heuristic"],
        default="direct",
        help="quality label passed to agentmon import-p2 for response-id filtered events",
    )
    parser.add_argument(
        "--include-p2-window-extras",
        action="store_true",
        help="also import unaligned P2 request rows inside the matched request time window; unsafe under shared-service concurrency",
    )
    parser.add_argument(
        "--skip-p2-service-global-copy",
        action="store_true",
        help="do not write the full service-global P2 stream to <batch-root>/p2-service-global.events.jsonl",
    )
    parser.add_argument(
        "--p2-service-global-missing-window-policy",
        choices=["full", "skip"],
        default="full",
        help="when a task has no P2 time window, either copy the full global stream or skip it",
    )
    parser.add_argument("--p2-window-margin-seconds", type=float, default=1.0)
    parser.add_argument(
        "--min-p2-match-rate",
        type=float,
        default=DEFAULT_MIN_P2_MATCH_RATE,
        help="minimum matched P2/model fraction; default comes from the analysis schema profile",
    )
    parser.add_argument(
        "--min-p2-lifecycle-rate",
        type=float,
        default=DEFAULT_MIN_P2_LIFECYCLE_RATE,
        help="minimum complete P2 lifecycle fraction; default comes from the analysis schema profile",
    )
    parser.add_argument("--ascend-devices", help="target NPU ids passed to step-sequence analysis, e.g. 0,1,2,3")
    parser.add_argument(
        "--step-sequence-script",
        default=str(ROOT / "scripts" / "agentmon_step_sequence.py"),
        help="path to agentmon_step_sequence.py",
    )
    parser.add_argument("--results", help="output TSV path; defaults to <batch-root>/batch-results.tsv")
    parser.add_argument(
        "--judge-command-template",
        help="optional benchmark judge command; may reference task fields, run_id, run_dir and workspace",
    )
    parser.add_argument("--expected-task-count", type=int, help="fail preflight when the task file count differs")
    parser.add_argument("--task-package", help="task manifest/archive/directory hashed for cross-batch comparability")
    parser.add_argument("--model-path", help="model path recorded in the experiment manifest")
    parser.add_argument("--temperature", type=float, help="agent/model request temperature recorded in metadata")
    parser.add_argument("--seed", type=int, help="agent/model request seed recorded in metadata")
    parser.add_argument("--vllm-launch-command-file", help="file containing the vLLM launch command for reproducibility")
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        help="additional experiment metadata as KEY=VALUE; may be repeated",
    )
    parser.add_argument("--continue-on-error", action="store_true", help="run remaining tasks after a worker failure")
    parser.add_argument("--skip-timeline", action="store_true", help="skip batch timeline generation during analysis")
    parser.add_argument(
        "--skip-combined-step-csv",
        action="store_true",
        help="skip combining per-task step_sequence.csv files into step-sequence/all-steps.csv",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execution-only",
        action="store_true",
        help="run workloads and persist deferred run metadata without trajectory/P2/report analysis",
    )
    mode.add_argument(
        "--defer-analysis",
        dest="execution_only",
        action="store_true",
        help="alias for --execution-only; run workloads now and analyze later with --analyze-existing",
    )
    mode.add_argument(
        "--analyze-existing",
        action="store_true",
        help="skip workload execution and analyze the existing batch-results.tsv runs",
    )
    parser.add_argument(
        "--defer-analysis-if-p2-over-bytes",
        type=int,
        help=(
            "after workload execution, skip P2/report/step analysis when the service P2 "
            "events file is at least this many bytes; explicit safeguard for Deep P2 runs"
        ),
    )
    return parser


def run_batch(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")
    if args.analysis_workers < 1:
        raise ValueError("--analysis-workers must be >= 1")
    for name in ("min_p2_match_rate", "min_p2_lifecycle_rate"):
        configured = getattr(args, name)
        if configured is None:
            continue
        value = float(configured)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if args.defer_analysis_if_p2_over_bytes is not None and args.defer_analysis_if_p2_over_bytes < 0:
        raise ValueError("--defer-analysis-if-p2-over-bytes must be >= 0")
    if args.concurrency > 1 and args.include_p2_window_extras:
        raise ValueError("--include-p2-window-extras is unsafe when --concurrency is greater than 1")
    if not args.analyze_existing and not args.command_template and not args.command_column:
        raise ValueError("provide --command-template or --command-column")

    batch_root = Path(args.batch_root).expanduser().resolve()
    make_private_dir(batch_root)
    historical_measurements: dict[str, Any] = {}
    analysis_output = batch_root
    if args.analyze_existing:
        for name in ("batch-manifest.json", "batch-summary.json"):
            path = batch_root / name
            if path.is_file():
                historical_measurements.update(json.loads(path.read_text(encoding="utf-8")))
        analysis_output = batch_root / "analysis-resume" / str(time.time_ns())
    phase_events_path = analysis_output / "batch-phase-events.jsonl"
    phase_recorder = BatchPhaseRecorder(phase_events_path)
    phase_recorder.mark("pipeline", "started")
    phase_recorder.mark("preprocessing", "started")
    pipeline_profile_start = capture_pipeline_resources(batch_root)
    for name in ("logs", "homes", "xdg_config", "workspaces", "p2-filtered", "step-sequence"):
        make_private_dir(batch_root / name)

    tasks = read_tasks(Path(args.task_file), task_id_column=args.task_id_column)
    if args.expected_task_count is not None and len(tasks) != args.expected_task_count:
        raise ValueError(f"expected {args.expected_task_count} tasks, found {len(tasks)}")
    env_base = os.environ.copy()
    if args.env_file:
        env_base.update(read_env_file(Path(args.env_file)))
    started_wall_ns = time.time_ns()
    started_mono_ns = time.monotonic_ns()
    source_roots = protected_source_roots(args, env_base, default_root=ROOT)
    args._protected_source_roots = source_roots
    workspace_contract_index: Path | None = None
    source_integrity_before: dict[str, Any] | None = None
    runtime_snapshot: dict[str, Any] | None = None
    runtime_environment_isolation: dict[str, Any] | None = None
    if not args.analyze_existing:
        try:
            workspace_contracts = build_workspace_contracts(
                args,
                tasks,
                batch_root,
                source_roots,
            )
        except WorkspacePolicyError as exc:
            write_json_private(
                batch_root / "workspace-contracts.json",
                {
                    "schema_version": "1.0",
                    "status": "FAIL",
                    "error": str(exc),
                    "next": "Correct --workspace-template/--cwd-template or explicitly select --workspace-policy external.",
                    "forbidden_conclusions": ["No workload was started; do not treat this batch as an experiment."],
                },
            )
            raise
        args._workspace_contracts = workspace_contracts
        workspace_contract_index = write_workspace_contracts(batch_root, workspace_contracts)
        if args.runtime_source_integrity != "off":
            source_integrity_before = capture_source_manifest(ROOT)
            write_json_private(batch_root / "runtime-source-manifest-before.json", source_integrity_before)
        if args.runtime_source_isolation == "snapshot":
            runtime_snapshot = prepare_runtime_source_snapshot(
                ROOT,
                batch_root,
                identity=str(started_wall_ns),
            )
            env_base, runtime_environment_isolation = build_isolated_runtime_env(
                env_base,
                snapshot_pythonpath=Path(str(runtime_snapshot["pythonpath"])),
                protected_roots=source_roots,
            )
            runtime_environment_isolation["import_verification"] = verify_runtime_snapshot(
                args.agentmon_python,
                env_base,
                expected_pythonpath=Path(str(runtime_snapshot["pythonpath"])),
            )
            write_json_private(
                batch_root / "runtime-source-isolation.json",
                {
                    "schema_version": "1.0",
                    "status": "PASS",
                    "snapshot": runtime_snapshot,
                    "environment": runtime_environment_isolation,
                },
            )
    else:
        args._workspace_contracts = {}
    npu_mapping = (
        as_dict(historical_measurements.get("npu_process_mapping")) if args.analyze_existing
        else collect_npu_process_mapping(args, env_base, batch_root)
    )

    existing_manifest_path = batch_root / "batch-manifest.json"
    if args.analyze_existing and existing_manifest_path.is_file():
        manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        manifest["analysis_resume_wall_ns"] = started_wall_ns
        manifest["analysis_resume_mono_ns"] = started_mono_ns
    else:
        manifest = build_batch_manifest(args, tasks, env_base, started_wall_ns=started_wall_ns)
        manifest["start_mono_ns"] = started_mono_ns
    manifest["npu_process_mapping"] = npu_mapping
    if args.analyze_existing:
        manifest.setdefault(
            "workspace_safety",
            {
                "policy": "UNKNOWN",
                "contract_index": None,
                "protected_source_roots": [],
                "status": "UNKNOWN",
            },
        )
        manifest.setdefault(
            "runtime_source_safety",
            {
                "isolation_mode": "UNKNOWN",
                "integrity_mode": "UNKNOWN",
                "snapshot_root": None,
                "before_manifest": None,
                "status": "UNKNOWN",
            },
        )
    else:
        manifest["workspace_safety"] = {
            "policy": args.workspace_policy,
            "contract_index": str(workspace_contract_index) if workspace_contract_index else None,
            "protected_source_roots": [str(path) for path in source_roots],
            "status": "PASS",
        }
        manifest["runtime_source_safety"] = {
            "isolation_mode": args.runtime_source_isolation,
            "integrity_mode": args.runtime_source_integrity,
            "snapshot_root": runtime_snapshot.get("snapshot_root") if runtime_snapshot else None,
            "before_manifest": (
                str(batch_root / "runtime-source-manifest-before.json")
                if source_integrity_before is not None
                else None
            ),
            "status": "ACTIVE",
        }
    write_json_private(batch_root / "batch-manifest.json", manifest)
    phase_recorder.mark("preprocessing", "finished")
    preprocessing_profile_end = capture_pipeline_resources(batch_root)

    workload_phase = "results_load" if args.analyze_existing else "workload_phase"
    workload_phase_start = phase_recorder.mark(workload_phase, "started")
    workload_phase_start_ns = int(workload_phase_start["monotonic_time_ns"])
    results: list[dict[str, Any]] = []
    if args.analyze_existing:
        existing_results_path = Path(args.results) if args.results else batch_root / "batch-results.tsv"
        results = read_results_tsv(existing_results_path)
        if not results:
            raise ValueError(f"--analyze-existing found no rows in {existing_results_path}")
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_workload_task, args, task, batch_root, env_base): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = failed_worker_result(task, exc)
                results.append(result)
                print(
                    f"[workload:{result.get('status')}] task={result.get('task_id')} run={result.get('run_id') or 'N/A'} "
                    f"exit={result.get('agentmon_exit_code')}",
                    file=sys.stderr,
                    flush=True,
                )
    workload_phase_end = phase_recorder.mark(workload_phase, "finished")
    workload_phase_end_ns = int(workload_phase_end["monotonic_time_ns"])
    workload_profile_end = capture_pipeline_resources(batch_root)

    if args.analyze_existing:
        integrity_path = batch_root / "runtime-source-integrity.json"
        if integrity_path.is_file():
            runtime_source_integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        else:
            runtime_source_integrity = {
                "schema_version": "1.0",
                "status": "UNKNOWN",
                "mode": "historical_batch",
                "data_quality": "UNRESOLVED",
                "limitations": ["Historical batch has no runtime source integrity artifact."],
                "forbidden_conclusions": ["Do not claim the runtime source stayed unchanged."],
            }
    else:
        runtime_source_integrity = evaluate_runtime_source_integrity(
            args,
            batch_root,
            source_before=source_integrity_before,
            runtime_snapshot=runtime_snapshot,
        )
    annotate_integrity_violation(results, runtime_source_integrity)
    manifest["runtime_source_safety"].update(
        {
            "status": runtime_source_integrity.get("status"),
            "integrity_report": str(batch_root / "runtime-source-integrity.json"),
        }
    )
    write_json_private(batch_root / "batch-manifest.json", manifest)

    defer_reason = analysis_defer_reason(args)
    if (
        not args.analyze_existing
        and runtime_source_integrity.get("status") == "CHANGED"
        and args.runtime_source_integrity == "strict"
    ):
        defer_reason = {
            "reason": "runtime_source_integrity_changed",
            "status": "INVALID",
            "integrity_report": str(batch_root / "runtime-source-integrity.json"),
        }
    if defer_reason is not None:
        return finalize_deferred_analysis_batch(
            args,
            batch_root,
            results,
            manifest,
            phase_recorder,
            pipeline_profile_start,
            preprocessing_profile_end,
            workload_profile_end,
            workload_phase_start_ns,
            workload_phase_end_ns,
            started_mono_ns,
            defer_reason,
            runtime_source_integrity,
        )

    p2_index_started = phase_recorder.mark("p2_index", "started")
    p2_index_started_ns = int(p2_index_started["monotonic_time_ns"])
    p2_index = (
        build_or_load_p2_batch_index(
            Path(args.p2_events).expanduser(),
            batch_root,
            cache_root=p2_index_cache_root(args),
        )
        if args.p2_events
        else None
    )
    if p2_index is not None:
        if should_write_p2_service_global_copy(args):
            global_path = batch_root / "p2-service-global.events.jsonl"
            write_jsonl_events(global_path, p2_index.get("global_events", []))
            p2_index["global_events_path"] = str(global_path)
            manifest["p2_service_global_copy"] = {
                "status": "written",
                "path": str(global_path),
                "event_count": len(p2_index.get("global_events", [])),
            }
        else:
            p2_index["global_events_path"] = None
            manifest["p2_service_global_copy"] = {
                "status": "skipped",
                "reason": "analysis_preset_or_explicit_skip",
                "event_count": len(p2_index.get("global_events", [])),
            }
        update_manifest_p2_metadata(manifest, p2_index)
        write_json_private(batch_root / "batch-manifest.json", manifest)
        write_json_private(batch_root / "p2-index-metrics.json", as_dict(p2_index.get("metrics")))
    p2_index_ended = phase_recorder.mark("p2_index", "finished")
    p2_index_ended_ns = int(p2_index_ended["monotonic_time_ns"])
    p2_index_profile_end = capture_pipeline_resources(batch_root)

    task_by_id = {task["task_id"]: task for task in tasks}
    analysis_phase_start = phase_recorder.mark("analysis_phase", "started")
    analysis_phase_start_ns = int(analysis_phase_start["monotonic_time_ns"])
    analyzed_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.analysis_workers) as pool:
        futures = {
            pool.submit(
                analyze_one_task,
                args,
                task_by_id[result["task_id"]],
                result,
                batch_root,
                env_base,
                p2_index,
            ): result
            for result in results
        }
        for future in as_completed(futures):
            original = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = dict(original)
                result["analysis_pipeline_success"] = False
                result["pipeline_success"] = False
                result["status"] = "analysis_worker_failed"
                result["error"] = append_error(result.get("error", ""), f"analysis worker: {exc}")
            analyzed_results.append(result)
            print(
                f"[analysis:{result.get('status')}] task={result.get('task_id')} "
                f"p2_match={result.get('p2_match_rate')}",
                file=sys.stderr,
                flush=True,
            )
    results = analyzed_results
    annotate_integrity_violation(results, runtime_source_integrity)
    analysis_phase_end = phase_recorder.mark("analysis_phase", "finished")
    analysis_phase_end_ns = int(analysis_phase_end["monotonic_time_ns"])
    analysis_profile_end = capture_pipeline_resources(batch_root)

    finalization_start = phase_recorder.mark("finalization", "started")
    finalization_start_ns = int(finalization_start["monotonic_time_ns"])
    results.sort(key=lambda row: str(row.get("task_id") or ""))
    results_path = Path(args.results) if args.results else batch_root / "batch-results.tsv"
    write_results_tsv(results_path, results)
    csv_export_started_ns = time.monotonic_ns()
    csv_export_profile_start = capture_pipeline_resources()
    if should_build_combined_step_csv(args):
        combined_step_csv = combine_step_csvs(
            results,
            batch_root / "step-sequence" / "all-steps.csv",
        )
        csv_export_quality = "exact_combined_csv"
    else:
        combined_step_csv = None
        csv_export_quality = "skipped_by_analysis_preset"
    csv_export_ended_ns = time.monotonic_ns()
    csv_export_profile_end = capture_pipeline_resources()
    ended_wall_ns = time.time_ns()
    ended_mono_ns = time.monotonic_ns()
    finalization_profile_end = capture_pipeline_resources(batch_root)
    batch_wall_s = optional_number(phase_recorder.phase_timings().get("pipeline_wall_s")) or (
        ended_mono_ns - started_mono_ns
    ) / 1_000_000_000
    phase_timings = {
        **phase_recorder.phase_timings(),
        "batch_startup_s": (workload_phase_start_ns - started_mono_ns) / 1_000_000_000,
        "csv_export_s": (csv_export_ended_ns - csv_export_started_ns) / 1_000_000_000,
    }
    phase_validation = validate_phase_timings(phase_timings)
    phase_resource_profiles = {
        "preprocessing": pipeline_resource_delta(pipeline_profile_start, preprocessing_profile_end),
        "workload": pipeline_resource_delta(preprocessing_profile_end, workload_profile_end),
        "p2_index": pipeline_resource_delta(workload_profile_end, p2_index_profile_end),
        "analysis": pipeline_resource_delta(p2_index_profile_end, analysis_profile_end),
        "csv_export": pipeline_resource_delta(csv_export_profile_start, csv_export_profile_end),
        "finalization": pipeline_resource_delta(analysis_profile_end, finalization_profile_end),
    }
    batch_summary = build_batch_summary(
        results,
        batch_wall_s=historical_measurements.get("batch_wall_s") if args.analyze_existing else batch_wall_s,
        phase_timings=as_dict(historical_measurements.get("phase_timings")) if args.analyze_existing else phase_timings,
        configured_concurrency=int(historical_measurements.get("configured_concurrency") or args.concurrency),
        expected_task_count=len(tasks),
        runtime_source_integrity_status=str(runtime_source_integrity.get("status") or "UNKNOWN"),
        analysis_complete=True,
    )
    batch_summary["artifacts"] = {"all_steps_csv": str(combined_step_csv) if combined_step_csv else None}
    batch_summary["artifacts"]["batch_phase_events"] = str(batch_root / "batch-phase-events.jsonl")
    batch_summary["phase_resource_profiles"] = phase_resource_profiles
    batch_summary["phase_timestamps"] = phase_recorder.timestamps
    batch_summary["pipeline_phase_validation"] = phase_validation
    batch_summary["npu_process_mapping"] = npu_mapping
    if p2_index is not None:
        batch_summary["p2_index_metrics"] = as_dict(p2_index.get("metrics"))
    pipeline_profile_path = analysis_output / "pipeline-profile.json"
    write_json_private(
        pipeline_profile_path,
        {
            "schema_version": "1.0",
            "configured_concurrency": args.concurrency,
            "analysis_workers": args.analysis_workers,
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "pipeline_phase_validation": phase_validation,
            "phase_resource_profiles": phase_resource_profiles,
            "p2_index_metrics": as_dict(p2_index.get("metrics")) if p2_index is not None else None,
            "npu_process_mapping": npu_mapping,
            "task_stage_statistics": aggregate_task_stage_profiles(results),
        },
    )
    batch_summary["artifacts"]["pipeline_profile"] = str(batch_root / "pipeline-profile.json")
    if args.analyze_existing:
        restore_execution_measurements(batch_summary, manifest, historical_measurements)
        batch_summary["artifacts"]["analysis_phase_events"] = str(phase_events_path)
        batch_summary["artifacts"]["analysis_pipeline_profile"] = str(pipeline_profile_path)
    write_json_private(batch_root / "batch-summary.json", batch_summary)
    manifest.update(
        {
            "status": (
                "complete"
                if batch_summary["validity_status"] == "VALID"
                else f"complete_{str(batch_summary['validity_status']).lower()}"
            ),
            "controller_finished": True,
            "result_table_complete": batch_summary["completion"]["result_table_complete"],
            "workload_attempt_complete": batch_summary["completion"]["workload_attempt_complete"],
            "analysis_complete": True,
            "experiment_valid": batch_summary["experiment_valid"],
            "validity_status": batch_summary["validity_status"],
            "completion": batch_summary["completion"],
            "end_wall_ns": ended_wall_ns,
            "end_mono_ns": ended_mono_ns,
            "batch_wall_s": batch_wall_s,
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "pipeline_phase_validation": phase_validation,
            "phase_resource_profiles": phase_resource_profiles,
            "npu_process_mapping": npu_mapping,
            "result_count": len(results),
            "results_path": str(results_path),
            "batch_summary_path": str(batch_root / "batch-summary.json"),
            "all_steps_csv": str(combined_step_csv) if combined_step_csv else None,
            "stage_counts": batch_summary["stage_counts"],
            "throughput": batch_summary["throughput"],
        }
    )
    if args.p2_events:
        if p2_index is not None:
            update_manifest_p2_metadata(manifest, p2_index)
    if args.analyze_existing:
        restore_execution_measurements(batch_summary, manifest, historical_measurements)
    write_json_private(batch_root / "batch-manifest.json", manifest)
    timeline_started_ns = time.monotonic_ns()
    timeline_profile_start = capture_pipeline_resources()
    if should_build_timeline(args):
        try:
            timeline_summary = generate_batch_timeline(batch_root)
            batch_summary["artifacts"]["timeline"] = timeline_summary.get("output_dir")
            batch_summary["timeline_publishability_status"] = timeline_summary.get("publishability_status")
        except Exception as exc:
            timeline_summary = {"publishability_status": "UNPUBLISHABLE", "error": str(exc)}
            batch_summary["timeline_publishability_status"] = "UNPUBLISHABLE"
            batch_summary["timeline_error"] = str(exc)
    else:
        timeline_summary = {
            "publishability_status": "SKIPPED",
            "status": "SKIPPED",
            "reason": "skipped_by_analysis_preset",
        }
        batch_summary["timeline_publishability_status"] = "SKIPPED"
    timeline_ended_ns = time.monotonic_ns()
    timeline_profile_end = capture_pipeline_resources()
    phase_timings["timeline_build_s"] = (timeline_ended_ns - timeline_started_ns) / 1_000_000_000
    phase_resource_profiles["timeline_build"] = pipeline_resource_delta(
        timeline_profile_start, timeline_profile_end
    )
    phase_resource_profiles["timeline_build"]["quality"] = (
        "executed" if should_build_timeline(args) else "skipped_by_analysis_preset"
    )
    phase_resource_profiles["csv_export"]["quality"] = csv_export_quality
    file_scan_started_ns = time.monotonic_ns()
    file_inventory = capture_file_inventory(batch_root)
    phase_timings["file_scan_s"] = (time.monotonic_ns() - file_scan_started_ns) / 1_000_000_000
    phase_timings["archive_s"] = None
    phase_resource_profiles["file_scan"] = {
        **file_inventory,
        "wall_s": phase_timings["file_scan_s"],
        "quality": "exact_filesystem_walk",
    }
    phase_resource_profiles["archive"] = {
        "wall_s": None,
        "quality": "not_configured",
        "missing_reason": "batch_archiving_not_requested",
    }
    phase_recorder.mark("finalization", "finished")
    finished_event = phase_recorder.mark("pipeline", "finished")
    final_ended_mono_ns = int(finished_event["monotonic_time_ns"])
    final_ended_wall_ns = int(finished_event["wall_time_ns"])
    batch_wall_s = optional_number(phase_recorder.phase_timings().get("pipeline_wall_s")) or (
        final_ended_mono_ns - started_mono_ns
    ) / 1_000_000_000
    phase_timings["pipeline_wall_s"] = batch_wall_s
    phase_timings["postprocessing_s"] = (
        final_ended_mono_ns - workload_phase_end_ns
    ) / 1_000_000_000
    phase_timings["finalization_s"] = (
        final_ended_mono_ns - analysis_phase_end_ns
    ) / 1_000_000_000
    phase_validation = validate_phase_timings(phase_timings)
    artifacts = dict(batch_summary.get("artifacts") or {})
    timeline_status = batch_summary.get("timeline_publishability_status")
    timeline_error = batch_summary.get("timeline_error")
    batch_summary = build_batch_summary(
        results,
        batch_wall_s=historical_measurements.get("batch_wall_s") if args.analyze_existing else batch_wall_s,
        phase_timings=as_dict(historical_measurements.get("phase_timings")) if args.analyze_existing else phase_timings,
        configured_concurrency=int(historical_measurements.get("configured_concurrency") or args.concurrency),
        expected_task_count=len(tasks),
        runtime_source_integrity_status=str(runtime_source_integrity.get("status") or "UNKNOWN"),
        analysis_complete=True,
    )
    batch_summary["artifacts"] = artifacts
    batch_summary["timeline_publishability_status"] = timeline_status
    if timeline_error is not None:
        batch_summary["timeline_error"] = timeline_error
    task_publication_ok = (
        batch_summary["experiment_valid"] is True
        and all(row.get("publishability_status") == "PUBLISHABLE" for row in results)
    )
    if timeline_summary.get("publishability_status") == "SKIPPED":
        batch_summary["publishability_status"] = "NOT_EVALUATED" if task_publication_ok else "UNPUBLISHABLE"
    else:
        batch_summary["publishability_status"] = (
            "PUBLISHABLE"
            if task_publication_ok and timeline_summary.get("publishability_status") == "PUBLISHABLE"
            else "UNPUBLISHABLE"
        )
    manifest["publishability_status"] = batch_summary["publishability_status"]
    manifest["status"] = (
        "complete"
        if batch_summary["validity_status"] == "VALID"
        else f"complete_{str(batch_summary['validity_status']).lower()}"
    )
    manifest["controller_finished"] = True
    manifest["result_table_complete"] = batch_summary["completion"]["result_table_complete"]
    manifest["workload_attempt_complete"] = batch_summary["completion"]["workload_attempt_complete"]
    manifest["analysis_complete"] = True
    manifest["experiment_valid"] = batch_summary["experiment_valid"]
    manifest["validity_status"] = batch_summary["validity_status"]
    manifest["completion"] = batch_summary["completion"]
    manifest["end_wall_ns"] = final_ended_wall_ns
    manifest["end_mono_ns"] = final_ended_mono_ns
    manifest["batch_wall_s"] = batch_wall_s
    manifest["stage_counts"] = batch_summary["stage_counts"]
    manifest["throughput"] = batch_summary["throughput"]
    manifest["timeline"] = timeline_summary
    manifest["phase_timings"] = phase_timings
    manifest["phase_resource_profiles"] = phase_resource_profiles
    manifest["phase_timestamps"] = phase_recorder.timestamps
    manifest["pipeline_phase_validation"] = phase_validation
    manifest["npu_process_mapping"] = npu_mapping
    if p2_index is not None:
        manifest["p2_index_metrics"] = as_dict(p2_index.get("metrics"))
    batch_summary["phase_timings"] = phase_timings
    batch_summary["phase_timestamps"] = phase_recorder.timestamps
    batch_summary["pipeline_phase_validation"] = phase_validation
    batch_summary["phase_resource_profiles"] = phase_resource_profiles
    batch_summary["npu_process_mapping"] = npu_mapping
    if p2_index is not None:
        batch_summary["p2_index_metrics"] = as_dict(p2_index.get("metrics"))
    write_json_private(
        pipeline_profile_path,
        {
            "schema_version": "1.0",
            "configured_concurrency": args.concurrency,
            "analysis_workers": args.analysis_workers,
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "pipeline_phase_validation": phase_validation,
            "phase_resource_profiles": phase_resource_profiles,
            "p2_index_metrics": as_dict(p2_index.get("metrics")) if p2_index is not None else None,
            "npu_process_mapping": npu_mapping,
            "task_stage_statistics": aggregate_task_stage_profiles(results),
        },
    )
    if args.analyze_existing:
        resume = {
            "started_wall_ns": started_wall_ns,
            "finished_wall_ns": final_ended_wall_ns,
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "phase_resource_profiles": phase_resource_profiles,
            "pipeline_profile": str(pipeline_profile_path),
            "phase_events": str(phase_events_path),
        }
        restore_execution_measurements(batch_summary, manifest, historical_measurements)
        batch_summary["analysis_resume"] = resume
        manifest["analysis_resume"] = resume
    write_json_private(batch_root / "batch-summary.json", batch_summary)
    write_json_private(batch_root / "batch-manifest.json", manifest)
    return results














def finalize_deferred_analysis_batch(
    args: argparse.Namespace,
    batch_root: Path,
    results: list[dict[str, Any]],
    manifest: dict[str, Any],
    phase_recorder: BatchPhaseRecorder,
    pipeline_profile_start: dict[str, Any],
    preprocessing_profile_end: dict[str, Any],
    workload_profile_end: dict[str, Any],
    workload_phase_start_ns: int,
    workload_phase_end_ns: int,
    started_mono_ns: int,
    defer_reason: dict[str, Any],
    runtime_source_integrity: dict[str, Any],
) -> list[dict[str, Any]]:
    results.sort(key=lambda row: str(row.get("task_id") or ""))
    for result in results:
        result["analysis_deferred"] = True
        result["analysis_deferred_reason"] = defer_reason.get("reason")
        if result.get("source_integrity_violation") is True or result.get("status") in {
            "agentmon_run_id_missing",
            "worker_error",
            "workspace_policy_rejected",
        }:
            if result.get("source_integrity_violation") is True:
                result["status"] = "runtime_source_integrity_violation"
            result["pipeline_success"] = False
            result["overall_pipeline_success"] = False
            result["usable_run"] = False
            result["publishability_status"] = "UNPUBLISHABLE"
        elif result.get("execution_success") is True:
            result["status"] = "execution_complete_analysis_deferred"
        if result.get("status") not in INVALID_EXECUTION_STATUSES:
            result["monitoring_success"] = None
            result["analysis_pipeline_success"] = None
            result["pipeline_success"] = None
            result["usable_run"] = None
            result["publishability_status"] = "NOT_EVALUATED"
    results_path = Path(args.results) if args.results else batch_root / "batch-results.tsv"
    write_results_tsv(results_path, results)
    phase_recorder.mark("finalization", "started")
    phase_recorder.mark("finalization", "finished")
    finished_event = phase_recorder.mark("pipeline", "finished")
    phase_timings = phase_recorder.phase_timings()
    phase_timings["batch_startup_s"] = (workload_phase_start_ns - started_mono_ns) / 1_000_000_000
    phase_timings["workload_phase_s"] = (workload_phase_end_ns - workload_phase_start_ns) / 1_000_000_000
    phase_timings["postprocessing_s"] = (
        int(finished_event["monotonic_time_ns"]) - workload_phase_end_ns
    ) / 1_000_000_000
    phase_validation = validate_phase_timings(phase_timings)
    phase_resource_profiles = {
        "preprocessing": pipeline_resource_delta(pipeline_profile_start, preprocessing_profile_end),
        "workload": pipeline_resource_delta(preprocessing_profile_end, workload_profile_end),
    }
    batch_wall_s = optional_number(phase_timings.get("pipeline_wall_s")) or (
        int(finished_event["monotonic_time_ns"]) - started_mono_ns
    ) / 1_000_000_000
    batch_summary = build_batch_summary(
        results,
        batch_wall_s=batch_wall_s,
        phase_timings=phase_timings,
        configured_concurrency=args.concurrency,
        expected_task_count=int(manifest.get("task_count") or len(results)),
        runtime_source_integrity_status=str(runtime_source_integrity.get("status") or "UNKNOWN"),
        analysis_complete=False,
    )
    batch_summary["analysis_deferred"] = True
    batch_summary["analysis_deferred_reason"] = defer_reason
    batch_summary["publishability_status"] = (
        "NOT_EVALUATED" if batch_summary["experiment_valid"] is True else "UNPUBLISHABLE"
    )
    batch_summary["artifacts"] = {
        "batch_phase_events": str(batch_root / "batch-phase-events.jsonl"),
        "pipeline_profile": str(batch_root / "pipeline-profile.json"),
    }
    batch_summary["phase_resource_profiles"] = phase_resource_profiles
    batch_summary["phase_timestamps"] = phase_recorder.timestamps
    batch_summary["pipeline_phase_validation"] = phase_validation
    pipeline_profile_path = batch_root / "pipeline-profile.json"
    write_json_private(
        pipeline_profile_path,
        {
            "schema_version": "1.0",
            "configured_concurrency": args.concurrency,
            "analysis_workers": args.analysis_workers,
            "analysis_deferred": True,
            "analysis_deferred_reason": defer_reason,
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "pipeline_phase_validation": phase_validation,
            "phase_resource_profiles": phase_resource_profiles,
        },
    )
    write_json_private(batch_root / "batch-summary.json", batch_summary)
    manifest.update(
        {
            "status": (
                "execution_complete_analysis_deferred"
                if batch_summary["validity_status"] == "VALID"
                else f"execution_{str(batch_summary['validity_status']).lower()}_analysis_deferred"
            ),
            "controller_finished": True,
            "result_table_complete": batch_summary["completion"]["result_table_complete"],
            "workload_attempt_complete": batch_summary["completion"]["workload_attempt_complete"],
            "analysis_complete": False,
            "experiment_valid": batch_summary["experiment_valid"],
            "validity_status": batch_summary["validity_status"],
            "completion": batch_summary["completion"],
            "execution_only": bool(args.execution_only),
            "analysis_deferred": True,
            "analysis_deferred_reason": defer_reason,
            "workload_phase_s": phase_timings["workload_phase_s"],
            "end_wall_ns": int(finished_event["wall_time_ns"]),
            "end_mono_ns": int(finished_event["monotonic_time_ns"]),
            "batch_wall_s": batch_wall_s,
            "results_path": str(results_path),
            "batch_summary_path": str(batch_root / "batch-summary.json"),
            "phase_timings": phase_timings,
            "phase_timestamps": phase_recorder.timestamps,
            "pipeline_phase_validation": phase_validation,
            "phase_resource_profiles": phase_resource_profiles,
        }
    )
    write_json_private(batch_root / "batch-manifest.json", manifest)
    return results






















































































































































if __name__ == "__main__":
    raise SystemExit(main())
