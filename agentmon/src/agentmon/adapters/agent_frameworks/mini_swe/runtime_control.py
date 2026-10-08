from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.control import ModelRequestGate, PooledToolExecutor
from agentmon.optimizer import OptimizerService
from agentmon.optimizer.types import OptimizerDecision

from .environment import action_to_argv


class RuntimeControlRejected(RuntimeError):
    pass


class RuntimeControlledModel:
    """Opt-in mini-SWE model wrapper that applies runtime model gate."""

    def __init__(
        self,
        inner_model: Any,
        cfg: AgentMonConfig,
        *,
        gate: ModelRequestGate | None = None,
        decision: OptimizerDecision | None = None,
        current_inflight_requests: int | None = None,
    ) -> None:
        self.inner_model = inner_model
        self.cfg = cfg
        self.gate = gate or ModelRequestGate(cfg)
        self.decision = decision
        self.current_inflight_requests = current_inflight_requests

    def query(self, messages: Any, **kwargs: Any) -> Any:
        request_id = kwargs.pop("agentmon_model_request_id", None)
        if not self.cfg.runtime_optimization.enabled:
            return self.inner_model.query(messages, **kwargs)
        decision = self.decision or OptimizerService(self.cfg).decision()
        lease = self.gate.try_acquire(
            decision,
            request_id=request_id,
            current_inflight_requests=self.current_inflight_requests,
        )
        if not lease.admitted:
            raise RuntimeControlRejected(f"model request rejected by runtime gate: {lease.reason}")
        try:
            return self.inner_model.query(messages, **kwargs)
        finally:
            if self.current_inflight_requests is None:
                self.gate.release(lease.request_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner_model, name)


@dataclass(frozen=True)
class RuntimeControlledToolResult:
    output: str
    returncode: int | None
    runtime_control: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "returncode": self.returncode,
            "runtime_control": self.runtime_control,
        }


class RuntimeControlledLocalEnvironment:
    """Opt-in mini-SWE local tool executor backed by PooledToolExecutor."""

    def __init__(
        self,
        cfg: AgentMonConfig,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        executor: PooledToolExecutor | None = None,
    ) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.timeout = timeout
        self.executor = executor or PooledToolExecutor(cfg)

    def execute(self, action: Any, **kwargs: Any) -> dict[str, Any]:
        argv = action_to_argv(action)
        result = self.executor.execute(
            argv,
            cwd=kwargs.pop("cwd", self.cwd),
            timeout_seconds=kwargs.pop("timeout", self.timeout),
        )
        output = result.stdout
        if result.stderr:
            output = output + result.stderr
        return RuntimeControlledToolResult(
            output=output,
            returncode=result.exit_code,
            runtime_control=result.to_dict(),
        ).to_dict()
