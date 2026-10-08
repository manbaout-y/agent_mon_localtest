from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from pathlib import Path
import sys
from typing import Sequence
from uuid import uuid4

from . import __version__
from .adapters import adapter_capability_catalog
from .adapters.inference.vllm_018.importer import (
    is_p2_global_event,
    is_p2_run_capability_event,
    normalize_imported_p2_event,
    summarize_p2_imported_events,
)
from .adapters.inference.vllm_018.p2_index import (
    is_p2_request_event_type,
    is_p2_request_or_keyed_event_type,
)
from .analysis.cli import add_analysis_parser, dispatch_analysis
from .config import ConfigError, load_config
from .analysis.batch_compare import compare_batch_steps
from .control import GatedOpenAICompatibleProvider, ModelRequestGate, PooledToolExecutor, ToolPoolManager
from .doctor import run_doctor
from .events import Event
from .experiments.prefix_cache import format_prefix_cache_plan_table, plan_prefix_cache_experiment, plan_to_json
from .experiments.matrix import run_repeated_matrix
from .optimizer import OptimizerService
from .optimizer.service import model_service_snapshot_from_file
from .paths import find_run_dir, output_root
from .replay import ReplaySimulator
from .reporting.report import (
    build_report,
    compare_reports,
    export_run,
    format_text_report,
    load_events,
    summarize_p1_spans,
    write_reports,
)
from .reporting.canonical import write_canonical_dataset
from .reporting.summary import (
    build_aggregate,
    build_run_summary,
    format_aggregate_table,
    format_summary_table,
    aggregate_to_csv,
    passes_filters,
    summaries_to_csv,
)
from .reporting.timeline import format_timeline_table, generate_batch_timeline
from .run_controller import RunController
from .runtime import RunSpec, RuntimeService
from .scheduler import scheduler_strategy_catalog
from .storage.jsonl import JsonlWriter
from .optimizer import optimizer_strategy_catalog
from .context_compression import (
    context_compression_strategy_catalog,
    context_compression_trigger_catalog,
)
from .trajectory.mini_swe import import_trajectory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentmon")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the agentmon version and exit",
    )

    subparsers = parser.add_subparsers(dest="command")
    add_analysis_parser(subparsers)
    doctor = subparsers.add_parser("doctor", help="probe environment capabilities")
    add_config_args(doctor)
    doctor.add_argument("--output", help="write doctor JSON to this path")

    run = subparsers.add_parser("run", help="run a command under P0 monitoring")
    add_config_args(run)
    run.add_argument("--label", help="optional run label")
    run.add_argument("--cwd", help="child process working directory")
    run.add_argument("--trajectory", help="explicit mini-SWE trajectory path")
    run.add_argument(
        "--defer-report",
        action="store_true",
        help="finish collection without generating reports; intended for batched post-processing",
    )
    run.add_argument("command_argv", nargs=argparse.REMAINDER)

    runtime = subparsers.add_parser("runtime", help="submit and inspect runtime-managed runs")
    runtime_subparsers = runtime.add_subparsers(dest="runtime_command")
    runtime_submit = runtime_subparsers.add_parser("submit", help="submit a command through a runtime backend")
    add_config_args(runtime_submit)
    runtime_submit.add_argument("--backend", default="process", help="runtime backend name")
    runtime_submit.add_argument("--task-id", help="task id; defaults to a generated id")
    runtime_submit.add_argument("--label", help="optional run label")
    runtime_submit.add_argument("--priority", type=int, default=0, help="scheduler priority for queued execution")
    runtime_submit.add_argument("--cwd", help="child process working directory")
    runtime_submit.add_argument("--trajectory", help="explicit trajectory path")
    runtime_submit.add_argument(
        "--queue",
        action="store_true",
        help="persist the run for runtime daemon execution instead of running synchronously",
    )
    runtime_submit.add_argument(
        "--defer-report",
        action="store_true",
        help="finish collection without generating reports",
    )
    runtime_submit.add_argument("command_argv", nargs=argparse.REMAINDER)
    runtime_status = runtime_subparsers.add_parser("status", help="show one runtime run status")
    add_config_args(runtime_status)
    runtime_status.add_argument("run_id")
    runtime_task_state = runtime_subparsers.add_parser(
        "task-state",
        help="show derived task-state advisory for one runtime run",
    )
    add_config_args(runtime_task_state)
    runtime_task_state.add_argument("run_id")
    runtime_queue = runtime_subparsers.add_parser("queue", help="show the current local runtime run snapshot")
    add_config_args(runtime_queue)
    runtime_capabilities = runtime_subparsers.add_parser("capabilities", help="show registered runtime backend capabilities")
    add_config_args(runtime_capabilities)
    runtime_daemon = runtime_subparsers.add_parser("daemon", help="execute persisted runtime queue records")
    add_config_args(runtime_daemon)
    runtime_daemon.add_argument("--once", action="store_true", help="process one bounded queue-drain pass and exit")
    runtime_daemon.add_argument("--max-runs", type=int, default=1, help="maximum queued runs to process in this pass")
    runtime_daemon.add_argument("--loop", action="store_true", help="keep draining queue passes until idle limit is reached")
    runtime_daemon.add_argument("--idle-sleep", type=float, default=1.0, help="seconds to sleep between idle loop passes")
    runtime_daemon.add_argument("--lease-seconds", type=float, default=3600.0, help="queue running lease duration")
    runtime_daemon.add_argument(
        "--heartbeat-interval",
        type=float,
        default=5.0,
        help="seconds between queue lease heartbeats; 0 disables heartbeat",
    )
    runtime_daemon.add_argument(
        "--max-idle-cycles",
        type=int,
        default=1,
        help="idle loop passes before exit; 0 means run until interrupted",
    )
    runtime_recover = runtime_subparsers.add_parser("recover", help="recover stale runtime queue running records")
    add_config_args(runtime_recover)
    runtime_recover.add_argument("--stale-seconds", type=float, default=3600.0)
    runtime_recover.add_argument("--action", choices=["defer", "fail"], default="defer")
    runtime_replay_events = runtime_subparsers.add_parser("replay-events", help="replay runtime audit events")
    add_config_args(runtime_replay_events)
    runtime_replay_events.add_argument("--events", required=True, help="runtime audit JSONL file")

    optimizer = subparsers.add_parser("optimizer", help="inspect runtime optimizer decisions")
    optimizer_subparsers = optimizer.add_subparsers(dest="optimizer_command")
    optimizer_decisions = optimizer_subparsers.add_parser("decisions", help="print the current optimizer decision")
    add_config_args(optimizer_decisions)
    optimizer_decisions.add_argument("--model-service-snapshot", help="model service telemetry snapshot JSON file")
    optimizer_replay = optimizer_subparsers.add_parser("replay", help="replay an optimizer decision from a snapshot JSON")
    add_config_args(optimizer_replay)
    optimizer_replay.add_argument("--snapshot", required=True, help="scheduler/runtime/queue snapshot JSON file")
    optimizer_experiment = optimizer_subparsers.add_parser(
        "experiment",
        help="build offline evidence and replay optimizer advice over a batch",
    )
    add_config_args(optimizer_experiment)
    optimizer_experiment.add_argument("--batch-root", required=True, help="batch directory containing AgentMon batch outputs")
    optimizer_experiment.add_argument("--output-dir", required=True, help="directory for optimizer experiment outputs")
    optimizer_experiment.add_argument(
        "--strategy",
        action="append",
        dest="strategies",
        default=[],
        help="optimizer strategy to replay; may be repeated",
    )
    optimizer_experiment.add_argument(
        "--baseline-strategy",
        help="strategy to use as the offline advice-proxy baseline; defaults to the first replayed strategy",
    )
    optimizer_experiment.add_argument(
        "--mechanism-profile",
        help="optional JSON object with mechanism evaluation mappings and fusion policy",
    )
    optimizer_experiment.add_argument(
        "--schema-profile",
        help="optional JSON object with step CSV and P2 service event field mappings",
    )
    optimizer_experiment.add_argument(
        "--run-search-root",
        action="append",
        default=[],
        help="explicit root to search recursively for run_id/events.jsonl; may be repeated",
    )
    optimizer_experiment.add_argument(
        "--preflight-only",
        action="store_true",
        help="inspect batch inputs and timeline evidence without running replay",
    )
    optimizer_experiment.add_argument("--status-file", help="write optimizer experiment progress JSON")
    optimizer_experiment.add_argument(
        "--progress-interval-seconds",
        type=float,
        help="minimum wall-clock interval between detailed progress updates",
    )
    optimizer_experiment.add_argument("--resume", action="store_true", help="reuse validated completed pipeline stages when identities match")
    optimizer_experiment.add_argument(
        "--memory-budget-bytes",
        type=int,
        default=32 * 1024 * 1024,
        help="bounded in-memory budget for replay sorting and snapshot parts",
    )

    strategy = subparsers.add_parser("strategy", help="inspect registered runtime strategies")
    strategy_subparsers = strategy.add_subparsers(dest="strategy_command")
    strategy_list = strategy_subparsers.add_parser("list", help="list registered strategies")
    strategy_list.add_argument(
        "--domain",
        choices=[
            "all",
            "scheduler",
            "optimizer",
            "context_compression",
            "context_compression_trigger",
        ],
        default="all",
    )

    adapter = subparsers.add_parser("adapter", help="inspect registered adapter capabilities")
    adapter_subparsers = adapter.add_subparsers(dest="adapter_command")
    adapter_capabilities = adapter_subparsers.add_parser("capabilities", help="list adapter capabilities")
    adapter_capabilities.add_argument(
        "--domain",
        choices=["all", "agent_framework", "inference", "resource", "tool"],
        default="all",
    )

    control = subparsers.add_parser("control", help="inspect runtime control-plane status")
    control_subparsers = control.add_subparsers(dest="control_command")
    model_gate = control_subparsers.add_parser("model-gate", help="inspect model request gate status")
    model_gate_subparsers = model_gate.add_subparsers(dest="model_gate_command")
    model_gate_status = model_gate_subparsers.add_parser("status", help="print model request gate status")
    add_config_args(model_gate_status)
    model_gate_status.add_argument("--current-inflight", type=int, help="override current inflight request count")
    model_gate_acquire = model_gate_subparsers.add_parser("acquire", help="try to acquire a model request gate slot")
    add_config_args(model_gate_acquire)
    model_gate_acquire.add_argument("--request-id", help="optional request id for the lease")
    model_gate_acquire.add_argument("--current-inflight", type=int, help="override current inflight request count")
    model_gate_chat = model_gate_subparsers.add_parser(
        "chat-completions",
        help="post an OpenAI-compatible chat completion request through the model gate",
    )
    add_config_args(model_gate_chat)
    model_gate_chat.add_argument("--payload", required=True, help="JSON request body file")
    model_gate_chat.add_argument("--request-id", help="optional request id for the lease")
    model_gate_chat.add_argument("--current-inflight", type=int, help="override current inflight request count")
    tool_pool = control_subparsers.add_parser("tool-pool", help="inspect tool pool status")
    tool_pool_subparsers = tool_pool.add_subparsers(dest="tool_pool_command")
    tool_pool_status = tool_pool_subparsers.add_parser("status", help="print tool pool status")
    add_config_args(tool_pool_status)
    tool_pool_classify = tool_pool_subparsers.add_parser("classify", help="classify a command into a tool pool")
    add_config_args(tool_pool_classify)
    tool_pool_classify.add_argument("command_argv", nargs=argparse.REMAINDER)
    tool_pool_acquire = tool_pool_subparsers.add_parser("acquire", help="try to acquire a tool pool slot")
    add_config_args(tool_pool_acquire)
    tool_pool_acquire.add_argument("--running-count", type=int, help="override running count for the selected pool")
    tool_pool_acquire.add_argument("command_argv", nargs=argparse.REMAINDER)
    tool_pool_exec = tool_pool_subparsers.add_parser("exec", help="execute a command through the tool pool")
    add_config_args(tool_pool_exec)
    tool_pool_exec.add_argument("--cwd", help="child process working directory")
    tool_pool_exec.add_argument("--timeout-seconds", type=float, help="tool execution timeout")
    tool_pool_exec.add_argument("--running-count", type=int, help="override running count for the selected pool")
    tool_pool_exec.add_argument("command_argv", nargs=argparse.REMAINDER)

    live = subparsers.add_parser("live", help="show a simple live view for a run")
    add_config_args(live)
    live.add_argument("run_id")
    live.add_argument("--once", action="store_true", help="print once and exit")
    live.add_argument("--interval", type=float, default=1.0)

    report = subparsers.add_parser("report", help="generate and print a run report")
    add_config_args(report)
    report.add_argument("run_id")

    compare = subparsers.add_parser("compare", help="compare two run reports")
    add_config_args(compare)
    compare.add_argument("run_id_a", nargs="?")
    compare.add_argument("run_id_b", nargs="?")
    compare.add_argument("--baseline", help="baseline batch directory for task/step pairing")
    compare.add_argument("--target", help="target batch directory for task/step pairing")
    compare.add_argument("--pair-by", choices=["task", "task,step"], default="task,step")

    importer = subparsers.add_parser("import-trajectory", help="import a mini-SWE trajectory")
    add_config_args(importer)
    importer.add_argument("path")
    importer.add_argument("--run-id", help="append trajectory event to an existing run")
    importer.add_argument("--defer-report", action="store_true", help="append events without regenerating reports")

    p2_importer = subparsers.add_parser("import-p2", help="import a vLLM P2 service event slice into a run")
    add_config_args(p2_importer)
    p2_importer.add_argument("path", help="service-scoped P2 events.jsonl")
    p2_importer.add_argument("--run-id", required=True, help="append P2 events to this AgentMon run")
    p2_importer.add_argument("--start-offset", type=int, help="start byte offset in the source P2 JSONL")
    p2_importer.add_argument("--end-offset", type=int, help="exclusive end byte offset in the source P2 JSONL")
    p2_importer.add_argument(
        "--quality",
        choices=["direct", "service_exclusive", "service_stream", "heuristic"],
        default="service_stream",
        help="attribution quality label for the imported slice",
    )
    p2_importer.add_argument("--filter-run-id", help="keep only P2 events whose top-level or payload run_id matches")
    p2_importer.add_argument(
        "--filter-api-request-id",
        action="append",
        default=[],
        help="keep P2 request events matching this payload.api_request_id; may be repeated",
    )
    p2_importer.add_argument(
        "--filter-api-request-id-file",
        help="file containing api request ids, one per line or JSON array",
    )
    p2_importer.add_argument(
        "--filter-response-ids-from-trajectory",
        help="mini-SWE trajectory whose assistant extra.response.id values select P2 requests",
    )
    p2_importer.add_argument(
        "--include-p2-window-extras",
        action="store_true",
        help="also include unmatched P2 request rows inside the matched request time window; unsafe for shared-service concurrency",
    )
    p2_importer.add_argument(
        "--p2-window-margin-seconds",
        type=float,
        default=1.0,
        help="time margin for importing KV snapshots/context around matched P2 requests",
    )
    p2_importer.add_argument(
        "--global-event-policy",
        choices=["auto", "exclude", "copy"],
        default="auto",
        help="global P2 event handling; auto excludes them for filtered imports and copies them only for unfiltered slices",
    )
    p2_importer.add_argument(
        "--require-events",
        action="store_true",
        help="exit non-zero if no vllm_p2 events were imported",
    )
    p2_importer.add_argument("--defer-report", action="store_true", help="append events without regenerating reports")

    exporter = subparsers.add_parser("export", help="export report data for a run")
    add_config_args(exporter)
    exporter.add_argument("run_id")
    exporter.add_argument("--format", choices=["json", "csv"], default="json")

    canonical = subparsers.add_parser("canonical", help="export stable runs/requests/tools/events/system tables")
    add_config_args(canonical)
    canonical.add_argument("run_ids", nargs="*", help="run ids or run directories")
    canonical.add_argument("--runs-root", help="scan every run directory under this root")
    canonical.add_argument("--output-dir", help="canonical dataset output directory")
    canonical.add_argument(
        "--format",
        action="append",
        choices=["jsonl", "csv", "parquet"],
        dest="canonical_formats",
        help="output format; may be repeated (default: JSONL, CSV, and Parquet when pyarrow is available)",
    )
    canonical.add_argument(
        "--require-parquet",
        action="store_true",
        help="exit non-zero unless Parquet files were generated",
    )

    summary = subparsers.add_parser("summary", help="print a compact machine-readable run summary")
    add_config_args(summary)
    summary.add_argument("run_id")
    summary.add_argument("--format", choices=["table", "json", "csv"], default="table")
    summary.add_argument("--strict", action="store_true", help="exit non-zero on failed runs or data integrity issues")

    aggregate = subparsers.add_parser("aggregate", help="aggregate compact summaries across runs")
    add_config_args(aggregate)
    aggregate.add_argument("run_ids", nargs="*")
    aggregate.add_argument("--runs-root", help="scan run directories under this root")
    aggregate.add_argument("--label", help="include only runs with this manifest label")
    aggregate.add_argument("--status", help="include only runs with this run status")
    aggregate.add_argument("--started-after", help="include runs starting at or after this epoch seconds/ns timestamp")
    aggregate.add_argument("--started-before", help="include runs starting at or before this epoch seconds/ns timestamp")
    aggregate.add_argument("--format", choices=["table", "json", "csv"], default="table")
    aggregate.add_argument("--strict", action="store_true", help="exit non-zero if any included run violates strict checks")

    timeline = subparsers.add_parser("timeline", help="build batch run/model/tool timelines and integrity checks")
    timeline.add_argument("batch_dir", help="batch directory containing batch-manifest.json and batch-results.tsv")
    timeline.add_argument("--output-dir", help="output directory; defaults to <batch_dir>/timeline")
    timeline.add_argument(
        "--bucket-seconds",
        action="append",
        type=float,
        dest="bucket_seconds",
        help="timeline bucket size; may be repeated (default: 1 and 5)",
    )
    timeline.add_argument("--format", choices=["table", "json"], default="table")
    timeline.add_argument("--strict", action="store_true", help="exit non-zero when timeline validation is unpublishable")

    matrix = subparsers.add_parser("matrix", help="repeat an experiment command in isolated output directories")
    matrix.add_argument("--repeat", type=int, required=True)
    matrix.add_argument("--output-root", required=True)
    matrix.add_argument("--label")
    matrix.add_argument("--continue-on-error", action="store_true")
    matrix.add_argument("command_argv", nargs=argparse.REMAINDER)

    experiment = subparsers.add_parser("experiment", help="plan and inspect controlled experiments")
    experiment_subparsers = experiment.add_subparsers(dest="experiment_command")
    plan = experiment_subparsers.add_parser("plan", help="create a non-mutating experiment plan")
    plan_subparsers = plan.add_subparsers(dest="plan_type")
    prefix_cache = plan_subparsers.add_parser("prefix-cache", help="plan a prefix-cache A/B experiment")
    add_config_args(prefix_cache)
    prefix_cache.add_argument("--baseline-run", action="append", required=True, help="baseline AgentMon run id")
    prefix_cache.add_argument("--variant-config", required=True, help="candidate config for the variant condition")
    prefix_cache.add_argument("--format", choices=["json", "table"], default="json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"agentmon {__version__}")
        return 0

    if args.command == "doctor":
        return _doctor(args)
    if args.command == "analysis":
        return dispatch_analysis(args)
    if args.command == "run":
        return _run(args)
    if args.command == "runtime":
        return _runtime(args)
    if args.command == "optimizer":
        return _optimizer(args)
    if args.command == "strategy":
        return _strategy(args)
    if args.command == "adapter":
        return _adapter(args)
    if args.command == "control":
        return _control(args)
    if args.command == "live":
        return _live(args)
    if args.command == "report":
        return _report(args)
    if args.command == "compare":
        return _compare(args)
    if args.command == "import-trajectory":
        return _import_trajectory(args)
    if args.command == "import-p2":
        return _import_p2(args)
    if args.command == "export":
        return _export(args)
    if args.command == "canonical":
        return _canonical(args)
    if args.command == "summary":
        return _summary(args)
    if args.command == "aggregate":
        return _aggregate(args)
    if args.command == "timeline":
        return _timeline(args)
    if args.command == "matrix":
        return _matrix(args)
    if args.command == "experiment":
        return _experiment(args)

    parser.print_help()
    return 0


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to agentmon TOML config")
    parser.add_argument(
        "--allow-unknown-config",
        action="store_true",
        help="ignore unknown config keys during migrations",
    )
    parser.add_argument("--output-root", help="override general.output_root")


def _load_cfg(args: argparse.Namespace):
    overrides = {}
    if getattr(args, "output_root", None):
        overrides["general.output_root"] = args.output_root
    return load_config(
        config_path=getattr(args, "config", None),
        allow_unknown_config=getattr(args, "allow_unknown_config", False),
        cli_overrides=overrides,
    )


def _doctor(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
    except ConfigError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "command": "doctor",
                    "status": "misconfigured",
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    report, exit_code = run_doctor(cfg, output=args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def _run(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
    except ConfigError as exc:
        print(f"agentmon config error: {exc}", file=sys.stderr)
        return 2
    argv = list(args.command_argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("agentmon run requires a command after --", file=sys.stderr)
        return 64
    controller = RunController(
        cfg,
        argv,
        label=args.label,
        cwd=args.cwd,
        trajectory=args.trajectory,
        write_final_report=not args.defer_report,
    )
    exit_code = controller.run()
    print(f"agentmon run_id={controller.run_id} report={controller.run_dir / 'report.txt'}", file=sys.stderr)
    return exit_code


def _runtime(args: argparse.Namespace) -> int:
    if args.runtime_command is None:
        print("agentmon runtime requires a supported subcommand", file=sys.stderr)
        return 64
    try:
        cfg = _load_cfg(args)
    except ConfigError as exc:
        print(f"agentmon runtime config error: {exc}", file=sys.stderr)
        return 2
    service = RuntimeService.with_default_backends(cfg)
    if args.runtime_command == "capabilities":
        print(json.dumps(service.backend_capabilities(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "queue":
        print(json.dumps(service.queue_snapshot(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "daemon":
        try:
            if args.loop and not args.once:
                result = _runtime_daemon_loop(
                    service,
                    max_runs=args.max_runs,
                    idle_sleep=args.idle_sleep,
                    max_idle_cycles=args.max_idle_cycles,
                    lease_seconds=args.lease_seconds,
                    heartbeat_interval_seconds=args.heartbeat_interval,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 1 if result["failed_count"] else 0
            result = service.drain_queue_once(
                max_runs=args.max_runs,
                lease_seconds=args.lease_seconds,
                heartbeat_interval_seconds=args.heartbeat_interval,
            )
        except Exception as exc:
            print(f"agentmon runtime daemon error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if result.failed_count else 0
    if args.runtime_command == "recover":
        try:
            result = service.recover_queue_orphans(stale_seconds=args.stale_seconds, action=args.action)
        except Exception as exc:
            print(f"agentmon runtime recover error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "replay-events":
        try:
            result = ReplaySimulator(cfg).replay_runtime_events_file(args.events)
        except Exception as exc:
            print(f"agentmon runtime replay-events error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "status":
        try:
            status = service.status(args.run_id)
        except Exception as exc:
            print(f"agentmon runtime status error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "task-state":
        try:
            state = service.task_state(args.run_id)
        except Exception as exc:
            print(f"agentmon runtime task-state error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.runtime_command == "submit":
        argv = list(args.command_argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            print("agentmon runtime submit requires a command after --", file=sys.stderr)
            return 64
        task_id = args.task_id or f"runtime-{uuid4().hex[:8]}"
        labels = {"label": args.label} if args.label else {}
        spec = RunSpec(
            task_id=task_id,
            backend=args.backend,
            command=tuple(argv),
            cwd=args.cwd,
            labels=labels,
            priority=args.priority,
            trajectory=args.trajectory,
            write_final_report=not args.defer_report,
        )
        try:
            if args.queue:
                queued = service.enqueue(spec)
                print(json.dumps(queued.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = service.submit_blocking(spec)
        except Exception as exc:
            print(f"agentmon runtime submit error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return result.exit_code
    print("agentmon runtime requires a supported subcommand", file=sys.stderr)
    return 64


def _runtime_daemon_loop(
    service: RuntimeService,
    *,
    max_runs: int,
    idle_sleep: float,
    max_idle_cycles: int,
    lease_seconds: float,
    heartbeat_interval_seconds: float,
) -> dict[str, object]:
    passes: list[dict[str, object]] = []
    totals = {
        "processed_count": 0,
        "started_count": 0,
        "deferred_count": 0,
        "failed_count": 0,
    }
    idle_cycles = 0
    while True:
        result = service.drain_queue_once(
            max_runs=max_runs,
            lease_seconds=lease_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        result_dict = result.to_dict()
        passes.append(result_dict)
        for key in totals:
            totals[key] += int(result_dict[key])
        if result.processed_count == 0:
            idle_cycles += 1
            if max_idle_cycles > 0 and idle_cycles >= max_idle_cycles:
                break
            time.sleep(max(0.0, idle_sleep))
            continue
        idle_cycles = 0
    return {
        "schema_version": "1.0",
        "result_type": "agentmon.runtime.daemon_loop_result",
        "pass_count": len(passes),
        "idle_cycles": idle_cycles,
        **totals,
        "passes": passes,
    }


def _optimizer(args: argparse.Namespace) -> int:
    if args.optimizer_command is None:
        print("agentmon optimizer requires a supported subcommand", file=sys.stderr)
        return 64
    try:
        cfg = _load_cfg(args)
    except ConfigError as exc:
        print(f"agentmon optimizer config error: {exc}", file=sys.stderr)
        return 2
    if args.optimizer_command == "experiment":
        try:
            from .optimizer.harness.runner import run_optimizer_experiment_harness
            from .optimizer.harness.schemas import UNRESOLVED
            from .pipeline import FAILED

            mechanism_profile = read_optional_json_object(args.mechanism_profile)
            schema_profile = read_optional_json_object(args.schema_profile, "schema profile")
            result = run_optimizer_experiment_harness(
                batch_root=args.batch_root,
                output_dir=args.output_dir,
                cfg=cfg,
                strategies=args.strategies or [cfg.optimizer.strategy],
                baseline_strategy=args.baseline_strategy,
                mechanism_profile=mechanism_profile,
                schema_profile=schema_profile,
                run_search_roots=args.run_search_root,
                preflight_only=args.preflight_only,
                status_file=args.status_file,
                progress_interval_seconds=args.progress_interval_seconds,
                resume=args.resume,
                memory_budget_bytes=args.memory_budget_bytes,
            )
        except Exception as exc:
            print(f"agentmon optimizer experiment error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if result.get("status") == FAILED:
            return 2
        return 3 if result.get("status") == UNRESOLVED else 0
    if args.optimizer_command == "decisions":
        service = OptimizerService(cfg)
        model_service = (
            model_service_snapshot_from_file(args.model_service_snapshot)
            if args.model_service_snapshot
            else None
        )
        print(json.dumps(service.decision(model_service=model_service).to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.optimizer_command == "replay":
        try:
            result = ReplaySimulator(cfg).replay_optimizer_snapshot_file(args.snapshot)
        except Exception as exc:
            print(f"agentmon optimizer replay error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print("agentmon optimizer requires a supported subcommand", file=sys.stderr)
    return 64


def read_optional_json_object(path: str | None, label: str = "mechanism profile") -> dict[str, object] | None:
    if not path:
        return None
    value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _strategy(args: argparse.Namespace) -> int:
    if args.strategy_command is None:
        print("agentmon strategy requires a supported subcommand", file=sys.stderr)
        return 64
    if args.strategy_command == "list":
        rows: list[dict[str, str]] = []
        if args.domain in {"all", "scheduler"}:
            rows.extend(scheduler_strategy_catalog())
        if args.domain in {"all", "optimizer"}:
            rows.extend(optimizer_strategy_catalog())
        if args.domain in {"all", "context_compression"}:
            rows.extend(context_compression_strategy_catalog())
        if args.domain in {"all", "context_compression_trigger"}:
            rows.extend(context_compression_trigger_catalog())
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "result_type": "agentmon.strategy.catalog",
                    "strategies": rows,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print("agentmon strategy requires a supported subcommand", file=sys.stderr)
    return 64


def _adapter(args: argparse.Namespace) -> int:
    if args.adapter_command is None:
        print("agentmon adapter requires a supported subcommand", file=sys.stderr)
        return 64
    if args.adapter_command == "capabilities":
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "result_type": "agentmon.adapter.capability_catalog",
                    "adapters": adapter_capability_catalog(domain=args.domain),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print("agentmon adapter requires a supported subcommand", file=sys.stderr)
    return 64


def _control(args: argparse.Namespace) -> int:
    if args.control_command is None:
        print("agentmon control requires a supported subcommand", file=sys.stderr)
        return 64
    try:
        cfg = _load_cfg(args)
    except ConfigError as exc:
        print(f"agentmon control config error: {exc}", file=sys.stderr)
        return 2
    if args.control_command == "model-gate" and args.model_gate_command == "status":
        decision = OptimizerService(cfg).decision()
        status = ModelRequestGate(cfg).status(decision, current_inflight_requests=args.current_inflight)
        print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.control_command == "model-gate" and args.model_gate_command == "acquire":
        decision = OptimizerService(cfg).decision()
        lease = ModelRequestGate(cfg).try_acquire(
            decision,
            request_id=args.request_id,
            current_inflight_requests=args.current_inflight,
        )
        print(json.dumps(lease.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if lease.admitted else 75
    if args.control_command == "model-gate" and args.model_gate_command == "chat-completions":
        try:
            payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            decision = OptimizerService(cfg).decision()
            result = GatedOpenAICompatibleProvider(cfg).chat_completions(
                payload,
                decision=decision,
                request_id=args.request_id,
                current_inflight_requests=args.current_inflight,
            )
        except Exception as exc:
            print(f"agentmon control model-gate chat-completions error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if not result.accepted:
            return 75
        return 0 if result.error is None else 1
    if args.control_command == "tool-pool" and args.tool_pool_command == "status":
        status = ToolPoolManager(cfg).status()
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.control_command == "tool-pool" and args.tool_pool_command == "classify":
        argv = list(args.command_argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            print("agentmon control tool-pool classify requires a command after --", file=sys.stderr)
            return 64
        assignment = ToolPoolManager(cfg).classify(argv)
        print(json.dumps(assignment.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.control_command == "tool-pool" and args.tool_pool_command == "acquire":
        argv = list(args.command_argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            print("agentmon control tool-pool acquire requires a command after --", file=sys.stderr)
            return 64
        manager = ToolPoolManager(cfg)
        assignment = manager.classify(argv)
        running_counts = None
        if args.running_count is not None:
            running_counts = {assignment.pool_id: args.running_count}
        lease = manager.try_acquire(argv, running_counts=running_counts)
        print(json.dumps(lease.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if lease.admitted else 75
    if args.control_command == "tool-pool" and args.tool_pool_command == "exec":
        argv = list(args.command_argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            print("agentmon control tool-pool exec requires a command after --", file=sys.stderr)
            return 64
        manager = ToolPoolManager(cfg)
        assignment = manager.classify(argv)
        running_counts = None
        if args.running_count is not None:
            running_counts = {assignment.pool_id: args.running_count}
        result = PooledToolExecutor(cfg, manager).execute(
            argv,
            cwd=args.cwd,
            timeout_seconds=args.timeout_seconds,
            running_counts=running_counts,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if not result.accepted:
            return 75
        return result.exit_code or 0
    print("agentmon control requires a supported subcommand", file=sys.stderr)
    return 64


def _report(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        run_dir = find_run_dir(cfg, args.run_id)
        report = write_reports(run_dir)
    except Exception as exc:
        print(f"agentmon report error: {exc}", file=sys.stderr)
        return 2
    print(format_text_report(report))
    return 0


def _export(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        print(export_run(find_run_dir(cfg, args.run_id), fmt=args.format), end="")
    except Exception as exc:
        print(f"agentmon export error: {exc}", file=sys.stderr)
        return 2
    return 0


def _canonical(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        base = Path(args.runs_root).expanduser() if args.runs_root else output_root(cfg)
        if args.run_ids:
            run_dirs = [resolve_run_arg(base, value) for value in args.run_ids]
        elif args.runs_root:
            run_dirs = sorted(
                path for path in base.iterdir() if path.is_dir() and (path / "manifest.json").is_file()
            ) if base.exists() else []
        else:
            raise ValueError("canonical requires at least one run id or --runs-root")
        if not run_dirs:
            raise ValueError("no AgentMon run directories found")
        output_dir = (
            Path(args.output_dir).expanduser()
            if args.output_dir
            else run_dirs[0] / "canonical"
            if len(run_dirs) == 1
            else base / "canonical-dataset"
        )
        formats = args.canonical_formats or ["jsonl", "csv", "parquet-if-available"]
        manifest = write_canonical_dataset(run_dirs, output_dir, formats=formats)
    except Exception as exc:
        print(f"agentmon canonical error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.require_parquet and manifest.get("parquet_status") != "complete" else 0


def _summary(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        report = load_report_for_run(find_run_dir(cfg, args.run_id))
        summary = build_run_summary(report)
    except Exception as exc:
        print(f"agentmon summary error: {exc}", file=sys.stderr)
        return 2
    print(format_summary_output(summary, args.format), end="" if args.format == "csv" else "\n")
    return 1 if args.strict and summary.get("strict_failures") else 0


def _aggregate(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        run_dirs = resolve_aggregate_run_dirs(cfg, args)
        started_after_ns = parse_time_filter(args.started_after)
        started_before_ns = parse_time_filter(args.started_before)
        summaries: list[dict[str, object]] = []
        reports: list[dict[str, object]] = []
        skipped: list[dict[str, str]] = []
        for run_dir in run_dirs:
            try:
                report = load_report_for_run(run_dir)
                summary = build_run_summary(report)
            except Exception as exc:
                skipped.append({"run_dir": str(run_dir), "error": str(exc)})
                continue
            if not passes_filters(
                summary,
                label=args.label,
                status=args.status,
                started_after_ns=started_after_ns,
                started_before_ns=started_before_ns,
                report=report,
            ):
                continue
            summaries.append(summary)
            reports.append(report)
        aggregate = build_aggregate(summaries, reports=reports)
        aggregate["skipped_runs"] = skipped
    except Exception as exc:
        print(f"agentmon aggregate error: {exc}", file=sys.stderr)
        return 2
    print(format_aggregate_output(aggregate, args.format), end="" if args.format == "csv" else "\n")
    return 1 if args.strict and aggregate.get("strict_failure_run_count") else 0


def _timeline(args: argparse.Namespace) -> int:
    try:
        summary = generate_batch_timeline(
            args.batch_dir,
            output_dir=args.output_dir,
            bucket_sizes=args.bucket_seconds or (1.0, 5.0),
        )
    except Exception as exc:
        print(f"agentmon timeline error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_timeline_table(summary))
    return 1 if args.strict and summary.get("publishability_status") != "PUBLISHABLE" else 0


def _matrix(args: argparse.Namespace) -> int:
    argv = list(args.command_argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    try:
        result = run_repeated_matrix(
            argv,
            repeat=args.repeat,
            output_root=args.output_root,
            label=args.label,
            continue_on_error=args.continue_on_error,
        )
    except Exception as exc:
        print(f"agentmon matrix error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result.get("failure_count") else 0


def _experiment(args: argparse.Namespace) -> int:
    if args.experiment_command == "plan" and args.plan_type == "prefix-cache":
        return _experiment_plan_prefix_cache(args)
    print("agentmon experiment requires a supported subcommand", file=sys.stderr)
    return 64


def _experiment_plan_prefix_cache(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        reports = [load_report_for_run(find_run_dir(cfg, run_id)) for run_id in args.baseline_run]
        summaries = [build_run_summary(report) for report in reports]
        plan = plan_prefix_cache_experiment(
            baseline_reports=reports,
            baseline_summaries=summaries,
            variant_config=args.variant_config,
        )
    except Exception as exc:
        print(f"agentmon experiment plan prefix-cache error: {exc}", file=sys.stderr)
        return 2
    if args.format == "table":
        print(format_prefix_cache_plan_table(plan))
    else:
        print(plan_to_json(plan))
    return 0


def _compare(args: argparse.Namespace) -> int:
    try:
        if args.baseline or args.target:
            if not args.baseline or not args.target:
                raise ValueError("batch comparison requires both --baseline and --target")
            comparison = compare_batch_steps(args.baseline, args.target, pair_by=args.pair_by)
            print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if comparison.get("comparable") else 1
        if not args.run_id_a or not args.run_id_b:
            raise ValueError("run comparison requires run_id_a and run_id_b")
        cfg = _load_cfg(args)
        comparison = compare_reports(
            find_run_dir(cfg, args.run_id_a),
            find_run_dir(cfg, args.run_id_b),
        )
    except Exception as exc:
        print(f"agentmon compare error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_report_for_run(run_dir: str | Path) -> dict[str, object]:
    run_path = Path(run_dir)
    report_path = run_path / "report.json"
    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))
    try:
        report = build_report(run_path)
        if report.get("run_id") or report.get("manifest"):
            return report
    except Exception:
        pass
    text_path = run_path / "report.txt"
    if text_path.exists():
        return parse_text_report(text_path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"run report not found: {run_path}")


def parse_text_report(text: str) -> dict[str, object]:
    report: dict[str, object] = {"schema_version": "parsed-text-fallback"}
    lines = text.splitlines()
    section_map = {
        "Data quality": "data_quality",
        "Event Log": "event_log",
        "Agent": "agent",
        "P1 Spans": "p1_spans",
        "Tool Attribution": "tool_attribution",
        "vLLM": "vllm",
        "vLLM P2": "vllm_p2",
        "KV Cache": "kv_cache",
        "Proc": "proc",
        "Ascend": "ascend",
    }
    for index, line in enumerate(lines[:-1]):
        key = section_map.get(line.strip())
        if not key:
            continue
        try:
            report[key] = json.loads(lines[index + 1])
        except json.JSONDecodeError:
            continue
    if lines:
        header = lines[2] if len(lines) > 2 else ""
        parts = header.split()
        for part in parts:
            if part.startswith("status="):
                report["status"] = part.split("=", 1)[1]
            elif part.startswith("exit="):
                try:
                    report["exit_code"] = int(part.split("=", 1)[1])
                except ValueError:
                    report["exit_code"] = None
    return report


def resolve_aggregate_run_dirs(cfg: object, args: argparse.Namespace) -> list[Path]:
    base = Path(args.runs_root).expanduser() if args.runs_root else output_root(cfg)  # type: ignore[arg-type]
    if args.run_ids:
        return [resolve_run_arg(base, run_id) for run_id in args.run_ids]
    if not base.exists():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir())


def resolve_run_arg(base: Path, run_id: str) -> Path:
    path = Path(run_id).expanduser()
    if path.exists():
        return path
    return base / run_id


def parse_time_filter(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
        return int(numeric if numeric > 10_000_000_000_000 else numeric * 1_000_000_000)
    except ValueError:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)


def as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def integer_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def format_summary_output(summary: dict[str, object], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if fmt == "csv":
        return summaries_to_csv([summary])
    return format_summary_table(summary)


def format_aggregate_output(aggregate: dict[str, object], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True)
    if fmt == "csv":
        return aggregate_to_csv(aggregate)
    return format_aggregate_table(aggregate)


def _import_trajectory(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        if args.run_id:
            run_dir = find_run_dir(cfg, args.run_id)
            imported = import_trajectory(args.path, run_id=args.run_id, raw_dir=run_dir / "raw" / "trajectory")
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(
                    Event(
                        run_id=args.run_id,
                        source="trajectory",
                        event_type="sample",
                        quality="estimated",
                        payload=imported,
                    )
                )
            if not args.defer_report:
                write_reports(run_dir)
        else:
            imported = import_trajectory(args.path)
    except Exception as exc:
        print(f"agentmon import-trajectory error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(imported, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _import_p2(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        run_dir = find_run_dir(cfg, args.run_id)
        source = Path(args.path).expanduser()
        read_result = read_p2_event_slice(source, start_offset=args.start_offset, end_offset=args.end_offset)
        api_request_ids = load_p2_api_request_id_filters(args)
        filtered_import = bool(api_request_ids or args.filter_run_id)
        include_global_events = args.global_event_policy == "copy" or (
            args.global_event_policy == "auto" and not filtered_import
        )
        selection_diagnostics = p2_selection_diagnostics(
            read_result["events"],
            api_request_ids,
            window_margin_seconds=args.p2_window_margin_seconds,
        )
        selected_events = select_p2_events_for_api_request_ids(
            read_result["events"],
            api_request_ids,
            include_window_extras=args.include_p2_window_extras,
            window_margin_seconds=args.p2_window_margin_seconds,
            include_global_events=include_global_events,
        ) if api_request_ids else list(read_result["events"])
        imported_events: list[dict[str, object]] = []
        for event in selected_events:
            if event.get("source") != "vllm_p2":
                continue
            if args.filter_run_id and not p2_event_matches_run(event, args.filter_run_id):
                continue
            if filtered_import and not include_global_events and is_p2_global_event(event):
                continue
            imported_events.append(
                normalize_imported_p2_event(
                    event,
                    run_id=args.run_id,
                    source_path=source,
                    start_offset=args.start_offset,
                    end_offset=args.end_offset,
                    quality=args.quality,
                    attribution_scope=(
                        "shared_service_observation"
                        if is_p2_global_event(event)
                        else "request_attributed"
                    ),
                )
            )
        if imported_events:
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                for event in imported_events:
                    writer.write_event(event)
        event_counts = summarize_p2_imported_events(imported_events)
        report = write_reports(run_dir) if not args.defer_report else {
            "vllm_p2": {
                "status": "complete" if event_counts.get("p2_request_count") else "missing",
                "request_count": event_counts.get("p2_request_count", 0),
            }
        }
        import_metadata = {
            "schema_version": "1.0",
            "source": str(source),
            "source_sha256": file_sha256(source),
            "global_event_policy": args.global_event_policy,
            "global_events_copied": include_global_events,
            "observation_scope": "request_attributed_with_service_reference" if filtered_import else "service_stream",
            **selection_diagnostics,
            **event_counts,
        }
        p2_import_path = run_dir / "raw" / "p2_import.json"
        p2_import_path.parent.mkdir(parents=True, exist_ok=True)
        p2_import_path.write_text(
            json.dumps(import_metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"agentmon import-p2 error: {exc}", file=sys.stderr)
        return 2

    result = {
        "schema_version": "1.0",
        "command": "import-p2",
        "run_id": args.run_id,
        "source": str(Path(args.path).expanduser()),
        "start_offset": args.start_offset,
        "end_offset": args.end_offset,
        "quality": args.quality,
        "filter_run_id": args.filter_run_id,
        "filter_api_request_id_count": len(api_request_ids),
        "include_p2_window_extras": args.include_p2_window_extras,
        "p2_window_margin_seconds": args.p2_window_margin_seconds,
        "global_event_policy": args.global_event_policy,
        "global_events_copied": include_global_events,
        "scanned_lines": read_result["scanned_lines"],
        "invalid_lines": read_result["invalid_lines"],
        "non_p2_event_count": read_result["non_p2_event_count"],
        "imported_event_count": len(imported_events),
        **event_counts,
        **selection_diagnostics,
        "vllm_p2_status": as_dict(report.get("vllm_p2")).get("status"),
        "vllm_p2_request_count": as_dict(report.get("vllm_p2")).get("request_count"),
        "report": str(run_dir / "report.txt"),
        "p2_import_metadata": str(p2_import_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.require_events and not imported_events else 0


def read_p2_event_slice(path: Path, *, start_offset: int | None, end_offset: int | None) -> dict[str, object]:
    if start_offset is not None and start_offset < 0:
        raise ValueError("start-offset must be >= 0")
    if end_offset is not None and end_offset < 0:
        raise ValueError("end-offset must be >= 0")
    if start_offset is not None and end_offset is not None and end_offset < start_offset:
        raise ValueError("end-offset must be >= start-offset")

    start = start_offset or 0
    file_size = path.stat().st_size
    length = None if end_offset is None else max(0, end_offset - start)
    with path.open("rb") as handle:
        drop_first = False
        if start > 0:
            handle.seek(start - 1)
            drop_first = handle.read(1) != b"\n"
        handle.seek(start)
        raw = handle.read() if length is None else handle.read(length)
    lines = raw.splitlines()
    if drop_first and lines:
        lines = lines[1:]
    if end_offset is not None and end_offset < file_size and raw and not raw.endswith(b"\n") and lines:
        lines = lines[:-1]

    events: list[dict[str, object]] = []
    invalid_lines = 0
    non_p2_event_count = 0
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(item, dict):
            invalid_lines += 1
            continue
        if item.get("source") != "vllm_p2":
            non_p2_event_count += 1
        events.append(item)
    return {
        "events": events,
        "scanned_lines": len(lines),
        "invalid_lines": invalid_lines,
        "non_p2_event_count": non_p2_event_count,
    }


def load_p2_api_request_id_filters(args: argparse.Namespace) -> set[str]:
    ids: set[str] = set()
    for value in getattr(args, "filter_api_request_id", []) or []:
        ids.update(split_request_ids(value))
    if getattr(args, "filter_api_request_id_file", None):
        ids.update(read_request_id_file(Path(args.filter_api_request_id_file).expanduser()))
    if getattr(args, "filter_response_ids_from_trajectory", None):
        ids.update(extract_response_ids_from_trajectory(Path(args.filter_response_ids_from_trajectory).expanduser()))
    return {value for value in ids if value}


def split_request_ids(value: str) -> set[str]:
    return {item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()}


def read_request_id_file(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return set()
    if stripped[0] in "[{":
        data = json.loads(stripped)
        if isinstance(data, list):
            return {str(item) for item in data if item not in {None, ""}}
        if isinstance(data, dict):
            for key in ("api_request_ids", "response_ids", "request_ids", "ids"):
                value = data.get(key)
                if isinstance(value, list):
                    return {str(item) for item in value if item not in {None, ""}}
    return split_request_ids(text)


def extract_response_ids_from_trajectory(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    ids: set[str] = set()
    for message in trajectory_messages(data):
        if message.get("role") != "assistant" or not isinstance(message.get("tool_calls"), list):
            continue
        response = as_dict(as_dict(message.get("extra")).get("response"))
        response_id = string_or_none(response.get("id"))
        if response_id:
            ids.add(response_id)
    return ids


def trajectory_messages(data: object) -> list[dict[str, object]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    messages = data.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    nested = data.get("trajectory")
    if isinstance(nested, dict):
        nested_messages = nested.get("messages")
        if isinstance(nested_messages, list):
            return [item for item in nested_messages if isinstance(item, dict)]
    return []


def select_p2_events_for_api_request_ids(
    events: object,
    api_request_ids: set[str],
    *,
    include_window_extras: bool,
    window_margin_seconds: float,
    include_global_events: bool = False,
) -> list[dict[str, object]]:
    rows = [event for event in events if isinstance(event, dict)]
    matched_api_ids: set[str] = set()
    matched_vllm_ids: set[str] = set()
    matched_times: list[int] = []
    for event in rows:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        if not is_p2_request_event_type(event_type):
            continue
        payload = as_dict(event.get("payload"))
        api_request_id = string_or_none(payload.get("api_request_id"))
        if api_request_id not in api_request_ids:
            continue
        matched_api_ids.add(api_request_id)
        for key in ("vllm_request_id", "request_id"):
            value = string_or_none(payload.get(key))
            if value:
                matched_vllm_ids.add(value)
        event_ns = p2_event_time_ns(event, payload)
        if event_ns is not None:
            matched_times.append(event_ns)

    if not matched_api_ids:
        return []
    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    selected: list[dict[str, object]] = []
    for event in rows:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        include = False
        if is_p2_request_or_keyed_event_type(event_type):
            include = p2_payload_matches_ids(payload, matched_api_ids, matched_vllm_ids)
            if not include and include_window_extras:
                include = p2_event_in_window(event, payload, window_start, window_end)
        elif is_p2_global_event(event) and include_global_events:
            include = (
                True
                if is_p2_run_capability_event(event)
                else p2_event_in_window(event, payload, window_start, window_end)
            )
        if include:
            selected.append(event)
    return selected


def p2_selection_diagnostics(
    events: object,
    api_request_ids: set[str],
    *,
    window_margin_seconds: float,
) -> dict[str, object]:
    rows = [event for event in events if isinstance(event, dict) and event.get("source") == "vllm_p2"]
    available_api_ids: set[str] = set()
    request_times: dict[str, list[int]] = {}
    matched_times: list[int] = []
    for event in rows:
        if not is_p2_request_event_type(str(event.get("event_type") or "")):
            continue
        payload = as_dict(event.get("payload"))
        api_request_id = string_or_none(payload.get("api_request_id"))
        if api_request_id is None:
            continue
        available_api_ids.add(api_request_id)
        event_ns = p2_event_time_ns(event, payload)
        if event_ns is not None:
            request_times.setdefault(api_request_id, []).append(event_ns)
            if api_request_id in api_request_ids:
                matched_times.append(event_ns)

    matched_ids = api_request_ids & available_api_ids
    unmatched_ids = api_request_ids - available_api_ids
    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    extras: set[str] = set()
    if window_start is not None and window_end is not None:
        for request_id, timestamps in request_times.items():
            if request_id in matched_ids:
                continue
            if any(window_start <= timestamp <= window_end for timestamp in timestamps):
                extras.add(request_id)
    return {
        "trajectory_response_id_count": len(api_request_ids),
        "matched_p2_request_count": len(matched_ids),
        "matched_p2_request_ids": sorted(matched_ids),
        "unmatched_trajectory_id_count": len(unmatched_ids),
        "unmatched_trajectory_ids": sorted(unmatched_ids),
        "p2_requests_in_task_window_not_in_trajectory_count": len(extras),
        "p2_requests_in_task_window_not_in_trajectory": sorted(extras),
        "task_window_start_mono_ns": window_start,
        "task_window_end_mono_ns": window_end,
        "extras_attribution_quality": "diagnostic_shared_window_not_request_attributed",
    }


def p2_payload_matches_ids(payload: dict[str, object], api_ids: set[str], vllm_ids: set[str]) -> bool:
    api_request_id = string_or_none(payload.get("api_request_id"))
    if api_request_id in api_ids:
        return True
    for key in ("vllm_request_id", "request_id"):
        value = string_or_none(payload.get(key))
        if value in vllm_ids:
            return True
    return False


def p2_event_in_window(
    event: dict[str, object],
    payload: dict[str, object],
    start_ns: int | None,
    end_ns: int | None,
) -> bool:
    if start_ns is None or end_ns is None:
        return False
    event_ns = p2_event_time_ns(event, payload)
    return event_ns is not None and start_ns <= event_ns <= end_ns


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def p2_event_time_ns(event: dict[str, object], payload: dict[str, object]) -> int | None:
    for key in (
        "received_ns",
        "queued_ns",
        "first_scheduled_ns",
        "prefill_completed_ns",
        "first_token_ns",
        "first_output_ns",
        "engine_finished_ns",
        "finished_ns",
    ):
        value = integer_or_none(payload.get(key))
        if value is not None:
            return value
    return integer_or_none(event.get("monotonic_time_ns"))


def p2_event_matches_run(event: dict[str, object], run_id: str) -> bool:
    payload = as_dict(event.get("payload"))
    return (
        event.get("run_id") == run_id
        or payload.get("run_id") == run_id
        or payload.get("agentmon_run_id") == run_id
    )


def _live(args: argparse.Namespace) -> int:
    try:
        cfg = _load_cfg(args)
        run_dir = find_run_dir(cfg, args.run_id)
    except Exception as exc:
        print(f"agentmon live error: {exc}", file=sys.stderr)
        return 2
    while True:
        manifest_path = Path(run_dir) / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"manifest unavailable: {exc}", file=sys.stderr)
            return 2
        print(
            f"Run {manifest.get('run_id')} | status {manifest.get('status')} | "
            f"pid {manifest.get('child_pid')} | exit {manifest.get('exit_code')} | "
            f"quality {manifest.get('data_quality')} | {format_live_p1(run_dir)}"
        )
        if args.once or manifest.get("status") not in {"initializing", "running"}:
            return 0
        time.sleep(args.interval)


def format_live_p1(run_dir: Path) -> str:
    try:
        events = load_events(run_dir)
    except Exception:
        return "p1 unavailable"
    p1 = summarize_p1_spans(events)
    if p1.get("status") == "missing":
        return "p1 spans ?"
    recent = ""
    spans = p1.get("spans") or []
    if spans:
        last = spans[-1]
        recent = f" last={last.get('span_type')}:{last.get('status')}"
    return (
        f"p1 steps {p1.get('agent_steps', 0)} model {p1.get('model_requests', 0)} "
        f"tools {p1.get('tool_calls', 0)} open {p1.get('open_span_count', 0)}{recent}"
    )
