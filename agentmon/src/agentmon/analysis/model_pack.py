from __future__ import annotations

from pathlib import Path
import csv
import json
from typing import Any

from agentmon.analysis.io import hardlink_or_copy, read_bundle_table, read_json, write_json, write_markdown
from agentmon.analysis.claims import infer_claim_module
from agentmon.analysis.schemas import metric_dictionary_markdown
from agentmon.paths import open_private_text


PACK_FILES = (
    ("manifest/experiment_manifest.json", "experiment_manifest.json"),
    ("00_inventory/experiment_contract.json", "experiment_contract.json"),
    ("00_inventory/expected_artifacts.json", "expected_artifacts.json"),
    ("02_validation/validation_status.json", "validation_status.json"),
    ("02_validation/analysis_ready.json", "analysis_ready.json"),
    ("02_validation/data_quality_gate.json", "data_quality_gate.json"),
    ("02_semantics/semantics_manifest.json", "semantics_manifest.json"),
    ("05_handoff/handoff_manifest.json", "handoff_manifest.json"),
    ("04_evidence/verified_facts.json", "verified_facts.json"),
    ("04_evidence/statistical_associations.json", "statistical_associations.json"),
    ("04_evidence/mechanism_inferences.json", "mechanism_inferences.json"),
    ("04_evidence/unresolved_questions.json", "unresolved_questions.json"),
    ("04_evidence/excluded_hypotheses.json", "excluded_hypotheses.json"),
    ("04_evidence/allowed_conclusions.json", "allowed_conclusions.json"),
    ("04_evidence/forbidden_conclusions.json", "forbidden_conclusions.json"),
    ("04_evidence/claim_registry.json", "claim_registry.json"),
)

PACK_TABLES = (
    ("01_normalized/requests", "request_details"),
    ("01_normalized/request_lifecycle", "request_lifecycle_details"),
    ("01_normalized/request_batch_membership", "request_batch_membership"),
    ("01_normalized/scheduler_iterations", "scheduler_iteration_details"),
    ("01_normalized/kv_block_events", "kv_block_events"),
    ("01_normalized/tool_calls", "tool_call_details"),
    ("01_normalized/error_events", "error_event_details"),
    ("01_normalized/task_results", "task_result_details"),
    ("00_inventory/research_questions", "research_questions"),
    ("00_inventory/contract_validation", "contract_validation"),
    ("02_semantics/metric_dictionary", "metric_dictionary"),
    ("02_semantics/entity_set_definitions", "entity_set_definitions"),
    ("02_semantics/clock_domain_audit", "clock_domain_audit"),
    ("02_validation/conservation_checks", "conservation_checks"),
    ("03_analysis/concurrency_summary", "concurrency_summary"),
    ("03_analysis/throughput_summary", "throughput_summary"),
    ("03_analysis/request_stage_summary", "request_stage_summary"),
    ("03_analysis/workload_service_decomposition", "workload_decomposition"),
    ("03_analysis/pipeline_summary", "pipeline_summary"),
    ("03_analysis/completion_tail", "tail_summary"),
    ("03_analysis/scheduler_summary", "scheduler_summary"),
    ("03_analysis/resource_summary", "resource_summary"),
    ("03_analysis/npu_mapping_evidence", "npu_mapping_evidence"),
    ("03_analysis/token_timeseries", "token_timeseries"),
    ("03_analysis/p1_capture_summary", "p1_capture_summary"),
    ("03_analysis/p1_capture_audit", "p1_capture_audit"),
    ("03_analysis/p2_match_audit", "p2_gate_summary"),
    ("03_analysis/tail_task_audit", "task_outliers"),
    ("04_evidence/evidence_index", "evidence_index"),
    ("04_evidence/claim_registry", "claim_registry"),
    ("05_handoff/observation_register", "observation_register"),
    ("05_handoff/anomaly_register", "anomaly_register"),
    ("05_handoff/contradiction_register", "contradiction_register"),
    ("05_handoff/unresolved_question_register", "unresolved_question_register"),
    ("05_handoff/analysis_backlog", "analysis_backlog"),
)

MODULE_TABLES = {
    "overview": ("throughput_summary",),
    "throughput": ("throughput_summary", "steady_state_throughput"),
    "concurrency": ("concurrency_summary",),
    "lifecycle": ("request_stage_summary", "token_normalized_performance"),
    "p1_p2": ("p1_capture_summary", "p2_match_audit", "publishability_gap_mapping"),
    "pipeline": ("pipeline_summary", "worker_utilization", "agentmon_overhead"),
    "scheduler": ("scheduler_summary", "batching_efficiency", "kv_analysis"),
    "workload_path": ("workload_service_decomposition", "workload_drift"),
    "tail": ("completion_tail", "tail_task_audit"),
    "resources": ("resource_summary", "npu_mapping_evidence", "npu_balance"),
}


def build_model_input_pack(
    *,
    bundle_root: str | Path,
    output_root: str | Path | None = None,
    include_modules: bool = True,
    max_module_rows: int = 500,
    max_module_bytes: int = 5 * 1024 * 1024,
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    output = Path(output_root).expanduser() if output_root else bundle / "model_input_pack"
    output.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source_rel, dest_name in PACK_FILES:
        source = bundle / source_rel
        if source.exists():
            hardlink_or_copy(source, output / dest_name)
            copied.append(dest_name)
    semantics_metric_dictionary = bundle / "02_semantics" / "metric_dictionary.md"
    if semantics_metric_dictionary.is_file():
        hardlink_or_copy(semantics_metric_dictionary, output / "metric_dictionary.md")
    else:
        write_markdown(output / "metric_dictionary.md", metric_dictionary_markdown("agentmon_default"))
    copied.append("metric_dictionary.md")
    for stem_rel, dest_stem in PACK_TABLES:
        source = first_existing_table(bundle / stem_rel)
        if source:
            dest = output / f"{dest_stem}{''.join(source.suffixes)}"
            hardlink_or_copy(source, dest)
            copied.append(dest.name)
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    for image in (bundle / "figures").glob("*.png") if (bundle / "figures").exists() else []:
        hardlink_or_copy(image, figures / image.name)
    write_markdown(output / "README.md", model_pack_readme())
    write_markdown(output / "report_prompt.md", report_prompt())
    module_manifest = build_module_packs(
        bundle,
        output,
        max_rows=max_module_rows,
        max_bytes=max_module_bytes,
    ) if include_modules else {"status": "NOT_APPLICABLE", "reason": "disabled_by_config", "modules": []}
    manifest = {
        "schema_version": "1.0",
        "model_pack_path": str(output),
        "source_bundle": str(bundle),
        "files": sorted(copied + ["README.md", "report_prompt.md", "figures/"]),
        "status": "PASS" if all((output / name).exists() for name in ("analysis_ready.json", "verified_facts.json", "claim_registry.json")) else "PARTIAL",
        "module_pack_status": module_manifest.get("status"),
        "modules": module_manifest.get("modules", []),
    }
    write_json(output / "model_pack_manifest.json", manifest)
    return manifest


def build_module_packs(
    bundle: Path,
    output: Path,
    *,
    max_rows: int,
    max_bytes: int,
) -> dict[str, Any]:
    modules_root = output / "modules"
    modules_root.mkdir(parents=True, exist_ok=True)
    ready = read_json(bundle / "02_validation" / "analysis_ready.json", default={})
    facts = read_json(bundle / "04_evidence" / "verified_facts.json", default=[])
    associations = read_json(bundle / "04_evidence" / "statistical_associations.json", default=[])
    inferences = read_json(bundle / "04_evidence" / "mechanism_inferences.json", default=[])
    unresolved = read_json(bundle / "04_evidence" / "unresolved_questions.json", default=[])
    allowed = read_json(bundle / "04_evidence" / "allowed_conclusions.json", default=[])
    forbidden = read_json(bundle / "04_evidence" / "forbidden_conclusions.json", default=[])
    evidence_index = read_bundle_table(bundle, "04_evidence", "evidence_index")
    manifests: list[dict[str, Any]] = []
    for module, tables in MODULE_TABLES.items():
        target = modules_root / module
        target.mkdir(parents=True, exist_ok=True)
        module_status = module_status_value(ready, module)
        scoped_ready = {**ready, "module": module, "module_pack_status": module_status}
        write_json(target / "validation_status.json", scoped_ready)
        write_json(target / "verified_facts.json", filter_evidence(facts, module))
        write_json(target / "statistical_associations.json", filter_evidence(associations, module))
        write_json(target / "mechanism_inferences.json", filter_evidence(inferences, module))
        write_json(target / "unresolved_questions.json", filter_evidence(unresolved, module, include_overview=True))
        write_json(target / "allowed_conclusions.json", allowed)
        write_json(target / "forbidden_conclusions.json", forbidden)
        write_markdown(target / "metric_dictionary.md", metric_dictionary_markdown("agentmon_default"))
        summary_rows: list[dict[str, Any]] = []
        for table in tables:
            for row in read_bundle_table(bundle, "03_analysis", table):
                summary_rows.append({"source_table": table, **row})
                if len(summary_rows) >= max_rows:
                    break
            if len(summary_rows) >= max_rows:
                break
        write_plain_csv(target / "summary.csv", summary_rows)
        scoped_index = [row for row in evidence_index if infer_claim_module(str(row.get("evidence_id") or ""), row) in {module, "overview"}]
        write_plain_csv(target / "evidence_index.csv", scoped_index[:max_rows])
        write_markdown(target / "README.md", module_readme(module, module_status, len(summary_rows)))
        write_markdown(target / "report_prompt.md", module_report_prompt(module))
        size_bytes = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
        status = "PASS" if size_bytes <= max_bytes else "PARTIAL"
        manifests.append(
            {
                "module": module,
                "path": str(target),
                "status": status,
                "validation_status": module_status,
                "summary_rows": len(summary_rows),
                "size_bytes": size_bytes,
                "max_bytes": max_bytes,
                "reason": None if status == "PASS" else "module_pack_size_limit_exceeded",
            }
        )
    status = "PASS" if all(row["status"] == "PASS" for row in manifests) else "PARTIAL"
    payload = {"schema_version": "1.0", "status": status, "modules": manifests}
    write_json(modules_root / "module_pack_manifest.json", payload)
    return payload


def filter_evidence(items: Any, module: str, *, include_overview: bool = False) -> list[dict[str, Any]]:
    rows = [row for row in items if isinstance(row, dict)] if isinstance(items, list) else []
    selected = [row for row in rows if infer_claim_module(evidence_id(row), row) == module]
    if include_overview:
        selected.extend(row for row in rows if infer_claim_module(evidence_id(row), row) == "overview" and row not in selected)
    return selected


def evidence_id(row: dict[str, Any]) -> str:
    for key in ("fact_id", "association_id", "inference_id", "question_id", "hypothesis_id", "evidence_id"):
        if row.get(key):
            return str(row[key])
    return "unknown"


def module_status_value(ready: dict[str, Any], module: str) -> str:
    statuses = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    mapping = {
        "overview": "inventory",
        "lifecycle": "lifecycle",
        "p1_p2": "p2_audit",
        "pipeline": "pipeline_reconciliation",
        "scheduler": "token_timeseries",
        "resources": "resource",
    }
    return str(statuses.get(mapping.get(module, "inventory")) or ready.get("overall_status") or "PARTIAL")


def write_plain_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({str(key) for row in rows for key in row})
    with open_private_text(path, newline="") as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key)) for key in fields})


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def module_readme(module: str, status: str, row_count: int) -> str:
    return f"""# {module} Module Pack

Validation status: `{status}`

Summary rows: {row_count}

This directory is self-contained deterministic input for one report module. Raw logs are intentionally excluded.
"""


def module_report_prompt(module: str) -> str:
    return f"""# {module} Module Report Prompt

Task scope: analyze only the `{module}` module using files in this directory.

Rules:

1. Do not read raw JSONL or files outside this module.
2. Do not recalculate metrics or change metric definitions.
3. Keep PARTIAL, FAIL, and unresolved states explicit.
4. Describe statistical associations as associations, not causes.
5. Do not emit any forbidden conclusion or superseded claim.
6. Cite evidence IDs for every important number.
7. Answer at most five material questions.

Output sections: problem, data scope, verified facts, statistical associations, mechanism candidates, alternatives, unresolved questions, engineering actions, next experiment, status.
"""


def first_existing_table(stem: Path) -> Path | None:
    for suffix in (".parquet", ".csv.gz", ".csv"):
        path = Path(str(stem) + suffix)
        if path.exists():
            return path
    return None


def model_pack_readme() -> str:
    return """# AgentMon Model Input Pack

This pack contains deterministic analysis outputs for report generation.

Rules:

- Use only files in this pack.
- Do not recalculate metrics from raw JSONL.
- Treat `verified_facts.json` as observed facts.
- Treat `statistical_associations.json` as associations, not causes.
- Keep `mechanism_inferences.json` cautious and include alternatives.
- Do not turn unresolved questions into conclusions.
- Do not write any statement listed in `forbidden_conclusions.json`.
"""


def report_prompt() -> str:
    return """# Report Generation Prompt

You are writing an AgentMon coding-agent load experiment report from this `model_input_pack`.

Hard constraints:

1. Use only module summaries, `claim_registry.json`, the global metric dictionary, and validation status in this model pack.
2. Do not recalculate raw metrics or change metric definitions.
3. Verified facts may be written as deterministic observations.
4. Statistical associations must be labeled as associations.
5. Mechanism inferences must retain alternative explanations and missing evidence.
6. Unresolved questions must not become conclusions.
7. Forbidden conclusions must not appear in the executive summary or final conclusions.
8. Every important number must reference `evidence_index`.
9. Keep task, request, token, run, batch, and pipeline levels separate.
10. Keep task execution outcome separate from observability quality.

Generate:

- technical deep-dive report;
- executive summary;
- ranked root-cause hypotheses;
- AgentMon improvement recommendations;
- follow-up controlled experiments.
"""
