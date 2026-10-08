from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.io import float_or_none, read_bundle_table, read_json, write_json, write_table, write_table_manifest
from agentmon.analysis.statistics import wilson_interval
from agentmon.analysis.claims import build_claim_registry


EVIDENCE_TABLES = (
    "evidence_index",
)


def build_evidence_bundle(
    *,
    bundle_root: str | Path,
    output_format: str = "parquet",
    include_claim_registry: bool = True,
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    manifest = read_json(bundle / "manifest" / "experiment_manifest.json", default={})
    ready = read_json(bundle / "02_validation" / "analysis_ready.json", default={})
    runs = read_bundle_table(bundle, "01_normalized", "runs")
    requests = read_bundle_table(bundle, "01_normalized", "requests")
    throughput = read_bundle_table(bundle, "03_analysis", "throughput_summary")
    concurrency = read_bundle_table(bundle, "03_analysis", "concurrency_summary")
    p2_audit = read_bundle_table(bundle, "03_analysis", "p2_match_audit")
    p1_summary = read_bundle_table(bundle, "03_analysis", "p1_capture_summary")
    token_timeseries = read_bundle_table(bundle, "03_analysis", "token_timeseries")
    facts = verified_facts(manifest, ready, runs, requests, throughput, p1_summary, token_timeseries)
    associations = statistical_associations(concurrency, throughput)
    inferences = mechanism_inferences(ready)
    unresolved = unresolved_questions(ready)
    excluded = excluded_hypotheses(ready)
    allowed = [{"statement": item} for item in ready.get("allowed_conclusions", [])]
    forbidden = [{"statement": item} for item in ready.get("forbidden_conclusions", [])]
    evidence_index = build_evidence_index(facts, associations, inferences, p2_audit)
    out = bundle / "04_evidence"
    write_json(out / "verified_facts.json", facts)
    write_json(out / "statistical_associations.json", associations)
    write_json(out / "mechanism_inferences.json", inferences)
    write_json(out / "unresolved_questions.json", unresolved)
    write_json(out / "excluded_hypotheses.json", excluded)
    write_json(out / "allowed_conclusions.json", allowed)
    write_json(out / "forbidden_conclusions.json", forbidden)
    artifact = write_table(out, "evidence_index", evidence_index, output_format=output_format)
    write_table_manifest(out / "evidence_manifest.json", {"evidence_index": artifact})
    write_json(
        bundle / "03_analysis" / "publishability_gate_audit.json",
        {
            "schema_version": "1.0",
            "p2_audit_rows": len(p2_audit),
            "p2_module_status": ready.get("module_status", {}).get("p2_audit"),
            "notes": "P2 eligibility is evidence-based. Error requests enter the denominator when request identity or service-side evidence exists.",
        },
    )
    claim_registry = build_claim_registry(bundle) if include_claim_registry else {"status": "NOT_APPLICABLE", "reason": "disabled_by_config"}
    return {
        "schema_version": "1.0",
        "fact_count": len(facts),
        "association_count": len(associations),
        "mechanism_inference_count": len(inferences),
        "unresolved_question_count": len(unresolved),
        "forbidden_conclusion_count": len(forbidden),
        "evidence_index_count": len(evidence_index),
        "claim_registry_status": claim_registry.get("status"),
        "claim_registry": claim_registry,
    }


def verified_facts(
    manifest: dict[str, Any],
    ready: dict[str, Any],
    runs: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    throughput: list[dict[str, Any]],
    p1_summary: list[dict[str, Any]],
    token_timeseries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = [
        fact(
            "fact.experiment.run_count",
            f"Parsed {len(runs)} run rows from the experiment.",
            len(runs),
            "run",
            "experiment",
            ["01_normalized/runs"],
            ready,
        ),
        fact(
            "fact.experiment.request_attempt_count",
            f"Parsed {len(requests)} model request attempt rows.",
            len(requests),
            "request",
            "request",
            ["01_normalized/requests"],
            ready,
        ),
    ]
    for row in throughput:
        if row.get("whole_workload_generation_tok_s") not in {None, ""}:
            facts.append(
                fact(
                    f"fact.throughput.{row.get('trial_id')}.{row.get('batch_id')}",
                    "Whole-workload generation throughput was computed deterministically.",
                    float_or_none(row.get("whole_workload_generation_tok_s")),
                    "token/s",
                    "batch",
                    ["03_analysis/throughput_summary"],
                    ready,
                    time_window="whole_workload",
                )
            )
    if manifest.get("configured_concurrency"):
        facts.append(
            fact(
                "fact.experiment.configured_concurrency_values",
                "Configured concurrency values were discovered from manifests or inferred path/config evidence.",
                manifest.get("configured_concurrency"),
                "count",
                "experiment",
                ["manifest/experiment_manifest.json"],
                ready,
            )
        )
    for row in p1_summary:
        facts.append(
            fact(
                "fact.p1.capture_rate",
                "P1 capture audit compared raw trajectory model attempts with normalized P1 request rows.",
                row.get("p1_capture_rate"),
                "ratio",
                "request",
                ["03_analysis/p1_capture_summary"],
                ready,
            )
        )
    actual_token_rows = [row for row in token_timeseries if row.get("actual_generated_tokens_delta") not in {None, ""}]
    if actual_token_rows:
        facts.append(
            fact(
                "fact.token_timeseries.actual_generated_delta_rows",
                "Actual generated token delta rows are available for steady-state generated-token analysis.",
                len(actual_token_rows),
                "row",
                "scheduler_iteration",
                ["03_analysis/token_timeseries"],
                ready,
            )
        )
    return facts


def statistical_associations(concurrency: list[dict[str, Any]], throughput: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if len(throughput) > 1:
        rows.append(
            {
                "association_id": "assoc.configured_concurrency.throughput",
                "statement": "Throughput and configured concurrency can be compared as a statistical association, not a causal claim.",
                "variables": ["configured_concurrency", "whole_workload_generation_tok_s"],
                "sample_count": len(throughput),
                "confidence": "low" if len(throughput) < 3 else "medium",
                "evidence_files": ["03_analysis/throughput_summary"],
                "conclusion_class": "STATISTICAL_ASSOCIATION",
            }
        )
    if concurrency:
        rows.append(
            {
                "association_id": "assoc.configured.actual_concurrency",
                "statement": "Configured concurrency and observed active concurrency are reported separately.",
                "variables": ["configured_concurrency", "active_model_spans"],
                "sample_count": len(concurrency),
                "confidence": "medium",
                "evidence_files": ["03_analysis/concurrency_summary"],
                "conclusion_class": "STATISTICAL_ASSOCIATION",
            }
        )
    return rows


def mechanism_inferences(ready: dict[str, Any]) -> list[dict[str, Any]]:
    module_status = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    if module_status.get("p2_audit") == "PASS" and module_status.get("npu_mapping") == "PASS":
        return [
            {
                "inference_id": "infer.scheduler_resource_ready",
                "statement": "Scheduler/resource mechanism analysis has enough P2 and NPU mapping evidence for cautious mechanism hypotheses.",
                "supporting_evidence": ["02_validation/analysis_ready.json"],
                "alternative_explanations": ["task mix", "host supply", "token length mix", "tool interleaving"],
                "missing_evidence": [],
                "required_control_experiment": "Disable/alter one mechanism at a time before claiming causality.",
                "confidence": "medium",
                "conclusion_class": "MECHANISM_INFERENCE",
            }
        ]
    return []


def unresolved_questions(ready: dict[str, Any]) -> list[dict[str, Any]]:
    unresolved = ready.get("unresolved_inputs", [])
    rows = [
        {
            "question_id": f"unresolved.{index}",
            "statement": str(item),
            "required_evidence": "See analysis_ready.required_followup_experiments",
            "conclusion_class": "UNRESOLVED_HYPOTHESIS",
        }
        for index, item in enumerate(unresolved, start=1)
    ]
    if not rows:
        rows.append({"question_id": "unresolved.none", "statement": "No unresolved input blocker was recorded.", "conclusion_class": "OBSERVED_FACT"})
    return rows


def excluded_hypotheses(ready: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "hypothesis_id": f"excluded.{index}",
            "statement": item,
            "reason": "Forbidden by analysis readiness policy.",
            "conclusion_class": "UNRESOLVED_HYPOTHESIS",
        }
        for index, item in enumerate(ready.get("forbidden_conclusions", []), start=1)
    ]


def build_evidence_index(
    facts: list[dict[str, Any]],
    associations: list[dict[str, Any]],
    inferences: list[dict[str, Any]],
    p2_audit: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        rows.append(index_row(item.get("fact_id"), item.get("statement"), item.get("evidence_files"), item.get("conclusion_class")))
    for item in associations:
        rows.append(index_row(item.get("association_id"), item.get("statement"), item.get("evidence_files"), item.get("conclusion_class")))
    for item in inferences:
        rows.append(index_row(item.get("inference_id"), item.get("statement"), item.get("supporting_evidence"), item.get("conclusion_class")))
    for index, item in enumerate(p2_audit, start=1):
        rows.append(index_row(f"p2.audit.{index}", item.get("root_cause"), ["03_analysis/p2_match_audit"], "OBSERVED_FACT"))
    return rows


def fact(
    fact_id: str,
    statement: str,
    value: Any,
    unit: str,
    aggregation_level: str,
    evidence_files: list[str],
    ready: dict[str, Any],
    *,
    time_window: str = "experiment",
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "statement": statement,
        "value": value,
        "unit": unit,
        "aggregation_level": aggregation_level,
        "time_window": time_window,
        "confidence": "high",
        "evidence_files": evidence_files,
        "source_rows": [],
        "validator_status": ready.get("overall_status"),
        "conclusion_class": "OBSERVED_FACT",
    }


def index_row(item_id: Any, statement: Any, evidence_files: Any, conclusion_class: Any) -> dict[str, Any]:
    return {
        "evidence_id": item_id,
        "statement": statement,
        "evidence_files": evidence_files or [],
        "conclusion_class": conclusion_class,
    }
