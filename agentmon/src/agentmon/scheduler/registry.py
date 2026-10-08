from __future__ import annotations

from collections.abc import Callable

from agentmon.config import AgentMonConfig
from agentmon.strategy import StrategyRegistry

from .service import FixedConcurrencyScheduler, WorkloadAwareScheduler
from .types import SchedulerStrategy


SchedulerFactory = Callable[[AgentMonConfig], SchedulerStrategy]

SCHEDULER_STRATEGIES: StrategyRegistry[SchedulerStrategy] = StrategyRegistry("scheduler")
SCHEDULER_STRATEGIES.register(
    FixedConcurrencyScheduler.strategy,
    FixedConcurrencyScheduler,
    description="Admit runs while active runtime manifests are below max_running_runs.",
)
SCHEDULER_STRATEGIES.register(
    WorkloadAwareScheduler.strategy,
    WorkloadAwareScheduler,
    description="Admit runs using fixed concurrency plus configured task-state workload budgets.",
)


def build_scheduler(cfg: AgentMonConfig) -> SchedulerStrategy:
    return SCHEDULER_STRATEGIES.create(cfg.scheduler.strategy, cfg)


def register_scheduler_strategy(
    name: str,
    factory: SchedulerFactory,
    *,
    description: str = "",
    replace: bool = False,
) -> None:
    SCHEDULER_STRATEGIES.register(name, factory, description=description, replace=replace)


def unregister_scheduler_strategy(name: str) -> None:
    SCHEDULER_STRATEGIES.unregister(name)


def scheduler_strategy_catalog() -> list[dict[str, str]]:
    return SCHEDULER_STRATEGIES.catalog()
