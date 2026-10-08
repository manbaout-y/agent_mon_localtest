#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kvreuse_b5.state_research.io import write_json
from kvreuse_b5.state_research.offline_fixtures import write_fixture_bundle
from kvreuse_b5.state_research.offline_suite import run_suite


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phases 2/3 model-free synthetic acceptance")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = Path(args.output)
    if root.exists() and any(root.iterdir()):
        parser.error("output must be new/empty")
    config = write_fixture_bundle(root / "inputs")
    report = run_suite(config, root / "inputs", root / "analysis")
    read = lambda name: json.loads((root / "analysis" / name).read_text())
    timeline = read("state_timeline.json")
    plans = read("recovery_plans.json")
    checks = {
        "STAGES": report["status"] == "OFFLINE_CHECKS_PASS",
        "ALLOCATION_ALIAS_ACCOUNTING": timeline["peak_physical_bytes"] == 96,
        "LIFECYCLE_RELEASE": timeline["snapshots"][-1]["physical_allocated_bytes"] == 0,
        "THREE_POLICY_BASELINES": len(report["policy_summary"]) == 3,
        "DEPENDENCY_INVALIDATION": read("dependency_impact.json")["potentially_invalidated"] == ["index", "main", "routing"],
        "NO_UNPROVEN_DIRECT_REUSE": plans[0]["candidates"][0]["status"] == "BLOCKED_VALIDITY_EVIDENCE",
        "COSTED_PLAN_NOT_EXECUTION": plans[0]["selected_path"] == "repair" and plans[0]["execution_status"] == "NOT_EXECUTED",
        "ALIGNED_STATE_METRIC": read("state_comparison.json")[0]["energy_explained"] == .75,
        "AGENTMON_EXACT_JOIN": read("agentmon_correlation.json")["matched"] == 10,
        "CAPSULE_BOUND": (root / "analysis/capsule.txt").stat().st_size <= 45000,
    }
    passed = all(checks.values())
    write_json(root / "smoke_result.json", {"status": "PASS" if passed else "FAIL", "checks": checks,
                                           "source": "SYNTHETIC", "real_acceptance": "PENDING"})
    for name, value in checks.items():
        print(f"{name}={'PASS' if value else 'FAIL'}")
    print("MODEL_EXECUTION=NOT_RUN; REAL_ACCEPTANCE=PENDING")
    print(f"OUTPUT={root.resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
