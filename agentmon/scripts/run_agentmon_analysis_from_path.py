#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.analysis.config import load_analysis_config
from agentmon.analysis.cli import strict_exit_code
from agentmon.analysis.pipeline import build_status_lines, run_all_analysis


# If you do not want command-line arguments, edit these two values directly.
INPUT_PATH = ""
OUTPUT_PATH = ""
PROFILE = "auto"
JOBS = 4
FAST_SMOKE = True
OMIT_NORMALIZED_TABLES: list[str] = ["event_inventory"]
SKIP_EVIDENCE = False
SKIP_HANDOFF = False
SKIP_MODEL_PACK = False
EXPERIMENT_CONTRACT = ""
RUN_PREFLIGHT = True
STRICT = False
RESUME = True
EXPECTED_RUNS: int | None = None
EXPECTED_REQUEST_ATTEMPTS: int | None = None
EXPECTED_P2_REQUESTS: int | None = None
TIMEOUT_SECONDS: int | None = None


def main() -> int:
    input_path = os.environ.get("AGENTMON_ANALYSIS_INPUT") or (sys.argv[1] if len(sys.argv) > 1 else INPUT_PATH)
    output_path = os.environ.get("AGENTMON_ANALYSIS_OUTPUT") or (
        sys.argv[2] if len(sys.argv) > 2 else OUTPUT_PATH
    )
    if not input_path:
        print("Set INPUT_PATH in this script, pass argv[1], or set AGENTMON_ANALYSIS_INPUT.", file=sys.stderr)
        return 64
    if not output_path:
        output_path = str(Path(input_path).expanduser().resolve().parent / "agentmon_analysis_bundle")

    jobs = int(os.environ.get("AGENTMON_ANALYSIS_JOBS") or JOBS or 1)
    fast_smoke = bool_from_env("AGENTMON_ANALYSIS_FAST_SMOKE", FAST_SMOKE)
    skip_evidence = bool_from_env("AGENTMON_ANALYSIS_SKIP_EVIDENCE", SKIP_EVIDENCE)
    skip_handoff = bool_from_env("AGENTMON_ANALYSIS_SKIP_HANDOFF", SKIP_HANDOFF)
    skip_model_pack = bool_from_env("AGENTMON_ANALYSIS_SKIP_MODEL_PACK", SKIP_MODEL_PACK)
    experiment_contract = os.environ.get("AGENTMON_ANALYSIS_EXPERIMENT_CONTRACT") or EXPERIMENT_CONTRACT or None
    omitted = [
        item.strip()
        for item in os.environ.get("AGENTMON_ANALYSIS_OMIT_TABLES", "").split(",")
        if item.strip()
    ] or list(OMIT_NORMALIZED_TABLES)
    expected_runs = int_from_env("AGENTMON_ANALYSIS_EXPECTED_RUNS", EXPECTED_RUNS)
    expected_requests = int_from_env("AGENTMON_ANALYSIS_EXPECTED_REQUESTS", EXPECTED_REQUEST_ATTEMPTS)
    expected_p2 = int_from_env("AGENTMON_ANALYSIS_EXPECTED_P2", EXPECTED_P2_REQUESTS)
    timeout_seconds = int_from_env("AGENTMON_ANALYSIS_TIMEOUT_SECONDS", TIMEOUT_SECONDS)
    status_file = str(Path(output_path) / "analysis_status.json")
    cfg = load_analysis_config(
        overrides={
            "profile": os.environ.get("AGENTMON_ANALYSIS_PROFILE") or PROFILE,
            "output": {
                "format": "csv",
                "figures": False,
                "fast_smoke": fast_smoke,
                "include_evidence": not skip_evidence,
                "include_handoff": not skip_handoff,
                "include_model_pack": not skip_model_pack,
                "omit_normalized_tables": omitted,
            },
            "runtime": {"jobs": jobs, "status_file": status_file, "timeout_seconds": timeout_seconds},
            "baseline": {
                "expected_runs": expected_runs,
                "expected_request_attempts": expected_requests,
                "expected_p2_requests": expected_p2,
            },
        }
    )
    strict = bool_from_env("AGENTMON_ANALYSIS_STRICT", STRICT)
    result = run_all_analysis(
        input_root=input_path,
        output_root=output_path,
        cfg=cfg,
        output_format="csv",
        no_figures=True,
        preflight=bool_from_env("AGENTMON_ANALYSIS_PREFLIGHT", RUN_PREFLIGHT),
        strict=strict,
        resume=bool_from_env("AGENTMON_ANALYSIS_RESUME", RESUME),
        experiment_contract=experiment_contract,
    )
    for line in build_status_lines(output_path):
        print(line)
    print(f"FAST_SMOKE={fast_smoke}")
    print(f"JOBS={jobs}")
    print(f"EXPERIMENT_CONTRACT={experiment_contract or ''}")
    print(f"OMITTED_NORMALIZED_TABLES={','.join(omitted)}")
    print(f"STATUS_FILE={status_file}")
    print(f"RUN_SUMMARY={Path(output_path) / 'run_summary.json'}")
    return strict_exit_code(result) if strict else 0 if result.get("analysis_status") in {"PASS", "PARTIAL"} else 1


def bool_from_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def int_from_env(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


if __name__ == "__main__":
    raise SystemExit(main())
