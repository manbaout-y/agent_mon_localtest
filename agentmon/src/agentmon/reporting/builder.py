from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.reporting.report import build_report, write_reports

__all__ = ["build_report", "write_reports"]


def build_run_report(run_dir: str | Path, *, event_log: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_report(run_dir, event_log=event_log)
