from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import signal
import subprocess
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.runtime.audit import RuntimeAuditLog

from .resource_actuator import LocalToolResourceActuator
from .tool_pool import ToolPoolLease, ToolPoolManager


def run_tool_process(
    command: list[str], *, cwd: str | None, env: dict[str, str],
    timeout: float | None, preexec_fn: Any,
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=os.name == "posix", preexec_fn=preexec_fn,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException as exc:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            if isinstance(exc, subprocess.TimeoutExpired):
                # communicate() decodes the complete captured output, including
                # output produced before the timeout, using the text stream.
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from exc
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@dataclass(frozen=True)
class ToolExecutionResult:
    accepted: bool
    lease: ToolPoolLease
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    resource_control: dict[str, Any] | None = None
    quality: str = "local_subprocess_tool"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.tool_execution_result",
            **asdict(self),
            "lease": self.lease.to_dict(),
        }


class PooledToolExecutor:
    def __init__(self, cfg: AgentMonConfig, manager: ToolPoolManager | None = None) -> None:
        self.cfg = cfg
        self.manager = manager or ToolPoolManager(cfg)
        self.audit = RuntimeAuditLog(cfg)

    def execute(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        running_counts: dict[str, int] | None = None,
    ) -> ToolExecutionResult:
        lease = self.manager.try_acquire(command, running_counts=running_counts)
        actuator = LocalToolResourceActuator(self.cfg)
        resource_control = actuator.plan().to_dict()
        if not lease.admitted:
            result = ToolExecutionResult(
                accepted=False,
                lease=lease,
                error=lease.reason,
                resource_control=resource_control,
                quality="local_pool_rejected",
            )
            self.audit.emit(
                "control.tool_execution.rejected",
                source="control",
                payload=result.to_dict(),
            )
            return result
        try:
            completed = run_tool_process(
                command,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                timeout=timeout_seconds,
                preexec_fn=actuator.preexec_fn(),
            )
            result = ToolExecutionResult(
                accepted=True,
                lease=lease,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                resource_control=resource_control,
            )
            self.audit.emit(
                "control.tool_execution.completed",
                source="control",
                payload={
                    "accepted": result.accepted,
                    "lease": result.lease.to_dict(),
                    "exit_code": result.exit_code,
                    "error": result.error,
                    "resource_control": result.resource_control,
                },
            )
            return result
        except subprocess.TimeoutExpired as exc:
            result = ToolExecutionResult(
                accepted=True,
                lease=lease,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error="tool_timeout",
                resource_control=resource_control,
            )
            self.audit.emit(
                "control.tool_execution.error",
                source="control",
                payload={
                    "accepted": result.accepted,
                    "lease": result.lease.to_dict(),
                    "exit_code": result.exit_code,
                    "error": result.error,
                    "resource_control": result.resource_control,
                },
            )
            return result
        except Exception as exc:
            result = ToolExecutionResult(
                accepted=True,
                lease=lease,
                exit_code=70,
                error=str(exc),
                resource_control=resource_control,
            )
            self.audit.emit(
                "control.tool_execution.error",
                source="control",
                payload={
                    "accepted": result.accepted,
                    "lease": result.lease.to_dict(),
                    "exit_code": result.exit_code,
                    "error": result.error,
                    "resource_control": result.resource_control,
                },
            )
            return result
        finally:
            if running_counts is None:
                self.manager.release(lease.pool_id)
