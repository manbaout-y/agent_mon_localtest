from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.io import float_or_none, read_bundle_table, read_json, write_json, write_table, write_table_manifest


HANDOFF_SCHEMA_VERSION = "1.0"


def build_handoff_package(
    *,
    bundle_root: str | Path,
    output_format: str = "parquet",
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    ready = read_json(bundle / "02_validation" / "analysis_ready.json", default={})
    quality_gate = read_json(bundle / "02_validation" / "data_quality_gate.json", default={})
    evidence_unresolved = read_json(bundle / "04_evidence" / "unresolved_questions.json", default=[])
    evidence_forbidden = read_json(bundle / "04_evidence" / "forbidden_conclusions.json", default=[])
    tables = load_basic_tables(bundle)
    observation_rows = observation_register_rows(ready, quality_gate, tables)
    anomaly_rows = anomaly_register_rows(ready, tables)
    contradiction_rows = contradiction_register_rows(ready, evidence_forbidden)
    unresolved_rows = unresolved_question_rows(ready, evidence_unresolved)
    backlog_rows = analysis_backlog_rows(unresolved_rows, anomaly_rows)

    output = bundle / "05_handoff"
    artifacts = {
        "observation_register": write_table(output, "observation_register", observation_rows, output_format=output_format),
        "anomaly_register": write_table(output, "anomaly_register", anomaly_rows, output_format=output_format),
        "contradiction_register": write_table(output, "contradiction_register", contradiction_rows, output_format=output_format),
        "unresolved_question_register": write_table(output, "unresolved_question_register", unresolved_rows, output_format=output_format),
        "analysis_backlog": write_table(output, "analysis_backlog", backlog_rows, output_format=output_format),
    }
    status = "PASS" if ready.get("overall_status") == "PASS" else "PARTIAL"
    manifest = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "status": status,
        "handoff_scope": "basic_analysis_only",
        "customized_deep_analysis_status": "NOT_IMPLEMENTED_BY_SCOPE",
        "observation_count": len(observation_rows),
        "anomaly_count": len(anomaly_rows),
        "contradiction_count": len(contradiction_rows),
        "unresolved_question_count": len(unresolved_rows),
        "backlog_count": len(backlog_rows),
        "tables": artifacts,
    }
    write_json(output / "handoff_manifest.json", manifest)
    write_table_manifest(output / "handoff_table_manifest.json", artifacts)
    return manifest


def load_basic_tables(bundle: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "throughput_summary": read_bundle_table(bundle, "03_analysis", "throughput_summary"),
        "concurrency_summary": read_bundle_table(bundle, "03_analysis", "concurrency_summary"),
        "request_stage_summary": read_bundle_table(bundle, "03_analysis", "request_stage_summary"),
        "workload_drift": read_bundle_table(bundle, "03_analysis", "workload_drift"),
        "completion_tail": read_bundle_table(bundle, "03_analysis", "completion_tail"),
        "scheduler_summary": read_bundle_table(bundle, "03_analysis", "scheduler_summary"),
        "pipeline_summary": read_bundle_table(bundle, "03_analysis", "pipeline_summary"),
        "resource_summary": read_bundle_table(bundle, "03_analysis", "resource_summary"),
        "p2_match_audit": read_bundle_table(bundle, "03_analysis", "p2_match_audit"),
    }


def observation_register_rows(
    ready: dict[str, Any],
    quality_gate: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append(
        observation_row(
            "obs.analysis_ready",
            "validation",
            "02_validation.analysis_ready",
            "overall_status",
            ready.get("overall_status"),
            ready.get("overall_status") or "UNKNOWN",
            "Analysis readiness status produced by deterministic validation.",
        )
    )
    rows.append(
        observation_row(
            "obs.data_quality_gate",
            "validation",
            "02_validation.data_quality_gate",
            "gate_status",
            quality_gate.get("gate_status"),
            quality_gate.get("gate_status") or "UNKNOWN",
            "Data quality gate status summarizing validation limitations and blocked conclusions.",
        )
    )
    for index, row in enumerate(tables.get("throughput_summary", []), start=1):
        rows.append(
            observation_row(
                f"obs.throughput.{index}",
                "throughput",
                "03_analysis.throughput_summary",
                "throughput_row",
                row,
                row.get("status") or "PASS",
                "Standardized throughput summary row. Metric definitions remain in 02_semantics.",
            )
        )
    for index, row in enumerate(tables.get("concurrency_summary", []), start=1):
        rows.append(
            observation_row(
                f"obs.concurrency.{index}",
                "concurrency",
                "03_analysis.concurrency_summary",
                "concurrency_row",
                row,
                row.get("status") or "PASS",
                "Observed actual concurrency summary; keep separate from configured concurrency.",
            )
        )
    for index, row in enumerate(tables.get("request_stage_summary", []), start=1):
        rows.append(
            observation_row(
                f"obs.lifecycle.{index}",
                "lifecycle",
                "03_analysis.request_stage_summary",
                "request_stage_row",
                row,
                row.get("status") or "PASS",
                "Request lifecycle stage summary with validation status.",
            )
        )
    if len(rows) == 2:
        rows.append(
            observation_row(
                "obs.none",
                "overview",
                "analysis_bundle",
                "basic_analysis_outputs",
                None,
                "PARTIAL",
                "No basic analysis rows were available beyond validation summaries.",
            )
        )
    return rows


def observation_row(
    observation_id: str,
    module: str,
    source_table: str,
    subject: str,
    value: Any,
    status: Any,
    statement: str,
) -> dict[str, Any]:
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "observation_id": observation_id,
        "module": module,
        "source_table": source_table,
        "subject": subject,
        "value": value,
        "status": status,
        "conclusion_class": "OBSERVED_FACT",
        "statement": statement,
    }


def anomaly_register_rows(ready: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    module_status = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    for module, status in sorted(module_status.items()):
        if status not in {"PASS", "SKIPPED", "NOT_APPLICABLE"}:
            rows.append(
                anomaly_row(
                    f"anom.quality.{module}",
                    "data_quality",
                    "02_validation.analysis_ready",
                    f"{module} status is {status}.",
                    status,
                    "Repair or collect missing evidence before drawing stronger conclusions for this module.",
                )
            )
    rows.extend(negative_scaling_rows(tables.get("throughput_summary", [])))
    rows.extend(workload_drift_rows(tables.get("workload_drift", [])))
    if not rows:
        rows.append(
            anomaly_row(
                "anom.none",
                "overview",
                "analysis_bundle",
                "No deterministic basic-analysis anomaly was generated.",
                "PASS",
                None,
            )
        )
    return rows


def anomaly_row(
    anomaly_id: str,
    module: str,
    evidence_source: str,
    statement: str,
    status: Any,
    suggested_basic_action: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "anomaly_id": anomaly_id,
        "module": module,
        "evidence_source": evidence_source,
        "statement": statement,
        "status": status,
        "requires_followup": bool(suggested_basic_action),
        "suggested_basic_action": suggested_basic_action,
    }


def negative_scaling_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if float_or_none(row.get("configured_concurrency")) is not None
        and first_numeric_metric(row) is not None
    ]
    candidates.sort(key=lambda item: float_or_none(item.get("configured_concurrency")) or 0.0)
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in candidates:
        metric_name, metric_value = first_numeric_metric(row) or (None, None)
        if previous is not None and metric_name:
            _, previous_value = first_numeric_metric(previous) or (None, None)
            if previous_value is not None and metric_value is not None and metric_value < previous_value:
                result.append(
                    anomaly_row(
                        f"anom.scaling.{row.get('configured_concurrency')}",
                        "throughput",
                        "03_analysis.throughput_summary",
                        f"{metric_name} decreased at a higher configured load level.",
                        row.get("status") or "PARTIAL",
                        "Confirm load-level comparability and inspect existing throughput/lifecycle evidence.",
                    )
                )
        previous = row
    return result


def first_numeric_metric(row: dict[str, Any]) -> tuple[str, float] | None:
    preferred = (
        "whole_workload_generation_tok_s",
        "generation_tok_s",
        "task_throughput_per_hour",
        "request_throughput_per_second",
        "throughput",
    )
    for name in preferred:
        value = float_or_none(row.get(name))
        if value is not None:
            return name, value
    return None


def workload_drift_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        status = str(row.get("status") or "")
        if status not in {"PASS", "SKIPPED", "NOT_APPLICABLE"}:
            result.append(
                anomaly_row(
                    f"anom.workload_drift.{index}",
                    "workload",
                    "03_analysis.workload_drift",
                    "Workload drift table contains a non-PASS row.",
                    row.get("status") or "PARTIAL",
                    "Review task comparability before paired or fixed-effect comparisons.",
                )
            )
    return result


def contradiction_register_rows(ready: dict[str, Any], forbidden: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    forbidden_rows = forbidden if isinstance(forbidden, list) else []
    for index, item in enumerate(forbidden_rows, start=1):
        statement = item.get("statement") if isinstance(item, dict) else str(item)
        rows.append(
            {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "contradiction_id": f"contra.forbidden.{index}",
                "source": "04_evidence.forbidden_conclusions",
                "statement": statement,
                "status": "ACTIVE_GUARDRAIL",
                "resolution": "Do not emit this conclusion in basic reports.",
            }
        )
    if not rows:
        for index, statement in enumerate(ready.get("forbidden_conclusions", []) if isinstance(ready.get("forbidden_conclusions"), list) else [], start=1):
            rows.append(
                {
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "contradiction_id": f"contra.ready.{index}",
                    "source": "02_validation.analysis_ready",
                    "statement": statement,
                    "status": "ACTIVE_GUARDRAIL",
                    "resolution": "Do not emit this conclusion in basic reports.",
                }
            )
    return rows or [
        {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "contradiction_id": "contra.none",
            "source": "analysis_bundle",
            "statement": "No forbidden-conclusion guardrail rows were available.",
            "status": "PARTIAL",
            "resolution": "Keep validation status visible.",
        }
    ]


def unresolved_question_rows(ready: dict[str, Any], evidence_unresolved: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    unresolved_rows = evidence_unresolved if isinstance(evidence_unresolved, list) else []
    for index, item in enumerate(unresolved_rows, start=1):
        if isinstance(item, dict):
            rows.append(
                {
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "question_id": item.get("question_id") or f"unresolved.evidence.{index}",
                    "source": "04_evidence.unresolved_questions",
                    "question": item.get("question") or item.get("statement"),
                    "blocking_evidence": item.get("missing_evidence") or item.get("evidence_gap"),
                    "status": item.get("status") or "UNRESOLVED",
                }
            )
    for index, question in enumerate(ready.get("unresolved_inputs", []) if isinstance(ready.get("unresolved_inputs"), list) else [], start=1):
        rows.append(
            {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "question_id": f"unresolved.input.{index}",
                "source": "02_validation.analysis_ready",
                "question": question,
                "blocking_evidence": question,
                "status": "UNRESOLVED",
            }
        )
    for index, followup in enumerate(ready.get("required_followup_experiments", []) if isinstance(ready.get("required_followup_experiments"), list) else [], start=1):
        rows.append(
            {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "question_id": f"unresolved.followup.{index}",
                "source": "02_validation.analysis_ready",
                "question": followup,
                "blocking_evidence": followup,
                "status": "UNRESOLVED",
            }
        )
    return rows or [
        {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "question_id": "unresolved.none",
            "source": "analysis_bundle",
            "question": "No unresolved basic-analysis question was generated.",
            "blocking_evidence": None,
            "status": "PASS",
        }
    ]


def analysis_backlog_rows(unresolved_rows: list[dict[str, Any]], anomaly_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(unresolved_rows, start=1):
        if row.get("status") == "PASS":
            continue
        rows.append(
            {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "backlog_id": f"backlog.unresolved.{index}",
                "source_id": row.get("question_id"),
                "item_type": "missing_or_unresolved_evidence",
                "scope": "basic_analysis",
                "status": "NOT_PLANNED",
                "suggested_next_action": "Collect missing evidence and rerun the standard analysis pipeline.",
            }
        )
    for index, row in enumerate(anomaly_rows, start=1):
        if row.get("status") == "PASS":
            continue
        rows.append(
            {
                "schema_version": HANDOFF_SCHEMA_VERSION,
                "backlog_id": f"backlog.anomaly.{index}",
                "source_id": row.get("anomaly_id"),
                "item_type": "basic_anomaly_review",
                "scope": "basic_analysis",
                "status": "NOT_PLANNED",
                "suggested_next_action": row.get("suggested_basic_action") or "Review existing basic-analysis evidence.",
            }
        )
    return rows or [
        {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "backlog_id": "backlog.none",
            "source_id": None,
            "item_type": "none",
            "scope": "basic_analysis",
            "status": "PASS",
            "suggested_next_action": None,
        }
    ]
