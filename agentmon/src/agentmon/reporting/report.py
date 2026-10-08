from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.reporting import _legacy_report as _legacy
from agentmon.reporting import report_builder as _builder
from agentmon.reporting._legacy_report import *  # noqa: F401,F403 - public compatibility facade
from agentmon.reporting.report_builder import build_report as _build_report
from agentmon.reporting.report_builder import write_reports as _write_reports


_WRAPPER_NAMES = {
    "build_report",
    "write_reports",
    "export_run",
    "compare_reports",
}


def _apply_legacy_overrides() -> dict[str, Any]:
    """Temporarily propagate facade-level monkeypatches to the legacy implementation."""
    backups: dict[str, Any] = {}
    for name, value in globals().items():
        if name.startswith("_") or name in _WRAPPER_NAMES:
            continue
        if hasattr(_legacy, name):
            backups[name] = getattr(_legacy, name)
            setattr(_legacy, name, value)
    return backups


def _restore_legacy_overrides(backups: dict[str, Any]) -> None:
    for name, value in backups.items():
        setattr(_legacy, name, value)


def build_report(run_dir: str | Path, *, event_log: dict[str, Any] | None = None) -> dict[str, Any]:
    backups = _apply_legacy_overrides()
    try:
        _builder.read_event_log = _legacy.read_event_log
        return _build_report(run_dir, event_log=event_log)
    finally:
        _restore_legacy_overrides(backups)


def write_reports(
    run_dir: str | Path,
    *,
    event_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backups = _apply_legacy_overrides()
    try:
        _builder.read_event_log = _legacy.read_event_log
        return _write_reports(run_dir, event_log=event_log)
    finally:
        _restore_legacy_overrides(backups)


def export_run(run_dir: str | Path, *, fmt: str) -> str:
    backups = _apply_legacy_overrides()
    try:
        return _legacy.export_run(run_dir, fmt=fmt)
    finally:
        _restore_legacy_overrides(backups)


def compare_reports(run_a: str | Path, run_b: str | Path) -> dict[str, Any]:
    backups = _apply_legacy_overrides()
    try:
        return _legacy.compare_reports(run_a, run_b)
    finally:
        _restore_legacy_overrides(backups)
