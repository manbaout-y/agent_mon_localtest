from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import time
from typing import Any
from uuid import uuid4


JsonDict = dict[str, Any]


def sanitize_for_json(value: Any, path: str = "$") -> tuple[Any, list[str]]:
    """Return a JSON-safe copy and paths where non-finite floats were replaced."""

    if isinstance(value, float):
        if math.isfinite(value):
            return value, []
        return None, [path]
    if isinstance(value, dict):
        sanitized: JsonDict = {}
        paths: list[str] = []
        for key, item in value.items():
            safe_key = str(key)
            safe_item, item_paths = sanitize_for_json(item, f"{path}.{safe_key}")
            sanitized[safe_key] = safe_item
            paths.extend(item_paths)
        return sanitized, paths
    if isinstance(value, (list, tuple)):
        sanitized_items: list[Any] = []
        paths = []
        for index, item in enumerate(value):
            safe_item, item_paths = sanitize_for_json(item, f"{path}[{index}]")
            sanitized_items.append(safe_item)
            paths.extend(item_paths)
        return sanitized_items, paths
    if isinstance(value, Path):
        return str(value), []
    if isinstance(value, (str, int, bool)) or value is None:
        return value, []
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace"), []
    return value, []


def safe_json_dumps(value: Any) -> str:
    sanitized, _ = sanitize_for_json(value)
    return json.dumps(
        sanitized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass
class Event:
    run_id: str
    source: str
    event_type: str
    quality: str
    wall_time_ns: int = field(default_factory=time.time_ns)
    monotonic_time_ns: int = field(default_factory=time.monotonic_ns)
    labels: JsonDict = field(default_factory=dict)
    payload: JsonDict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: str = "1.0"

    def to_dict(self) -> JsonDict:
        data: JsonDict = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "source": self.source,
            "event_type": self.event_type,
            "wall_time_ns": self.wall_time_ns,
            "monotonic_time_ns": self.monotonic_time_ns,
            "quality": self.quality,
            "labels": dict(self.labels),
            "payload": dict(self.payload),
        }
        sanitized, paths = sanitize_for_json(data)
        if paths:
            payload = sanitized.setdefault("payload", {})
            if isinstance(payload, dict):
                payload["_json_sanitization"] = {"non_finite_paths": paths}
        return sanitized

    def to_json(self) -> str:
        return safe_json_dumps(self.to_dict())
