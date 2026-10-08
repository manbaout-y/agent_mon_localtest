from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


TelemetryQuality = Literal["exact", "derived", "sampled", "heuristic", "unavailable"]


@dataclass(frozen=True)
class TelemetryCapability:
    name: str
    quality: TelemetryQuality
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_fields"] = list(self.required_fields)
        data["optional_fields"] = list(self.optional_fields)
        return data


@dataclass(frozen=True)
class RequestIdentity:
    request_attempt_id: str | None = None
    trajectory_response_id: str | None = None
    api_request_id: str | None = None
    engine_request_id: str | None = None
    model_call_id: str | None = None
    client_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def identity_aliases(identity: RequestIdentity | dict[str, Any]) -> dict[str, str]:
    data = identity.to_dict() if isinstance(identity, RequestIdentity) else dict(identity)
    aliases: dict[str, str] = {}
    for key in (
        "request_attempt_id",
        "trajectory_response_id",
        "api_request_id",
        "engine_request_id",
        "model_call_id",
        "client_request_id",
    ):
        value = data.get(key)
        if value not in {None, ""}:
            aliases[key] = str(value)
    return aliases
