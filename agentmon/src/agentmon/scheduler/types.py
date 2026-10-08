from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from agentmon.runtime.types import RunSpec


SchedulingAction = Literal["admit", "defer"]


@dataclass(frozen=True)
class SchedulingDecision:
    action: SchedulingAction
    strategy: str
    task_id: str
    reason: str
    running_count: int
    max_running_runs: int
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SchedulerStrategy(Protocol):
    strategy: str

    def decide(self, spec: "RunSpec") -> SchedulingDecision:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...
