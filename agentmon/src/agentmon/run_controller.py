from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Sequence
from uuid import uuid4

from agentmon import __version__
from agentmon.clock import ClockAnchor, host_boot_id, monotonic_clock_domain, source_host
from agentmon.config import AgentMonConfig
from agentmon.events import safe_json_dumps
from agentmon.paths import make_private_dir, open_private_text, output_root, write_private_text
from agentmon.reporting.report import load_events, write_reports
from agentmon.runtime.p2_import import (
    P2RunWindow,
    import_p2_run_window,
    open_p2_run_window,
)
from agentmon.sampling.ascend_smi import AscendSmiSampler
from agentmon.sampling.host import HostSampler
from agentmon.sampling.procfs import ProcSampler
from agentmon.sampling.vllm_metrics import VllmMetricsSampler, fetch_metrics
from agentmon.security import command_hash, read_command_hash_salt, redacted_argv
from agentmon.storage.event_queue import EventSink
from agentmon.trajectory.mini_swe import (
    find_new_or_modified_trajectories,
    import_trajectory,
    snapshot_trajectory_glob,
)


def make_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]


class RunController:
    def __init__(
        self,
        cfg: AgentMonConfig,
        argv: Sequence[str],
        *,
        label: str | None = None,
        cwd: str | None = None,
        trajectory: str | None = None,
        write_final_report: bool = True,
    ) -> None:
        if not argv:
            raise ValueError("run requires a command after --")
        self.cfg = cfg
        self.label = label
        self.cwd = cwd or os.getcwd()
        self.run_id = make_run_id()
        self.run_dir = output_root(cfg) / self.run_id
        self.argv = expand_run_id_placeholders(list(argv), self.run_id)
        self.trajectory = expand_run_id_placeholders([trajectory], self.run_id)[0] if trajectory else None
        self.write_final_report = write_final_report
        self.detected_trajectory_paths = detect_trajectory_output_paths(self.argv)
        self.raw_dir = self.run_dir / "raw"
        self.logs_dir = self.run_dir / "logs"
        self.child_pid: int | None = None
        self.child_pgid: int | None = None
        self.start_anchor = ClockAnchor.capture()
        self.end_anchor: ClockAnchor | None = None
        self.manifest: dict[str, Any] = {}
        self.command_hash_salt = read_command_hash_salt(self.cfg.privacy.command_hash_salt_file)
        self.host_id = source_host()
        self.host_boot_id = host_boot_id()
        self.clock_domain_id = monotonic_clock_domain(host=self.host_id, boot_id=self.host_boot_id)

    def run(self) -> int:
        self._prepare_dirs()

        before_trajectories = snapshot_trajectory_glob(self.cfg.agent.trajectory_glob)
        p2_window = open_p2_run_window(self._resolve_p2_events_path())
        self.manifest = self._initial_manifest()
        self.manifest["p2_window"] = p2_window.to_dict()
        self._write_manifest()
        self._write_resolved_config()
        child_env = build_child_env(
            self.cfg,
            self.run_id,
            self.run_dir,
            command_hash_salt=self.command_hash_salt,
        )

        sink = EventSink(
            self.run_id,
            self.run_dir / "events.jsonl",
            self.run_dir / "metrics.sqlite3" if self.cfg.storage.sqlite else None,
            max_items=self.cfg.storage.queue_max_items,
            flush_interval_seconds=self.cfg.storage.flush_interval_seconds,
        )
        sink.emit("controller", "lifecycle", "exact", payload={"phase": "initializing"}, critical=True)
        self._snapshot_vllm("start", sink)

        proc_sampler = ProcSampler(
            sink,
            self.cfg,
            child_pid_getter=lambda: self.child_pid,
            child_pgid_getter=lambda: self.child_pgid,
        )
        samplers = [
            proc_sampler,
            HostSampler(sink, interval_seconds=max(1.0, self.cfg.proc.interval_seconds)),
            VllmMetricsSampler(sink, self.cfg),
            AscendSmiSampler(sink, self.cfg, self.raw_dir / "npu_smi"),
        ]

        process: subprocess.Popen[str] | None = None
        exit_code = 70
        term_signal = None
        interrupted = {"count": 0}
        old_sigint = signal.getsignal(signal.SIGINT)
        old_sigterm = signal.getsignal(signal.SIGTERM)

        def forward_signal(signum: int, _frame: object) -> None:
            interrupted["count"] += 1
            if self.child_pgid is not None:
                try:
                    os.killpg(self.child_pgid, signum)
                except ProcessLookupError:
                    pass
            if interrupted["count"] >= 2 and self.child_pgid is not None:
                try:
                    os.killpg(self.child_pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        try:
            for sampler in samplers:
                sampler.start()
            stdout_path = self.logs_dir / "child.stdout.log"
            stderr_path = self.logs_dir / "child.stderr.log"
            with open_private_text(stdout_path) as stdout, open_private_text(stderr_path) as stderr:
                process = subprocess.Popen(
                    self.argv,
                    cwd=self.cwd,
                    env=child_env,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    start_new_session=True,
                )
                self.child_pid = process.pid
                try:
                    self.child_pgid = os.getpgid(process.pid)
                except ProcessLookupError:
                    self.child_pgid = process.pid
                self.manifest.update(
                    {
                        "status": "running",
                        "child_pid": self.child_pid,
                        "child_pgid": self.child_pgid,
                    }
                )
                self._write_manifest()
                sink.emit(
                    "controller",
                    "lifecycle",
                    "exact",
                    payload={"phase": "running", "child_pid": self.child_pid, "child_pgid": self.child_pgid},
                    critical=True,
                )
                signal.signal(signal.SIGINT, forward_signal)
                signal.signal(signal.SIGTERM, forward_signal)
                while process.poll() is None:
                    time.sleep(0.1)
                raw_returncode = process.returncode
                if raw_returncode is None:
                    raw_returncode = 70
                if raw_returncode < 0:
                    term_signal = -raw_returncode
                    exit_code = 128 + term_signal
                else:
                    exit_code = raw_returncode
        except FileNotFoundError as exc:
            sink.emit(
                "controller",
                "error",
                "exact",
                payload={"phase": "launch", "error_type": type(exc).__name__, "message": str(exc)},
                critical=True,
            )
            exit_code = 70
        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)
            self.manifest["finalization"] = self._finalize_run(
                sink=sink,
                samplers=samplers,
                before_trajectories=before_trajectories,
                p2_window=p2_window,
                exit_code=exit_code,
                term_signal=term_signal,
                interrupt_count=interrupted["count"],
                process=process,
            )

        self.manifest["data_quality"] = infer_data_quality(load_events(self.run_dir))
        self._write_manifest()
        if self.write_final_report:
            write_reports(self.run_dir)
        return exit_code

    def _finalize_run(
        self,
        *,
        sink: EventSink,
        samplers: Sequence[Any],
        before_trajectories: dict[str, float],
        p2_window: P2RunWindow,
        exit_code: int,
        term_signal: int | None,
        interrupt_count: int,
        process: subprocess.Popen[str] | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        finalization: dict[str, Any] = {
            "schema_version": "1.0",
            "started_wall_ns": time.time_ns(),
            "child_done": process is None or process.poll() is not None,
            "child_exit_code": exit_code,
            "p1_adapter_done": True,
            "p2_window": p2_window.to_dict(),
            "completed": False,
        }
        sink.emit(
            "controller",
            "finalization.started",
            "exact",
            payload={
                "child_done": finalization["child_done"],
                "child_exit_code": exit_code,
            },
            critical=True,
        )

        self._snapshot_vllm("end", sink)
        collector_statuses = self._stop_collectors(samplers, sink)
        all_collectors_stopped = all(bool(item.get("joined")) for item in collector_statuses)
        self.end_anchor = ClockAnchor.capture()
        status = status_from_exit(exit_code, term_signal, interrupt_count)
        self.manifest.update(
            {
                "status": status,
                "end_mono_ns": self.end_anchor.monotonic_time_ns,
                "end_wall_ns": self.end_anchor.wall_time_ns,
                "duration_ns": self.end_anchor.monotonic_time_ns - self.start_anchor.monotonic_time_ns,
                "exit_code": exit_code,
                "term_signal": term_signal,
                "dropped_samples_total": sink.dropped_samples_total,
                "rejected_events_total": sink.rejected_events_total,
                "sqlite_errors_total": sink.sqlite_errors_total,
                "writer_errors_total": sink.writer_errors_total,
            }
        )

        trajectory_result = self._import_trajectory(before_trajectories, sink)
        p2_result = self._import_p2_window(p2_window, trajectory_result.get("imported_paths", []), sink)
        sink.emit(
            "controller",
            "lifecycle",
            "exact",
            payload={"phase": "finished", "status": status, "exit_code": exit_code},
            critical=True,
        )
        sink.emit(
            "controller",
            "event_writer.drain_started",
            "exact",
            payload={"pending_queue_size": sink.pending_queue_size()},
            critical=True,
        )
        drain_result = sink.drain(timeout_seconds=self.cfg.finalization.writer_close_timeout_seconds)
        sink.emit(
            "controller",
            "event_writer.drained",
            "exact" if drain_result.get("drained") else "estimated",
            payload=drain_result,
            critical=True,
        )
        preclose_completed = {
            "child_done": bool(finalization["child_done"]),
            "p1_adapter_done": True,
            "all_collectors_stopped": all_collectors_stopped,
            "event_queue_drained": bool(drain_result.get("drained")),
            "trajectory_decision_final": trajectory_result.get("status") not in {"PENDING"},
            "p2_import_finished": p2_result.get("p2_import_status") not in {"PENDING"},
        }
        sink.emit(
            "controller",
            "finalization.completed",
            "exact" if all(preclose_completed.values()) else "estimated",
            payload=preclose_completed,
            critical=True,
        )
        close_result = sink.close(timeout_seconds=self.cfg.finalization.writer_close_timeout_seconds)
        completed = bool(
            preclose_completed["child_done"]
            and preclose_completed["p1_adapter_done"]
            and preclose_completed["all_collectors_stopped"]
            and preclose_completed["event_queue_drained"]
            and preclose_completed["trajectory_decision_final"]
            and preclose_completed["p2_import_finished"]
            and close_result.get("closed")
            and close_result.get("drained")
            and close_result.get("writer_joined")
        )
        finalization.update(
            {
                **preclose_completed,
                "completed": completed,
                "duration_seconds": time.monotonic() - started,
                "collector_statuses": collector_statuses,
                "collectors_joined": all_collectors_stopped,
                "event_writer_drain": drain_result,
                "event_writer_close": close_result,
                "event_writer_closed": bool(close_result.get("closed")),
                "event_writer_joined": bool(close_result.get("writer_joined")),
                "event_writer_flushed": bool(close_result.get("closed")),
                "trajectory": trajectory_result,
                "trajectory_status": trajectory_result.get("status"),
                "p2": p2_result,
                "p2_status": p2_result.get("p2_import_status"),
            }
        )
        self.manifest.update(
            {
                "dropped_samples_total": sink.dropped_samples_total,
                "rejected_events_total": sink.rejected_events_total,
                "sqlite_errors_total": sink.sqlite_errors_total,
                "writer_errors_total": sink.writer_errors_total,
            }
        )
        return finalization

    def _stop_collectors(self, samplers: Sequence[Any], sink: EventSink) -> list[dict[str, Any]]:
        statuses: list[dict[str, Any]] = []
        timeout = self.cfg.finalization.collector_join_timeout_seconds
        for sampler in samplers:
            source = getattr(sampler, "source", type(sampler).__name__)
            sink.emit(
                "controller",
                "collector.stop_requested",
                "exact",
                payload={"component": source, "timeout_seconds": timeout},
                critical=True,
            )
            try:
                status = sampler.stop(timeout_seconds=timeout).to_dict()
            except Exception as exc:
                status = {
                    "source": source,
                    "stop_requested": True,
                    "joined": False,
                    "timeout_seconds": timeout,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "alive": None,
                }
            statuses.append(status)
            sink.emit(
                "controller",
                "collector.joined" if status.get("joined") else "collector.join_timeout",
                "exact" if status.get("joined") else "estimated",
                payload={
                    "component": source,
                    "phase": "shutdown",
                    "timeout_seconds": timeout,
                    "thread_name": status.get("thread_name"),
                    "thread_ident": status.get("thread_ident"),
                    "recoverability": "none_needed" if status.get("joined") else "data_quality_partial",
                    **status,
                },
                critical=True,
            )
        return statuses

    def _prepare_dirs(self) -> None:
        for path in (
            self.run_dir,
            self.raw_dir,
            self.raw_dir / "vllm_samples",
            self.raw_dir / "npu_smi",
            self.raw_dir / "trajectory",
            self.raw_dir / "process_snapshots",
            self.logs_dir,
        ):
            make_private_dir(path)

    def _initial_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "label": self.label,
            "status": "initializing",
            "created_wall_ns": self.start_anchor.wall_time_ns,
            "start_wall_ns": self.start_anchor.wall_time_ns,
            "start_mono_ns": self.start_anchor.monotonic_time_ns,
            "end_wall_ns": None,
            "end_mono_ns": None,
            "duration_ns": None,
            "command_argv": redacted_argv(
                self.argv,
                store_full_command=self.cfg.privacy.store_full_command,
            ),
            "command_sha256": command_hash(self.argv, salt=self.command_hash_salt),
            "command_hash_salted": bool(self.command_hash_salt),
            "cwd": self.cwd,
            "child_env_unset": list(self.cfg.agent.env_unset),
            "child_env_set_keys": [entry.split("=", 1)[0] for entry in self.cfg.agent.env_set],
            "child_pid": None,
            "child_pgid": None,
            "exit_code": None,
            "term_signal": None,
            "host_id": self.host_id,
            "host_boot_id": self.host_boot_id,
            "clock_domain_id": self.clock_domain_id,
            "agentmon_version": __version__,
            "prefix_caching_status": self.cfg.vllm.prefix_caching,
            "data_quality": {
                "proc": "missing",
                "vllm": "missing",
                "npu": "missing",
                "trajectory": "missing",
            },
        }

    def _write_manifest(self) -> None:
        write_private_text(
            self.run_dir / "manifest.json",
            json.dumps(self.manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _write_resolved_config(self) -> None:
        write_private_text(
            self.run_dir / "config.resolved.toml",
            toml_from_dict(self.cfg.to_dict()),
        )

    def _snapshot_vllm(self, phase: str, sink: EventSink) -> None:
        raw_path = self.raw_dir / f"vllm_{phase}.prom"
        try:
            text, status, byte_count = fetch_metrics(
                self.cfg.vllm.metrics_url,
                timeout=self.cfg.vllm.request_timeout_seconds,
                raw_path=raw_path,
            )
            sink.emit(
                "vllm",
                "counter_snapshot",
                "sampled",
                payload={"phase": phase, "status": status, "bytes": byte_count, "raw_ref": str(raw_path)},
                critical=True,
            )
        except Exception as exc:
            sink.emit(
                "vllm",
                "error",
                "sampled",
                payload={
                    "phase": phase,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "raw_ref": str(raw_path),
                },
                critical=True,
            )

    def _import_trajectory(self, before: dict[str, float], sink: EventSink) -> dict[str, Any]:
        candidate = self._trajectory_candidate_paths(before)
        paths = candidate["paths"]
        if not paths:
            status = "NOT_CONFIGURED" if candidate["mode"] == "not_configured" else "MISSING_AFTER_TIMEOUT"
            event_type = "status" if status == "NOT_CONFIGURED" else "error"
            payload = {
                "status": status,
                "mode": candidate["mode"],
                "message": "trajectory import not configured"
                if status == "NOT_CONFIGURED"
                else "no trajectory file became ready before timeout",
            }
            sink.emit("trajectory", event_type, "exact" if status == "NOT_CONFIGURED" else "estimated", payload=payload, critical=True)
            return {
                "status": status,
                "mode": candidate["mode"],
                "candidate_count": 0,
                "imported_count": 0,
                "imported_paths": [],
                "decisions": [payload],
            }

        decisions: list[dict[str, Any]] = []
        imported_paths: list[str] = []
        imported_count = 0
        for path in paths:
            readiness = wait_for_stable_file(
                path,
                timeout_seconds=self.cfg.finalization.trajectory_wait_timeout_seconds,
                poll_interval_seconds=self.cfg.finalization.trajectory_poll_interval_seconds,
                stable_checks=self.cfg.finalization.trajectory_stable_checks,
                require_json=True,
            )
            decisions.append(readiness)
            if readiness["status"] != "FOUND":
                event_type = "error" if candidate["mode"] in {"explicit", "detected"} else "status"
                sink.emit(
                    "trajectory",
                    event_type,
                    "estimated",
                    payload={
                        **readiness,
                        "status": "MISSING_AFTER_TIMEOUT"
                        if readiness["status"] == "MISSING_AFTER_TIMEOUT"
                        else readiness["status"],
                        "mode": candidate["mode"],
                    },
                    critical=True,
                )
                continue
            try:
                imported = import_trajectory(path, run_id=self.run_id, raw_dir=self.raw_dir / "trajectory")
                sink.emit("trajectory", "sample", "estimated", payload=imported, critical=True)
                imported_count += 1
                imported_paths.append(str(path))
            except Exception as exc:
                error = {
                    "status": "INVALID",
                    "path": str(path),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "mode": candidate["mode"],
                }
                decisions.append(error)
                sink.emit("trajectory", "error", "estimated", payload=error, critical=True)
        status = "IMPORTED" if imported_count else "MISSING_AFTER_TIMEOUT"
        if any(item.get("status") == "INVALID" for item in decisions) and imported_count == 0:
            status = "INVALID"
        return {
            "status": status,
            "mode": candidate["mode"],
            "candidate_count": len(paths),
            "imported_count": imported_count,
            "imported_paths": imported_paths,
            "decisions": decisions,
        }

    def _trajectory_candidate_paths(self, before: dict[str, float]) -> dict[str, Any]:
        if self.trajectory:
            return {"mode": "explicit", "paths": [Path(self.trajectory).expanduser()]}
        if self.detected_trajectory_paths:
            return {"mode": "detected", "paths": [path.expanduser() for path in self.detected_trajectory_paths]}
        if not self.cfg.agent.trajectory_glob:
            return {"mode": "not_configured", "paths": []}
        deadline = time.monotonic() + self.cfg.finalization.trajectory_wait_timeout_seconds
        paths: list[Path] = []
        while True:
            paths = find_new_or_modified_trajectories(self.cfg.agent.trajectory_glob, before)
            if paths or time.monotonic() >= deadline:
                break
            time.sleep(min(self.cfg.finalization.trajectory_poll_interval_seconds, max(0.0, deadline - time.monotonic())))
        return {"mode": "glob", "paths": paths}

    def _resolve_p2_events_path(self) -> Path | None:
        candidates = [
            self.cfg.finalization.p2_events_path,
            os.environ.get("AGENTMON_P2_EVENTS_PATH", ""),
            os.environ.get("AGENTMON_VLLM_P2_EVENTS_PATH", ""),
            os.environ.get("AGENTMON_SERVICE_P2_EVENTS_PATH", ""),
            os.environ.get("AGENTMON_EVENTS_PATH", ""),
        ]
        run_events_path = (self.run_dir / "events.jsonl").expanduser()
        for raw in candidates:
            if not raw:
                continue
            path = Path(str(raw)).expanduser()
            if path == run_events_path:
                continue
            return path
        return None

    def _import_p2_window(
        self,
        p2_window: P2RunWindow,
        trajectory_paths: Sequence[str],
        sink: EventSink,
    ) -> dict[str, Any]:
        try:
            result = import_p2_run_window(
                run_id=self.run_id,
                window=p2_window,
                sink=sink,
                trajectory_paths=[Path(path) for path in trajectory_paths],
                quality=self.cfg.finalization.p2_import_quality,
                global_event_policy=self.cfg.finalization.p2_global_event_policy,
                include_window_extras=self.cfg.finalization.p2_include_window_extras,
                window_margin_seconds=self.cfg.finalization.p2_window_margin_seconds,
            )
        except Exception as exc:
            result = {
                "schema_version": "1.0",
                "status": "ERROR",
                "p2_import_status": "ERROR",
                "p2_source_path": str(p2_window.source_path) if p2_window.source_path is not None else None,
                "p2_start_offset": p2_window.start_offset,
                "p2_end_offset": None,
                "p2_event_count": 0,
                "p2_request_count": 0,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            sink.emit("controller", "p2.import_error", "estimated", payload=result, critical=True)
            return result
        sink.emit(
            "controller",
            "p2.imported",
            "exact" if result.get("p2_import_status") == "IMPORTED" else "estimated",
            payload=result,
            critical=True,
        )
        if result.get("p2_import_status") not in {"NOT_CONFIGURED"}:
            write_private_text(
                self.raw_dir / "p2_import.json",
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            )
        return result


def status_from_exit(exit_code: int, term_signal: int | None, interrupt_count: int) -> str:
    if interrupt_count or term_signal in {signal.SIGINT, signal.SIGTERM}:
        return "cancelled"
    return "success" if exit_code == 0 else "failed"


def expand_run_id_placeholders(argv: list[str], run_id: str) -> list[str]:
    return [
        item.replace("{run_id}", run_id).replace("{AGENTMON_RUN_ID}", run_id)
        for item in argv
    ]


def detect_trajectory_output_paths(argv: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in {"-o", "--output"} and index + 1 < len(argv):
            paths.append(Path(argv[index + 1]))
            index += 2
            continue
        if item.startswith("--output="):
            value = item.split("=", 1)[1]
            if value:
                paths.append(Path(value))
        index += 1
    return paths


def wait_for_stable_file(
    path: Path,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    stable_checks: int,
    require_json: bool = False,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    required_stable_checks = max(1, stable_checks)
    last_signature: tuple[int, int] | None = None
    stable_count = 0
    last_size: int | None = None
    last_mtime_ns: int | None = None
    last_error: dict[str, str] | None = None
    while True:
        try:
            stat = path.stat()
        except OSError:
            stat = None
        if stat is not None:
            last_size = stat.st_size
            last_mtime_ns = stat.st_mtime_ns
            if stat.st_size > 0:
                signature = (stat.st_size, stat.st_mtime_ns)
                stable_count = stable_count + 1 if signature == last_signature else 1
                last_signature = signature
                if stable_count >= required_stable_checks:
                    try:
                        with path.open("rb") as handle:
                            data = handle.read()
                        if require_json:
                            json.loads(data.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError as exc:
                        return {
                            "status": "INVALID",
                            "path": str(path),
                            "size_bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                            "stable_checks": stable_count,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    except OSError as exc:
                        last_error = {"error_type": type(exc).__name__, "message": str(exc)}
                    return {
                        "status": "FOUND",
                        "path": str(path),
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "stable_checks": stable_count,
                    }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "status": "MISSING_AFTER_TIMEOUT",
                "path": str(path),
                "timeout_seconds": timeout_seconds,
                "last_size_bytes": last_size,
                "last_mtime_ns": last_mtime_ns,
                "stable_checks": stable_count,
                **(last_error or {}),
            }
        time.sleep(min(max(0.0, poll_interval_seconds), remaining))


def build_child_env(
    cfg: AgentMonConfig,
    run_id: str,
    run_dir: Path,
    *,
    command_hash_salt: str = "",
) -> dict[str, str]:
    env = os.environ.copy()
    for name in cfg.agent.env_unset:
        env.pop(name, None)
    for entry in cfg.agent.env_set:
        key, value = entry.split("=", 1)
        env[key] = value
    env["AGENTMON_RUN_ID"] = run_id
    env["AGENTMON_RUN_DIR"] = str(run_dir)
    env["AGENTMON_EVENTS_PATH"] = str(run_dir / "events.jsonl")
    env["AGENTMON_ROOT_SPAN_ID"] = f"run:{run_id}"
    env["AGENTMON_CONFIG"] = str(run_dir / "config.resolved.toml")
    if cfg.agent.agent_id.strip():
        env["AGENTMON_AGENT_ID"] = cfg.agent.agent_id.strip()
    if command_hash_salt:
        env["AGENTMON_COMMAND_HASH_SALT"] = command_hash_salt
    return env


def infer_data_quality(events: list[dict[str, Any]]) -> dict[str, str]:
    quality = {"proc": "missing", "vllm": "missing", "npu": "missing", "trajectory": "missing"}
    for event in events:
        source = event.get("source")
        event_type = event.get("event_type")
        if source == "proc" and event_type == "sample":
            quality["proc"] = "complete"
        elif source == "vllm" and event_type in {"sample", "counter_snapshot"}:
            quality["vllm"] = "complete"
        elif source == "vllm" and event_type == "error" and quality["vllm"] == "missing":
            quality["vllm"] = "partial"
        elif source == "ascend" and event_type == "sample":
            quality["npu"] = "complete"
        elif source == "ascend" and event_type in {"error", "capability"} and quality["npu"] == "missing":
            payload_status = event.get("payload", {}).get("status")
            quality["npu"] = "missing" if payload_status in {"not_found", "unsupported"} else "partial"
        elif source == "trajectory" and event_type == "sample":
            quality["trajectory"] = "complete"
        elif source == "trajectory" and event_type in {"error", "status"} and quality["trajectory"] == "missing":
            payload_status = event.get("payload", {}).get("status")
            quality["trajectory"] = (
                "missing"
                if payload_status in {"missing", "MISSING_AFTER_TIMEOUT", "NOT_CONFIGURED", "NOT_EXPECTED"}
                else "partial"
            )
    return quality


def toml_from_dict(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if value is None:
        return '""'
    return safe_json_dumps(str(value))
