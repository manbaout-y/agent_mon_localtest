#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kvreuse_b5.adapters import DeepSeekPrefixC4Adapter, QwenRawKVAdapter  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load one real-state pair on CPU and print adapter diagnostics")
    parser.add_argument("--adapter", required=True, choices=("qwen", "deepseek-c4"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--pair-id", required=True, type=int)
    parser.add_argument("--model-family", choices=("Qwen3-8B", "Qwen3.5-27B"))
    parser.add_argument("--condition", choices=("PREFIX_ONLY", "POSITION_ONLY", "REAL"))
    parser.add_argument("--mapping")
    parser.add_argument("--split", default="test", choices=("train", "test"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.adapter == "qwen":
        if not args.model_family or not args.condition:
            raise SystemExit("qwen requires --model-family and --condition")
        pairs = QwenRawKVAdapter(args.root, args.model_family).load_pair(
            args.pair_id, args.condition, split=args.split
        )
    else:
        if not args.mapping:
            raise SystemExit("deepseek-c4 requires --mapping")
        pairs = DeepSeekPrefixC4Adapter(args.root, args.mapping).load_pair(args.pair_id, split=args.split)

    print("pair\tcondition\tlayer\tstate_kind\tA_shape\tB_shape\tpositions_min\tpositions_max\tbaseline_sse")
    for pair in sorted(pairs, key=lambda item: (item.layer_id, item.state_kind)):
        baseline_sse = float(np.sum(np.square(pair.b.astype(np.float64) - pair.a.astype(np.float64))))
        pos_min = int(pair.positions.min()) if len(pair.positions) else "NA"
        pos_max = int(pair.positions.max()) if len(pair.positions) else "NA"
        print(
            f"{pair.pair_id}\t{pair.condition}\t{pair.layer_id}\t{pair.state_kind}\t"
            f"{tuple(pair.a.shape)}\t{tuple(pair.b.shape)}\t{pos_min}\t{pos_max}\t{baseline_sse:.17g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
