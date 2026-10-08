from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from agentmon.config import ConfigError, load_config
from agentmon.optimizer.harness.schemas import UNRESOLVED
from agentmon.pipeline import FAILED

from .runner import run_optimizer_experiment_harness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build AgentMon optimizer experiment evidence and advice replay outputs",
    )
    parser.add_argument("--batch-root", required=True, help="batch directory containing AgentMon batch outputs")
    parser.add_argument("--output-dir", required=True, help="directory for optimizer experiment outputs")
    parser.add_argument("--strategy", action="append", dest="strategies", default=[])
    parser.add_argument(
        "--baseline-strategy",
        help="strategy to use as the offline advice-proxy baseline; defaults to the first replayed strategy",
    )
    parser.add_argument(
        "--mechanism-profile",
        help="optional JSON object with mechanism evaluation mappings and fusion policy",
    )
    parser.add_argument(
        "--schema-profile",
        help="optional JSON object with step CSV and P2 service event field mappings",
    )
    parser.add_argument("--config", help="path to agentmon TOML config")
    parser.add_argument(
        "--allow-unknown-config",
        action="store_true",
        help="ignore unknown config keys during migrations",
    )
    parser.add_argument("--output-root", help="override general.output_root in the AgentMon config")
    parser.add_argument(
        "--run-search-root",
        action="append",
        default=[],
        help="explicit root to search recursively for run_id/events.jsonl; may be repeated",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="inspect batch inputs and timeline evidence without running replay",
    )
    parser.add_argument("--status-file", help="write optimizer experiment progress JSON")
    parser.add_argument(
        "--progress-interval-seconds",
        type=float,
        help="minimum wall-clock interval between detailed progress updates",
    )
    parser.add_argument("--resume", action="store_true", help="reuse validated completed pipeline stages when identities match")
    parser.add_argument(
        "--memory-budget-bytes",
        type=int,
        default=32 * 1024 * 1024,
        help="bounded in-memory budget for replay sorting and snapshot parts",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {"general.output_root": args.output_root} if args.output_root else {}
    try:
        cfg = load_config(
            config_path=args.config,
            allow_unknown_config=args.allow_unknown_config,
            cli_overrides=overrides,
        )
        result = run_optimizer_experiment_harness(
            batch_root=args.batch_root,
            output_dir=args.output_dir,
            cfg=cfg,
            strategies=args.strategies or [cfg.optimizer.strategy],
            baseline_strategy=args.baseline_strategy,
            mechanism_profile=read_optional_json_object(args.mechanism_profile),
            schema_profile=read_optional_json_object(args.schema_profile, "schema profile"),
            run_search_roots=args.run_search_root,
            preflight_only=args.preflight_only,
            status_file=args.status_file,
            progress_interval_seconds=args.progress_interval_seconds,
            resume=args.resume,
            memory_budget_bytes=args.memory_budget_bytes,
        )
    except ConfigError as exc:
        print(f"agentmon optimizer experiment config error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"agentmon optimizer experiment error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == FAILED:
        return 2
    return 3 if result.get("status") == UNRESOLVED else 0


def read_optional_json_object(path: str | None, label: str = "mechanism profile") -> dict[str, object] | None:
    if not path:
        return None
    value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value
