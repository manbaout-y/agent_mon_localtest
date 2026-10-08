from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


AdmissionPressure = Literal["none", "moderate", "high"]


@dataclass(frozen=True)
class NodeSnapshot:
    cpu_util_pct: float | None = None
    memory_used_pct: float | None = None
    io_pressure: float | None = None
    quality: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelServiceSnapshot:
    service_id: str
    provider: str = "unknown"
    running_requests: int | None = None
    waiting_requests: int | None = None
    queue_latency_p95_s: float | None = None
    kv_usage_ratio: float | None = None
    quality: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunRuntimeProfile:
    run_id: str
    task_id: str | None
    state: str
    backend: str = "unknown"
    duration_ns: int | None = None
    classification: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueueSnapshot:
    running_count: int
    max_running_runs: int
    run_count: int
    state_counts: dict[str, int] = field(default_factory=dict)
    runs: tuple[RunRuntimeProfile, ...] = ()
    quality: str = "manifest_scan"

    def to_dict(self) -> dict[str, Any]:
        return {
            "running_count": self.running_count,
            "max_running_runs": self.max_running_runs,
            "run_count": self.run_count,
            "state_counts": dict(self.state_counts),
            "runs": [run.to_dict() for run in self.runs],
            "quality": self.quality,
        }


@dataclass(frozen=True)
class OptimizerDecision:
    strategy: str
    mode: str
    admission_pressure: AdmissionPressure
    target_concurrency: dict[str, int]
    task_class_limits: dict[str, int] = field(default_factory=dict)
    route_preferences: dict[str, float] = field(default_factory=dict)
    budget_recommendations: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: float = 1.0
    queue: QueueSnapshot | None = None
    model_service: ModelServiceSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.optimizer.decision",
            "strategy": self.strategy,
            "mode": self.mode,
            "admission_pressure": self.admission_pressure,
            "target_concurrency": dict(self.target_concurrency),
            "task_class_limits": dict(self.task_class_limits),
            "route_preferences": dict(self.route_preferences),
            "budget_recommendations": dict(self.budget_recommendations),
            "risk_flags": list(self.risk_flags),
            "reason": self.reason,
            "confidence": self.confidence,
            "queue": self.queue.to_dict() if self.queue else None,
            "model_service": self.model_service.to_dict() if self.model_service else None,
        }
