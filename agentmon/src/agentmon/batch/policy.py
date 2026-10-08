from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

def analysis_defer_reason(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.analyze_existing:
        return None
    if args.execution_only:
        return {
            "reason": "defer_analysis_requested",
            "mode": "execution_only",
            "requested_by": "--execution-only/--defer-analysis",
        }
    threshold = args.defer_analysis_if_p2_over_bytes
    if threshold is None or not args.p2_events:
        return None
    p2_path = Path(args.p2_events).expanduser()
    try:
        p2_size = p2_path.stat().st_size
    except OSError:
        return None
    if p2_size < threshold:
        return None
    return {
        "reason": "p2_source_size_over_threshold",
        "mode": "auto_defer_large_p2",
        "p2_source": str(p2_path),
        "p2_source_bytes": p2_size,
        "threshold_bytes": threshold,
    }


def p2_index_cache_root(args: argparse.Namespace) -> Path | None:
    return Path(args.p2_index_cache_root).expanduser() if args.p2_index_cache_root else None


def should_write_p2_service_global_copy(args: argparse.Namespace) -> bool:
    if args.skip_p2_service_global_copy:
        return False
    return args.analysis_preset == "full"


def p2_service_global_missing_window_policy(args: argparse.Namespace) -> str:
    if args.analysis_preset in {"standard", "quick"}:
        return "skip"
    return args.p2_service_global_missing_window_policy


def should_build_combined_step_csv(args: argparse.Namespace) -> bool:
    return not args.skip_combined_step_csv and args.analysis_preset != "quick"


def should_build_timeline(args: argparse.Namespace) -> bool:
    return not args.skip_timeline and args.analysis_preset != "quick"
