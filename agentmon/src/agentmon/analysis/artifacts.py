from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.io import read_json, write_json, write_plain_csv, write_table


ARTIFACT_SCHEMA_VERSION = "1.0"


def build_artifact_manifest(bundle_root: str | Path, cfg: AnalysisConfig) -> dict[str, Any]:
    bundle = Path(bundle_root)
    normalized_manifest = read_json(bundle / "01_normalized" / "normalized_manifest.json", default={})
    inventory_contract_manifest = read_json(bundle / "00_inventory" / "contract_manifest.json", default={})
    semantics_manifest = read_json(bundle / "02_semantics" / "semantics_table_manifest.json", default={})
    validation_manifest = read_json(bundle / "02_validation" / "validation_manifest.json", default={})
    analysis_manifest = read_json(bundle / "03_analysis" / "analysis_manifest.json", default={})
    profile_rows = table_entries(normalized_manifest, "profile_detection_audit")
    detected_vllm = profile_table_mentions_vllm(bundle, profile_rows)
    rows: list[dict[str, Any]] = []

    add_file(rows, bundle, "analysis_ready", "REQUIRED_CORE", "validation", "analysis_ready.json")
    add_file(rows, bundle, "run_summary", "REQUIRED_CORE", "final_summary", "run_summary.json")
    add_file(rows, bundle, "analysis_manifest", "REQUIRED_CORE", "final_summary", "analysis_manifest.json")
    add_file(rows, bundle, "experiment_contract", "REQUIRED_CORE", "contract", "00_inventory/experiment_contract.json")
    add_file(rows, bundle, "expected_artifacts", "REQUIRED_CORE", "contract", "00_inventory/expected_artifacts.json")
    add_file(rows, bundle, "semantics_manifest", "REQUIRED_CORE", "semantics_export", "02_semantics/semantics_manifest.json")
    add_file(rows, bundle, "data_quality_gate", "REQUIRED_CORE", "validation", "02_validation/data_quality_gate.json")
    add_table(rows, "normalized_runs", "REQUIRED_CORE", "normalize_runs", normalized_manifest, "runs")
    add_table(rows, "normalized_requests", "REQUIRED_CORE", "normalize_requests", normalized_manifest, "requests")
    add_table(rows, "request_parse_audit", "REQUIRED_CORE", "p1_p2_audit", normalized_manifest, "request_parse_audit")
    add_table(rows, "request_identity_audit", "REQUIRED_CORE", "p1_p2_audit", normalized_manifest, "request_identity_audit")
    add_file(rows, bundle, "validation_status", "REQUIRED_CORE", "validation", "02_validation/validation_status.json")

    if detected_vllm:
        add_table(rows, "p2_requests", "REQUIRED_FOR_PROFILE", "normalize_requests", normalized_manifest, "p2_requests")
        add_file(rows, bundle, "p2_index_metrics", "REQUIRED_FOR_PROFILE", "build_p2_index", "00_inventory/p2_index_metrics.json")
    else:
        rows.extend(
            [
                artifact_row("p2_requests", "REQUIRED_FOR_PROFILE", "NOT_APPLICABLE", "profile_not_detected", None, "normalize_requests", None),
                artifact_row("p2_index_metrics", "REQUIRED_FOR_PROFILE", "NOT_APPLICABLE", "no_shared_p2_source", None, "build_p2_index", None),
            ]
        )

    add_manifest_tables(rows, inventory_contract_manifest, "REQUIRED_CORE", "contract")
    add_manifest_tables(rows, semantics_manifest, "REQUIRED_CORE", "semantics_export")
    add_manifest_tables(rows, validation_manifest, "REQUIRED_CORE", "validation")
    add_manifest_tables(rows, analysis_manifest, "REQUIRED_CORE", "analysis")
    add_optional_dir(rows, bundle, cfg, "evidence_bundle", "evidence_build", "04_evidence", cfg.output.include_evidence and not cfg.output.fast_smoke)
    if cfg.output.include_evidence and cfg.output.include_claim_registry and not cfg.output.fast_smoke:
        add_file(rows, bundle, "claim_registry", "REQUIRED_CORE", "evidence_build", "04_evidence/claim_registry.json")
    else:
        rows.append(artifact_row("claim_registry", "OPTIONAL", "NOT_APPLICABLE", "disabled_by_config", str(bundle / "04_evidence" / "claim_registry.json"), "evidence_build", None))
    add_optional_dir(rows, bundle, cfg, "handoff_package", "handoff_build", "05_handoff", cfg.output.include_handoff and not cfg.output.fast_smoke)
    add_optional_dir(rows, bundle, cfg, "model_input_pack", "model_pack_build", "model_input_pack", cfg.output.include_model_pack and not cfg.output.fast_smoke)
    add_optional_dir(rows, bundle, cfg, "figures", "analysis", "figures", cfg.output.figures)

    required_failures = [row for row in rows if row["category"] in {"REQUIRED_CORE", "REQUIRED_FOR_PROFILE"} and row["status"] == "UNAVAILABLE"]
    optional_unavailable = [row for row in rows if row["category"] == "OPTIONAL" and row["status"] == "UNAVAILABLE"]
    status = "FAIL" if required_failures else "PARTIAL" if optional_unavailable else "PASS"
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_status": status,
        "required_artifact_status": "FAIL" if required_failures else "PASS",
        "required_failure_count": len(required_failures),
        "optional_unavailable_count": len(optional_unavailable),
        "artifacts": rows,
    }
    write_json(bundle / "artifact_manifest.json", payload)
    write_table(bundle, "artifact_manifest", rows, output_format="csv")
    write_plain_csv(bundle / "artifact_manifest.csv", rows)
    return payload


def add_file(rows: list[dict[str, Any]], bundle: Path, name: str, category: str, stage: str, relative: str) -> None:
    path = bundle / relative
    rows.append(
        artifact_row(
            name,
            category,
            "PASS" if path.is_file() else "UNAVAILABLE",
            None if path.is_file() else "missing_required_output",
            str(path),
            stage,
            None,
        )
    )


def add_table(
    rows: list[dict[str, Any]],
    name: str,
    category: str,
    stage: str,
    manifest: dict[str, Any],
    table_name: str,
) -> None:
    entry = table_entries(manifest, table_name)
    path = Path(str(entry.get("path") or "")) if entry else None
    exists = bool(path and path.is_file())
    reason = None
    if not exists:
        reason = "missing_required_output"
    elif entry.get("parquet_status") == "unavailable":
        reason = "parquet_dependency_unavailable_csv_fallback_used"
    rows.append(
        artifact_row(
            name,
            category,
            "PASS" if exists else "UNAVAILABLE",
            reason,
            str(path) if path else None,
            stage,
            entry.get("row_count") if entry else None,
        )
    )


def add_manifest_tables(rows: list[dict[str, Any]], manifest: dict[str, Any], category: str, stage: str) -> None:
    tables = manifest.get("tables", {}) if isinstance(manifest, dict) else {}
    if not isinstance(tables, dict):
        return
    for name in sorted(tables):
        if any(row["artifact_name"] == f"{stage}.{name}" for row in rows):
            continue
        add_table(rows, f"{stage}.{name}", category, stage, manifest, name)


def add_optional_dir(
    rows: list[dict[str, Any]],
    bundle: Path,
    cfg: AnalysisConfig,
    name: str,
    stage: str,
    relative: str,
    enabled: bool,
) -> None:
    path = bundle / relative
    if not enabled:
        rows.append(artifact_row(name, "OPTIONAL", "NOT_APPLICABLE", "disabled_by_config", str(path), stage, None))
    elif path.is_dir() and any(path.iterdir()):
        rows.append(artifact_row(name, "OPTIONAL", "PASS", None, str(path), stage, None))
    else:
        rows.append(artifact_row(name, "OPTIONAL", "UNAVAILABLE", "optional_output_missing", str(path), stage, None))


def artifact_row(
    name: str,
    category: str,
    status: str,
    reason_code: str | None,
    path: str | None,
    source_stage: str,
    row_count: Any,
) -> dict[str, Any]:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_name": name,
        "category": category,
        "status": status,
        "reason_code": reason_code,
        "row_count": row_count,
        "source_stage": source_stage,
        "path": path,
        "validation_status": "PASS" if status in {"PASS", "NOT_APPLICABLE"} else "FAIL" if category.startswith("REQUIRED") else "PARTIAL",
    }


def table_entries(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    tables = manifest.get("tables", {}) if isinstance(manifest, dict) else {}
    value = tables.get(name, {}) if isinstance(tables, dict) else {}
    return value if isinstance(value, dict) else {}


def profile_table_mentions_vllm(bundle: Path, entry: dict[str, Any]) -> bool:
    path = Path(str(entry.get("path") or "")) if entry else None
    if not path or not path.is_file():
        return False
    from agentmon.analysis.io import read_table

    return any("vllm" in str(row.get("detected_profile") or "").lower() for row in read_table(path))
