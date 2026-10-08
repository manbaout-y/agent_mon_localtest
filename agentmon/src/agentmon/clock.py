from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any


BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def source_host() -> str:
    return os.uname().nodename if hasattr(os, "uname") else ""


def host_boot_id(path: Path = BOOT_ID_PATH) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def monotonic_clock_domain(*, host: str | None = None, boot_id: str | None = None) -> str:
    host_value = source_host() if host is None else host
    boot_value = host_boot_id() if boot_id is None else boot_id
    parts = [host_value or "unknown-host"]
    if boot_value:
        parts.append(boot_value)
    parts.append("CLOCK_MONOTONIC")
    return ":".join(parts)


@dataclass(frozen=True)
class ClockAnchor:
    """Wall/monotonic timestamp pair captured at one observation point."""

    wall_time_ns: int
    monotonic_time_ns: int
    schema_version: str = "1.0"

    @classmethod
    def capture(cls) -> "ClockAnchor":
        return cls(
            wall_time_ns=time.time_ns(),
            monotonic_time_ns=time.monotonic_ns(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "wall_time_ns": self.wall_time_ns,
            "monotonic_time_ns": self.monotonic_time_ns,
        }
