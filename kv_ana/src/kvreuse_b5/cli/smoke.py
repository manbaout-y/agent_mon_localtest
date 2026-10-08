from __future__ import annotations

import argparse
import json
from pathlib import Path

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import smoke_dataset
from kvreuse_b5.execution.config import load_config
from kvreuse_b5.execution.runner import ExperimentRunner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the CPU-only synthetic smoke experiment")
    parser.add_argument("--config", default="configs/synthetic_smoke.yaml")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    records, summary = ExperimentRunner(config).run(StatePairDataset(smoke_dataset()), args.output)
    ok = sum(
        row["status"] in {"OK", "ZERO_RESIDUAL", "ZERO_BASELINE_DEGRADED", "CAPABILITY_MISSING"}
        for row in records
    )
    print(json.dumps({
        "status": "PASS",
        "version": "2.0.1",
        "records": len(records),
        "accepted": ok,
        "summary_groups": len(summary["global_summary"]),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
