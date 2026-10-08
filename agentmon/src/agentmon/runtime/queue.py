from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from agentmon.config import AgentMonConfig
from agentmon.paths import chmod_private_file, make_private_dir, output_root, write_private_text

from .audit import RuntimeAuditLog
from .types import RunSpec, RuntimeEnqueueResult


PENDING_STATES = {"queued", "deferred"}
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


@dataclass(frozen=True)
class QueuedRunRecord:
    queue_id: str
    state: str
    spec: RunSpec
    submitted_wall_ns: int
    updated_wall_ns: int
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_wall_ns: int | None = None
    scheduler_decision: dict[str, object] | None = None
    run_result: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueuedRunRecord":
        return cls(
            queue_id=str(data["queue_id"]),
            state=str(data.get("state") or "queued"),
            spec=RunSpec.from_dict(dict(data["spec"])),
            submitted_wall_ns=int(data.get("submitted_wall_ns") or 0),
            updated_wall_ns=int(data.get("updated_wall_ns") or data.get("submitted_wall_ns") or 0),
            attempts=int(data.get("attempts") or 0),
            lease_owner=str(data["lease_owner"]) if data.get("lease_owner") not in {None, ""} else None,
            lease_expires_wall_ns=int(data["lease_expires_wall_ns"])
            if data.get("lease_expires_wall_ns") is not None
            else None,
            scheduler_decision=dict(data["scheduler_decision"]) if isinstance(data.get("scheduler_decision"), dict) else None,
            run_result=dict(data["run_result"]) if isinstance(data.get("run_result"), dict) else None,
            error=str(data["error"]) if data.get("error") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "queue_id": self.queue_id,
            "state": self.state,
            "spec": self.spec.to_storage_dict(),
            "submitted_wall_ns": self.submitted_wall_ns,
            "updated_wall_ns": self.updated_wall_ns,
            "attempts": self.attempts,
            "lease_owner": self.lease_owner,
            "lease_expires_wall_ns": self.lease_expires_wall_ns,
            "scheduler_decision": self.scheduler_decision,
            "run_result": self.run_result,
            "error": self.error,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "state": self.state,
            "task_id": self.spec.task_id,
            "backend": self.spec.backend,
            "priority": self.spec.priority,
            "attempts": self.attempts,
            "submitted_wall_ns": self.submitted_wall_ns,
            "updated_wall_ns": self.updated_wall_ns,
            "scheduler_decision": self.scheduler_decision,
            "lease_owner": self.lease_owner,
            "lease_expires_wall_ns": self.lease_expires_wall_ns,
            "run_result": self.run_result,
            "error": self.error,
        }


class RuntimeQueueStore:
    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg
        self.queue_dir = output_root(cfg) / "_runtime" / "queue"
        self.lock_path = output_root(cfg) / "_runtime" / "queue.lock"
        self.audit = RuntimeAuditLog(cfg)

    def enqueue(self, spec: RunSpec) -> RuntimeEnqueueResult:
        make_private_dir(self.queue_dir)
        now = time.time_ns()
        queue_id = f"q-{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        record = QueuedRunRecord(
            queue_id=queue_id,
            state="queued",
            spec=spec,
            submitted_wall_ns=now,
            updated_wall_ns=now,
        )
        self._write(record)
        self.audit.emit(
            "runtime.queue.enqueued",
            payload={
                "queue_id": record.queue_id,
                "task_id": spec.task_id,
                "backend": spec.backend,
                "priority": spec.priority,
            },
        )
        return RuntimeEnqueueResult(
            queue_id=record.queue_id,
            state=record.state,
            task_id=spec.task_id,
            backend=spec.backend,
            priority=spec.priority,
            submitted_wall_ns=record.submitted_wall_ns,
        )

    def list(self) -> list[QueuedRunRecord]:
        if not self.queue_dir.exists():
            return []
        records = []
        for path in sorted(self.queue_dir.glob("*.json")):
            record = self._read(path)
            if record is not None:
                records.append(record)
        return records

    def get(self, queue_id: str) -> QueuedRunRecord | None:
        path = self._path(queue_id)
        if not path.is_file():
            return None
        return self._read(path)

    def next_pending(self) -> QueuedRunRecord | None:
        now = time.time_ns()
        pending = [record for record in self.list() if self._is_claimable(record, now)]
        if not pending:
            return None
        return sorted(pending, key=lambda record: (-record.spec.priority, record.submitted_wall_ns))[0]

    def claim_running(
        self,
        record: QueuedRunRecord,
        scheduler_decision: dict[str, object],
        *,
        owner: str,
        lease_seconds: float = 3600.0,
    ) -> QueuedRunRecord | None:
        with self._locked():
            current = self.get(record.queue_id)
            if current is None or not self._is_claimable(current, time.time_ns()):
                return None
            updated = self._replace(
                current,
                state="running",
                attempts=current.attempts + 1,
                lease_owner=owner,
                lease_expires_wall_ns=time.time_ns() + int(lease_seconds * 1_000_000_000),
                scheduler_decision=scheduler_decision,
                error=None,
            )
            self._write(updated)
        self.audit.emit(
            "runtime.queue.running",
            payload={
                "queue_id": current.queue_id,
                "task_id": current.spec.task_id,
                "lease_owner": owner,
                "attempts": updated.attempts,
                "scheduler_decision": scheduler_decision,
            },
        )
        return updated

    def heartbeat(self, queue_id: str, *, owner: str, lease_seconds: float = 3600.0) -> QueuedRunRecord | None:
        with self._locked():
            current = self.get(queue_id)
            if current is None or current.state != "running" or current.lease_owner != owner:
                return None
            updated = self._replace(
                current,
                lease_expires_wall_ns=time.time_ns() + int(lease_seconds * 1_000_000_000),
            )
            self._write(updated)
        self.audit.emit(
            "runtime.queue.heartbeat",
            payload={
                "queue_id": updated.queue_id,
                "task_id": updated.spec.task_id,
                "lease_owner": owner,
                "lease_expires_wall_ns": updated.lease_expires_wall_ns,
            },
        )
        return updated

    def recover_orphans(self, *, stale_seconds: float, action: str) -> list[dict[str, Any]]:
        if action not in {"defer", "fail"}:
            raise ValueError("orphan recovery action must be defer or fail")
        now = time.time_ns()
        stale_ns = int(max(0.0, stale_seconds) * 1_000_000_000)
        recovered: list[dict[str, Any]] = []
        with self._locked():
            for record in self.list():
                if not self._is_orphan(record, now=now, stale_ns=stale_ns):
                    continue
                state = "deferred" if action == "defer" else "failed"
                error = "recovered_orphan_running_record"
                updated = self._replace(
                    record,
                    state=state,
                    lease_owner=None,
                    lease_expires_wall_ns=None,
                    error=error,
                )
                self._write(updated)
                recovered.append(updated.summary())
        for row in recovered:
            self.audit.emit(
                "runtime.queue.orphan_recovered",
                payload={"queue_id": row["queue_id"], "state": row["state"], "action": action},
            )
        return recovered

    def mark_deferred(self, record: QueuedRunRecord, scheduler_decision: dict[str, object]) -> QueuedRunRecord:
        updated = self._replace(
            record,
            state="deferred",
            lease_owner=None,
            lease_expires_wall_ns=None,
            scheduler_decision=scheduler_decision,
            error=None,
        )
        self._write(updated)
        self.audit.emit(
            "runtime.queue.deferred",
            payload={
                "queue_id": record.queue_id,
                "task_id": record.spec.task_id,
                "scheduler_decision": scheduler_decision,
            },
        )
        return updated

    def mark_finished(self, record: QueuedRunRecord, run_result: dict[str, Any]) -> QueuedRunRecord:
        status = run_result.get("status") if isinstance(run_result.get("status"), dict) else {}
        state = str(status.get("state") or "unknown")
        terminal_state = state if state in TERMINAL_STATES else "failed"
        updated = self._replace(
            record,
            state=terminal_state,
            lease_owner=None,
            lease_expires_wall_ns=None,
            run_result=run_result,
            error=None,
        )
        self._write(updated)
        self.audit.emit(
            "runtime.queue.finished",
            payload={
                "queue_id": record.queue_id,
                "task_id": record.spec.task_id,
                "state": terminal_state,
                "run_result": run_result,
            },
        )
        return updated

    def mark_error(self, record: QueuedRunRecord, error: str) -> QueuedRunRecord:
        updated = self._replace(record, state="failed", lease_owner=None, lease_expires_wall_ns=None, error=error)
        self._write(updated)
        self.audit.emit(
            "runtime.queue.failed",
            payload={"queue_id": record.queue_id, "task_id": record.spec.task_id, "error": error},
            quality="exact",
        )
        return updated

    def snapshot(self) -> dict[str, Any]:
        records = self.list()
        state_counts: dict[str, int] = {}
        for record in records:
            state_counts[record.state] = state_counts.get(record.state, 0) + 1
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.persistent_queue_snapshot",
            "queue_dir": str(self.queue_dir),
            "record_count": len(records),
            "pending_count": sum(1 for record in records if record.state in PENDING_STATES),
            "state_counts": state_counts,
            "records": [record.summary() for record in records],
        }

    def _path(self, queue_id: str) -> Path:
        return self.queue_dir / f"{queue_id}.json"

    def _read(self, path: Path) -> QueuedRunRecord | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return QueuedRunRecord.from_dict(data)
        except Exception:
            return None

    def _write(self, record: QueuedRunRecord) -> None:
        make_private_dir(self.queue_dir)
        path = self._path(record.queue_id)
        tmp_path = path.with_suffix(".json.tmp")
        write_private_text(tmp_path, json.dumps(record.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        tmp_path.replace(path)
        chmod_private_file(path)

    def _replace(self, record: QueuedRunRecord, **changes: Any) -> QueuedRunRecord:
        data = record.to_dict()
        data.update(changes)
        data["updated_wall_ns"] = time.time_ns()
        return QueuedRunRecord.from_dict(data)

    def _is_claimable(self, record: QueuedRunRecord, now: int) -> bool:
        return record.state in PENDING_STATES

    def _is_orphan(self, record: QueuedRunRecord, *, now: int, stale_ns: int) -> bool:
        if record.state != "running":
            return False
        stale_by_update = record.updated_wall_ns + stale_ns <= now
        lease_expired = record.lease_expires_wall_ns is None or record.lease_expires_wall_ns <= now
        return stale_by_update and lease_expired

    @contextmanager
    def _locked(self):
        make_private_dir(self.lock_path.parent)
        fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            chmod_private_file(self.lock_path)
