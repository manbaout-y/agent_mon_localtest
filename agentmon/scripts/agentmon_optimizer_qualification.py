#!/usr/bin/env python3
from __future__ import annotations

"""Run a package-verified optimizer analysis against a server experiment directory."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    qualification_path = output / "qualification_manifest.json"
    gate_path = output / "full-source-gate.json"
    test_manifest = output / "TEST_MANIFEST.full.json"
    python = Path(args.agentmon_python).expanduser().resolve()
    gate_command = [
        str(python),
        str(ROOT / "scripts" / "agentmon_release_gate.py"),
        "--level", "full",
        "--package", str(Path(args.package).expanduser().resolve()),
        "--installed-source-root", str(Path(args.installed_source_root).expanduser().resolve()),
        "--agentmon-python", str(python),
        "--output", str(gate_path),
        "--test-manifest", str(test_manifest),
    ]
    if args.vllm_python:
        gate_command.extend(["--vllm-python", str(Path(args.vllm_python).expanduser().resolve())])
    if args.gate_timeout_seconds is not None:
        gate_command.extend(["--timeout-seconds", str(args.gate_timeout_seconds)])
    gate = subprocess.run(gate_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    gate_payload = read_json(gate_path)
    if gate.returncode != 0 or gate_payload.get("status") != "PASS":
        write_json(
            qualification_path,
            qualification_payload(
                status="NOT_QUALIFIED",
                reason="source_gate_failed",
                args=args,
                gate_path=gate_path,
                gate_payload=gate_payload,
                harness_result=None,
            ),
        )
        print(f"qualification: NOT_QUALIFIED ({qualification_path})", file=sys.stderr)
        return 1

    harness_command = [
        str(python), "-m", "agentmon.cli", "optimizer", "experiment",
        "--batch-root", str(Path(args.batch_root).expanduser().resolve()),
        "--output-dir", str(output),
    ]
    for strategy in args.strategy:
        harness_command.extend(["--strategy", strategy])
    for root in args.run_search_root:
        harness_command.extend(["--run-search-root", root])
    if args.config:
        harness_command.extend(["--config", str(Path(args.config).expanduser().resolve())])
    if args.schema_profile:
        harness_command.extend(["--schema-profile", str(Path(args.schema_profile).expanduser().resolve())])
    if args.mechanism_profile:
        harness_command.extend(["--mechanism-profile", str(Path(args.mechanism_profile).expanduser().resolve())])
    if args.memory_budget_bytes is not None:
        harness_command.extend(["--memory-budget-bytes", str(args.memory_budget_bytes)])
    harness = subprocess.run(
        harness_command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=target_import_environment(Path(args.installed_source_root).expanduser().resolve()),
    )
    result = parse_last_json_object(harness.stdout)
    identity_matches = analysis_identity_matches_gate(result, gate_payload)
    status = (
        "QUALIFIED"
        if harness.returncode == 0
        and result.get("status") in {"COMPLETE", "PARTIAL"}
        and Path(result.get("artifact_provenance") or "").is_file()
        and identity_matches
        else "NOT_QUALIFIED"
    )
    write_json(
        qualification_path,
        qualification_payload(
            status=status,
            reason="qualified" if status == "QUALIFIED" else qualification_failure_reason(harness.returncode, result, identity_matches),
            args=args,
            gate_path=gate_path,
            gate_payload=gate_payload,
            harness_result=result,
            harness_stdout=harness.stdout,
            harness_stderr=harness.stderr,
        ),
    )
    print(f"qualification: {status} ({qualification_path})")
    return 0 if status == "QUALIFIED" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify an installed AgentMon package, then run server optimizer analysis.")
    parser.add_argument("--batch-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--package", required=True, help="clean source tarball used for the server installation")
    parser.add_argument("--installed-source-root", required=True, help="source root actually imported by --agentmon-python")
    parser.add_argument("--agentmon-python", required=True, help="target server Python, normally Python 3.11")
    parser.add_argument("--vllm-python", help="optional target vLLM/CANN Python for the release gate")
    parser.add_argument("--config")
    parser.add_argument("--strategy", action="append", default=[])
    parser.add_argument("--run-search-root", action="append", default=[])
    parser.add_argument("--schema-profile")
    parser.add_argument("--mechanism-profile")
    parser.add_argument("--memory-budget-bytes", type=int)
    parser.add_argument("--gate-timeout-seconds", type=float)
    return parser


def qualification_payload(
    *,
    status: str,
    reason: str,
    args: argparse.Namespace,
    gate_path: Path,
    gate_payload: dict[str, Any],
    harness_result: dict[str, Any] | None,
    harness_stdout: str | None = None,
    harness_stderr: str | None = None,
) -> dict[str, Any]:
    provenance = read_json(Path(harness_result["artifact_provenance"])) if harness_result and harness_result.get("artifact_provenance") else {}
    return {
        "schema_version": "1.0",
        "result_type": "agentmon.optimizer_experiment.server_qualification",
        "status": status,
        "reason": reason,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_root": str(Path(args.batch_root).expanduser().resolve()),
        "full_source_gate": {
            "path": str(gate_path),
            "sha256": sha256_file(gate_path) if gate_path.is_file() else None,
            "status": gate_payload.get("status", "NOT_VERIFIED"),
            "source_tree_sha256": gate_payload.get("source_tree_sha256", "UNKNOWN"),
        },
        "analysis": {
            "result": harness_result or {},
            "artifact_provenance": provenance,
        },
        "limitations": [
            "Qualification establishes package identity and successful offline analysis only.",
            "It does not prove online optimizer benefit or exclusive resource ownership.",
        ],
        # Keep process diagnostics out of model-facing analysis artifacts.
        "execution_diagnostics": {
            "harness_stdout_sha256": sha256_text(harness_stdout) if harness_stdout is not None else None,
            "harness_stderr_sha256": sha256_text(harness_stderr) if harness_stderr is not None else None,
        },
    }


def target_import_environment(installed_source_root: Path) -> dict[str, str]:
    """Force the harness subprocess to import only the gate-verified tree first."""
    environment = dict(os.environ)
    source_path = str(installed_source_root / "src")
    inherited = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join([source_path, inherited]).rstrip(os.pathsep)
    return environment


def analysis_identity_matches_gate(result: dict[str, Any], gate_payload: dict[str, Any]) -> bool:
    identity = result.get("source_identity")
    if not isinstance(identity, dict):
        return False
    return (
        identity.get("source_identity_mode") == "packaged_verified"
        and identity.get("source_tree_sha256") == gate_payload.get("source_tree_sha256")
    )


def qualification_failure_reason(harness_returncode: int, result: dict[str, Any], identity_matches: bool) -> str:
    if harness_returncode != 0:
        return "pipeline_command_failed"
    if result.get("status") == "UNRESOLVED":
        return "pipeline_unresolved"
    if not Path(result.get("artifact_provenance") or "").is_file():
        return "artifact_provenance_missing"
    if not identity_matches:
        return "analysis_source_identity_mismatch"
    return "pipeline_not_qualified"


def parse_last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index in reversed([position for position, character in enumerate(text) if character == "{"]):
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not text[index + end:].strip():
            return value
    return {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
