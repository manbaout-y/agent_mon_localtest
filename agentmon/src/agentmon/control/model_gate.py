from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from agentmon.config import AgentMonConfig
from agentmon.optimizer.types import OptimizerDecision
from agentmon.runtime.audit import RuntimeAuditLog


@dataclass(frozen=True)
class ModelGateStatus:
    service_id: str
    enabled: bool
    mode: str
    pressure: str
    max_inflight_requests: int
    target_inflight_requests: int
    current_inflight_requests: int
    available_slots: int
    reason: str
    quality: str = "static_local_gate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.model_gate_status",
            **asdict(self),
        }


@dataclass(frozen=True)
class ModelGateLease:
    request_id: str
    admitted: bool
    service_id: str
    mode: str
    pressure: str
    target_inflight_requests: int
    current_inflight_requests: int
    available_slots: int
    reason: str
    quality: str = "local_gate_lease"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.model_gate_lease",
            **asdict(self),
        }


class ModelRequestGate:
    """Local model request gate target calculator.

    Phase 4 does not intercept real model requests yet. It turns optimizer
    pressure into an auditable target so the next step can enforce it in a
    provider wrapper.
    """

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg
        self._current_inflight_requests = 0
        self.audit = RuntimeAuditLog(cfg)

    def status(
        self,
        decision: OptimizerDecision | None = None,
        *,
        current_inflight_requests: int | None = None,
    ) -> ModelGateStatus:
        if current_inflight_requests is None:
            current_inflight_requests = self._current_inflight_requests
        pressure = decision.admission_pressure if decision is not None else "none"
        target = self._target_for_pressure(pressure)
        available = max(0, target - current_inflight_requests)
        reason = decision.reason if decision is not None else "no optimizer decision supplied"
        enabled = (
            self.cfg.runtime_optimization.enabled
            and self.cfg.control.enabled
            and self.cfg.model_gate.enabled
        )
        if not enabled:
            target = self.cfg.model_gate.max_inflight_requests
            available = max(0, target - current_inflight_requests)
            reason = (
                "runtime optimization disabled"
                if not self.cfg.runtime_optimization.enabled
                else "control or model gate disabled"
            )
        return ModelGateStatus(
            service_id=self.cfg.model_gate.service_id,
            enabled=enabled,
            mode=self.cfg.control.mode,
            pressure=pressure,
            max_inflight_requests=self.cfg.model_gate.max_inflight_requests,
            target_inflight_requests=target,
            current_inflight_requests=current_inflight_requests,
            available_slots=available,
            reason=reason,
        )

    def try_acquire(
        self,
        decision: OptimizerDecision | None = None,
        *,
        request_id: str | None = None,
        current_inflight_requests: int | None = None,
    ) -> ModelGateLease:
        status = self.status(decision, current_inflight_requests=current_inflight_requests)
        enforce = status.enabled and status.mode == "enforce"
        admitted = status.available_slots > 0 or not enforce
        reason = "slot_available"
        if not status.enabled:
            reason = "gate_disabled"
        elif not enforce:
            reason = "advise_only"
        elif not admitted:
            reason = "target_inflight_reached"
        if admitted and current_inflight_requests is None and status.enabled:
            self._current_inflight_requests += 1
        lease = ModelGateLease(
            request_id=request_id or f"model-gate-{uuid4().hex[:8]}",
            admitted=admitted,
            service_id=status.service_id,
            mode=status.mode,
            pressure=status.pressure,
            target_inflight_requests=status.target_inflight_requests,
            current_inflight_requests=status.current_inflight_requests,
            available_slots=status.available_slots,
            reason=reason,
        )
        self.audit.emit(
            "control.model_gate.acquire",
            source="control",
            payload=lease.to_dict(),
        )
        return lease

    def release(self, request_id: str | None = None) -> ModelGateStatus:
        if self._current_inflight_requests > 0:
            self._current_inflight_requests -= 1
        status = self.status()
        self.audit.emit(
            "control.model_gate.release",
            source="control",
            payload={"request_id": request_id, "status": status.to_dict()},
        )
        return status

    def _target_for_pressure(self, pressure: str) -> int:
        if pressure == "high":
            raw = self.cfg.model_gate.high_pressure_target
        elif pressure == "moderate":
            raw = self.cfg.model_gate.moderate_pressure_target
        else:
            raw = self.cfg.model_gate.max_inflight_requests
        max_inflight = self.cfg.model_gate.max_inflight_requests
        min_inflight = self.cfg.model_gate.min_inflight_requests
        return max(min_inflight, min(max_inflight, raw))
