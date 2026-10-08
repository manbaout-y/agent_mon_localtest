from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from agentmon.config import AgentMonConfig
from agentmon.paths import output_root
from agentmon.scheduler.registry import build_scheduler
from agentmon.scheduler.types import SchedulerStrategy
from agentmon.runtime.task_state import TaskStatePolicy, build_task_state_from_events

from .audit import RuntimeAuditLog
from .backend import AgentRuntimeBackend
from .queue import RuntimeQueueStore
from .types import (
    BackendStatus,
    RunHandle,
    RunSpec,
    RuntimeContext,
    RuntimeDaemonResult,
    RuntimeEnqueueResult,
    RuntimeRecoveryResult,
    RuntimeRunResult,
    RuntimeSubmitResult,
)


class RuntimeService:
    """Small runtime facade over backend implementations.

    Phase 1 intentionally runs backends synchronously. That keeps the old
    AgentMon execution path intact while giving scheduler/optimizer work a
    stable interface to target next.
    """

    def __init__(
        self,
        cfg: AgentMonConfig,
        backends: list[AgentRuntimeBackend],
        scheduler: SchedulerStrategy | None = None,
    ) -> None:
        self.cfg = cfg
        self._backends = {backend.name: backend for backend in backends}
        self.scheduler = scheduler or build_scheduler(cfg)
        self.queue_store = RuntimeQueueStore(cfg)
        self.audit = RuntimeAuditLog(cfg)
        self.daemon_owner = f"daemon-{uuid4().hex[:8]}"

    @classmethod
    def with_default_backends(cls, cfg: AgentMonConfig) -> "RuntimeService":
        from .backends.process import ProcessBackend

        return cls(cfg, [ProcessBackend(cfg)], scheduler=build_scheduler(cfg))

    def backend_capabilities(self) -> dict[str, dict[str, Any]]:
        return {name: backend.capabilities().to_dict() for name, backend in sorted(self._backends.items())}

    def submit_blocking(self, spec: RunSpec) -> RuntimeSubmitResult:
        decision = self.scheduler.decide(spec)
        if decision.action == "defer":
            return RuntimeSubmitResult(
                accepted=False,
                scheduler_decision=decision.to_dict(),
                run_result=None,
            )
        return self._run_admitted(spec, decision.to_dict())

    def enqueue(self, spec: RunSpec) -> RuntimeEnqueueResult:
        return self.queue_store.enqueue(spec)

    def drain_queue_once(
        self,
        max_runs: int = 1,
        *,
        lease_seconds: float = 3600.0,
        heartbeat_interval_seconds: float = 5.0,
    ) -> RuntimeDaemonResult:
        processed_count = 0
        started_count = 0
        deferred_count = 0
        failed_count = 0
        results: list[dict[str, Any]] = []

        for _ in range(max(0, max_runs)):
            record = self.queue_store.next_pending()
            if record is None:
                break
            processed_count += 1
            decision = self.scheduler.decide(record.spec)
            if decision.action == "defer":
                self.queue_store.mark_deferred(record, decision.to_dict())
                deferred_count += 1
                results.append(
                    {
                        "queue_id": record.queue_id,
                        "state": "deferred",
                        "scheduler_decision": decision.to_dict(),
                    }
                )
                continue
            running = self.queue_store.claim_running(
                record,
                decision.to_dict(),
                owner=self.daemon_owner,
                lease_seconds=lease_seconds,
            )
            if running is None:
                continue
            try:
                result = self._run_queued_record(
                    running,
                    decision.to_dict(),
                    lease_seconds=lease_seconds,
                    heartbeat_interval_seconds=heartbeat_interval_seconds,
                )
            except Exception as exc:
                self.queue_store.mark_error(running, str(exc))
                failed_count += 1
                results.append({"queue_id": running.queue_id, "state": "failed", "error": str(exc)})
                continue
            result_dict = result.to_dict()
            self.queue_store.mark_finished(running, result_dict)
            started_count += 1
            if result.exit_code != 0:
                failed_count += 1
            results.append({"queue_id": running.queue_id, "state": "finished", "result": result_dict})

        result = RuntimeDaemonResult(
            processed_count=processed_count,
            started_count=started_count,
            deferred_count=deferred_count,
            failed_count=failed_count,
            results=results,
        )
        self.audit.emit(
            "runtime.daemon.pass_completed",
            payload={
                "processed_count": processed_count,
                "started_count": started_count,
                "deferred_count": deferred_count,
                "failed_count": failed_count,
            },
        )
        return result

    def recover_queue_orphans(self, *, stale_seconds: float, action: str) -> RuntimeRecoveryResult:
        recovered = self.queue_store.recover_orphans(stale_seconds=stale_seconds, action=action)
        result = RuntimeRecoveryResult(recovered_count=len(recovered), action=action, records=recovered)
        self.audit.emit(
            "runtime.daemon.orphan_recovery_completed",
            payload={"recovered_count": result.recovered_count, "action": action},
        )
        return result

    def _run_queued_record(
        self,
        record: Any,
        scheduler_decision: dict[str, object],
        *,
        lease_seconds: float,
        heartbeat_interval_seconds: float,
    ) -> RuntimeSubmitResult:
        stop = threading.Event()
        thread: threading.Thread | None = None
        interval = max(0.0, heartbeat_interval_seconds)
        if interval > 0:
            thread = threading.Thread(
                target=self._heartbeat_until_stopped,
                args=(record.queue_id, stop, lease_seconds, interval),
                daemon=True,
            )
            thread.start()
        try:
            return self._run_admitted(record.spec, scheduler_decision)
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=1.0)

    def _heartbeat_until_stopped(
        self,
        queue_id: str,
        stop: threading.Event,
        lease_seconds: float,
        heartbeat_interval_seconds: float,
    ) -> None:
        while not stop.wait(heartbeat_interval_seconds):
            self.queue_store.heartbeat(queue_id, owner=self.daemon_owner, lease_seconds=lease_seconds)

    def _run_admitted(self, spec: RunSpec, scheduler_decision: dict[str, object]) -> RuntimeSubmitResult:
        backend = self._backend(spec.backend)
        context = RuntimeContext(task_id=spec.task_id, run_id=None, run_dir=None, labels=dict(spec.labels))
        prepared = backend.prepare(spec, context)
        handle = backend.start(prepared)
        status = backend.poll(handle)
        return RuntimeSubmitResult(
            accepted=True,
            scheduler_decision=scheduler_decision,
            run_result=RuntimeRunResult(handle=handle, status=status, capabilities=backend.capabilities()),
        )

    def status(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            queue_record = self.queue_store.get(run_id)
            if queue_record is not None:
                return {
                    "schema_version": "1.0",
                    "result_type": "agentmon.runtime.queued_status",
                    **queue_record.summary(),
                }
            raise FileNotFoundError(f"runtime run manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.status",
            "run_id": manifest.get("run_id", run_dir.name),
            "run_dir": str(run_dir),
            "backend": manifest.get("runtime_backend", "unknown"),
            "task_id": manifest.get("runtime_task_id"),
            "state": manifest_to_runtime_state(manifest),
            "agentmon_status": manifest.get("status"),
            "exit_code": manifest.get("exit_code"),
            "duration_ns": manifest.get("duration_ns"),
            "label": manifest.get("label"),
            "data_quality": manifest.get("data_quality", {}),
        }

    def task_state(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        state_path = run_dir / "runtime_task_state.json"
        if state_path.is_file():
            return json.loads(state_path.read_text(encoding="utf-8"))
        manifest_path = run_dir / "manifest.json"
        task_id = None
        resource_budget = None
        initial_prediction = None
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
            task_id = manifest.get("runtime_task_id")
            spec = manifest.get("runtime_spec") if isinstance(manifest.get("runtime_spec"), dict) else {}
            resource_budget = spec.get("resource_budget")
            spec_input = spec.get("input") if isinstance(spec.get("input"), dict) else {}
            initial_prediction = spec_input.get("workload_prediction") if isinstance(spec_input, dict) else None
        events = read_jsonl_events(run_dir / "events.jsonl")
        return build_task_state_from_events(
            events,
            task_id=str(task_id) if task_id not in {None, ""} else None,
            run_id=run_dir.name,
            resource_budget=resource_budget if isinstance(resource_budget, dict) else None,
            policy=TaskStatePolicy.from_config(self.cfg),
            initial_prediction=initial_prediction if isinstance(initial_prediction, dict) else None,
        ).to_dict()

    def queue_snapshot(self) -> dict[str, Any]:
        runs_root = output_root(self.cfg)
        runs = []
        if runs_root.exists():
            for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
                manifest_path = run_dir / "manifest.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                runs.append(
                    {
                        "run_id": manifest.get("run_id", run_dir.name),
                        "run_dir": str(run_dir),
                        "backend": manifest.get("runtime_backend", "unknown"),
                        "task_id": manifest.get("runtime_task_id"),
                        "state": manifest_to_runtime_state(manifest),
                        "agentmon_status": manifest.get("status"),
                        "created_wall_ns": manifest.get("created_wall_ns"),
                        "duration_ns": manifest.get("duration_ns"),
                    }
                )
        state_counts: dict[str, int] = {}
        for run in runs:
            state = str(run.get("state") or "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.queue_snapshot",
            "runs_root": str(runs_root),
            "run_count": len(runs),
            "state_counts": state_counts,
            "runs": runs,
            "persistent_queue": self.queue_store.snapshot(),
            "audit_log": str(self.audit.path),
            "capabilities": self.backend_capabilities(),
            "scheduler": self.scheduler.snapshot(),
        }

    def _backend(self, name: str) -> AgentRuntimeBackend:
        try:
            return self._backends[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._backends)) or "none"
            raise ValueError(f"runtime backend not found: {name}; available: {available}") from exc

    def _resolve_run_dir(self, run_id: str) -> Path:
        path = Path(run_id).expanduser()
        if path.exists():
            return path
        return output_root(self.cfg) / run_id


def manifest_to_runtime_state(manifest: dict[str, Any]) -> str:
    status = manifest.get("status")
    if status == "running":
        return "running"
    if status == "initializing":
        return "starting"
    if status == "success":
        return "succeeded"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "failed"
    return "unknown"


def read_jsonl_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows
