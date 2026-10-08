#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.batch import runtime as _runtime
from agentmon.batch import analysis_runner as _analysis_runner
from agentmon.batch import commands as _commands
from agentmon.batch import workload as _workload
from agentmon.batch.runtime import *  # noqa: F401,F403 - compatibility script facade


_WRAPPER_NAMES = {"main", "run_batch", "run_one_task", "import_p2"}
_SYNC_TARGETS = (_runtime, _analysis_runner, _commands, _workload)


def _apply_runtime_overrides() -> dict[tuple[Any, str], Any]:
    """Temporarily propagate script-level monkeypatches to the package implementation."""
    backups: dict[tuple[Any, str], Any] = {}
    for name, value in globals().items():
        if name.startswith("_") or name in _WRAPPER_NAMES:
            continue
        for target in _SYNC_TARGETS:
            if hasattr(target, name):
                backups[(target, name)] = getattr(target, name)
                setattr(target, name, value)
    return backups


def _restore_runtime_overrides(backups: dict[tuple[Any, str], Any]) -> None:
    for (target, name), value in backups.items():
        setattr(target, name, value)


def run_batch(args: Any) -> list[dict[str, Any]]:
    backups = _apply_runtime_overrides()
    try:
        return _runtime.run_batch(args)
    finally:
        _restore_runtime_overrides(backups)


def run_one_task(
    args: Any,
    task: dict[str, str],
    batch_root: Path,
    env_base: dict[str, str],
) -> dict[str, Any]:
    backups = _apply_runtime_overrides()
    try:
        return _runtime.run_one_task(args, task, batch_root, env_base)
    finally:
        _restore_runtime_overrides(backups)


def import_p2(*args: Any, **kwargs: Any) -> dict[str, Any]:
    backups = _apply_runtime_overrides()
    try:
        return _runtime.import_p2(*args, **kwargs)
    finally:
        _restore_runtime_overrides(backups)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = run_batch(args)
    except Exception as exc:
        print(f"agentmon batch runtime error: {exc}", file=sys.stderr)
        return 2

    failures = [result for result in results if _runtime.result_is_failure(result)]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
