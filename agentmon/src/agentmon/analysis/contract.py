from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.config import AnalysisConfig, read_config_file
from agentmon.analysis.discovery import DiscoveryResult
from agentmon.analysis.io import write_json, write_table, write_table_manifest


CONTRACT_SCHEMA_VERSION = "1.0"
REQUIRED_CONTRACT_FIELDS = (
    "objective",
    "independent_variables",
    "load_levels",
    "control_variables",
    "baseline",
    "task_set",
    "repetitions",
    "key_config",
    "success_definition",
    "availability_definition",
    "throughput_numerator",
    "throughput_denominator",
    "batch_time_window",
    "comparison_unit",
    "fixed_task_pairing",
)


def default_contract_template() -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "experiment_id": None,
        "objective": None,
        "research_questions": [
            {
                "question_id": "rq.primary",
                "question": None,
                "analysis_scope": "basic_load_analysis",
                "required_tables": [
                    "requests",
                    "request_lifecycle",
                    "scheduler_iterations",
                    "resource_samples",
                    "task_results",
                ],
            }
        ],
        "independent_variables": [],
        "load_levels": [],
        "control_variables": {},
        "baseline": None,
        "task_set": {"task_count": None, "task_set_hash": None, "selection_rule": None},
        "repetitions": {"runs_per_load_level": None, "random_seed_policy": None},
        "key_config": {},
        "success_definition": None,
        "availability_definition": None,
        "publishability_definition": None,
        "throughput_numerator": None,
        "throughput_denominator": None,
        "batch_time_window": None,
        "comparison_unit": None,
        "fixed_task_pairing": None,
        "expected_data_sources": [
            {"source_name": "events.jsonl", "role": "authoritative_raw_events", "required": True},
            {"source_name": "manifest.json", "role": "run_metadata", "required": False},
            {"source_name": "batch-manifest.json", "role": "batch_metadata", "required": False},
            {"source_name": "batch-results.tsv", "role": "task_result_index", "required": False},
        ],
        "forbidden_mechanism_conclusions": [
            "Do not infer a root cause from basic load-analysis outputs alone.",
            "Do not treat configured concurrency as actual model concurrency.",
            "Do not treat request membership in a batch as exclusive resource ownership.",
        ],
        "notes": None,
    }


def load_experiment_contract(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    loaded = read_config_file(Path(path).expanduser())
    if not isinstance(loaded, dict):
        raise ValueError("experiment contract must be a mapping")
    return loaded


def write_contract_template(path: str | Path) -> dict[str, Any]:
    payload = default_contract_template()
    write_json(path, payload)
    return {"schema_version": CONTRACT_SCHEMA_VERSION, "status": "PASS", "path": str(Path(path))}


def write_experiment_contract(
    *,
    bundle_root: str | Path,
    discovery: DiscoveryResult,
    cfg: AnalysisConfig,
    output_format: str,
    contract_path: str | Path | None = None,
) -> dict[str, Any]:
    explicit = load_experiment_contract(contract_path)
    contract = build_contract(discovery, cfg, explicit=explicit, contract_path=contract_path)
    validation_rows = validate_contract(contract)
    research_questions = research_question_rows(contract)
    expected_artifacts = expected_artifacts_payload(discovery, contract)
    artifacts = {
        "contract_validation": write_table(Path(bundle_root) / "00_inventory", "contract_validation", validation_rows, output_format=output_format),
        "research_questions": write_table(Path(bundle_root) / "00_inventory", "research_questions", research_questions, output_format=output_format),
    }
    write_json(Path(bundle_root) / "00_inventory" / "experiment_contract.json", contract)
    write_json(Path(bundle_root) / "00_inventory" / "expected_artifacts.json", expected_artifacts)
    write_table_manifest(Path(bundle_root) / "00_inventory" / "contract_manifest.json", artifacts)
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "status": contract.get("contract_status"),
        "source": contract.get("contract_source"),
        "missing_required_fields": contract.get("missing_required_fields", []),
        "validation_rows": len(validation_rows),
        "research_question_rows": len(research_questions),
        "path": str(Path(bundle_root) / "00_inventory" / "experiment_contract.json"),
    }


def build_contract(
    discovery: DiscoveryResult,
    cfg: AnalysisConfig,
    *,
    explicit: dict[str, Any] | None,
    contract_path: str | Path | None,
) -> dict[str, Any]:
    template = default_contract_template()
    source = "EXPLICIT_CONTRACT" if explicit is not None else "DISCOVERY_DERIVED_PARTIAL"
    payload = deep_merged(template, explicit or {})
    payload["schema_version"] = CONTRACT_SCHEMA_VERSION
    payload["experiment_id"] = payload.get("experiment_id") or discovery.manifest.get("experiment_id")
    payload["analysis_profile"] = cfg.profile
    payload["contract_source"] = source
    payload["contract_source_path"] = str(Path(contract_path).expanduser()) if contract_path else None
    payload["field_sources"] = field_sources(payload, discovery, explicit is not None)

    if explicit is None:
        fill_discovery_fields(payload, discovery)

    validation_rows = validate_contract(payload)
    missing = [row["field_name"] for row in validation_rows if row["status"] != "PASS"]
    payload["missing_required_fields"] = missing
    payload["contract_status"] = "PASS" if not missing and explicit is not None else "PARTIAL"
    payload["formal_analysis_ready"] = payload["contract_status"] == "PASS"
    payload["basic_analysis_allowed"] = True
    payload["contract_limitations"] = contract_limitations(payload)
    return payload


def fill_discovery_fields(payload: dict[str, Any], discovery: DiscoveryResult) -> None:
    configured = discovery.manifest.get("configured_concurrency")
    if configured:
        payload["independent_variables"] = [{"name": "configured_concurrency", "source": "DISCOVERY"}]
        payload["load_levels"] = configured
    task_count = discovery.manifest.get("task_count")
    task_hash = discovery.manifest.get("task_set_sha256") or discovery.manifest.get("task_set_hash")
    payload["task_set"] = {
        "task_count": task_count,
        "task_set_hash": task_hash,
        "selection_rule": None,
        "source": "DISCOVERY",
    }
    payload["repetitions"] = {
        "runs_discovered": len(discovery.run_inventory),
        "runs_per_load_level": None,
        "random_seed_policy": None,
        "source": "DISCOVERY",
    }
    payload["key_config"] = {
        key: discovery.manifest.get(key)
        for key in ("model_name", "agent_framework", "inference_backend")
        if discovery.manifest.get(key) not in {None, ""}
    }


def validate_contract(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sources = contract.get("field_sources", {}) if isinstance(contract.get("field_sources"), dict) else {}
    for name in REQUIRED_CONTRACT_FIELDS:
        value = contract.get(name)
        present = has_contract_value(name, value)
        rows.append(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "field_name": name,
                "status": "PASS" if present else "PARTIAL",
                "value_status": "PRESENT" if present else "UNKNOWN",
                "source": sources.get(name) or ("EXPLICIT_CONTRACT" if present else "UNKNOWN"),
                "reason": None if present else "required_field_missing_or_unknown",
            }
        )
    return rows


def research_question_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    raw = contract.get("research_questions")
    questions = raw if isinstance(raw, list) else []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        if isinstance(item, dict):
            rows.append(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "question_id": item.get("question_id") or f"rq.{index}",
                    "question": item.get("question"),
                    "analysis_scope": item.get("analysis_scope") or "basic_load_analysis",
                    "required_tables": item.get("required_tables"),
                    "status": "PASS" if item.get("question") else "PARTIAL",
                }
            )
        else:
            rows.append(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "question_id": f"rq.{index}",
                    "question": str(item),
                    "analysis_scope": "basic_load_analysis",
                    "required_tables": None,
                    "status": "PASS",
                }
            )
    if rows:
        return rows
    return [
        {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "question_id": "rq.unresolved",
            "question": None,
            "analysis_scope": "basic_load_analysis",
            "required_tables": None,
            "status": "PARTIAL",
            "reason": "research_questions_unavailable",
        }
    ]


def expected_artifacts_payload(discovery: DiscoveryResult, contract: dict[str, Any]) -> dict[str, Any]:
    roles: dict[str, int] = {}
    for row in discovery.file_inventory:
        role = str(row.get("role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "status": "PASS" if roles.get("run_events", 0) else "PARTIAL",
        "contract_status": contract.get("contract_status"),
        "input_root": str(discovery.input_root),
        "expected_data_sources": contract.get("expected_data_sources", []),
        "observed_file_roles": roles,
        "authoritative_event_stream_count": roles.get("run_events", 0),
        "notes": "events.jsonl remains the authoritative raw event source; SQLite caches are optional query acceleration only.",
    }


def field_sources(contract: dict[str, Any], discovery: DiscoveryResult, explicit: bool) -> dict[str, str]:
    source = {name: "EXPLICIT_CONTRACT" for name in REQUIRED_CONTRACT_FIELDS if explicit and has_contract_value(name, contract.get(name))}
    if not explicit:
        if discovery.manifest.get("configured_concurrency"):
            source["independent_variables"] = "DISCOVERY"
            source["load_levels"] = "DISCOVERY"
        if discovery.manifest.get("task_count") or discovery.manifest.get("task_set_sha256") or discovery.run_inventory:
            source["task_set"] = "DISCOVERY"
            source["repetitions"] = "DISCOVERY"
        if any(discovery.manifest.get(key) not in {None, ""} for key in ("model_name", "agent_framework", "inference_backend")):
            source["key_config"] = "DISCOVERY"
    return source


def has_contract_value(name: str, value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    if isinstance(value, dict):
        if name == "task_set":
            return any(value.get(key) not in {None, ""} for key in ("task_count", "task_set_hash", "selection_rule"))
        if name == "repetitions":
            return any(value.get(key) not in {None, ""} for key in ("runs_per_load_level", "runs_discovered", "random_seed_policy"))
        return any(has_contract_value(name, item) for item in value.values())
    return True


def contract_limitations(contract: dict[str, Any]) -> list[str]:
    limitations = []
    if contract.get("contract_status") != "PASS":
        limitations.append("Formal experiment design fields are incomplete; report these as PARTIAL and keep basic observations separate from design-level claims.")
    if not contract.get("throughput_denominator"):
        limitations.append("Throughput denominator is not explicitly defined by contract; use standardized analyzer outputs without redefining the metric.")
    if not contract.get("fixed_task_pairing"):
        limitations.append("Fixed task pairing is unknown; paired comparisons must remain UNRESOLVED unless task-level evidence proves comparability.")
    return limitations


def deep_merged(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merged(result[key], value)
        else:
            result[key] = value
    return result
