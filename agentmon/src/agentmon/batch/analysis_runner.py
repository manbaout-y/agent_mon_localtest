from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import time
from types import ModuleType
from typing import Any

from agentmon.adapters.inference.vllm_018.importer import (
    is_p2_global_event,
    normalize_imported_p2_event,
    summarize_p2_imported_events,
)
from agentmon.adapters.inference.vllm_018.p2_index import (
    build_p2_batch_index,
    read_jsonl_events,
    select_indexed_p2_events,
    select_indexed_p2_global_events,
    write_jsonl_events,
)
from agentmon.batch.artifacts import (
    read_report_validation,
    read_step_totals,
    write_analysis_quality_bundle,
    write_task_validation,
)
from agentmon.batch.commands import append_error, agentmon_command, run_judge, run_logged
from agentmon.batch.io import as_dict, batch_context, make_private_dir, read_text, render_template, write_json_private
from agentmon.batch.manifest import file_hash_if_exists
from agentmon.batch.metrics import optional_number
from agentmon.batch.policy import p2_service_global_missing_window_policy
from agentmon.batch.resources import capture_pipeline_resources, pipeline_resource_delta
from agentmon.batch.workload import find_trajectory, find_run_trajectory, has_trajectory_sample, run_workload_task
from agentmon.reporting.report import read_event_log as read_run_event_log, write_reports
from agentmon.storage.jsonl import JsonlWriter

ROOT = Path(__file__).resolve().parents[3]
_STEP_SEQUENCE_MODULES: dict[str, ModuleType] = {}
_STEP_SEQUENCE_MODULE_LOCK = threading.Lock()

def analyze_one_task(
    args: argparse.Namespace,
    task: dict[str, str],
    workload_result: dict[str, Any],
    batch_root: Path,
    env_base: dict[str, str],
    p2_index: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(workload_result)
    task_id = task["task_id"]
    run_id = str(result.get("run_id") or "")
    run_dir = Path(str(result.get("run_dir") or ""))
    workspace = Path(str(result.get("workspace") or batch_root / "workspaces" / task_id))
    home = Path(str(result.get("home") or batch_root / "homes" / task_id))
    xdg_config = Path(str(result.get("xdg_config_home") or batch_root / "xdg_config" / task_id))
    log_dir = batch_root / "logs" / task_id
    context = batch_context(task, batch_root)
    cwd = Path(render_template(args.cwd_template, {**context, "workspace": str(workspace)})).expanduser()
    env = dict(env_base)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "AGENTMON_BATCH_ROOT": str(batch_root),
            "AGENTMON_BATCH_TASK_ID": task_id,
            "AGENTMON_TASK_WORKSPACE": str(workspace),
        }
    )
    result["analysis_start_wall_ns"] = time.time_ns()
    result["analysis_start_mono_ns"] = time.monotonic_ns()
    stage_durations: dict[str, float] = {}
    stage_resource_profiles: dict[str, dict[str, Any]] = {}
    result["analysis_deferred"] = False
    result["analysis_deferred_reason"] = ""

    if not run_id or not run_dir.is_dir():
        result["analysis_pipeline_success"] = False
        result["pipeline_success"] = False
        result["overall_pipeline_success"] = False
        result["usable_run"] = False
        result["status"] = "run_directory_missing"
        return result

    started = time.monotonic()
    stage_profile_start = capture_pipeline_resources(run_dir)
    existing_trajectory = find_run_trajectory(run_dir)
    trajectory_path = existing_trajectory or find_trajectory(
        args,
        task,
        context,
        run_dir,
        home,
        xdg_config,
        workspace,
    )
    if existing_trajectory is not None and has_trajectory_sample(run_dir):
        result["trajectory_import_success"] = True
        result["trajectory_path"] = str(existing_trajectory)
    elif trajectory_path is not None:
        result["trajectory_path"] = str(trajectory_path)
        try:
            import_trajectory(args, run_id, trajectory_path, env, log_dir, defer_report=True)
            result["trajectory_import_success"] = True
        except Exception as exc:
            result["trajectory_import_success"] = False
            result["error"] = append_error(result.get("error", ""), f"trajectory import: {exc}")
    else:
        result["trajectory_import_success"] = False
        result["error"] = append_error(result.get("error", ""), "trajectory missing")
    stage_durations["trajectory_import_s"] = time.monotonic() - started
    stage_resource_profiles["trajectory_import"] = pipeline_resource_delta(
        stage_profile_start, capture_pipeline_resources(run_dir)
    )

    run_trajectory = find_run_trajectory(run_dir) or trajectory_path
    response_ids = extract_trajectory_response_ids(run_trajectory) if run_trajectory else []
    result["trajectory_response_id_count"] = len(response_ids)
    task_service_p2_events: Path | None = None
    task_service_p2_event_log: dict[str, Any] | None = None

    if args.p2_events and p2_index is not None:
        started = time.monotonic()
        stage_profile_start = capture_pipeline_resources(run_dir)
        try:
            selected, diagnostics, mode = select_indexed_p2_events(
                p2_index,
                run_id=run_id,
                response_ids=set(response_ids),
                filter_mode=args.p2_filter_mode,
                include_window_extras=args.include_p2_window_extras,
                window_margin_seconds=args.p2_window_margin_seconds,
            )
            filtered_path = batch_root / "p2-filtered" / f"{task_id}.events.jsonl"
            write_jsonl_events(filtered_path, selected)
            service_global_events, service_global_diagnostics = select_indexed_p2_global_events(
                p2_index,
                start_ns=diagnostics.get("task_window_start_mono_ns"),
                end_ns=diagnostics.get("task_window_end_mono_ns"),
                missing_window_policy=p2_service_global_missing_window_policy(args),
            )
            task_service_p2_events = (
                batch_root / "p2-filtered" / f"{task_id}.service-global.events.jsonl"
            )
            write_jsonl_events(task_service_p2_events, service_global_events)
            task_service_p2_event_log = {"events": service_global_events, "invalid_lines": 0}
            result.update(service_global_diagnostics)
            result["p2_service_global_path"] = str(task_service_p2_events)
            imported = import_p2(
                args,
                run_id,
                filtered_path,
                env,
                log_dir,
                log_suffix=mode,
                prefiltered=True,
                prefiltered_events=selected,
                run_dir=run_dir,
                defer_report=True,
            )
            imported.update(diagnostics)
            copy_p2_result_fields(result, imported)
            result["p2_import_mode"] = mode
            result["p2_import_success"] = imported.get("p2_request_count", 0) > 0
            if not result["p2_import_success"]:
                result["error"] = append_error(result.get("error", ""), "P2 import matched no requests")
        except Exception as exc:
            result["p2_import_success"] = False
            result["error"] = append_error(result.get("error", ""), f"P2 import: {exc}")
        stage_durations["p2_import_s"] = time.monotonic() - started
        stage_resource_profiles["p2_import"] = pipeline_resource_delta(
            stage_profile_start, capture_pipeline_resources(run_dir)
        )

    shared_run_event_log = read_run_event_log(run_dir)
    result["p1_native_status"] = detect_native_p1_status(
        run_dir,
        events=shared_run_event_log["events"],
    )
    if args.p1_mode == "required" and result["p1_native_status"] != "complete":
        result["error"] = append_error(result.get("error", ""), "native P1 events are required but incomplete")

    started = time.monotonic()
    stage_profile_start = capture_pipeline_resources(run_dir)
    try:
        regenerate_report(
            args,
            run_id,
            env,
            log_dir,
            run_dir=run_dir,
            event_log=shared_run_event_log,
        )
        result["report_generation_success"] = True
        report_validation = read_report_validation(run_dir)
        result["report_publishability_status"] = report_validation.get("publishability_status", "UNPUBLISHABLE")
        result["report_blocking_failures"] = report_validation.get("blocking_failures", [])
        if result["report_publishability_status"] != "PUBLISHABLE":
            result["error"] = append_error(
                result.get("error", ""),
                f"run report unpublishable: {result['report_blocking_failures']}",
            )
    except Exception as exc:
        result["report_generation_success"] = False
        result["report_publishability_status"] = "UNPUBLISHABLE"
        result["report_blocking_failures"] = ["report_generation"]
        result["error"] = append_error(result.get("error", ""), f"report generation: {exc}")
    stage_durations["report_generation_s"] = time.monotonic() - started
    stage_resource_profiles["report_generation"] = pipeline_resource_delta(
        stage_profile_start, capture_pipeline_resources(run_dir)
    )

    started = time.monotonic()
    stage_profile_start = capture_pipeline_resources(run_dir)
    try:
        service_p2_events = (
            task_service_p2_events
            if task_service_p2_events is not None
            else Path(str(p2_index["global_events_path"]))
            if p2_index is not None and p2_index.get("global_events_path")
            else None
        )
        step_paths = write_step_sequence_outputs(
            args,
            run_dir,
            batch_root / "step-sequence" / task_id,
            env,
            service_p2_events=service_p2_events,
            run_event_log=shared_run_event_log,
            service_event_log=task_service_p2_event_log,
        )
        result.update(step_paths)
        result.update(read_step_totals(Path(step_paths["step_sequence_json"])))
        result["p1_analysis_success"] = True
    except Exception as exc:
        result["p1_analysis_success"] = False
        result["error"] = append_error(result.get("error", ""), f"step sequence: {exc}")
    stage_durations["step_analysis_s"] = time.monotonic() - started
    stage_resource_profiles["step_analysis"] = pipeline_resource_delta(
        stage_profile_start, capture_pipeline_resources(run_dir)
    )
    result["monitoring_success"] = bool(
        result.get("report_generation_success") and result.get("p1_analysis_success")
    )

    if args.p2_events:
        match_rate = optional_number(result.get("p2_match_rate"))
        lifecycle_rate = optional_number(result.get("p2_lifecycle_complete_rate"))
        result["p2_analysis_success"] = bool(
            result.get("p2_import_success")
            and match_rate is not None
            and (args.min_p2_match_rate is None or match_rate >= args.min_p2_match_rate)
            and lifecycle_rate is not None
            and (args.min_p2_lifecycle_rate is None or lifecycle_rate >= args.min_p2_lifecycle_rate)
        )
        if not result["p2_analysis_success"]:
            result["error"] = append_error(
                result.get("error", ""),
                f"P2 quality below threshold: match={match_rate} lifecycle={lifecycle_rate}",
            )
    else:
        result["p2_analysis_success"] = None

    if args.judge_command_template:
        started = time.monotonic()
        stage_profile_start = capture_pipeline_resources(run_dir)
        judge_context = {
            **context,
            **task,
            "workspace": str(workspace),
            "cwd": str(cwd),
            "run_id": run_id,
            "run_dir": str(run_dir),
        }
        judge_result, judge_stdout, judge_stderr = run_judge(args, judge_context, env, log_dir)
        result["judge_success"] = judge_result.returncode == 0
        result["judge_status"] = "passed" if result["judge_success"] else "failed"
        result["judge_stdout_log"] = str(judge_stdout)
        result["judge_stderr_log"] = str(judge_stderr)
        stage_durations["judge_s"] = time.monotonic() - started
        stage_resource_profiles["judge"] = pipeline_resource_delta(
            stage_profile_start, capture_pipeline_resources(run_dir)
        )

    analysis_success = bool(
        result.get("monitoring_success")
        and result.get("report_publishability_status") == "PUBLISHABLE"
        and result.get("trajectory_import_success") is not False
        and result.get("p2_import_success") is not False
        and result.get("p2_analysis_success") is not False
        and (args.p1_mode != "required" or result.get("p1_native_status") == "complete")
    )
    result["analysis_pipeline_success"] = analysis_success
    result["pipeline_success"] = analysis_success
    result["usable_run"] = analysis_success
    result["overall_pipeline_success"] = bool(
        analysis_success
        and result.get("execution_success") is True
        and result.get("judge_success") is not False
    )
    if not analysis_success:
        result["status"] = "analysis_failed"
    elif not result.get("execution_success"):
        result["status"] = "execution_failed"
    elif result.get("judge_success") is False:
        result["status"] = "judge_failed"
    else:
        result["status"] = "success"

    result["analysis_stage_durations_s"] = stage_durations
    for profile in stage_resource_profiles.values():
        profile["attribution_scope"] = "shared_analysis_process_observation"
        profile["request_attribution"] = "not_task_exclusive_when_analysis_workers_gt_1"
    result["analysis_stage_resources"] = stage_resource_profiles
    for stage_name, duration_s in stage_durations.items():
        result[f"analysis_{stage_name}"] = duration_s
    result["analysis_end_mono_ns"] = time.monotonic_ns()
    result["analysis_end_wall_ns"] = time.time_ns()
    result["analysis_wall_s"] = (
        result["analysis_end_mono_ns"] - result["analysis_start_mono_ns"]
    ) / 1_000_000_000
    pipeline_profile_path = run_dir / "analysis" / "pipeline_profile.json"
    write_json_private(
        pipeline_profile_path,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "analysis_workers": args.analysis_workers,
            "stage_durations_s": stage_durations,
            "stage_resources": stage_resource_profiles,
        },
    )
    result["analysis_pipeline_profile_path"] = str(pipeline_profile_path)
    validation = write_analysis_quality_bundle(result, run_dir / "analysis")
    result["publishability_status"] = validation["publishability_status"]
    result["validation_checks_path"] = str(run_dir / "analysis" / "validation_checks.json")
    write_task_validation(result, batch_root / "step-sequence" / task_id / "validation_checks.json")
    return result


def run_one_task(
    args: argparse.Namespace,
    task: dict[str, str],
    batch_root: Path,
    env_base: dict[str, str],
) -> dict[str, Any]:
    """Backward-compatible single-task path used by external callers."""
    workload = run_workload_task(args, task, batch_root, env_base)
    p2_index = build_p2_batch_index(Path(args.p2_events).expanduser()) if args.p2_events else None
    return analyze_one_task(args, task, workload, batch_root, env_base, p2_index)


def copy_p2_result_fields(result: dict[str, Any], imported: dict[str, Any]) -> None:
    mapping = {
        "p2_imported_event_count": "imported_event_count",
        "p2_request_count": "p2_request_count",
        "p2_request_event_count": "p2_request_event_count",
        "p2_request_keyed_event_count": "p2_request_keyed_event_count",
        "p2_global_event_count": "p2_global_event_count",
        "p2_sample_event_count": "p2_sample_event_count",
        "p2_total_event_count": "p2_total_event_count",
        "matched_p2_request_count": "matched_p2_request_count",
        "multiple_p2_api_request_id_count": "multiple_p2_api_request_id_count",
        "multiple_p2_api_request_ids": "multiple_p2_api_request_ids",
        "unmatched_trajectory_id_count": "unmatched_trajectory_id_count",
        "p2_requests_in_task_window_not_in_trajectory_count": (
            "p2_requests_in_task_window_not_in_trajectory_count"
        ),
        "p2_query_wall_s": "p2_query_wall_s",
        "p2_bytes_scanned_per_run": "p2_bytes_scanned_per_run",
        "p2_events_scanned_per_run": "p2_events_scanned_per_run",
        "p2_window_candidate_event_count": "p2_window_candidate_event_count",
        "p2_window_time_index_used": "p2_window_time_index_used",
        "p2_index_cache_hit": "index_cache_hit",
        "p2_index_source": "p2_index_source",
    }
    for target, source in mapping.items():
        default: Any = [] if target == "multiple_p2_api_request_ids" else 0
        result[target] = imported.get(source, default)


def detect_native_p1_status(
    run_dir: Path,
    *,
    events: list[dict[str, Any]] | None = None,
) -> str:
    event_types: set[str] = set()
    source_events = events if events is not None else read_jsonl_events(run_dir / "events.jsonl")
    for event in source_events:
        if event.get("source") != "mini_swe":
            continue
        event_type = str(event.get("event_type") or "")
        if event_type:
            event_types.add(event_type)
    required_prefixes = ("agent_step.", "model_request.", "tool_call.")
    present = sum(any(event_type.startswith(prefix) for event_type in event_types) for prefix in required_prefixes)
    if present == len(required_prefixes):
        return "complete"
    if present:
        return "partial"
    return "missing"


def import_trajectory(
    args: argparse.Namespace,
    run_id: str,
    trajectory_path: Path,
    env: dict[str, str],
    log_dir: Path,
    *,
    defer_report: bool = False,
) -> dict[str, Any]:
    stdout = log_dir / "import-trajectory.stdout.log"
    stderr = log_dir / "import-trajectory.stderr.log"
    command = agentmon_command(args, "import-trajectory", str(trajectory_path), "--run-id", run_id)
    if defer_report:
        command.append("--defer-report")
    result = run_logged(
        command,
        env=env,
        stdout_path=stdout,
        stderr_path=stderr,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agentmon import-trajectory failed for {run_id}; see {stderr}")
    return json.loads(read_text(stdout) or "{}")


def import_p2(
    args: argparse.Namespace,
    run_id: str,
    p2_path: Path,
    env: dict[str, str],
    log_dir: Path,
    *,
    trajectory_path: Path | None = None,
    filter_run_id: str | None = None,
    log_suffix: str = "",
    prefiltered: bool = False,
    prefiltered_events: list[dict[str, Any]] | None = None,
    run_dir: Path | None = None,
    defer_report: bool = False,
) -> dict[str, Any]:
    suffix = f".{log_suffix}" if log_suffix else ""
    stdout = log_dir / f"import-p2{suffix}.stdout.log"
    stderr = log_dir / f"import-p2{suffix}.stderr.log"
    if prefiltered_events is not None and run_dir is not None:
        imported_events = [
            normalize_imported_p2_event(
                event,
                run_id=run_id,
                source_path=p2_path,
                start_offset=None,
                end_offset=None,
                quality=args.p2_quality,
                attribution_scope="request_attributed",
            )
            for event in prefiltered_events
            if event.get("source") == "vllm_p2" and not is_p2_global_event(event)
        ]
        if imported_events:
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                for event in imported_events:
                    writer.write_event(event)
        event_counts = summarize_p2_imported_events(imported_events)
        import_metadata = {
            "schema_version": "1.0",
            "source": str(p2_path),
            "source_sha256": file_hash_if_exists(p2_path),
            "global_event_policy": "exclude",
            "global_events_copied": False,
            "observation_scope": "request_attributed_with_service_reference",
            **event_counts,
        }
        p2_import_path = run_dir / "raw" / "p2_import.json"
        write_json_private(p2_import_path, import_metadata)
        result = {
            "schema_version": "1.0",
            "command": "import-p2",
            "run_id": run_id,
            "source": str(p2_path),
            "quality": args.p2_quality,
            "global_event_policy": "exclude",
            "global_events_copied": False,
            "scanned_lines": len(prefiltered_events),
            "invalid_lines": 0,
            "non_p2_event_count": 0,
            "imported_event_count": len(imported_events),
            **event_counts,
            "vllm_p2_status": "complete" if event_counts.get("p2_request_count") else "missing",
            "vllm_p2_request_count": event_counts.get("p2_request_count", 0),
            "report": str(run_dir / "report.txt"),
            "p2_import_metadata": str(p2_import_path),
            "import_execution_mode": "in_process_prefiltered",
        }
        write_json_private(stdout, result)
        stderr.write_text("", encoding="utf-8")
        try:
            stderr.chmod(0o600)
        except OSError:
            pass
        return result
    command = agentmon_command(args, "import-p2", str(p2_path), "--run-id", run_id, "--quality", args.p2_quality)
    command.extend(["--global-event-policy", "exclude"])
    if filter_run_id is not None and not prefiltered:
        command.extend(["--filter-run-id", filter_run_id])
    if trajectory_path is not None and not prefiltered:
        command.extend(["--filter-response-ids-from-trajectory", str(trajectory_path)])
    if args.include_p2_window_extras:
        command.append("--include-p2-window-extras")
        command.extend(["--p2-window-margin-seconds", str(args.p2_window_margin_seconds)])
    if defer_report:
        command.append("--defer-report")
    result = run_logged(
        command,
        env=env,
        stdout_path=stdout,
        stderr_path=stderr,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agentmon import-p2 failed for {run_id}; see {stderr}")
    return json.loads(read_text(stdout) or "{}")


def regenerate_report(
    args: argparse.Namespace,
    run_id: str,
    env: dict[str, str],
    log_dir: Path,
    *,
    run_dir: Path | None = None,
    event_log: dict[str, Any] | None = None,
) -> None:
    stdout = log_dir / "report.stdout.log"
    stderr = log_dir / "report.stderr.log"
    if run_dir is not None and event_log is not None:
        write_reports(run_dir, event_log=event_log)
        stdout.write_text(f"report={run_dir / 'report.json'}\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        for path in (stdout, stderr):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return
    result = run_logged(
        agentmon_command(args, "report", run_id),
        env=env,
        stdout_path=stdout,
        stderr_path=stderr,
    )
    if result.returncode != 0:
        raise RuntimeError(f"agentmon report failed for {run_id}; see {stderr}")


def write_step_sequence_outputs(
    args: argparse.Namespace,
    run_dir: Path,
    output_dir: Path,
    env: dict[str, str],
    *,
    service_p2_events: Path | None = None,
    run_event_log: dict[str, Any] | None = None,
    service_event_log: dict[str, Any] | None = None,
) -> dict[str, str]:
    make_private_dir(output_dir)
    outputs = {
        "json": output_dir / "step_sequence.json",
        "csv": output_dir / "step_sequence.csv",
        "txt": output_dir / "step_sequence.txt",
    }
    default_script = (ROOT / "scripts" / "agentmon_step_sequence.py").resolve()
    configured_script = Path(args.step_sequence_script).expanduser().resolve()
    if run_event_log is not None and configured_script == default_script:
        module = load_step_sequence_module(configured_script)
        analysis = module.build_step_sequence(
            run_dir,
            service_p2_events=service_p2_events,
            ascend_devices=args.ascend_devices,
            run_event_log=run_event_log,
            service_event_log=service_event_log,
        )
        module.write_output_bundle(output_dir, analysis)
    else:
        command = [
            args.agentmon_python,
            args.step_sequence_script,
            str(run_dir),
            "--format",
            "json",
            "--output-dir",
            str(output_dir),
        ]
        if args.ascend_devices:
            command.extend(["--ascend-devices", args.ascend_devices])
        if service_p2_events is not None:
            command.extend(["--service-p2-events", str(service_p2_events)])
        completed = subprocess.run(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"step sequence failed for {run_dir}: {completed.stderr}")
    missing = [str(path) for path in outputs.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"step sequence did not create expected outputs: {', '.join(missing)}")
    return {
        "step_sequence_json": str(outputs["json"]),
        "step_sequence_csv": str(outputs["csv"]),
        "step_sequence_txt": str(outputs["txt"]),
    }


def load_step_sequence_module(path: Path) -> ModuleType:
    cache_key = str(path)
    with _STEP_SEQUENCE_MODULE_LOCK:
        cached = _STEP_SEQUENCE_MODULES.get(cache_key)
        if cached is not None:
            return cached
        spec = importlib.util.spec_from_file_location("_agentmon_step_sequence_runtime", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load step sequence analyzer: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _STEP_SEQUENCE_MODULES[cache_key] = module
        return module


def extract_trajectory_response_ids(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    messages = trajectory_messages(data)
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant" or not isinstance(message.get("tool_calls"), list):
            continue
        response = as_dict(as_dict(message.get("extra")).get("response"))
        response_id = response.get("id")
        if response_id not in {None, ""} and str(response_id) not in seen:
            seen.add(str(response_id))
            ids.append(str(response_id))
    return ids


def trajectory_messages(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    messages = data.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    nested = data.get("trajectory")
    if isinstance(nested, dict) and isinstance(nested.get("messages"), list):
        return [item for item in nested["messages"] if isinstance(item, dict)]
    return []
