from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentmon.contracts import ModelTarget, ResourceBudget, RetryPolicy


RunState = Literal[
    "submitted",
    "queued",
    "admitted",
    "starting",
    "running",
    "draining",
    "succeeded",
    "failed",
    "cancelled",
    "deferred",
    "unknown",
]


@dataclass(frozen=True)
class RunSpec:
    task_id: str
    backend: str
    input: dict[str, Any] = field(default_factory=dict)
    command: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    priority: int = 0
    resource_budget: ResourceBudget | None = None
    model_targets: tuple[ModelTarget, ...] = ()
    timeout_seconds: float | None = None
    retry_policy: RetryPolicy | None = None
    trajectory: str | None = None
    write_final_report: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "backend": self.backend,
            "input": dict(self.input),
            "command": list(self.command),
            "cwd": self.cwd,
            "env_keys": sorted(self.env),
            "labels": dict(self.labels),
            "priority": self.priority,
            "resource_budget": self.resource_budget.to_dict() if self.resource_budget else None,
            "model_targets": [target.to_dict() for target in self.model_targets],
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": self.retry_policy.to_dict() if self.retry_policy else None,
            "trajectory": self.trajectory,
            "write_final_report": self.write_final_report,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data["env"] = dict(self.env)
        data.pop("env_keys", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunSpec":
        resource_budget = data.get("resource_budget")
        retry_policy = data.get("retry_policy")
        return cls(
            task_id=str(data["task_id"]),
            backend=str(data.get("backend") or "process"),
            input=dict(data.get("input") or {}),
            command=tuple(str(item) for item in data.get("command") or ()),
            cwd=str(data["cwd"]) if data.get("cwd") not in {None, ""} else None,
            env={str(key): str(value) for key, value in (data.get("env") or {}).items()},
            labels={str(key): str(value) for key, value in (data.get("labels") or {}).items()},
            priority=int(data.get("priority") or 0),
            resource_budget=ResourceBudget(**resource_budget) if isinstance(resource_budget, dict) else None,
            model_targets=tuple(
                ModelTarget(
                    name=str(item["name"]),
                    provider=str(item.get("provider") or "default"),
                    weight=float(item.get("weight") if item.get("weight") is not None else 1.0),
                    labels={str(key): str(value) for key, value in (item.get("labels") or {}).items()},
                )
                for item in data.get("model_targets") or ()
                if isinstance(item, dict) and item.get("name")
            ),
            timeout_seconds=float(data["timeout_seconds"]) if data.get("timeout_seconds") is not None else None,
            retry_policy=RetryPolicy(
                max_retries=int(retry_policy.get("max_retries") or 0),
                retry_on=tuple(str(item) for item in retry_policy.get("retry_on") or ("runtime_failure",)),
            )
            if isinstance(retry_policy, dict)
            else None,
            trajectory=str(data["trajectory"]) if data.get("trajectory") not in {None, ""} else None,
            write_final_report=bool(data.get("write_final_report", True)),
        )


@dataclass(frozen=True)
class RuntimeContext:
    task_id: str
    run_id: str | None
    run_dir: Path | None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedRun:
    spec: RunSpec
    context: RuntimeContext
    backend_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    run_dir: Path
    backend: str
    state: RunState = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "backend": self.backend,
            "state": self.state,
        }


@dataclass(frozen=True)
class BackendStatus:
    state: RunState
    exit_code: int | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    start: bool = True
    poll: bool = True
    cancel: bool = False
    pause: bool = False
    resume: bool = False
    checkpoint: str = "none"
    semantic_events: tuple[str, ...] = ()
    tool_pid_attribution: bool = False
    model_request_id_injection: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start,
            "poll": self.poll,
            "cancel": self.cancel,
            "pause": self.pause,
            "resume": self.resume,
            "checkpoint": self.checkpoint,
            "semantic_events": list(self.semantic_events),
            "tool_pid_attribution": self.tool_pid_attribution,
            "model_request_id_injection": self.model_request_id_injection,
        }


@dataclass(frozen=True)
class RuntimeRunResult:
    handle: RunHandle
    status: BackendStatus
    capabilities: BackendCapabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.run_result",
            "handle": self.handle.to_dict(),
            "status": self.status.to_dict(),
            "capabilities": self.capabilities.to_dict(),
        }


@dataclass(frozen=True)
class RuntimeSubmitResult:
    accepted: bool
    scheduler_decision: dict[str, object]
    run_result: RuntimeRunResult | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.accepted and self.run_result is not None:
            data = self.run_result.to_dict()
            data["accepted"] = True
            data["scheduler_decision"] = dict(self.scheduler_decision)
            return data
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.submit_deferred",
            "accepted": False,
            "scheduler_decision": dict(self.scheduler_decision),
        }

    @property
    def exit_code(self) -> int:
        if not self.accepted:
            return 75
        if self.run_result is None or self.run_result.status.exit_code is None:
            return 0
        return self.run_result.status.exit_code


@dataclass(frozen=True)
class RuntimeEnqueueResult:
    queue_id: str
    state: str
    task_id: str
    backend: str
    priority: int
    submitted_wall_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.enqueue_result",
            "queue_id": self.queue_id,
            "state": self.state,
            "task_id": self.task_id,
            "backend": self.backend,
            "priority": self.priority,
            "submitted_wall_ns": self.submitted_wall_ns,
        }


@dataclass(frozen=True)
class RuntimeDaemonResult:
    processed_count: int
    started_count: int
    deferred_count: int
    failed_count: int
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.daemon_result",
            "processed_count": self.processed_count,
            "started_count": self.started_count,
            "deferred_count": self.deferred_count,
            "failed_count": self.failed_count,
            "results": list(self.results),
        }


@dataclass(frozen=True)
class RuntimeRecoveryResult:
    recovered_count: int
    action: str
    records: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.recovery_result",
            "recovered_count": self.recovered_count,
            "action": self.action,
            "records": list(self.records),
        }
