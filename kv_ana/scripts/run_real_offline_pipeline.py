#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kvreuse_b5.pipeline.runner import FatalPipelineError, RealOfflinePipeline, STAGES  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CPU real-state historical regression and offline tournament")
    parser.add_argument("--q8-root", required=True)
    parser.add_argument("--q35-root", required=True)
    parser.add_argument("--deepseek-root", required=True)
    parser.add_argument("--deepseek-mapping", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--config", default="configs/real_offline_pipeline.yaml")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-stage", choices=STAGES)
    parser.add_argument("--max-pairs", type=int)
    parser.add_argument("--capsule-max-bytes", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pipeline = RealOfflinePipeline(
            project_root=args.project_root, q8_root=args.q8_root, q35_root=args.q35_root,
            deepseek_root=args.deepseek_root, deepseek_mapping=args.deepseek_mapping,
            output_root=args.output, config_path=args.config, max_pairs=args.max_pairs,
            capsule_max_bytes=args.capsule_max_bytes,
        )
        state = pipeline.run(resume=args.resume, force_stage=args.force_stage)
    except FatalPipelineError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

