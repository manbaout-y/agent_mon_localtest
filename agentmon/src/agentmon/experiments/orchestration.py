from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable, Sequence

from agentmon.experiments.mechanisms import mechanism_definition, mechanism_registry


SCHEMA_VERSION = "1.0"
PASS = "PASS"
FAIL = "FAIL"
PARTIAL = "PARTIAL"
SKIPPED = "SKIPPED"
PLANNED = "PLANNED"
UNKNOWN = "UNKNOWN"

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_EXECUTION_PATHS = {
    "scheduler": ("runtime_service",),
    "workload_aware_scheduler": ("runtime_service",),
    "optimizer": ("runtime_service", "optimizer_advice"),
    "model_gate": ("runtime_controlled_model", "model_gate_proxy", "runtime_service"),
    "tool_pool": ("tool_pool_executor", "runtime_service"),
    "context_compression": ("agent_adapter_context_compression", "runtime_controlled_model"),
}

DEFAULT_ACTIVATION_PROFILES = {
    "scheduler": {
        "event_types": ("runtime.queue.running", "runtime.queue.deferred"),
        "negative_reason_paths": ("payload.scheduler_decision.reason",),
        "negative_reasons": ("runtime_optimization_disabled", "scheduler_disabled"),
    },
    "workload_aware_scheduler": {
        "event_types": ("runtime.queue.running", "runtime.queue.deferred"),
        "negative_reason_paths": ("payload.scheduler_decision.reason",),
        "negative_reasons": ("runtime_optimization_disabled", "scheduler_disabled"),
    },
    "optimizer": {
        "event_types": ("optimizer.decision",),
        "negative_reason_paths": ("payload.reason",),
        "negative_reasons": ("optimizer_disabled", "runtime_optimization_disabled"),
    },
    "model_gate": {
        "event_types": (
            "control.model_gate.acquire",
            "control.model_provider.completed",
            "control.model_provider.rejected",
            "control.model_provider.error",
        ),
        "negative_reason_paths": ("payload.reason", "payload.lease.reason"),
        "negative_reasons": (
            "gate_disabled",
            "control or model gate disabled",
            "runtime optimization disabled",
        ),
    },
    "tool_pool": {
        "event_types": (
            "control.tool_pool.acquire",
            "control.tool_execution.completed",
            "control.tool_execution.rejected",
            "control.tool_execution.error",
        ),
        "negative_reason_paths": ("payload.reason", "payload.lease.reason"),
        "negative_reasons": ("pool_disabled", "tool_pool_disabled", "runtime optimization disabled"),
    },
    "context_compression": {
        "event_types": ("context_compression.decision",),
        "negative_reason_paths": ("payload.context_compression_status",),
        "negative_reasons": ("DISABLED", "ERROR_BYPASS"),
    },
}

COMMAND_STAGES = (
    "preflight",
    "service_check",
    "ab_single_task",
    "pilot",
    "analysis",
    "formal_matrix",
)


def orchestrate_experiment(
    contract: dict[str, Any],
    *,
    output_dir: str | Path,
    execute: bool = False,
    run_formal: bool = False,
    stages: Sequence[str] | None = None,
    evaluate_after_commands: bool = False,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    contract_validation = validate_experiment_contract(contract)
    execution_path_validation = validate_execution_paths(contract)
    activation_report = evaluate_activation_probes(contract)
    pilot_gate = evaluate_pilot_gate(contract)
    analysis_route = select_analysis_route(contract)

    ready, blockers = determine_ready_for_formal(
        contract,
        contract_validation,
        execution_path_validation,
        activation_report,
        pilot_gate,
    )
    command_results = run_configured_commands(
        contract,
        output,
        analysis_route,
        execute=execute,
        run_formal=run_formal,
        ready_for_formal=ready,
        stages=stages,
    )
    if evaluate_after_commands or execute:
        activation_report = evaluate_activation_probes(contract)
        pilot_gate = evaluate_pilot_gate(contract)
        ready, blockers = determine_ready_for_formal(
            contract,
            contract_validation,
            execution_path_validation,
            activation_report,
            pilot_gate,
        )
    command_blockers = [
        f"command_failed:{row.get('stage')}"
        for row in command_results
        if row.get("status") == FAIL
    ]
    blockers = sorted(set(str(item) for item in blockers + command_blockers))
    ready = ready and not command_blockers
    status = FAIL if blockers else PASS
    report = {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.report",
        "experiment_id": str(contract.get("experiment_id") or UNKNOWN),
        "output_dir": str(output),
        "status": status,
        "ready_for_formal": "YES" if ready else "NO",
        "blocking_reasons": blockers,
        "contract_validation": contract_validation,
        "execution_path_validation": execution_path_validation,
        "activation_probe_report": activation_report,
        "pilot_gate": pilot_gate,
        "analysis_route": analysis_route,
        "command_results": command_results,
        "limitations": [
            "This orchestrator validates experiment intent and observed activation evidence.",
            "It does not prove runtime performance benefit.",
            "A configured mechanism is not considered active unless matching evidence is observed.",
            "Formal matrix execution is blocked unless READY_FOR_FORMAL=YES.",
        ],
    }
    write_json(output / "experiment_orchestration_report.json", report)
    write_json(output / "activation_probe_report.json", activation_report)
    write_json(output / "analysis_route.json", analysis_route)
    write_ready_env(output / "ready_for_formal.env", report)
    return report


def validate_experiment_contract(contract: dict[str, Any]) -> dict[str, Any]:
    variants = normalize_variants(contract.get("variants"))
    allowed_diffs = [str(item) for item in as_list(contract.get("allowed_variant_differences"))]
    baseline = select_baseline_variant(contract, variants)
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    if not variants:
        blockers.append("variants_missing")
    if baseline is None:
        blockers.append("baseline_variant_missing")
    if baseline is not None:
        baseline_config = flatten_mapping(as_dict(baseline.get("config")))
        for variant in variants:
            if variant is baseline:
                continue
            variant_config = flatten_mapping(as_dict(variant.get("config")))
            diff_paths = sorted(
                path
                for path in set(baseline_config) | set(variant_config)
                if baseline_config.get(path) != variant_config.get(path)
            )
            disallowed = [path for path in diff_paths if not path_allowed(path, allowed_diffs)]
            rows.append(
                {
                    "variant_id": variant.get("id") or UNKNOWN,
                    "baseline_variant_id": baseline.get("id") or UNKNOWN,
                    "diff_paths": diff_paths,
                    "allowed_diff_paths": [path for path in diff_paths if path not in disallowed],
                    "disallowed_diff_paths": disallowed,
                    "status": FAIL if disallowed else PASS,
                }
            )
            blockers.extend(f"disallowed_variant_diff:{variant.get('id')}:{path}" for path in disallowed)
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.contract_validation",
        "status": FAIL if blockers else PASS,
        "variant_count": len(variants),
        "baseline_variant_id": baseline.get("id") if baseline else None,
        "allowed_variant_differences": allowed_diffs,
        "variant_diff_checks": rows,
        "blocking_reasons": blockers,
    }


def validate_execution_paths(contract: dict[str, Any]) -> dict[str, Any]:
    variants = normalize_variants(contract.get("variants"))
    requirements = merge_execution_path_requirements(contract)
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for variant in variants:
        mechanisms = [str(item) for item in as_list(variant.get("mechanisms"))]
        execution_path = str(variant.get("execution_path") or UNKNOWN)
        for mechanism in mechanisms:
            allowed_paths = tuple(requirements.get(mechanism) or ())
            if not allowed_paths:
                status = PARTIAL
                reason = "mechanism_has_no_execution_path_rule"
            elif execution_path in allowed_paths:
                status = PASS
                reason = "execution_path_matches_mechanism"
            else:
                status = FAIL
                reason = "execution_path_does_not_match_mechanism"
                blockers.append(f"wrong_execution_path:{variant.get('id')}:{mechanism}:{execution_path}")
            rows.append(
                {
                    "variant_id": variant.get("id") or UNKNOWN,
                    "mechanism": mechanism,
                    "execution_path": execution_path,
                    "allowed_execution_paths": list(allowed_paths),
                    "status": status,
                    "reason": reason,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.execution_path_validation",
        "status": FAIL if blockers else PASS if rows else PARTIAL,
        "checks": rows,
        "blocking_reasons": blockers,
    }


def evaluate_activation_probes(contract: dict[str, Any]) -> dict[str, Any]:
    probes = activation_probe_configs(contract)
    rows = [evaluate_activation_probe(probe) for probe in probes]
    blockers = [
        f"mechanism_not_active:{row.get('variant_id')}:{row.get('mechanism')}"
        for row in rows
        if row.get("status") == FAIL
    ]
    status = FAIL if blockers else PASS if rows else PARTIAL
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.activation_probe_report",
        "status": status,
        "probe_count": len(rows),
        "probes": rows,
        "blocking_reasons": blockers,
        "forbidden_conclusions": [
            "Do not treat enabled=true as activation evidence.",
            "Do not start formal matrix when a required activation probe fails.",
        ],
    }


def evaluate_activation_probe(probe: dict[str, Any]) -> dict[str, Any]:
    mechanism = str(probe.get("mechanism") or UNKNOWN)
    profile = activation_profile(mechanism, as_dict(probe.get("profile")))
    evidence_files = expand_evidence_paths(as_list(probe.get("evidence_paths")))
    min_count = int_or_default(probe.get("min_count"), int_or_default(profile.get("min_count"), 1))
    min_ratio = float_or_none(probe.get("min_ratio"))
    if min_ratio is None:
        min_ratio = float_or_none(profile.get("min_ratio"))
    coverage_denominator = int_or_default(
        probe.get("coverage_denominator") or probe.get("expected_count"),
        0,
    )
    required_payload = as_dict(probe.get("required_payload"))
    matched_count = 0
    scanned_event_count = 0
    files_scanned: list[str] = []
    negative_reasons: dict[str, int] = {}
    for path in evidence_files:
        files_scanned.append(str(path))
        for event in iter_jsonl_events(path):
            scanned_event_count += 1
            if not event_matches_probe(event, profile, required_payload):
                reason = negative_activation_reason(event, profile)
                if reason:
                    negative_reasons[reason] = negative_reasons.get(reason, 0) + 1
                continue
            matched_count += 1
    count_status = PASS if matched_count >= min_count else FAIL
    observed_ratio: float | None = None
    if min_ratio is None:
        coverage_status = SKIPPED
    elif coverage_denominator > 0:
        observed_ratio = matched_count / coverage_denominator
        coverage_status = PASS if observed_ratio >= min_ratio else FAIL
    else:
        coverage_status = UNKNOWN
    status = FAIL if count_status == FAIL or coverage_status in {FAIL, UNKNOWN} else PASS
    return {
        "variant_id": probe.get("variant") or probe.get("variant_id") or UNKNOWN,
        "mechanism": mechanism,
        "status": status,
        "min_count": min_count,
        "min_ratio": min_ratio,
        "coverage_denominator": coverage_denominator or None,
        "observed_coverage_ratio": observed_ratio,
        "coverage_status": coverage_status,
        "matched_count": matched_count,
        "scanned_event_count": scanned_event_count,
        "evidence_files": files_scanned,
        "event_types": list(profile["event_types"]),
        "negative_reason_counts": dict(sorted(negative_reasons.items())),
        "required_payload": required_payload,
        "data_quality": PASS if files_scanned else UNKNOWN,
        "reason": "activation_evidence_observed" if status == PASS else "activation_evidence_missing",
        "forbidden_conclusions": list(profile.get("forbidden_conclusions") or ()),
    }


def evaluate_pilot_gate(contract: dict[str, Any]) -> dict[str, Any]:
    pilot = as_dict(contract.get("pilot"))
    if not pilot:
        return {
            "schema_version": SCHEMA_VERSION,
            "result_type": "agentmon.experiment_orchestration.pilot_gate",
            "status": SKIPPED,
            "reason": "pilot_not_configured",
        }
    batch_root = Path(str(pilot.get("batch_root") or "")).expanduser()
    min_task_count = int_or_default(pilot.get("min_task_count"), 0)
    min_success_count = int_or_default(pilot.get("min_success_count"), 0)
    rows = read_batch_results(batch_root)
    success_count = sum(1 for row in rows if successful_result_row(row))
    blockers: list[str] = []
    if not batch_root.is_dir():
        blockers.append("pilot_batch_root_missing")
    if min_task_count and len(rows) < min_task_count:
        blockers.append("pilot_task_count_below_minimum")
    if min_success_count and success_count < min_success_count:
        blockers.append("pilot_success_count_below_minimum")
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.pilot_gate",
        "status": FAIL if blockers else PASS,
        "batch_root": str(batch_root),
        "observed_task_count": len(rows),
        "successful_task_count": success_count,
        "min_task_count": min_task_count,
        "min_success_count": min_success_count,
        "blocking_reasons": blockers,
    }


def select_analysis_route(contract: dict[str, Any]) -> dict[str, Any]:
    analysis = as_dict(contract.get("analysis"))
    batch_root_raw = analysis.get("batch_root") or contract.get("batch_root")
    input_raw = analysis.get("input_path") or batch_root_raw
    python = str(analysis.get("agentmon_python") or sys.executable)
    if batch_root_raw:
        batch_root = Path(str(batch_root_raw)).expanduser()
        if is_known_batch_root(batch_root):
            command = [
                python,
                str(REPO_ROOT / "scripts" / "agentmon_batch_runtime.py"),
                "--batch-root",
                str(batch_root),
                "--analyze-existing",
                "--analysis-preset",
                str(analysis.get("analysis_preset") or "standard"),
            ]
            add_optional_arg(command, "--analysis-workers", analysis.get("analysis_workers"))
            add_optional_arg(command, "--task-file", analysis.get("task_file"))
            add_optional_arg(command, "--task-id-column", analysis.get("task_id_column"))
            add_optional_arg(command, "--agentmon-python", analysis.get("agentmon_python"))
            add_optional_arg(command, "--output-root", analysis.get("output_root"))
            add_optional_arg(command, "--env-file", analysis.get("env_file"))
            add_optional_arg(command, "--p2-events", analysis.get("p2_events"))
            add_optional_arg(command, "--p2-index-cache-root", analysis.get("p2_index_cache_root"))
            add_optional_arg(command, "--p1-mode", analysis.get("p1_mode"))
            return {
                "schema_version": SCHEMA_VERSION,
                "result_type": "agentmon.experiment_orchestration.analysis_route",
                "status": PASS,
                "route": "batch_native_analyze_existing",
                "reason": "batch_manifest_or_results_present",
                "batch_root": str(batch_root),
                "command": command,
            }
    if input_raw:
        output = str(analysis.get("output_path") or "analysis_bundle")
        command = [
            python,
            "-m",
            "agentmon",
            "analysis",
            "run-all",
            "--input",
            str(Path(str(input_raw)).expanduser()),
            "--output",
            output,
            "--output-format",
            str(analysis.get("output_format") or "csv"),
            "--preflight",
        ]
        if as_bool(analysis.get("fast_smoke")):
            command.append("--fast-smoke")
        return {
            "schema_version": SCHEMA_VERSION,
            "result_type": "agentmon.experiment_orchestration.analysis_route",
            "status": PASS,
            "route": "analysis_runtime_run_all",
            "reason": "known_batch_artifacts_not_detected",
            "input_path": str(Path(str(input_raw)).expanduser()),
            "command": command,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.experiment_orchestration.analysis_route",
        "status": PARTIAL,
        "route": UNKNOWN,
        "reason": "analysis_input_not_configured",
        "command": [],
    }


def determine_ready_for_formal(
    contract: dict[str, Any],
    contract_validation: dict[str, Any],
    execution_path_validation: dict[str, Any],
    activation_report: dict[str, Any],
    pilot_gate: dict[str, Any],
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    blockers.extend(contract_validation.get("blocking_reasons") or [])
    blockers.extend(execution_path_validation.get("blocking_reasons") or [])
    formal_gates = as_dict(contract.get("formal_gates"))
    if as_bool(formal_gates.get("require_activation", True)):
        blockers.extend(activation_report.get("blocking_reasons") or [])
        if activation_report.get("status") == PARTIAL and expected_mechanisms(contract):
            blockers.append("activation_probe_missing")
    if as_bool(formal_gates.get("require_pilot", bool(contract.get("pilot")))):
        blockers.extend(pilot_gate.get("blocking_reasons") or [])
        if pilot_gate.get("status") in {PARTIAL, SKIPPED, UNKNOWN}:
            blockers.append("pilot_gate_missing")
    return not blockers, sorted(set(str(item) for item in blockers))


def run_configured_commands(
    contract: dict[str, Any],
    output_dir: Path,
    analysis_route: dict[str, Any],
    *,
    execute: bool,
    run_formal: bool,
    ready_for_formal: bool,
    stages: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    commands = as_dict(contract.get("commands"))
    results: list[dict[str, Any]] = []
    command_dir = output_dir / "commands"
    command_dir.mkdir(parents=True, exist_ok=True)
    selected_stages = tuple(str(item) for item in stages) if stages is not None else COMMAND_STAGES
    failed_stage: str | None = None
    for stage in selected_stages:
        if failed_stage is not None:
            results.append({"stage": stage, "status": SKIPPED, "reason": f"prerequisite_failed:{failed_stage}"})
            continue
        if stage not in COMMAND_STAGES:
            results.append({"stage": stage, "status": FAIL, "reason": "unknown_command_stage"})
            failed_stage = stage
            continue
        spec = commands.get(stage)
        command = command_from_spec(spec)
        if stage == "analysis" and not command:
            command = command_from_spec(analysis_route.get("command"))
            if command:
                spec = {"command": command}
        if not command:
            results.append({"stage": stage, "status": SKIPPED, "reason": "command_not_configured"})
            continue
        if stage == "formal_matrix" and execute:
            ready_for_formal, _ = determine_ready_for_formal(
                contract, validate_experiment_contract(contract), validate_execution_paths(contract),
                evaluate_activation_probes(contract), evaluate_pilot_gate(contract),
            )
        if stage == "formal_matrix" and not ready_for_formal:
            results.append(
                {
                    "stage": stage,
                    "status": SKIPPED,
                    "reason": "READY_FOR_FORMAL=NO",
                    "command": command,
                }
            )
            continue
        if stage == "formal_matrix" and not run_formal:
            results.append(
                {
                    "stage": stage,
                    "status": SKIPPED,
                    "reason": "formal_matrix_requires_--run-formal",
                    "command": command,
                }
            )
            continue
        if not execute:
            results.append({"stage": stage, "status": PLANNED, "command": command})
            continue
        result = execute_command_stage(stage, command, command_dir, spec)
        results.append(result)
        if result["status"] == FAIL:
            failed_stage = stage
    return results


def execute_command_stage(
    stage: str,
    command: list[str],
    command_dir: Path,
    spec: Any,
) -> dict[str, Any]:
    started = time.monotonic()
    timeout = as_dict(spec).get("timeout_seconds")
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=float(timeout) if timeout not in {None, ""} else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        completed = subprocess.CompletedProcess(command, 124, stdout, stderr + f"\nCommand timed out after {timeout} seconds\n")
    except OSError as exc:
        completed = subprocess.CompletedProcess(command, 127, "", str(exc))
    stdout_path = command_dir / f"{stage}.stdout.log"
    stderr_path = command_dir / f"{stage}.stderr.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "stage": stage,
        "status": PASS if completed.returncode == 0 else FAIL,
        "returncode": completed.returncode,
        "command": command,
        "duration_seconds": time.monotonic() - started,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def activation_probe_configs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [as_dict(item) for item in as_list(contract.get("activation_probes")) if as_dict(item)]
    if explicit:
        return explicit
    default_evidence_paths = as_list(contract.get("activation_evidence_paths"))
    if not default_evidence_paths:
        default_evidence_paths = as_list(as_dict(contract.get("evidence")).get("activation_paths"))
    probes: list[dict[str, Any]] = []
    for variant in normalize_variants(contract.get("variants")):
        for mechanism in as_list(variant.get("mechanisms")):
            definition = mechanism_definition(str(mechanism)) or {}
            coverage = as_dict(definition.get("coverage"))
            min_count = int_or_default(coverage.get("min_count"), 1)
            probe: dict[str, Any] = {
                "variant": variant.get("id"),
                "mechanism": mechanism,
                "evidence_paths": variant.get("activation_evidence_paths") or default_evidence_paths,
                "min_count": min_count,
            }
            if coverage.get("min_ratio") not in {None, ""}:
                probe["min_ratio"] = coverage.get("min_ratio")
            if variant.get("activation_coverage_denominator") not in {None, ""}:
                probe["coverage_denominator"] = variant.get("activation_coverage_denominator")
            if variant.get("expected_activation_count") not in {None, ""}:
                probe["expected_count"] = variant.get("expected_activation_count")
            probes.append(
                probe
            )
    return probes


def activation_profile(mechanism: str, override: dict[str, Any]) -> dict[str, Any]:
    definition = mechanism_definition(mechanism) or {}
    if definition:
        base = {
            "event_types": definition.get("activation_event_types"),
            "negative_reason_paths": definition.get("negative_reason_paths"),
            "negative_reasons": definition.get("negative_reasons"),
            "forbidden_conclusions": definition.get("forbidden_conclusions"),
        }
        base.update(as_dict(definition.get("coverage")))
    else:
        base = dict(DEFAULT_ACTIVATION_PROFILES.get(mechanism, {}))
    base.setdefault("event_types", ())
    base.setdefault("negative_reason_paths", ())
    base.setdefault("negative_reasons", ())
    base.setdefault("forbidden_conclusions", ())
    for key, value in override.items():
        base[key] = value
    return {
        "event_types": tuple(str(item) for item in as_list_or_tuple(base.get("event_types"))),
        "negative_reason_paths": tuple(str(item) for item in as_list_or_tuple(base.get("negative_reason_paths"))),
        "negative_reasons": tuple(str(item) for item in as_list_or_tuple(base.get("negative_reasons"))),
        "min_count": base.get("min_count"),
        "min_ratio": base.get("min_ratio"),
        "forbidden_conclusions": tuple(str(item) for item in as_list_or_tuple(base.get("forbidden_conclusions"))),
    }


def event_matches_probe(
    event: dict[str, Any],
    profile: dict[str, Any],
    required_payload: dict[str, Any],
) -> bool:
    if str(event.get("event_type") or "") not in set(profile["event_types"]):
        return False
    if negative_activation_reason(event, profile):
        return False
    for key, expected in required_payload.items():
        if get_path(event, str(key)) != expected:
            return False
    return True


def negative_activation_reason(event: dict[str, Any], profile: dict[str, Any]) -> str | None:
    negative_reasons = {str(item) for item in profile["negative_reasons"]}
    if not negative_reasons:
        return None
    for path in profile["negative_reason_paths"]:
        value = get_path(event, path)
        if str(value) in negative_reasons:
            return str(value)
    return None


def select_baseline_variant(contract: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    requested = contract.get("baseline_variant")
    if requested:
        for variant in variants:
            if variant.get("id") == requested:
                return variant
    for variant in variants:
        if str(variant.get("role") or "").lower() == "baseline":
            return variant
    return variants[0] if variants else None


def normalize_variants(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        rows = []
        for key, value in sorted(raw.items()):
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("id", key)
            rows.append(row)
        return rows
    rows = []
    for index, value in enumerate(as_list(raw), start=1):
        row = dict(value) if isinstance(value, dict) else {}
        row.setdefault("id", f"variant-{index}")
        rows.append(row)
    return rows


def expected_mechanisms(contract: dict[str, Any]) -> list[str]:
    mechanisms: list[str] = []
    for variant in normalize_variants(contract.get("variants")):
        for mechanism in as_list(variant.get("mechanisms")):
            if str(mechanism) not in mechanisms:
                mechanisms.append(str(mechanism))
    return mechanisms


def merge_execution_path_requirements(contract: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    merged = {
        key: tuple(str(item) for item in definition.get("allowed_execution_paths", ()))
        for key, definition in mechanism_registry().items()
    }
    for key, value in DEFAULT_EXECUTION_PATHS.items():
        merged.setdefault(key, tuple(value))
    override = as_dict(contract.get("execution_path_requirements"))
    for mechanism, paths in override.items():
        merged[str(mechanism)] = tuple(str(item) for item in as_list(paths))
    return merged


def flatten_mapping(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for key, item in sorted(value.items()):
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            rows.update(flatten_mapping(item, path))
        else:
            rows[path] = item
    return rows


def path_allowed(path: str, allowed: Iterable[str]) -> bool:
    for rule in allowed:
        if rule == path:
            return True
        if rule.endswith(".*") and path.startswith(rule[:-1]):
            return True
    return False


def expand_evidence_paths(paths: list[Any]) -> list[Path]:
    expanded: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(str(raw)).expanduser()
        candidates: list[Path]
        if path.is_dir():
            direct = [path / "_runtime" / "events.jsonl", path / "events.jsonl"]
            candidates = [item for item in direct if item.is_file()]
            if not candidates:
                candidates = sorted(path.glob("**/events.jsonl"))
        else:
            candidates = [path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if candidate.is_file() and resolved not in seen:
                seen.add(resolved)
                expanded.append(resolved)
    return expanded


def iter_jsonl_events(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def read_batch_results(batch_root: Path) -> list[dict[str, str]]:
    path = batch_root / "batch-results.tsv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def successful_result_row(row: dict[str, str]) -> bool:
    value = str(row.get("execution_success") or row.get("status") or "").lower()
    return value in {"true", "success", "succeeded", "passed", "pass"}


def is_known_batch_root(path: Path) -> bool:
    return path.is_dir() and (
        (path / "batch-manifest.json").is_file()
        or (path / "batch-results.tsv").is_file()
        or (path / "batch-summary.json").is_file()
    )


def command_from_spec(spec: Any) -> list[str]:
    if isinstance(spec, list):
        return [str(item) for item in spec]
    if isinstance(spec, dict):
        command = spec.get("command")
        if isinstance(command, list):
            return [str(item) for item in command]
    return []


def add_optional_arg(command: list[str], name: str, value: Any) -> None:
    if value in {None, ""}:
        return
    command.extend([name, str(value)])


def get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_list_or_tuple(value: Any) -> list[Any] | tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return value
    if value in {None, ""}:
        return []
    return [value]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_ready_env(path: Path, report: dict[str, Any]) -> None:
    blockers = ",".join(str(item) for item in report.get("blocking_reasons", []))
    path.write_text(
        "\n".join(
            [
                f"READY_FOR_FORMAL={report.get('ready_for_formal')}",
                f"EXPERIMENT_ORCHESTRATION_STATUS={report.get('status')}",
                f"BLOCKING_REASONS={blockers}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def load_contract(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("experiment contract must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AgentMon experiment orchestration gates from a JSON contract",
    )
    parser.add_argument("--contract", required=True, help="experiment orchestration contract JSON")
    parser.add_argument("--output-dir", required=True, help="directory for orchestration reports")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="execute configured non-formal commands; default only writes the plan and gates",
    )
    parser.add_argument(
        "--run-formal",
        action="store_true",
        help="allow formal_matrix command execution when READY_FOR_FORMAL=YES",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=COMMAND_STAGES,
        help="limit command planning/execution to one stage; repeat for multiple stages",
    )
    parser.add_argument(
        "--evaluate-after-commands",
        action="store_true",
        help="re-scan activation and pilot evidence after configured commands finish",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = orchestrate_experiment(
            load_contract(args.contract),
            output_dir=args.output_dir,
            execute=args.execute,
            run_formal=args.run_formal,
            stages=args.stage,
            evaluate_after_commands=args.evaluate_after_commands,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should produce an auditable error line.
        print(f"agentmon experiment orchestration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
