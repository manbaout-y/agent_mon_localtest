from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agentmon.config import AgentMonConfig
from agentmon.strategy import StrategyRegistry

from .strategies import StaticThresholdOptimizer, WorkloadAwareOptimizer
from .types import ModelServiceSnapshot, OptimizerDecision, QueueSnapshot


class OptimizerStrategy(Protocol):
    name: str

    def evaluate(
        self,
        queue: QueueSnapshot,
        model_service: ModelServiceSnapshot | None = None,
    ) -> OptimizerDecision:
        ...


OptimizerFactory = Callable[[AgentMonConfig], OptimizerStrategy]

OPTIMIZER_STRATEGIES: StrategyRegistry[OptimizerStrategy] = StrategyRegistry("optimizer")
OPTIMIZER_STRATEGIES.register(
    StaticThresholdOptimizer.name,
    StaticThresholdOptimizer,
    description="Derive admission pressure from scheduler capacity utilization.",
)
OPTIMIZER_STRATEGIES.register(
    WorkloadAwareOptimizer.name,
    WorkloadAwareOptimizer,
    description="Add task-state low-progress and stalled-run advisories to static threshold decisions.",
)


def build_optimizer_strategy(cfg: AgentMonConfig) -> OptimizerStrategy:
    return OPTIMIZER_STRATEGIES.create(cfg.optimizer.strategy, cfg)


def register_optimizer_strategy(
    name: str,
    factory: OptimizerFactory,
    *,
    description: str = "",
    replace: bool = False,
) -> None:
    OPTIMIZER_STRATEGIES.register(name, factory, description=description, replace=replace)


def unregister_optimizer_strategy(name: str) -> None:
    OPTIMIZER_STRATEGIES.unregister(name)


def optimizer_strategy_catalog() -> list[dict[str, str]]:
    return OPTIMIZER_STRATEGIES.catalog()
