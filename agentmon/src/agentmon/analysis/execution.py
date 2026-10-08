from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import shutil
import signal
import sys
import tempfile
import threading
import time
from typing import Any

from agentmon.analysis.io import file_sha256, write_json, write_plain_csv
from agentmon.analysis.stages import STAGE_NAMES, stage_dag_edges, stage_dag_nodes, stage_spec
from agentmon.pipeline.model import ArtifactRef, PipelineStageResult
from agentmon.pipeline.identity import detect_source_identity


STAGE_SCHEMA_VERSION = "1.0"


class StageRangeComplete(Exception):
    """Internal control signal for a successfully completed --to-stage run."""

    def __init__(self, stage_name: str) -> None:
        super().__init__(f"requested terminal stage completed: {stage_name}")
        self.stage_name = stage_name


@dataclass
class StageRecord:
    stage_name: str
    status: str = "PENDING"
    started_at: str | None = None
    finished_at: str | None = None
    wall_s: float | None = None
    cpu_s: float | None = None
    peak_rss: int | None = None
    disk_used_bytes_before: int | None = None
    disk_used_bytes_after: int | None = None
    disk_growth_bytes: int | None = None
    input_hashes: dict[str, str] = field(default_factory=dict)
    config_hash: str | None = None
    code_version: str | None = None
    output_files: list[str] = field(default_factory=list)
    output_hashes: dict[str, str] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)
    cacheable: bool = True
    cache_hit: bool = False
    failure: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STAGE_SCHEMA_VERSION,
            "stage_name": self.stage_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_s": self.wall_s,
            "CPU_s": self.cpu_s,
            "peak_rss": self.peak_rss,
            "disk_used_bytes_before": self.disk_used_bytes_before,
            "disk_used_bytes_after": self.disk_used_bytes_after,
            "disk_growth_bytes": self.disk_growth_bytes,
            "input_hashes": self.input_hashes,
            "config_hash": self.config_hash,
            "code_version": self.code_version,
            "output_files": self.output_files,
            "output_hashes": self.output_hashes,
            "row_counts": self.row_counts,
            "cacheable": self.cacheable,
            "cache_hit": self.cache_hit,
            "failure": self.failure,
            "warnings": self.warnings,
        }


class AnalysisRunContext:
    def __init__(
        self,
        *,
        bundle_root: str | Path,
        status_file: str | Path | None = None,
        temp_dir: str | Path | None = None,
        progress_interval_seconds: float = 30.0,
        timeout_seconds: float | None = None,
        log_level: str = "INFO",
        config_hash: str | None = None,
        code_version: str | None = None,
    ) -> None:
        self.bundle = Path(bundle_root).expanduser().resolve()
        self.stage_dir = self.bundle / "stages"
        self.status_file = Path(status_file).expanduser().resolve() if status_file else self.bundle / "analysis_status.json"
        self.temp_dir = Path(temp_dir).expanduser().resolve() if temp_dir else Path(tempfile.gettempdir()).resolve()
        self.progress_interval_seconds = max(1.0, float(progress_interval_seconds or 30.0))
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds is not None else None
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("analysis timeout_seconds must be greater than zero")
        self.log_level = log_level
        self.started_mono = time.monotonic()
        self.started_at = now_iso()
        self.last_progress_mono = 0.0
        self.current_phase = "pending"
        self.progress: dict[str, Any] = {
            "RUN_STATUS": "RUNNING",
            "CURRENT_PHASE": self.current_phase,
            "STARTED_AT": self.started_at,
            "RUNS_DISCOVERED": 0,
            "RUNS_SCANNED": 0,
            "RUNS_PARSED": 0,
            "CURRENT_RUN_ID": None,
            "EVENT_FILES_COMPLETED": 0,
            "EVENT_BYTES_DISCOVERED": 0,
            "EVENT_BYTES_READ": 0,
            "RAW_EVENTS_READ": 0,
            "CURRENT_SOURCE_FILE": None,
            "P2_INDEX_EVENTS_INDEXED": 0,
            "REQUEST_ATTEMPTS_PARSED": 0,
            "P2_REQUESTS_PARSED": 0,
            "LAST_PROGRESS_AT": None,
            "LAST_WARNING": None,
            "FINAL_STATUS": None,
        }
        self.records: dict[str, StageRecord] = {
            name: StageRecord(name, config_hash=config_hash, code_version=code_version)
            for name in STAGE_NAMES
        }
        # The legacy analysis facade remains responsible for its detailed
        # instrumentation, while this identity is emitted through the same
        # stage-result contract used by the optimizer pipeline.
        self.source_identity = detect_source_identity(Path(__file__).resolve().parents[3])
        self._previous_signal_handlers: dict[int, Any] = {}
        self._status_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.install_signal_handlers()
        self.write_stage_dag()
        for record in self.records.values():
            if not (self.stage_dir / record.stage_name / "stage-status.json").is_file():
                self.write_stage(record)
        self.write_status(force=True)
        self._heartbeat_thread = threading.Thread(target=self._periodic_heartbeat, name="agentmon-analysis-heartbeat", daemon=True)
        self._heartbeat_thread.start()

    def start_stage(self, stage_name: str, **updates: Any) -> None:
        self._assert_dependencies_satisfied(stage_name)
        self.current_phase = stage_name
        record = self.records.setdefault(stage_name, StageRecord(stage_name))
        record.status = "RUNNING"
        record.started_at = now_iso()
        record.finished_at = None
        record.failure = None
        record.warnings.extend(as_list(updates.pop("warnings", [])))
        for key, value in updates.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record._started_mono = time.monotonic()  # type: ignore[attr-defined]
        record._started_cpu = process_cpu_seconds()  # type: ignore[attr-defined]
        record.disk_used_bytes_before = disk_used_bytes(self.bundle)
        self.update_progress(CURRENT_PHASE=stage_name)
        self.write_stage(record)
        self.log(f"[analysis:{stage_name}] start")
        self.check_timeout()

    def set_input_hashes(self, hashes: dict[str, str]) -> None:
        for record in self.records.values():
            record.input_hashes = dict(hashes)
            if record.status == "PENDING" and not (self.stage_dir / record.stage_name / "stage-status.json").is_file():
                self.write_stage(record)

    def attach_stage_outputs(self, stage_name: str, files: list[str]) -> None:
        record = self.records.setdefault(stage_name, StageRecord(stage_name))
        record.output_files = [str(path) for path in files]
        record.output_hashes = output_hashes(record.output_files)
        self.write_stage(record)

    def finish_stage(
        self,
        stage_name: str,
        *,
        status: str = "PASS",
        output_files: list[str] | None = None,
        row_counts: dict[str, int] | None = None,
        warnings: list[str] | None = None,
        cache_hit: bool = False,
    ) -> None:
        record = self.records.setdefault(stage_name, StageRecord(stage_name))
        record.status = status
        record.finished_at = now_iso()
        started = getattr(record, "_started_mono", None)
        record.wall_s = time.monotonic() - started if started is not None else record.wall_s
        started_cpu = getattr(record, "_started_cpu", None)
        cpu = process_cpu_seconds()
        record.cpu_s = cpu - started_cpu if started_cpu is not None else record.cpu_s
        record.peak_rss = peak_rss_bytes()
        record.disk_used_bytes_after = disk_used_bytes(self.bundle)
        record.disk_growth_bytes = disk_delta(record.disk_used_bytes_before, record.disk_used_bytes_after)
        record.output_files = output_files or record.output_files
        record.output_hashes = output_hashes(record.output_files)
        record.row_counts = row_counts or record.row_counts
        record.cache_hit = cache_hit
        if warnings:
            record.warnings.extend(warnings)
        self.write_stage(record)
        self.write_runtime_summary()
        self.update_progress(CURRENT_PHASE=stage_name, LAST_PROGRESS_AT=record.finished_at)
        self.log(f"[analysis:{stage_name}] finish status={status} wall_s={record.wall_s:.3f}" if record.wall_s is not None else f"[analysis:{stage_name}] finish status={status}")

    def fail_stage(self, stage_name: str, exc: BaseException) -> None:
        record = self.records.setdefault(stage_name, StageRecord(stage_name))
        record.status = "FAIL"
        record.finished_at = now_iso()
        started = getattr(record, "_started_mono", None)
        record.wall_s = time.monotonic() - started if started is not None else record.wall_s
        record.cpu_s = process_cpu_seconds() - getattr(record, "_started_cpu", process_cpu_seconds())
        record.peak_rss = peak_rss_bytes()
        record.disk_used_bytes_after = disk_used_bytes(self.bundle)
        record.disk_growth_bytes = disk_delta(record.disk_used_bytes_before, record.disk_used_bytes_after)
        record.failure = {"exception_type": type(exc).__name__, "exception_message": str(exc)}
        self.update_progress(CURRENT_PHASE=stage_name, LAST_WARNING=str(exc), FINAL_STATUS="FAIL")
        self.write_stage(record)
        self.write_runtime_summary()
        self.log(f"[analysis:{stage_name}] fail {type(exc).__name__}: {exc}")

    def update_progress(self, *, force: bool = False, **values: Any) -> None:
        self.progress.update(values)
        self.progress["CURRENT_PHASE"] = self.current_phase
        self.write_status(force=force)

    def heartbeat(self, **values: Any) -> None:
        self.progress.update(values)
        if self.write_status(force=False):
            self.log(
                "[analysis:progress] "
                f"phase={self.current_phase} runs={self.progress.get('RUNS_PARSED')} "
                f"events={self.progress.get('RAW_EVENTS_READ')} requests={self.progress.get('REQUEST_ATTEMPTS_PARSED')}"
            )
        self.check_timeout()

    def check_timeout(self) -> None:
        elapsed = time.monotonic() - self.started_mono
        if self.timeout_seconds is not None and elapsed > self.timeout_seconds:
            raise TimeoutError(f"analysis timeout exceeded after {elapsed:.3f}s (limit={self.timeout_seconds:.3f}s)")

    def complete(self, final_status: str) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread and self._heartbeat_thread is not threading.current_thread():
            self._heartbeat_thread.join(timeout=min(self.progress_interval_seconds, 2.0))
        self.progress["RUN_STATUS"] = "COMPLETE" if final_status in {"PASS", "PARTIAL", "SKIPPED"} else "FAIL"
        self.progress["FINAL_STATUS"] = final_status
        self.progress["CURRENT_PHASE"] = "complete"
        self.progress["FINISHED_AT"] = now_iso()
        for record in self.records.values():
            path = self.stage_dir / record.stage_name / "stage-status.json"
            if record.status != "PENDING" or not path.is_file():
                self.write_stage(record)
        self.write_status(force=True)
        self.write_runtime_summary()
        self.restore_signal_handlers()

    def write_status(self, *, force: bool) -> bool:
        with self._status_lock:
            return self._write_status_unlocked(force=force)

    def _write_status_unlocked(self, *, force: bool) -> bool:
        now = time.monotonic()
        if not force and now - self.last_progress_mono < self.progress_interval_seconds:
            return False
        self.last_progress_mono = now
        elapsed = now - self.started_mono
        runs_scanned = int(self.progress.get("RUNS_SCANNED") or 0)
        runs_discovered = int(self.progress.get("RUNS_DISCOVERED") or 0)
        bytes_read = int(self.progress.get("EVENT_BYTES_READ") or 0)
        run_rate = runs_scanned / elapsed if elapsed > 0 else 0.0
        remaining_runs = max(runs_discovered - runs_scanned, 0)
        payload = {
            **self.progress,
            "HEARTBEAT_AT": now_iso(),
            "ELAPSED_SECONDS": elapsed,
            "PROCESSED_FILES": self.progress.get("EVENT_FILES_COMPLETED", 0),
            "PROCESSED_BYTES": bytes_read,
            "PROCESSED_EVENTS": self.progress.get("RAW_EVENTS_READ", 0),
            "PROCESSED_RUNS": runs_scanned,
            "RUN_RATE_PER_SECOND": run_rate,
            "BYTE_RATE_PER_SECOND": bytes_read / elapsed if elapsed > 0 else 0.0,
            "ETA_SECONDS": remaining_runs / run_rate if remaining_runs and run_rate > 0 else None,
            "MEMORY_RSS_BYTES": peak_rss_bytes(),
            **filesystem_usage_fields(self.temp_dir),
            "STAGE_STATUS": {name: record.status for name, record in self.records.items()},
        }
        write_json(self.status_file, payload)
        return True

    def _periodic_heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(self.progress_interval_seconds):
            if self.write_status(force=True):
                self.log(
                    "[analysis:heartbeat] "
                    f"phase={self.current_phase} elapsed_s={time.monotonic() - self.started_mono:.1f} "
                    f"runs={self.progress.get('RUNS_PARSED')} events={self.progress.get('RAW_EVENTS_READ')}"
                )

    def write_stage(self, record: StageRecord) -> None:
        payload = record.to_dict()
        payload["pipeline_result"] = self._pipeline_result_for(record).to_dict()
        payload["source_identity"] = self.source_identity
        write_json(self.stage_dir / record.stage_name / "stage-status.json", payload)
        self.write_shared_pipeline_manifest()

    def write_stage_dag(self) -> None:
        payload = {"schema_version": STAGE_SCHEMA_VERSION, "nodes": stage_dag_nodes(), "edges": stage_dag_edges()}
        write_json(self.bundle / "stage_dag.json", payload)
        write_json(
            self.bundle / "pipeline_dag.json",
            {"schema_version": "2.0", "nodes": payload["nodes"], "edges": payload["edges"], "pipeline_kind": "analysis_compatibility_facade"},
        )

    def write_shared_pipeline_manifest(self) -> None:
        """Mirror legacy stage records into the common pipeline result schema."""
        write_json(
            self.bundle / "pipeline_manifest.json",
            {
                "schema_version": "2.0",
                "result_type": "agentmon.pipeline.manifest",
                "pipeline_kind": "analysis_compatibility_facade",
                "source_identity": self.source_identity,
                "stages": [
                    {
                        "stage_name": name,
                        "disposition": "CACHED" if record.cache_hit else "EXECUTED",
                        **self._pipeline_result_for(record).to_dict(),
                    }
                    for name, record in self.records.items()
                ],
            },
        )

    def _pipeline_result_for(self, record: StageRecord) -> PipelineStageResult:
        status_to_outcome = {
            "PASS": "COMPLETE",
            "CACHED": "COMPLETE",
            "PARTIAL": "PARTIAL",
            "FAIL": "FAILED",
            "SKIPPED": "SKIPPED",
            "PENDING": "SKIPPED",
            "RUNNING": "SKIPPED",
        }
        artifacts = tuple(
            ArtifactRef(path=path, sha256=record.output_hashes.get(path))
            for path in record.output_files
        )
        reason_codes = tuple(record.warnings)
        if not reason_codes and record.status in {"SKIPPED", "PENDING"}:
            reason_codes = (
                "legacy_stage_not_selected" if record.status == "SKIPPED" else "legacy_stage_not_started",
            )
        return PipelineStageResult(
            outcome=status_to_outcome.get(record.status, "UNRESOLVED"),
            artifacts=artifacts,
            row_counts=dict(record.row_counts),
            reason_codes=reason_codes,
            evidence_sources=tuple(record.input_hashes),
            data_quality=status_to_outcome.get(record.status, "UNRESOLVED"),
            payload={
                "legacy_status": record.status,
                "resource_usage": {
                    "wall_s": record.wall_s,
                    "cpu_s": record.cpu_s,
                    "peak_rss": record.peak_rss,
                    "disk_growth_bytes": record.disk_growth_bytes,
                },
            },
        )

    def _assert_dependencies_satisfied(self, stage_name: str) -> None:
        spec = stage_spec(stage_name)
        incomplete = [
            dependency
            for dependency in spec.dependencies
            # The legacy Analysis flow intentionally continues after a failed
            # quality gate to produce diagnostic artifacts.  It therefore
            # needs only an ordering guard here; a future extracted stage can
            # opt into PipelineExecutor capability blocking explicitly.
            if self.records[dependency].status in {"PENDING", "RUNNING"}
        ]
        if incomplete:
            raise RuntimeError(
                f"analysis stage {stage_name} cannot start before dependencies complete: {','.join(incomplete)}"
            )

    def write_runtime_summary(self) -> None:
        rows = []
        for record in self.records.values():
            rows.append(
                {
                    "stage_name": record.stage_name,
                    "status": record.status,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "wall_s": record.wall_s,
                    "CPU_s": record.cpu_s,
                    "peak_rss": record.peak_rss,
                    "disk_growth_bytes": record.disk_growth_bytes,
                    "cache_hit": record.cache_hit,
                    "warning_count": len(record.warnings),
                    "failure_type": record.failure.get("exception_type") if record.failure else None,
                }
            )
        write_plain_csv(self.bundle / "stage_runtime_summary.csv", rows)

    def skip_pending_after(self, stage_name: str, *, reason: str) -> None:
        boundary = STAGE_NAMES.index(stage_name)
        for index, name in enumerate(STAGE_NAMES):
            record = self.records[name]
            if index > boundary and record.status == "PENDING":
                record.status = "SKIPPED"
                record.finished_at = now_iso()
                record.warnings.append(reason)
                self.write_stage(record)
        self.write_runtime_summary()

    def log(self, message: str) -> None:
        if self.log_level.upper() not in {"ERROR", "CRITICAL"}:
            print(message)

    def install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous_signal_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            except (OSError, ValueError):
                continue

    def restore_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in self._previous_signal_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue
        self._previous_signal_handlers.clear()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        exc = RuntimeError(f"analysis interrupted by signal {signum}")
        if self.current_phase in self.records and self.records[self.current_phase].status == "RUNNING":
            self.fail_stage(self.current_phase, exc)
        self.progress["INTERRUPTED_BY_SIGNAL"] = signum
        self.complete("FAIL")
        raise KeyboardInterrupt(str(exc))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def peak_rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)


def disk_used_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).used
    except OSError:
        return None


def filesystem_usage_fields(path: Path) -> dict[str, int | str | None]:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
    except OSError:
        return {
            "TEMP_FILESYSTEM_PATH": str(path),
            "TEMP_DISK_USAGE_BYTES": None,
            "TEMP_DISK_FREE_BYTES": None,
        }
    return {
        "TEMP_FILESYSTEM_PATH": str(path),
        "TEMP_DISK_USAGE_BYTES": usage.used,
        "TEMP_DISK_FREE_BYTES": usage.free,
    }


def disk_delta(before: int | None, after: int | None) -> int | None:
    return after - before if before is not None and after is not None else None


def output_hashes(files: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in files:
        path = Path(item)
        if path.is_file():
            try:
                hashes[str(path)] = file_sha256(path)
            except OSError:
                pass
    return hashes


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def build_stage_execution_plan(
    bundle_root: str | Path,
    *,
    resume: bool,
    from_stage: str | None = None,
    to_stage: str | None = None,
    force_stages: list[str] | None = None,
    invalidate_stages: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    force = set(force_stages or [])
    invalidate = set(invalidate_stages or [])
    unknown = (force | invalidate | {value for value in (from_stage, to_stage) if value}) - set(STAGE_NAMES)
    if unknown:
        raise ValueError(f"unknown analysis stage(s): {','.join(sorted(unknown))}")
    start_index = STAGE_NAMES.index(from_stage) if from_stage else 0
    end_index = STAGE_NAMES.index(to_stage) if to_stage else len(STAGE_NAMES) - 1
    if start_index > end_index:
        raise ValueError("--from-stage must not follow --to-stage")
    prior_status = {}
    status_path = Path(bundle_root) / "analysis_manifest.json"
    if status_path.is_file():
        try:
            prior_status = json.loads(status_path.read_text(encoding="utf-8")).get("stage_completion_status", {})
        except (OSError, json.JSONDecodeError):
            prior_status = {}
    rows: list[dict[str, Any]] = []
    upstream_invalidated = False
    dependency_boundary = STAGE_NAMES.index("discovery")
    for index, stage in enumerate(STAGE_NAMES):
        in_range = start_index <= index <= end_index
        explicitly_invalid = stage in force or stage in invalidate
        prior_completed = prior_status.get(stage) in {"PASS", "CACHED"} and stage_cache_artifacts_ready(Path(bundle_root), stage)
        if explicitly_invalid or (resume and index >= dependency_boundary and in_range and not prior_completed):
            upstream_invalidated = True
        cacheable = stage_spec(stage).cacheable
        cached = resume and cacheable and not upstream_invalidated and prior_completed
        if index < start_index:
            action = "CACHED" if cached else "SKIP_OUTSIDE_RANGE"
        elif index > end_index:
            action = "SKIP_OUTSIDE_RANGE"
        else:
            action = "RUN" if explicitly_invalid or not cached else "CACHED"
        rows.append(
            {
                "stage_name": stage,
                "action": action,
                "reason": "outside_requested_range" if not in_range else "explicitly_forced_or_invalidated" if explicitly_invalid else "compatible_completed_stage" if cached else "not_cacheable_or_cache_unavailable",
                "prior_status": prior_status.get(stage),
                "cacheable": cacheable,
            }
        )
    payload = {
        "schema_version": STAGE_SCHEMA_VERSION,
        "resume": resume,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "force_stages": sorted(force),
        "invalidate_stages": sorted(invalidate),
        "dry_run": dry_run,
        "stages": rows,
    }
    write_json(Path(bundle_root) / "stage_execution_plan.json", payload)
    return payload


def stage_cache_artifacts_ready(bundle: Path, stage_name: str) -> bool:
    payload_path = bundle / "stages" / stage_name / "stage-status.json"
    if not payload_path.is_file():
        return False
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("status") not in {"PASS", "CACHED"}:
        return False
    files = payload.get("output_files")
    hashes = payload.get("output_hashes")
    if not files:
        return stage_name in {"discovery", "schema_resolution"}
    if not isinstance(files, list) or not isinstance(hashes, dict):
        return False
    for item in files:
        path = Path(str(item))
        expected = hashes.get(str(path))
        if not path.is_file() or not expected:
            return False
        try:
            if file_sha256(path) != expected:
                return False
        except OSError:
            return False
    return True


def stop_after_stage(stage_name: str, to_stage: str | None) -> None:
    if to_stage == stage_name:
        raise StageRangeComplete(stage_name)
