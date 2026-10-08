from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import time
from typing import Any

from agentmon.adapters.inference.vllm_018.p2_index import read_jsonl_events
from agentmon.batch.commands import (
    agentmon_command,
    configure_p1_command,
    parse_run_dir,
    parse_run_id,
    run_logged,
    task_command,
)
from agentmon.batch.io import batch_context, make_private_dir, read_text, render_template
from agentmon.batch.safety import revalidate_workspace_contract, render_workspace_contract

def run_workload_task(
    args: argparse.Namespace,
    task: dict[str, str],
    batch_root: Path,
    env_base: dict[str, str],
) -> dict[str, Any]:
    task_id = task["task_id"]
    context = batch_context(task, batch_root)
    protected_roots = getattr(args, "_protected_source_roots", [])
    contracts = getattr(args, "_workspace_contracts", {})
    contract = contracts.get(task_id)
    if contract is None:
        contract = render_workspace_contract(args, task, batch_root, protected_roots)
    contract = revalidate_workspace_contract(contract, protected_roots=protected_roots)
    workspace = Path(str(contract["workspace"]))
    cwd = Path(str(contract["cwd"]))
    home = batch_root / "homes" / task_id
    xdg_config = batch_root / "xdg_config" / task_id
    log_dir = batch_root / "logs" / task_id
    for path in (workspace, cwd, home, xdg_config, log_dir):
        make_private_dir(path)
    contract = revalidate_workspace_contract(contract, protected_roots=protected_roots)

    command = task_command(args, task, {**context, "workspace": str(workspace), "cwd": str(cwd)})
    command, p1_injection_status = configure_p1_command(command, args.p1_mode)
    label = render_template(args.label_template, {**context, "workspace": str(workspace), "cwd": str(cwd)})
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

    stdout_log = log_dir / "agentmon.stdout.log"
    stderr_log = log_dir / "agentmon.stderr.log"
    run_cmd = agentmon_command(
        args,
        "run",
        "--defer-report",
        "--label",
        label,
        "--cwd",
        str(cwd),
        "--",
        "bash",
        "-lc",
        command,
    )
    workload_start_wall_ns = time.time_ns()
    workload_start_mono_ns = time.monotonic_ns()
    run_result = run_logged(run_cmd, env=env, stdout_path=stdout_log, stderr_path=stderr_log)
    workload_end_mono_ns = time.monotonic_ns()
    workload_end_wall_ns = time.time_ns()
    stderr_text = read_text(stderr_log)
    run_id = parse_run_id(stderr_text)
    run_dir = parse_run_dir(stderr_text, args.output_root, run_id)

    result = initial_task_result(
        task_id=task_id,
        command=command,
        p1_injection_status=p1_injection_status,
        run_result=run_result,
        run_id=run_id,
        run_dir=run_dir,
        workspace=workspace,
        home=home,
        xdg_config=xdg_config,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        p2_events=args.p2_events,
    )
    result.update(
        {
            "cwd": str(cwd),
            "workspace_policy": contract["policy"],
            "workspace_policy_status": contract["status"],
            "workspace_contract_path": str(
                batch_root / "workspace-contracts" / f"{task_id}.json"
            ),
            "run_id_required": True,
            "workload_start_wall_ns": workload_start_wall_ns,
            "workload_end_wall_ns": workload_end_wall_ns,
            "workload_start_mono_ns": workload_start_mono_ns,
            "workload_end_mono_ns": workload_end_mono_ns,
            "workload_wall_s": (workload_end_mono_ns - workload_start_mono_ns) / 1_000_000_000,
        }
    )
    if not run_id or run_dir is None:
        result["status"] = "agentmon_run_id_missing"
        result["pipeline_success"] = False
        result["overall_pipeline_success"] = False
        result["publishability_status"] = "UNPUBLISHABLE"
        result["report_blocking_failures"] = ["agentmon_run_id_missing"]
        return result

    trajectory_path = find_trajectory(args, task, context, run_dir, home, xdg_config, workspace)
    if trajectory_path is not None:
        result["trajectory_path"] = str(trajectory_path)
    result["status"] = "workload_complete" if result["execution_success"] else "execution_failed"
    return result


def initial_task_result(
    *,
    task_id: str,
    command: str,
    p1_injection_status: str,
    run_result: subprocess.CompletedProcess[str],
    run_id: str | None,
    run_dir: Path | None,
    workspace: Path,
    home: Path,
    xdg_config: Path,
    stdout_log: Path,
    stderr_log: Path,
    p2_events: str | None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "running",
        "pipeline_success": None,
        "analysis_pipeline_success": None,
        "analysis_deferred": False,
        "analysis_deferred_reason": "",
        "overall_pipeline_success": None,
        "usable_run": None,
        "publishability_status": "NOT_EVALUATED",
        "execution_success": run_result.returncode == 0,
        "monitoring_success": False,
        "report_generation_success": None,
        "report_publishability_status": "NOT_EVALUATED",
        "report_blocking_failures": [],
        "p1_analysis_success": None,
        "trajectory_import_success": None,
        "p1_native_status": "unchecked",
        "p1_injection_status": p1_injection_status,
        "p2_import_success": None,
        "p2_analysis_success": None,
        "judge_success": None,
        "judge_status": "not_configured",
        "agentmon_exit_code": run_result.returncode,
        "run_id": run_id,
        "run_dir": str(run_dir) if run_dir else "",
        "workspace": str(workspace),
        "home": str(home),
        "xdg_config_home": str(xdg_config),
        "command": command,
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "trajectory_path": "",
        "trajectory_response_id_count": 0,
        "matched_p2_request_count": 0,
        "multiple_p2_api_request_id_count": 0,
        "multiple_p2_api_request_ids": [],
        "unmatched_trajectory_id_count": 0,
        "p2_requests_in_task_window_not_in_trajectory_count": 0,
        "p2_source_path": str(Path(p2_events).expanduser()) if p2_events else "",
        "p2_import_mode": "",
        "p2_imported_event_count": 0,
        "p2_request_count": 0,
        "p2_request_event_count": 0,
        "p2_request_keyed_event_count": 0,
        "p2_global_event_count": 0,
        "p2_sample_event_count": 0,
        "p2_total_event_count": 0,
        "p2_query_wall_s": None,
        "p2_bytes_scanned_per_run": None,
        "p2_events_scanned_per_run": None,
        "p2_window_candidate_event_count": None,
        "p2_window_time_index_used": None,
        "p2_service_global_filter_mode": "",
        "p2_service_global_source_event_count": None,
        "p2_service_global_written_event_count": None,
        "p2_service_global_events_scanned": None,
        "p2_service_global_time_index_used": None,
        "p2_service_global_path": "",
        "p2_index_cache_hit": None,
        "p2_index_source": "",
        "step_count": 0,
        "model_request_count": 0,
        "tool_call_count": 0,
        "model_time_s": 0.0,
        "tool_time_s": 0.0,
        "run_model_e2e_sum_s": 0.0,
        "run_tool_duration_sum_s": 0.0,
        "prompt_tokens": 0,
        "generation_tokens": 0,
        "p2_expected_model_request_count": 0,
        "p2_matched_model_request_count": 0,
        "p2_match_rate": None,
        "p2_lifecycle_complete_count": 0,
        "p2_lifecycle_complete_rate": None,
        "trajectory_tool_call_count": 0,
        "p1_tool_span_count": 0,
        "tool_resource_complete_count": 0,
        "analyzer_tool_row_count": 0,
        "step_sequence_json": "",
        "step_sequence_csv": "",
        "step_sequence_txt": "",
        "judge_stdout_log": "",
        "judge_stderr_log": "",
        "error": "",
    }


def failed_worker_result(task: dict[str, str], exc: Exception) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id", "unknown"),
        "status": "worker_error",
        "pipeline_success": False,
        "analysis_pipeline_success": False,
        "analysis_deferred": False,
        "analysis_deferred_reason": "",
        "overall_pipeline_success": False,
        "usable_run": False,
        "publishability_status": "UNPUBLISHABLE",
        "execution_success": None,
        "monitoring_success": False,
        "report_generation_success": False,
        "report_publishability_status": "UNPUBLISHABLE",
        "report_blocking_failures": ["worker_error"],
        "p1_analysis_success": False,
        "trajectory_import_success": None,
        "p1_native_status": "unchecked",
        "p2_import_success": None,
        "p2_analysis_success": None,
        "judge_success": None,
        "agentmon_exit_code": "",
        "run_id": "",
        "error": f"{type(exc).__name__}: {exc}",
    }


def find_trajectory(
    args: argparse.Namespace,
    task: dict[str, str],
    context: dict[str, str],
    run_dir: Path,
    home: Path,
    xdg_config: Path,
    workspace: Path,
) -> Path | None:
    candidates: list[Path] = []
    if args.trajectory_template:
        candidates.append(Path(render_template(args.trajectory_template, {**context, **task, "workspace": workspace})))
    candidates.extend(
        [
            run_dir / "raw" / "trajectory" / "mini.traj.json",
            workspace / "mini.traj.json",
            xdg_config / "mini-swe-agent" / "last_mini_run.traj.json",
            home / ".config" / "mini-swe-agent" / "last_mini_run.traj.json",
        ]
    )
    for candidate in candidates:
        path = candidate.expanduser()
        if path.is_file():
            return path
    return None


def find_run_trajectory(run_dir: Path) -> Path | None:
    trajectory_dir = run_dir / "raw" / "trajectory"
    candidates = [
        trajectory_dir / "mini.traj.json",
        trajectory_dir / "last_mini_run.traj.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if trajectory_dir.exists():
        for pattern in ("*.traj.json", "*.json"):
            matches = sorted(path for path in trajectory_dir.glob(pattern) if path.is_file())
            if matches:
                return matches[0]
    return None


def has_trajectory_sample(run_dir: Path) -> bool:
    return any(
        event.get("source") == "trajectory" and event.get("event_type") == "sample"
        for event in read_jsonl_events(run_dir / "events.jsonl")
    )
