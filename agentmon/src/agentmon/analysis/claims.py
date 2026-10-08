from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

import agentmon
from agentmon.analysis.io import read_json, write_json, write_plain_csv, write_table


CLAIM_SCHEMA_VERSION = "1.0"


def build_claim_registry(bundle_root: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_root)
    out = bundle / "04_evidence"
    ready = read_json(bundle / "02_validation" / "analysis_ready.json", default={})
    previous = read_json(out / "claim_registry.json", default={})
    previous_rows = previous.get("claims", []) if isinstance(previous, dict) else []
    sources = (
        ("verified_facts.json", "fact_id", "OBSERVED_FACT"),
        ("statistical_associations.json", "association_id", "STATISTICAL_ASSOCIATION"),
        ("mechanism_inferences.json", "inference_id", "MECHANISM_INFERENCE"),
        ("unresolved_questions.json", "question_id", "UNRESOLVED_HYPOTHESIS"),
        ("excluded_hypotheses.json", "hypothesis_id", "EXCLUDED_CLAIM"),
    )
    current: list[dict[str, Any]] = []
    previous_by_id = {str(row.get("claim_id")): row for row in previous_rows if isinstance(row, dict) and row.get("claim_id")}
    timestamp = deterministic_timestamp(bundle)
    for filename, id_field, default_class in sources:
        items = read_json(out / filename, default=[])
        for index, item in enumerate(items if isinstance(items, list) else [], start=1):
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement") or "").strip()
            if not statement:
                continue
            claim_id = str(item.get(id_field) or f"{filename}.{index}")
            module = infer_claim_module(claim_id, item)
            conclusion_class = str(item.get("conclusion_class") or default_class)
            validator_status = module_validator_status(ready, module)
            explicit_supersedes = sorted({str(value) for value in as_list(item.get("supersedes_claim_ids")) if value})
            prior = previous_by_id.get(claim_id)
            if prior and prior.get("normalized_statement_hash") != statement_hash(statement):
                old_id = f"{claim_id}@{str(prior.get('normalized_statement_hash') or '')[:12]}"
                explicit_supersedes.append(old_id)
            allowed_module = conclusion_class not in {"UNRESOLVED_HYPOTHESIS", "EXCLUDED_CLAIM"} and validator_status != "FAIL"
            allowed_final = allowed_module and not (conclusion_class == "MECHANISM_INFERENCE" and validator_status != "PASS")
            current.append(
                {
                    "schema_version": CLAIM_SCHEMA_VERSION,
                    "claim_id": claim_id,
                    "module": module,
                    "statement": statement,
                    "normalized_statement_hash": statement_hash(statement),
                    "conclusion_class": conclusion_class,
                    "status": "ACTIVE" if allowed_module else "WITHHELD",
                    "confidence": item.get("confidence") or "unavailable",
                    "metric_ids": json.dumps(as_list(item.get("metric_ids")), ensure_ascii=False, sort_keys=True),
                    "evidence_files": json.dumps(as_list(item.get("evidence_files") or item.get("supporting_evidence")), ensure_ascii=False, sort_keys=True),
                    "evidence_rows": json.dumps(as_list(item.get("source_rows")), ensure_ascii=False, sort_keys=True),
                    "validator_status": validator_status,
                    "supersedes_claim_ids": json.dumps(sorted(set(explicit_supersedes)), ensure_ascii=False),
                    "superseded_by_claim_id": None,
                    "allowed_in_module_report": allowed_module,
                    "allowed_in_final_report": allowed_final,
                    "created_at": prior.get("created_at") if prior else timestamp,
                    "updated_at": timestamp,
                    "code_version": getattr(agentmon, "__version__", "unknown"),
                }
            )

    historical = superseded_history(previous_rows, current, timestamp)
    rows = sorted(historical + current, key=lambda row: (str(row.get("claim_id")), str(row.get("status"))))
    payload = {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "status": "PASS",
        "active_claim_count": sum(row.get("status") == "ACTIVE" for row in rows),
        "withheld_claim_count": sum(row.get("status") == "WITHHELD" for row in rows),
        "superseded_claim_count": sum(row.get("status") == "SUPERSEDED" for row in rows),
        "claims": rows,
    }
    write_json(out / "claim_registry.json", payload)
    artifact = write_table(out, "claim_registry", rows, output_format="csv")
    plain_csv = write_plain_csv(out / "claim_registry.csv", rows)
    payload["csv_path"] = artifact.get("path")
    payload["plain_csv_path"] = str(plain_csv)
    return payload


def superseded_history(previous_rows: Iterable[Any], current: list[dict[str, Any]], timestamp: Any) -> list[dict[str, Any]]:
    current_by_id = {str(row.get("claim_id")): row for row in current}
    history: list[dict[str, Any]] = []
    for raw in previous_rows:
        if not isinstance(raw, dict) or not raw.get("claim_id"):
            continue
        claim_id = str(raw["claim_id"])
        replacement = current_by_id.get(claim_id)
        if replacement and replacement.get("normalized_statement_hash") == raw.get("normalized_statement_hash"):
            continue
        old = dict(raw)
        old["claim_id"] = claim_id if "@" in claim_id else f"{claim_id}@{str(raw.get('normalized_statement_hash') or '')[:12]}"
        old["status"] = "SUPERSEDED"
        old["allowed_in_module_report"] = False
        old["allowed_in_final_report"] = False
        old["superseded_by_claim_id"] = replacement.get("claim_id") if replacement else None
        old["updated_at"] = timestamp
        history.append(old)
    return history


def infer_claim_module(claim_id: str, item: dict[str, Any]) -> str:
    text = " ".join([claim_id, str(item.get("statement") or ""), " ".join(str(value) for value in as_list(item.get("evidence_files"))) ]).lower()
    mappings = (
        ("throughput", ("throughput", "token/s")),
        ("concurrency", ("concurrency", "inflight")),
        ("lifecycle", ("lifecycle", "request_stage")),
        ("p1_p2", ("p1", "p2", "publishability")),
        ("pipeline", ("pipeline", "preprocessing", "finalization")),
        ("scheduler", ("scheduler", "batching", "kv")),
        ("workload_path", ("workload", "tool", "service_decomposition")),
        ("tail", ("tail", "outlier")),
        ("resources", ("resource", "npu", "cpu", "hbm")),
    )
    for module, signals in mappings:
        if any(signal in text for signal in signals):
            return module
    return "overview"


def module_validator_status(ready: dict[str, Any], module: str) -> str:
    statuses = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    mapping = {
        "lifecycle": "lifecycle",
        "p1_p2": "p2_audit",
        "pipeline": "pipeline_reconciliation",
        "scheduler": "token_timeseries",
        "resources": "resource",
    }
    return str(statuses.get(mapping.get(module, "inventory")) or ready.get("overall_status") or "PARTIAL")


def statement_hash(statement: str) -> str:
    normalized = re.sub(r"\s+", " ", statement.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deterministic_timestamp(bundle: Path) -> Any:
    manifest = read_json(bundle / "manifest" / "experiment_manifest.json", default={})
    return manifest.get("experiment_finish") or manifest.get("experiment_start")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
