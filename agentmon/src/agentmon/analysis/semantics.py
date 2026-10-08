from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from agentmon.analysis.io import write_json, write_markdown, write_table, write_table_manifest
from agentmon.analysis.schemas import load_metric_definitions, metric_dictionary_markdown


SEMANTICS_SCHEMA_VERSION = "1.0"


def write_semantics_bundle(
    *,
    bundle_root: str | Path,
    profile: str,
    output_format: str,
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    resolved_profile = "agentmon_default" if profile in {"", "auto", None} else str(profile)
    status = "PASS"
    reason = None
    try:
        metric_rows = metric_dictionary_rows(resolved_profile)
        metric_markdown = metric_dictionary_markdown(resolved_profile)
    except (FileNotFoundError, ValueError) as exc:
        status = "PARTIAL"
        reason = f"{type(exc).__name__}: {exc}"
        resolved_profile = "agentmon_default"
        metric_rows = metric_dictionary_rows(resolved_profile)
        metric_markdown = metric_dictionary_markdown(resolved_profile)

    entity_rows = entity_set_definition_rows()
    clock_rows = clock_domain_audit_rows(metric_rows)
    semantics_dir = bundle / "02_semantics"
    artifacts = {
        "metric_dictionary": write_table(semantics_dir, "metric_dictionary", metric_rows, output_format=output_format),
        "entity_set_definitions": write_table(semantics_dir, "entity_set_definitions", entity_rows, output_format=output_format),
        "clock_domain_audit": write_table(semantics_dir, "clock_domain_audit", clock_rows, output_format=output_format),
    }
    write_markdown(semantics_dir / "metric_dictionary.md", metric_markdown)
    write_markdown(semantics_dir / "entity_set_definitions.md", entity_definitions_markdown(entity_rows))
    write_markdown(semantics_dir / "clock_domain_audit.md", clock_audit_markdown(clock_rows))
    manifest = {
        "schema_version": SEMANTICS_SCHEMA_VERSION,
        "status": status,
        "profile": resolved_profile,
        "fallback_reason": reason,
        "metric_count": len(metric_rows),
        "entity_set_count": len(entity_rows),
        "clock_domain_count": len(clock_rows),
        "tables": artifacts,
    }
    write_json(semantics_dir / "semantics_manifest.json", manifest)
    write_table_manifest(semantics_dir / "semantics_table_manifest.json", artifacts)
    return manifest


def metric_dictionary_rows(profile: str) -> list[dict[str, Any]]:
    definitions = load_metric_definitions(profile)
    return [
        {
            "schema_version": SEMANTICS_SCHEMA_VERSION,
            "profile": profile,
            **definition.to_dict(),
        }
        for definition in sorted(definitions.values(), key=lambda item: item.canonical_name)
    ]


def entity_set_definition_rows() -> list[dict[str, Any]]:
    return [
        entity_row(
            "attempted_requests",
            "request",
            "01_normalized.requests",
            "Every parsed model request attempt. Includes success, error, and partial attempts when evidence exists.",
            "request_attempt_id",
        ),
        entity_row(
            "successful_requests",
            "request",
            "01_normalized.requests",
            "Request attempts whose normalized request_status is success.",
            "request_status",
        ),
        entity_row(
            "p2_eligible_requests",
            "request",
            "01_normalized.requests",
            "Requests for which the active schema profile declares downstream P2 matching eligible.",
            "p2_eligible",
        ),
        entity_row(
            "p2_matched_requests",
            "request",
            "01_normalized.requests + 01_normalized.p2_requests",
            "P2-eligible requests with matched engine-side lifecycle evidence.",
            "p2_matched",
        ),
        entity_row(
            "lifecycle_complete_requests",
            "request",
            "01_normalized.request_lifecycle",
            "Requests with enough timestamp evidence to evaluate standardized lifecycle stages.",
            "lifecycle_complete",
        ),
        entity_row(
            "scheduler_iterations",
            "scheduler_iteration",
            "01_normalized.scheduler_iterations",
            "Scheduler iteration samples or hooks emitted by an inference adapter.",
            "scheduler_iteration_id",
        ),
        entity_row(
            "resource_samples",
            "device_sample",
            "01_normalized.resource_samples",
            "Host, process, or device telemetry samples provided by resource adapters.",
            "sample_time",
        ),
        entity_row(
            "task_results",
            "run",
            "01_normalized.task_results",
            "Task-level execution outcome rows. Task success is separate from observability quality.",
            "task_id",
        ),
    ]


def entity_row(name: str, level: str, source_table: str, definition: str, eligibility_field: str) -> dict[str, Any]:
    return {
        "schema_version": SEMANTICS_SCHEMA_VERSION,
        "entity_set": name,
        "data_level": level,
        "source_table": source_table,
        "definition": definition,
        "eligibility_field": eligibility_field,
        "status": "PASS",
    }


def clock_domain_audit_rows(metric_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metric_rows:
        grouped[str(row.get("clock_domain") or "UNKNOWN")].append(row)
    result: list[dict[str, Any]] = []
    for domain, rows in sorted(grouped.items()):
        result.append(
            {
                "schema_version": SEMANTICS_SCHEMA_VERSION,
                "clock_domain": domain,
                "metric_count": len(rows),
                "data_levels": sorted({str(row.get("data_level") or "UNKNOWN") for row in rows}),
                "metrics_sample": [str(row.get("canonical_name")) for row in rows[:10]],
                "status": "PASS" if domain != "UNKNOWN" else "PARTIAL",
                "reason": None if domain != "UNKNOWN" else "metric_clock_domain_unknown",
            }
        )
    return result or [
        {
            "schema_version": SEMANTICS_SCHEMA_VERSION,
            "clock_domain": "UNKNOWN",
            "metric_count": 0,
            "data_levels": [],
            "metrics_sample": [],
            "status": "PARTIAL",
            "reason": "no_metric_definitions",
        }
    ]


def entity_definitions_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Entity Set Definitions",
        "",
        "| Entity Set | Level | Source | Definition |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        definition = str(row.get("definition") or "").replace("|", "/")
        lines.append(f"| `{row.get('entity_set')}` | {row.get('data_level')} | `{row.get('source_table')}` | {definition} |")
    return "\n".join(lines) + "\n"


def clock_audit_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Clock Domain Audit",
        "",
        "| Clock Domain | Metrics | Status | Reason |",
        "| --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| `{row.get('clock_domain')}` | {row.get('metric_count')} | {row.get('status')} | {row.get('reason') or ''} |")
    return "\n".join(lines) + "\n"
