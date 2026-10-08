#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kvreuse_b5.state_research import SCHEMA_VERSION
from kvreuse_b5.state_research.fixtures import synthetic_events, synthetic_records, synthetic_states
from kvreuse_b5.state_research.io import write_json
from kvreuse_b5.state_research.prepare import prepare_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description="Model-free batch1 research input integration smoke")
    parser.add_argument("--output", required=True, help="new or empty directory")
    args = parser.parse_args(argv)
    root = Path(args.output)
    if root.exists() and any(root.iterdir()):
        parser.error("output must be empty")
    root.mkdir(parents=True, exist_ok=True)
    records = synthetic_records()
    write_json(root / "synthetic_input.json", records)
    quality = prepare_dataset(records, root / "prepared", dataset_id="synthetic",
                              events=synthetic_events(), states=synthetic_states(), order="observed")
    legacy = [{k: v for k, v in record.items() if k != "research"} for record in records]
    write_json(root / "legacy_schema_input.json", legacy)
    partial = prepare_dataset(legacy, root / "legacy", dataset_id="synthetic-legacy")
    transitions = [json.loads(line) for line in (root / "prepared/transitions.jsonl").read_text().splitlines()]
    checks = {
        "LIFEMEM_SCHEMA": quality["request_count"] == 5 and quality["chunk_count"] == 15,
        "ADJACENT_NOT_ALL_PAIRS": quality["adjacent_memory_transition_count"] == 12,
        "TOKEN_PROVENANCE": quality["token_aligned_pairs_for_state_review"] == 0 and all(
            t["token_evidence"] == "synthetic" for t in transitions),
        "NO_TOKEN_FALLBACK": partial["tokenization_sources"] == {"missing": 5},
        "AGENTMON_ID_JOIN": quality["correlation"]["matched"] == 10,
        "SHARED_ALLOCATION_ACCOUNTING": quality["state_inventory"]["physical_allocated_bytes"] == 400,
        "MODEL_EXECUTION": False,
    }
    passed = all(value for key, value in checks.items() if key != "MODEL_EXECUTION")
    write_json(root / "smoke_result.json", {"schema_version": SCHEMA_VERSION,
               "status": "PASS" if passed else "FAIL", "data_source": "SYNTHETIC", "checks": checks})
    print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
    for key, value in checks.items():
        print(f"{key}={'NOT_RUN' if key == 'MODEL_EXECUTION' else 'PASS' if value else 'FAIL'}")
    print(f"OUTPUT={root.resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
