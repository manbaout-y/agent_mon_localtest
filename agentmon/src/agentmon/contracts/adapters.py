from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from .telemetry import TelemetryCapability


AdapterDomain = Literal["agent_framework", "inference", "resource", "tool"]


@dataclass(frozen=True)
class AdapterCapability:
    name: str
    domain: AdapterDomain
    namespace: str
    level: str
    status: str
    capabilities: tuple[str, ...]
    legacy_namespace: str | None = None
    optional_dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        data["optional_dependencies"] = list(self.optional_dependencies)
        return data


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    domain: AdapterDomain
    namespace: str
    capabilities: tuple[TelemetryCapability, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "namespace": self.namespace,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "config_schema": dict(self.config_schema),
        }


class AgentFrameworkAdapter(Protocol):
    name: str

    def descriptor(self) -> AdapterDescriptor:
        ...

    def prepare_command(self, command: str, *, mode: str = "auto") -> tuple[str, str]:
        ...

    def extract_request_ids(self, trajectory_path: str) -> list[str]:
        ...


class InferenceTelemetryProvider(Protocol):
    name: str

    def descriptor(self) -> AdapterDescriptor:
        ...

    def build_index(self, source_path: str, cache_dir: str | None = None) -> dict[str, Any]:
        ...

    def select_events(self, index: dict[str, Any], *, run_id: str, request_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ...


class ResourceTelemetryProvider(Protocol):
    name: str

    def descriptor(self) -> AdapterDescriptor:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...
