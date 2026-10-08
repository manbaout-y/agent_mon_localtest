from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from agentmon.experiments.orchestration import (
    PASS,
    orchestrate_experiment,
    write_json,
    load_contract,
)
from agentmon.experiments.request import (
    CONTRACT_FILENAME,
    generate_experiment_contract,
    load_experiment_request,
    write_generated_contract,
)


SMOKE_STAGES = ("preflight", "service_check", "ab_single_task")
PILOT_STAGES = ("pilot",)
FORMAL_STAGES = ("formal_matrix",)
ANALYSIS_STAGES = ("analysis",)


def run_prepare(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = generate_experiment_contract(load_experiment_request(args.request))
    contract_path = output_dir / (args.contract_name or CONTRACT_FILENAME)
    write_generated_contract(contract, contract_path)
    report = {
        "status": "PLAN_READY",
        "phase": "prepare",
        "contract": str(contract_path),
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "experiment_prepare_report.json", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print("PLAN_READY")
    return 0


def run_smoke(args: argparse.Namespace) -> int:
    report = orchestrate_experiment(
        load_contract(args.contract),
        output_dir=args.output_dir,
        execute=args.execute,
        stages=SMOKE_STAGES,
        evaluate_after_commands=args.execute,
    )
    ready = ready_for_pilot(report)
    phase_report = phase_result(
        report,
        phase="smoke",
        marker="READY_FOR_PILOT",
        marker_value="YES" if ready else "NO",
    )
    write_phase_outputs(args.output_dir, "ready_for_pilot.env", phase_report)
    print(json.dumps(phase_report, ensure_ascii=False, sort_keys=True))
    print(f"READY_FOR_PILOT={phase_report['READY_FOR_PILOT']}")
    return 0 if ready else 1


def run_pilot(args: argparse.Namespace) -> int:
    report = orchestrate_experiment(
        load_contract(args.contract),
        output_dir=args.output_dir,
        execute=args.execute,
        stages=PILOT_STAGES,
        evaluate_after_commands=args.execute,
    )
    phase_report = phase_result(
        report,
        phase="pilot",
        marker="READY_FOR_FORMAL",
        marker_value=str(report.get("ready_for_formal") or "NO"),
    )
    write_phase_outputs(args.output_dir, "ready_for_formal_phase.env", phase_report)
    print(json.dumps(phase_report, ensure_ascii=False, sort_keys=True))
    print(f"READY_FOR_FORMAL={phase_report['READY_FOR_FORMAL']}")
    return 0 if phase_report["READY_FOR_FORMAL"] == "YES" else 1


def run_formal(args: argparse.Namespace) -> int:
    report = orchestrate_experiment(
        load_contract(args.contract),
        output_dir=args.output_dir,
        execute=args.execute,
        run_formal=args.run_formal,
        stages=FORMAL_STAGES,
    )
    phase_report = phase_result(
        report,
        phase="formal",
        marker="READY_FOR_FORMAL",
        marker_value=str(report.get("ready_for_formal") or "NO"),
    )
    write_phase_outputs(args.output_dir, "formal_phase.env", phase_report)
    print(json.dumps(phase_report, ensure_ascii=False, sort_keys=True))
    print(f"READY_FOR_FORMAL={phase_report['READY_FOR_FORMAL']}")
    return 0 if report.get("status") == PASS else 1


def run_analyze(args: argparse.Namespace) -> int:
    report = orchestrate_experiment(
        load_contract(args.contract),
        output_dir=args.output_dir,
        execute=args.execute,
        stages=ANALYSIS_STAGES,
    )
    route = report.get("analysis_route") or {}
    phase_report = phase_result(
        report,
        phase="analyze",
        marker="ANALYSIS_ROUTE",
        marker_value=str(route.get("route") or "UNKNOWN"),
    )
    write_phase_outputs(args.output_dir, "analysis_phase.env", phase_report)
    print(json.dumps(phase_report, ensure_ascii=False, sort_keys=True))
    print(f"ANALYSIS_ROUTE={phase_report['ANALYSIS_ROUTE']}")
    return 0 if report.get("status") == PASS else 1


def ready_for_pilot(report: dict[str, Any]) -> bool:
    return (
        as_status(report, "contract_validation") == PASS
        and as_status(report, "execution_path_validation") == PASS
        and as_status(report, "activation_probe_report") == PASS
        and not command_failures(report)
    )


def as_status(report: dict[str, Any], key: str) -> str:
    value = report.get(key)
    return str(value.get("status") if isinstance(value, dict) else "")


def command_failures(report: dict[str, Any]) -> list[str]:
    return [
        str(row.get("stage"))
        for row in report.get("command_results", [])
        if isinstance(row, dict) and row.get("status") == "FAIL"
    ]


def phase_result(
    report: dict[str, Any],
    *,
    phase: str,
    marker: str,
    marker_value: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "result_type": "agentmon.experiment.phase_result",
        "phase": phase,
        "status": report.get("status"),
        marker: marker_value,
        "orchestration_report": str(Path(str(report.get("output_dir") or ".")) / "experiment_orchestration_report.json"),
        "blocking_reasons": report.get("blocking_reasons", []),
        "command_results": report.get("command_results", []),
    }


def write_phase_outputs(output_dir: str | Path, env_name: str, report: dict[str, Any]) -> None:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / f"{report['phase']}_phase_report.json", report)
    env_lines = [
        f"{key}={value}"
        for key, value in report.items()
        if key.isupper() and isinstance(value, str)
    ]
    env_lines.append("")
    (output / env_name).write_text("\n".join(env_lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified AgentMon experiment prepare/smoke/pilot/formal/analyze entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="generate experiment-contract.json from a small request")
    prepare.add_argument("--request", required=True, help="experiment request JSON/TOML/YAML")
    prepare.add_argument("--output-dir", required=True, help="directory for generated plan artifacts")
    prepare.add_argument("--contract-name", help="contract filename; defaults to experiment-contract.json")
    prepare.set_defaults(func=run_prepare)

    smoke = subparsers.add_parser("smoke", help="run or plan preflight, service check, A/B single-task smoke, and activation gate")
    add_contract_stage_args(smoke)
    smoke.set_defaults(func=run_smoke)

    pilot = subparsers.add_parser("pilot", help="run or plan pilot and check READY_FOR_FORMAL")
    add_contract_stage_args(pilot)
    pilot.set_defaults(func=run_pilot)

    formal = subparsers.add_parser("formal", help="run or plan formal matrix behind READY_FOR_FORMAL")
    add_contract_stage_args(formal)
    formal.add_argument("--run-formal", action="store_true", help="allow formal matrix execution when READY_FOR_FORMAL=YES")
    formal.set_defaults(func=run_formal)

    analyze = subparsers.add_parser("analyze", help="route and run or plan analysis for a completed experiment")
    add_contract_stage_args(analyze)
    analyze.set_defaults(func=run_analyze)
    return parser


def add_contract_stage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", required=True, help="experiment contract JSON")
    parser.add_argument("--output-dir", required=True, help="directory for phase reports")
    parser.add_argument("--execute", action="store_true", help="execute configured commands; default only plans and gates")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - command-line experiment entry should be auditable.
        print(f"agentmon experiment error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
