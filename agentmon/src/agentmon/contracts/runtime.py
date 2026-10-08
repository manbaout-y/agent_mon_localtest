from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelTarget:
    name: str
    provider: str = "default"
    weight: float = 1.0
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceBudget:
    max_wall_time_s: float | None = None
    max_model_requests: int | None = None
    max_prompt_tokens: int | None = None
    max_completion_tokens: int | None = None
    max_tool_calls: int | None = None
    max_tool_cpu_s: float | None = None
    max_memory_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    retry_on: tuple[str, ...] = ("runtime_failure",)

    def to_dict(self) -> dict[str, Any]:
        return {"max_retries": self.max_retries, "retry_on": list(self.retry_on)}
