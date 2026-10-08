from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.runtime.audit import RuntimeAuditLog
from agentmon.scheduler.registry import build_scheduler
from agentmon.scheduler.types import SchedulerStrategy

from .registry import build_optimizer_strategy
from .types import ModelServiceSnapshot, OptimizerDecision, QueueSnapshot, RunRuntimeProfile


class OptimizerService:
    def __init__(self, cfg: AgentMonConfig, scheduler: SchedulerStrategy | None = None) -> None:
        self.cfg = cfg
        self.scheduler = scheduler or build_scheduler(cfg)
        self.strategy = build_optimizer_strategy(cfg)
        self.audit = RuntimeAuditLog(cfg)

    def queue_snapshot(self) -> QueueSnapshot:
        scheduler_snapshot = self.scheduler.snapshot()
        return queue_snapshot_from_scheduler(scheduler_snapshot)

    def decision(self, model_service: ModelServiceSnapshot | None = None) -> OptimizerDecision:
        decision = evaluate_strategy(self.strategy, self.queue_snapshot(), model_service)
        self.audit.emit(
            "optimizer.decision",
            source="optimizer",
            payload=decision.to_dict(),
        )
        return decision

def queue_snapshot_from_scheduler(snapshot: dict[str, Any]) -> QueueSnapshot:
    runs = tuple(
        RunRuntimeProfile(
            run_id=str(row.get("run_id") or ""),
            task_id=str(row["task_id"]) if row.get("task_id") not in {None, ""} else None,
            state=str(row.get("state") or "unknown"),
            backend=str(row.get("backend") or "unknown"),
            duration_ns=row.get("duration_ns") if isinstance(row.get("duration_ns"), int) else None,
            classification=runtime_task_classification(row),
        )
        for row in snapshot.get("runs", [])
        if isinstance(row, dict)
    )
    state_counts = {
        str(key): int(value)
        for key, value in (snapshot.get("state_counts") or {}).items()
        if isinstance(value, int)
    }
    return QueueSnapshot(
        running_count=int(snapshot.get("running_count") or 0),
        max_running_runs=int(snapshot.get("max_running_runs") or 0),
        run_count=int(snapshot.get("run_count") or len(runs)),
        state_counts=state_counts,
        runs=runs,
    )


def evaluate_strategy(
    strategy: Any,
    queue: QueueSnapshot,
    model_service: ModelServiceSnapshot | None = None,
) -> OptimizerDecision:
    signature = inspect.signature(strategy.evaluate)
    if "model_service" in signature.parameters or len(signature.parameters) >= 2:
        return strategy.evaluate(queue, model_service)
    return strategy.evaluate(queue)


def model_service_snapshot_from_file(path: str | Path) -> ModelServiceSnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("model service snapshot must be a JSON object")
    return model_service_snapshot_from_payload(payload)


def model_service_snapshot_from_payload(payload: dict[str, Any]) -> ModelServiceSnapshot:
    if isinstance(payload.get("model_service"), dict):
        payload = payload["model_service"]
    return ModelServiceSnapshot(
        service_id=str(payload.get("service_id") or "runtime.default"),
        provider=str(payload.get("provider") or payload.get("type") or "unknown"),
        running_requests=_optional_int(payload.get("running_requests")),
        waiting_requests=_optional_int(payload.get("waiting_requests")),
        queue_latency_p95_s=_optional_float(payload.get("queue_latency_p95_s")),
        kv_usage_ratio=_optional_float(payload.get("kv_usage_ratio")),
        quality=str(payload.get("quality") or "fixture"),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def runtime_task_classification(row: dict[str, Any]) -> str:
    state = row.get("runtime_task_state") if isinstance(row.get("runtime_task_state"), dict) else {}
    path_state = state.get("path_state")
    if path_state not in {None, ""}:
        return str(path_state)
    return "unknown"
