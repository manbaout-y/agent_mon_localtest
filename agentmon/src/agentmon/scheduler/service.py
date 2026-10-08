from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentmon.config import AgentMonConfig
from agentmon.paths import output_root

from .types import SchedulingDecision

if TYPE_CHECKING:
    from agentmon.runtime.types import RunSpec


ACTIVE_STATES = {"starting", "running", "admitted", "draining"}


class FixedConcurrencyScheduler:
    """Local admission controller based on run manifests.

    This is deliberately simple: Phase 2 only needs a stable scheduler seam.
    A future daemon-backed scheduler can replace this implementation without
    changing RuntimeService or backend contracts.
    """

    strategy = "fixed_concurrency"

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg

    def decide(self, spec: "RunSpec") -> SchedulingDecision:
        if not self.cfg.runtime_optimization.enabled or not self.cfg.scheduler.enabled:
            reason = (
                "runtime_optimization_disabled"
                if not self.cfg.runtime_optimization.enabled
                else "scheduler_disabled"
            )
            return SchedulingDecision(
                action="admit",
                strategy=self.strategy,
                task_id=spec.task_id,
                reason=reason,
                running_count=self.running_count(),
                max_running_runs=self.cfg.scheduler.max_running_runs,
                metadata={
                    "runtime_optimization_enabled": self.cfg.runtime_optimization.enabled,
                    "scheduler_configured_enabled": self.cfg.scheduler.enabled,
                },
            )
        running_count = self.running_count()
        max_running = self.cfg.scheduler.max_running_runs
        if running_count >= max_running:
            return SchedulingDecision(
                action="defer",
                strategy=self.strategy,
                task_id=spec.task_id,
                reason="max_running_runs_reached",
                running_count=running_count,
                max_running_runs=max_running,
            )
        return SchedulingDecision(
            action="admit",
            strategy=self.strategy,
            task_id=spec.task_id,
            reason="capacity_available",
            running_count=running_count,
            max_running_runs=max_running,
        )

    def running_count(self) -> int:
        return sum(1 for run in self.runs() if run.get("state") in ACTIVE_STATES)

    def snapshot(self) -> dict[str, Any]:
        runs = self.runs()
        state_counts: dict[str, int] = {}
        for run in runs:
            state = str(run.get("state") or "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.scheduler.snapshot",
            "strategy": self.strategy,
            "enabled": self.cfg.runtime_optimization.enabled and self.cfg.scheduler.enabled,
            "runtime_optimization_enabled": self.cfg.runtime_optimization.enabled,
            "scheduler_configured_enabled": self.cfg.scheduler.enabled,
            "max_running_runs": self.cfg.scheduler.max_running_runs,
            "running_count": sum(state_counts.get(state, 0) for state in ACTIVE_STATES),
            "state_counts": state_counts,
            "runs": runs,
        }

    def runs(self) -> list[dict[str, Any]]:
        runs_root = output_root(self.cfg)
        if not runs_root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
            manifest = read_manifest(run_dir)
            if manifest is None:
                continue
            rows.append(
                {
                    "run_id": manifest.get("run_id", run_dir.name),
                    "run_dir": str(run_dir),
                    "backend": manifest.get("runtime_backend", "unknown"),
                    "task_id": manifest.get("runtime_task_id"),
                    "state": manifest_to_scheduler_state(manifest),
                    "agentmon_status": manifest.get("status"),
                    "created_wall_ns": manifest.get("created_wall_ns"),
                    "duration_ns": manifest.get("duration_ns"),
                    "runtime_task_state": manifest.get("runtime_task_state"),
                }
            )
        return rows


class WorkloadAwareScheduler(FixedConcurrencyScheduler):
    """Admission controller that uses task-state predictions when available.

    Missing predictions remain unknown and fall back to the fixed-concurrency
    decision. This keeps the generic scheduler from inventing workload facts.
    """

    strategy = "workload_aware"

    def decide(self, spec: "RunSpec") -> SchedulingDecision:
        base = super().decide(spec)
        if not self.cfg.runtime_optimization.enabled or not self.cfg.scheduler.enabled:
            return SchedulingDecision(
                action=base.action,
                strategy=self.strategy,
                task_id=base.task_id,
                reason=base.reason,
                running_count=base.running_count,
                max_running_runs=base.max_running_runs,
                confidence=base.confidence,
                metadata={"base_strategy": base.strategy, **base.metadata},
            )
        if base.action == "defer":
            return SchedulingDecision(
                action=base.action,
                strategy=self.strategy,
                task_id=base.task_id,
                reason=base.reason,
                running_count=base.running_count,
                max_running_runs=base.max_running_runs,
                confidence=base.confidence,
                metadata={"base_strategy": base.strategy, **base.metadata},
            )
        active = [run for run in self.runs() if run.get("state") in ACTIVE_STATES]
        new_prediction = prediction_from_spec(spec)
        checks = self._workload_checks(active, new_prediction)
        for check in checks:
            if check["status"] == "defer":
                return SchedulingDecision(
                    action="defer",
                    strategy=self.strategy,
                    task_id=spec.task_id,
                    reason=str(check["reason"]),
                    running_count=base.running_count,
                    max_running_runs=base.max_running_runs,
                    confidence=float(check.get("confidence", 0.75)),
                    metadata={
                        "base_strategy": base.strategy,
                        "workload_checks": checks,
                        "new_prediction": new_prediction,
                    },
                )
        return SchedulingDecision(
            action="admit",
            strategy=self.strategy,
            task_id=spec.task_id,
            reason="capacity_and_workload_budget_available",
            running_count=base.running_count,
            max_running_runs=base.max_running_runs,
            confidence=0.85,
            metadata={
                "base_strategy": base.strategy,
                "workload_checks": checks,
                "new_prediction": new_prediction,
            },
        )

    def _workload_checks(
        self,
        active: list[dict[str, Any]],
        new_prediction: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks = []
        checks.append(
            budget_check(
                name="prompt_tokens_p90",
                budget=self.cfg.scheduler.workload_prompt_tokens_p90_budget,
                active=active,
                new_prediction=new_prediction,
                key="remaining_prompt_tokens_p90",
            )
        )
        checks.append(
            budget_check(
                name="generation_tokens_p90",
                budget=self.cfg.scheduler.workload_generation_tokens_p90_budget,
                active=active,
                new_prediction=new_prediction,
                key="remaining_generation_tokens_p90",
            )
        )
        checks.append(long_context_check(self.cfg, active, new_prediction))
        checks.append(abnormal_path_check(self.cfg, new_prediction))
        return [check for check in checks if check["status"] != "disabled"]


def read_manifest(run_dir: Path) -> dict[str, Any] | None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def manifest_to_scheduler_state(manifest: dict[str, Any]) -> str:
    runtime_state = manifest.get("runtime_state")
    if isinstance(runtime_state, str) and runtime_state:
        return runtime_state
    status = manifest.get("status")
    if status == "initializing":
        return "starting"
    if status == "running":
        return "running"
    if status == "success":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "unknown"


def prediction_from_spec(spec: "RunSpec") -> dict[str, Any]:
    raw = spec.input.get("workload_prediction") if isinstance(spec.input, dict) else None
    prediction = dict(raw) if isinstance(raw, dict) else {}
    for key in (
        "remaining_prompt_tokens_p90",
        "remaining_generation_tokens_p90",
        "remaining_model_requests_p90",
        "remaining_task_time_p90_s",
        "abnormal_path_probability",
        "active_context_tokens",
    ):
        if key in spec.input and key not in prediction:
            prediction[key] = spec.input[key]
    return prediction


def budget_check(
    *,
    name: str,
    budget: int | None,
    active: list[dict[str, Any]],
    new_prediction: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    if budget is None:
        return {"name": name, "status": "disabled", "reason": "budget_not_configured"}
    active_values = [task_prediction_value(run, key) for run in active]
    new_value = optional_number(new_prediction.get(key))
    known_values = [value for value in active_values if value is not None]
    known_total = sum(known_values) + (new_value or 0.0)
    unknown_count = len([value for value in active_values if value is None]) + (1 if new_value is None else 0)
    if new_value is not None and known_total > budget:
        return {
            "name": name,
            "status": "defer",
            "reason": f"{name}_budget_exceeded",
            "known_total": known_total,
            "budget": budget,
            "unknown_count": unknown_count,
            "confidence": 0.85,
        }
    return {
        "name": name,
        "status": "pass",
        "reason": "known_workload_within_budget",
        "known_total": known_total,
        "budget": budget,
        "unknown_count": unknown_count,
    }


def long_context_check(cfg: AgentMonConfig, active: list[dict[str, Any]], new_prediction: dict[str, Any]) -> dict[str, Any]:
    threshold = cfg.scheduler.long_context_token_threshold
    limit = cfg.scheduler.long_context_limit
    if threshold is None or limit is None:
        return {"name": "long_context", "status": "disabled", "reason": "long_context_policy_not_configured"}
    active_count = 0
    unknown_count = 0
    for run in active:
        value = task_context_tokens(run)
        if value is None:
            unknown_count += 1
        elif value >= threshold:
            active_count += 1
    new_value = optional_number(new_prediction.get("active_context_tokens"))
    new_is_long = new_value is not None and new_value >= threshold
    if new_is_long and active_count >= limit:
        return {
            "name": "long_context",
            "status": "defer",
            "reason": "long_context_limit_reached",
            "active_long_context_tasks": active_count,
            "limit": limit,
            "unknown_count": unknown_count,
            "confidence": 0.8,
        }
    return {
        "name": "long_context",
        "status": "pass",
        "reason": "long_context_capacity_available",
        "active_long_context_tasks": active_count,
        "limit": limit,
        "unknown_count": unknown_count + (1 if new_value is None else 0),
    }


def abnormal_path_check(cfg: AgentMonConfig, new_prediction: dict[str, Any]) -> dict[str, Any]:
    threshold = cfg.scheduler.abnormal_path_risk_threshold
    if threshold is None:
        return {"name": "abnormal_path", "status": "disabled", "reason": "abnormal_path_policy_not_configured"}
    risk = optional_number(new_prediction.get("abnormal_path_probability"))
    if risk is None:
        return {"name": "abnormal_path", "status": "pass", "reason": "abnormal_path_risk_unknown"}
    if risk >= threshold:
        return {
            "name": "abnormal_path",
            "status": "defer",
            "reason": "abnormal_path_risk_threshold_reached",
            "risk": risk,
            "threshold": threshold,
            "confidence": 0.75,
        }
    return {
        "name": "abnormal_path",
        "status": "pass",
        "reason": "abnormal_path_risk_below_threshold",
        "risk": risk,
        "threshold": threshold,
    }


def task_prediction_value(run: dict[str, Any], key: str) -> float | None:
    state = run.get("runtime_task_state") if isinstance(run.get("runtime_task_state"), dict) else {}
    prediction = state.get("prediction") if isinstance(state.get("prediction"), dict) else {}
    return optional_number(prediction.get(key))


def task_context_tokens(run: dict[str, Any]) -> float | None:
    state = run.get("runtime_task_state") if isinstance(run.get("runtime_task_state"), dict) else {}
    return optional_number(state.get("context_active_tokens"))


def optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
