from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shlex
from typing import Any
from uuid import uuid4

from agentmon.config import AgentMonConfig
from agentmon.runtime.audit import RuntimeAuditLog


@dataclass(frozen=True)
class ToolPoolStatus:
    pool_id: str
    enabled: bool
    max_concurrent: int
    running_count: int
    available_slots: int
    quality: str = "static_local_pool"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolPoolAssignment:
    pool_id: str
    executable: str | None
    command_argc: int
    max_concurrent: int
    reason: str
    quality: str = "static_command_shape"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.tool_pool_assignment",
            **asdict(self),
        }


@dataclass(frozen=True)
class ToolPoolLease:
    lease_id: str
    admitted: bool
    pool_id: str
    executable: str | None
    mode: str
    max_concurrent: int
    running_count: int
    available_slots: int
    reason: str
    quality: str = "local_tool_pool_lease"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.tool_pool_lease",
            **asdict(self),
        }


class ToolPoolManager:
    """Static tool pool capacity view.

    Phase 5 will attach this to ToolExecutor. For now it provides the capacity
    contract and CLI/status shape without taking OS-level actions.
    """

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg
        self._running_counts = {
            "default": 0,
            "cpu_heavy": 0,
            "io_heavy": 0,
            "high_priority": 0,
        }
        self.audit = RuntimeAuditLog(cfg)

    def status(self, *, running_counts: dict[str, int] | None = None) -> dict[str, Any]:
        counts = self._running_counts if running_counts is None else running_counts
        pools = [
            self._pool("default", self.cfg.tool_pool.default_max_concurrent, counts.get("default", 0)),
            self._pool("cpu_heavy", self.cfg.tool_pool.cpu_heavy_max_concurrent, counts.get("cpu_heavy", 0)),
            self._pool("io_heavy", self.cfg.tool_pool.io_heavy_max_concurrent, counts.get("io_heavy", 0)),
            self._pool(
                "high_priority",
                self.cfg.tool_pool.high_priority_max_concurrent,
                counts.get("high_priority", 0),
            ),
        ]
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.tool_pool_status",
            "enabled": self._enabled(),
            "runtime_optimization_enabled": self.cfg.runtime_optimization.enabled,
            "mode": self.cfg.control.mode,
            "pools": [pool.to_dict() for pool in pools],
        }

    def try_acquire(
        self,
        command: list[str],
        *,
        running_counts: dict[str, int] | None = None,
    ) -> ToolPoolLease:
        assignment = self.classify(command)
        counts = self._running_counts if running_counts is None else running_counts
        running_count = counts.get(assignment.pool_id, 0)
        available_slots = max(0, assignment.max_concurrent - running_count)
        enabled = self._enabled()
        enforce = enabled and self.cfg.control.mode == "enforce"
        admitted = available_slots > 0 or not enforce
        reason = "slot_available"
        if not enabled:
            reason = "pool_disabled"
        elif not enforce:
            reason = "advise_only"
        elif not admitted:
            reason = "pool_capacity_reached"
        if admitted and running_counts is None and enabled:
            self._running_counts[assignment.pool_id] = running_count + 1
        lease = ToolPoolLease(
            lease_id=f"tool-pool-{uuid4().hex[:8]}",
            admitted=admitted,
            pool_id=assignment.pool_id,
            executable=assignment.executable,
            mode=self.cfg.control.mode,
            max_concurrent=assignment.max_concurrent,
            running_count=running_count,
            available_slots=available_slots,
            reason=reason,
        )
        self.audit.emit(
            "control.tool_pool.acquire",
            source="control",
            payload=lease.to_dict(),
        )
        return lease

    def release(self, pool_id: str) -> dict[str, Any]:
        if pool_id in self._running_counts and self._running_counts[pool_id] > 0:
            self._running_counts[pool_id] -= 1
        status = self.status()
        self.audit.emit(
            "control.tool_pool.release",
            source="control",
            payload={"pool_id": pool_id, "status": status},
        )
        return status

    def classify(self, command: list[str]) -> ToolPoolAssignment:
        executable, argc = command_shape(command)
        pool_id = "default"
        reason = "default_pool"
        if executable in set(self.cfg.tool_pool.cpu_heavy_executables):
            pool_id = "cpu_heavy"
            reason = "matched_cpu_heavy_executable"
        elif executable in set(self.cfg.tool_pool.io_heavy_executables):
            pool_id = "io_heavy"
            reason = "matched_io_heavy_executable"
        max_concurrent = {
            "default": self.cfg.tool_pool.default_max_concurrent,
            "cpu_heavy": self.cfg.tool_pool.cpu_heavy_max_concurrent,
            "io_heavy": self.cfg.tool_pool.io_heavy_max_concurrent,
            "high_priority": self.cfg.tool_pool.high_priority_max_concurrent,
        }[pool_id]
        assignment = ToolPoolAssignment(
            pool_id=pool_id,
            executable=executable,
            command_argc=argc,
            max_concurrent=max_concurrent,
            reason=reason,
        )
        self.audit.emit(
            "control.resource.assignment.updated",
            source="control",
            payload=assignment.to_dict(),
        )
        return assignment

    def _pool(self, pool_id: str, max_concurrent: int, running_count: int) -> ToolPoolStatus:
        return ToolPoolStatus(
            pool_id=pool_id,
            enabled=self._enabled(),
            max_concurrent=max_concurrent,
            running_count=running_count,
            available_slots=max(0, max_concurrent - running_count),
        )

    def _enabled(self) -> bool:
        return (
            self.cfg.runtime_optimization.enabled
            and self.cfg.control.enabled
            and self.cfg.tool_pool.enabled
        )


def command_shape(command: list[str]) -> tuple[str | None, int]:
    if not command:
        return None, 0
    if len(command) == 1:
        try:
            parts = shlex.split(command[0])
        except ValueError:
            parts = command[0].split()
        if not parts:
            return None, 0
        return Path(parts[0]).name, len(parts)
    if len(command) >= 3 and command[1] in {"-c", "-lc"}:
        try:
            parts = shlex.split(command[2])
        except ValueError:
            parts = command[2].split()
        if parts:
            return Path(parts[0]).name, len(parts)
    return Path(command[0]).name, len(command)
