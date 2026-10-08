from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Callable

from agentmon.config import AgentMonConfig


@dataclass(frozen=True)
class ToolResourceControlPlan:
    nice_increment: int
    memory_limit_bytes: int | None
    cpu_affinity_cpus: tuple[int, ...]
    applied_in_child: bool
    quality: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cpu_affinity_cpus"] = list(self.cpu_affinity_cpus)
        return data


class LocalToolResourceActuator:
    """Best-effort local process resource controls for tool subprocesses."""

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg

    def plan(self) -> ToolResourceControlPlan:
        enabled = (
            self.cfg.runtime_optimization.enabled
            and self.cfg.control.enabled
            and self.cfg.tool_pool.enabled
        )
        if not enabled:
            return ToolResourceControlPlan(
                nice_increment=0,
                memory_limit_bytes=None,
                cpu_affinity_cpus=(),
                applied_in_child=False,
                quality=(
                    "runtime_optimization_disabled"
                    if not self.cfg.runtime_optimization.enabled
                    else "tool_resource_control_disabled"
                ),
            )
        cpu_affinity = tuple(_parse_cpu_list(self.cfg.tool_pool.cpu_affinity_cpus))
        requested = any(
            (
                self.cfg.tool_pool.nice_increment,
                self.cfg.tool_pool.memory_limit_bytes is not None,
                cpu_affinity,
            )
        )
        return ToolResourceControlPlan(
            nice_increment=self.cfg.tool_pool.nice_increment,
            memory_limit_bytes=self.cfg.tool_pool.memory_limit_bytes,
            cpu_affinity_cpus=cpu_affinity,
            applied_in_child=requested and self.cfg.control.mode == "enforce",
            quality=(
                "advise_only" if self.cfg.control.mode == "advise"
                else "best_effort_child_preexec" if requested else "no_resource_controls_requested"
            ),
        )

    def preexec_fn(self) -> Callable[[], None] | None:
        plan = self.plan()
        if not plan.applied_in_child or os.name != "posix":
            return None

        def apply_controls() -> None:
            if plan.nice_increment:
                try:
                    os.nice(plan.nice_increment)
                except Exception:
                    pass
            if plan.memory_limit_bytes is not None:
                try:
                    import resource

                    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                    target = plan.memory_limit_bytes
                    if hard not in {-1, resource.RLIM_INFINITY}:
                        target = min(target, hard)
                    resource.setrlimit(resource.RLIMIT_AS, (target, hard))
                except Exception:
                    pass
            if plan.cpu_affinity_cpus and hasattr(os, "sched_setaffinity"):
                try:
                    os.sched_setaffinity(0, set(plan.cpu_affinity_cpus))
                except Exception:
                    pass

        return apply_controls


def _parse_cpu_list(values: list[str]) -> list[int]:
    cpus: list[int] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                cpu = int(part)
            except ValueError:
                continue
            if cpu >= 0:
                cpus.append(cpu)
    return sorted(set(cpus))
