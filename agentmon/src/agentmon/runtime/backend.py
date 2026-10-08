from __future__ import annotations

from typing import Protocol

from .types import (
    BackendCapabilities,
    BackendStatus,
    PreparedRun,
    RunHandle,
    RunSpec,
    RuntimeContext,
)


class AgentRuntimeBackend(Protocol):
    name: str

    def capabilities(self) -> BackendCapabilities:
        ...

    def prepare(self, spec: RunSpec, context: RuntimeContext) -> PreparedRun:
        ...

    def start(self, prepared: PreparedRun) -> RunHandle:
        ...

    def poll(self, handle: RunHandle) -> BackendStatus:
        ...

    def cancel(self, handle: RunHandle, reason: str) -> None:
        ...
