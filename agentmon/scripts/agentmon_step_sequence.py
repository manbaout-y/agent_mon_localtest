#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.step_sequence import core as _core
from agentmon.step_sequence.core import *  # noqa: F401,F403 - compatibility script facade


_WRAPPER_NAMES = {"main", "build_step_sequence", "write_output_bundle", "format_output"}


def _apply_core_overrides() -> dict[str, Any]:
    """Temporarily propagate script-level monkeypatches to the package implementation."""
    backups: dict[str, Any] = {}
    for name, value in globals().items():
        if name.startswith("_") or name in _WRAPPER_NAMES:
            continue
        if hasattr(_core, name):
            backups[name] = getattr(_core, name)
            setattr(_core, name, value)
    return backups


def _restore_core_overrides(backups: dict[str, Any]) -> None:
    for name, value in backups.items():
        setattr(_core, name, value)


def build_step_sequence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    backups = _apply_core_overrides()
    try:
        return _core.build_step_sequence(*args, **kwargs)
    finally:
        _restore_core_overrides(backups)


def write_output_bundle(*args: Any, **kwargs: Any) -> None:
    backups = _apply_core_overrides()
    try:
        return _core.write_output_bundle(*args, **kwargs)
    finally:
        _restore_core_overrides(backups)


def format_output(*args: Any, **kwargs: Any) -> str:
    backups = _apply_core_overrides()
    try:
        return _core.format_output(*args, **kwargs)
    finally:
        _restore_core_overrides(backups)


def main(argv: Sequence[str] | None = None) -> int:
    backups = _apply_core_overrides()
    try:
        return _core.main(argv)
    finally:
        _restore_core_overrides(backups)


if __name__ == "__main__":
    raise SystemExit(main())
