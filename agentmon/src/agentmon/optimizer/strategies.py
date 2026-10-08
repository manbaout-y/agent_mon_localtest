from __future__ import annotations

from agentmon.config import AgentMonConfig

from .types import ModelServiceSnapshot, OptimizerDecision, QueueSnapshot


class StaticThresholdOptimizer:
    name = "static_threshold"

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        queue: QueueSnapshot,
        model_service: ModelServiceSnapshot | None = None,
    ) -> OptimizerDecision:
        target = {"runtime.default": queue.max_running_runs}
        if not self.cfg.runtime_optimization.enabled or not self.cfg.optimizer.enabled:
            return OptimizerDecision(
                strategy=self.name,
                mode=self.cfg.optimizer.mode,
                admission_pressure="none",
                target_concurrency=target,
                risk_flags=[],
                reason=(
                    "runtime_optimization_disabled"
                    if not self.cfg.runtime_optimization.enabled
                    else "optimizer_disabled"
                ),
                confidence=1.0,
                queue=queue,
                model_service=model_service,
            )
        model_pressure = self._model_pressure(model_service)
        if model_pressure:
            return OptimizerDecision(
                strategy=self.name,
                mode=self.cfg.optimizer.mode,
                admission_pressure="high",
                target_concurrency=target,
                risk_flags=model_pressure,
                reason="model service telemetry indicates high pressure",
                confidence=0.9,
                queue=queue,
                model_service=model_service,
            )
        if queue.max_running_runs <= 0:
            return OptimizerDecision(
                strategy=self.name,
                mode=self.cfg.optimizer.mode,
                admission_pressure="high",
                target_concurrency=target,
                risk_flags=["scheduler_capacity_unavailable"],
                reason="scheduler max_running_runs is zero",
                confidence=1.0,
                queue=queue,
                model_service=model_service,
            )
        utilization = queue.running_count / queue.max_running_runs
        if queue.running_count >= queue.max_running_runs:
            return OptimizerDecision(
                strategy=self.name,
                mode=self.cfg.optimizer.mode,
                admission_pressure="high",
                target_concurrency=target,
                risk_flags=["scheduler_capacity_full"],
                reason="running count has reached scheduler capacity",
                confidence=1.0,
                queue=queue,
                model_service=model_service,
            )
        if utilization >= self.cfg.optimizer.high_utilization_ratio:
            return OptimizerDecision(
                strategy=self.name,
                mode=self.cfg.optimizer.mode,
                admission_pressure="moderate",
                target_concurrency=target,
                risk_flags=["scheduler_capacity_near_limit"],
                reason="running count is near scheduler capacity",
                confidence=0.85,
                queue=queue,
                model_service=model_service,
            )
        return OptimizerDecision(
            strategy=self.name,
            mode=self.cfg.optimizer.mode,
            admission_pressure="none",
            target_concurrency=target,
            risk_flags=[],
            reason="scheduler capacity is available",
            confidence=0.9,
            queue=queue,
            model_service=model_service,
        )

    def _model_pressure(self, model_service: ModelServiceSnapshot | None) -> list[str]:
        if model_service is None:
            return []
        flags: list[str] = []
        waiting = model_service.waiting_requests
        if waiting is not None and waiting >= self.cfg.optimizer.model_waiting_requests_high:
            flags.append("model_waiting_requests_high")
        latency = model_service.queue_latency_p95_s
        if latency is not None and latency >= self.cfg.optimizer.model_queue_latency_high_s:
            flags.append("model_queue_latency_high")
        kv_ratio = model_service.kv_usage_ratio
        if kv_ratio is not None and kv_ratio >= self.cfg.optimizer.model_kv_high_ratio:
            flags.append("model_kv_usage_high")
        return flags


class WorkloadAwareOptimizer(StaticThresholdOptimizer):
    name = "workload_aware"

    def evaluate(
        self,
        queue: QueueSnapshot,
        model_service: ModelServiceSnapshot | None = None,
    ) -> OptimizerDecision:
        base = super().evaluate(queue, model_service)
        if not self.cfg.runtime_optimization.enabled or not self.cfg.optimizer.enabled:
            return OptimizerDecision(
                strategy=self.name,
                mode=base.mode,
                admission_pressure=base.admission_pressure,
                target_concurrency=base.target_concurrency,
                task_class_limits=base.task_class_limits,
                route_preferences=base.route_preferences,
                budget_recommendations=base.budget_recommendations,
                risk_flags=base.risk_flags,
                reason=base.reason,
                confidence=base.confidence,
                queue=queue,
                model_service=model_service,
            )
        stalled = [run for run in queue.runs if run.classification == "STALLED"]
        low_progress = [run for run in queue.runs if run.classification == "LOW_PROGRESS"]
        if not stalled and not low_progress:
            return OptimizerDecision(
                strategy=self.name,
                mode=base.mode,
                admission_pressure=base.admission_pressure,
                target_concurrency=base.target_concurrency,
                task_class_limits=base.task_class_limits,
                route_preferences=base.route_preferences,
                budget_recommendations=base.budget_recommendations,
                risk_flags=base.risk_flags,
                reason=base.reason,
                confidence=base.confidence,
                queue=queue,
                model_service=model_service,
            )
        risk_flags = list(base.risk_flags)
        if stalled:
            risk_flags.append("stalled_runtime_tasks_present")
        if low_progress:
            risk_flags.append("low_progress_runtime_tasks_present")
        budget_recommendations = dict(base.budget_recommendations)
        for run in stalled:
            if run.run_id:
                budget_recommendations[run.run_id] = {
                    "action": "defer_or_degrade_future_model_requests",
                    "classification": run.classification,
                    "reason": "task_state_stalled",
                    "mode": self.cfg.optimizer.mode,
                }
        for run in low_progress:
            if run.run_id and run.run_id not in budget_recommendations:
                budget_recommendations[run.run_id] = {
                    "action": "compact_context_and_replan",
                    "classification": run.classification,
                    "reason": "task_state_low_progress",
                    "mode": self.cfg.optimizer.mode,
                }
        pressure = base.admission_pressure
        reason = base.reason
        confidence = min(base.confidence, 0.85)
        if stalled and pressure == "none":
            pressure = "moderate"
            reason = "runtime task-state advisories indicate stalled tasks"
        elif low_progress and pressure == "none":
            pressure = "moderate"
            reason = "runtime task-state advisories indicate low-progress tasks"
        return OptimizerDecision(
            strategy=self.name,
            mode=base.mode,
            admission_pressure=pressure,
            target_concurrency=base.target_concurrency,
            task_class_limits=base.task_class_limits,
            route_preferences=base.route_preferences,
            budget_recommendations=budget_recommendations,
            risk_flags=risk_flags,
            reason=reason,
            confidence=confidence,
            queue=queue,
            model_service=model_service,
        )
