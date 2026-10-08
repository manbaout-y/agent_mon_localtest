from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.discovery import DiscoveryResult
from agentmon.analysis.io import float_or_none, integer_or_none, read_bundle_table, write_json, write_table, write_table_manifest
from agentmon.analysis.normalize import NormalizedDataset
from agentmon.analysis.statistics import concurrency_series, mean, relative_error, time_weighted_summary
from agentmon.profile_data import profile_quality_gate


VALIDATION_TABLES = (
    "integrity_issues",
    "metric_coverage",
    "time_conservation",
    "lifecycle_conservation",
    "little_law_validation",
    "pipeline_reconciliation",
    "conservation_checks",
    "request_id_consistency",
    "request_conservation",
    "profile_detection",
    "resource_sampling_validation",
)


def validate_analysis_bundle(
    *,
    bundle_root: str | Path,
    cfg: AnalysisConfig,
    discovery: DiscoveryResult | None = None,
    normalized: NormalizedDataset | None = None,
    strict: bool = False,
    output_format: str = "parquet",
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    tables = normalized.tables if normalized else load_normalized_tables(bundle)
    inventory_status = inventory_status_from_discovery(discovery) if discovery else "PARTIAL"
    validation_rows = {
        "integrity_issues": build_integrity_issues(discovery),
        "metric_coverage": build_metric_coverage(tables),
        "time_conservation": validate_time_conservation(tables, cfg),
        "lifecycle_conservation": validate_lifecycle_conservation(tables, cfg),
        "little_law_validation": validate_little_law(tables, cfg),
        "pipeline_reconciliation": validate_pipeline(tables, cfg),
        "request_id_consistency": validate_request_ids(tables),
        "request_conservation": validate_request_conservation(tables),
        "profile_detection": validate_profile_detection(tables),
        "resource_sampling_validation": validate_resource_sampling(tables, cfg),
    }
    module_status = {
        "inventory": inventory_status,
        "schema": "PASS",
        "time_conservation": rows_status(validation_rows["time_conservation"]),
        "lifecycle": rows_status(validation_rows["lifecycle_conservation"]),
        "little_law": rows_status(validation_rows["little_law_validation"]),
        "pipeline_reconciliation": rows_status(validation_rows["pipeline_reconciliation"]),
        "resource": rows_status(validation_rows["resource_sampling_validation"]),
        "p1_capture": p1_capture_status(tables),
        "token_timeseries": token_timeseries_status(tables),
        "p2_audit": p2_status(tables, cfg),
        "request_conservation": rows_status(validation_rows["request_conservation"]),
        "profile_detection": rows_status(validation_rows["profile_detection"]),
        "npu_mapping": npu_mapping_status(tables),
    }
    overall = combine_status(module_status.values(), unresolved_ok=True)
    allowed, forbidden, unresolved, followups = conclusion_policy(module_status, tables)
    validation_rows["conservation_checks"] = build_conservation_checks(validation_rows)
    data_quality_gate = build_data_quality_gate(
        overall_status=overall,
        module_status=module_status,
        allowed=allowed,
        forbidden=forbidden,
        unresolved=unresolved,
        followups=followups,
        validation_rows=validation_rows,
    )
    status = {
        "schema_version": "1.0",
        "overall_status": overall,
        "strict": strict,
        "module_status": module_status,
        "allowed_conclusions": allowed,
        "forbidden_conclusions": forbidden,
        "unresolved_inputs": unresolved,
        "required_followup_experiments": followups,
    }
    validation_dir = bundle / "02_validation"
    artifacts: dict[str, dict[str, Any]] = {}
    for name, rows in validation_rows.items():
        artifacts[name] = write_table(validation_dir, name, rows, output_format=output_format)
    write_json(validation_dir / "validation_status.json", status)
    write_json(validation_dir / "analysis_ready.json", status)
    write_json(validation_dir / "data_quality_gate.json", data_quality_gate)
    write_table_manifest(validation_dir / "validation_manifest.json", artifacts)
    write_json(bundle / "analysis_ready.json", status)
    return status


def build_conservation_checks(validation_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    checks = (
        ("time_conservation", "run_time_budget"),
        ("lifecycle_conservation", "request_lifecycle_budget"),
        ("little_law_validation", "queueing_consistency"),
        ("pipeline_reconciliation", "batch_pipeline_budget"),
        ("request_conservation", "request_candidate_accounting"),
    )
    rows: list[dict[str, Any]] = []
    for table_name, check_type in checks:
        for index, row in enumerate(validation_rows.get(table_name, []), start=1):
            rows.append(
                {
                    "schema_version": "1.0",
                    "check_type": check_type,
                    "source_table": table_name,
                    "source_row": index,
                    "status": row.get("status") or "PARTIAL",
                    "residual_s": row.get("residual_s"),
                    "residual": row.get("residual"),
                    "relative_error": row.get("relative_error"),
                    "reason": row.get("reason"),
                    "evidence_source": row.get("evidence_source"),
                }
            )
    return rows or [{"schema_version": "1.0", "check_type": "none", "status": "PARTIAL", "reason": "no conservation validation rows"}]


def build_data_quality_gate(
    *,
    overall_status: str,
    module_status: dict[str, str],
    allowed: list[str],
    forbidden: list[str],
    unresolved: list[str],
    followups: list[str],
    validation_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    module_counts = {
        "pass": sum(1 for value in module_status.values() if value == "PASS"),
        "partial": sum(1 for value in module_status.values() if value == "PARTIAL"),
        "fail": sum(1 for value in module_status.values() if value == "FAIL"),
        "unresolved": sum(1 for value in module_status.values() if value == "UNRESOLVED"),
        "skipped": sum(1 for value in module_status.values() if value == "SKIPPED"),
    }
    row_counts = {
        name: {
            "pass": sum(1 for row in rows if row.get("status") == "PASS"),
            "partial": sum(1 for row in rows if row.get("status") == "PARTIAL"),
            "fail": sum(1 for row in rows if row.get("status") == "FAIL"),
            "unresolved": sum(1 for row in rows if row.get("status") == "UNRESOLVED"),
            "total": len(rows),
        }
        for name, rows in sorted(validation_rows.items())
    }
    blocking = [
        module
        for module, status in sorted(module_status.items())
        if status == "FAIL"
    ]
    return {
        "schema_version": "1.0",
        "gate_status": overall_status,
        "module_status": module_status,
        "module_status_counts": module_counts,
        "validation_row_status_counts": row_counts,
        "blocking_module_count": len(blocking),
        "blocking_modules": blocking,
        "partial_or_unresolved_module_count": module_counts["partial"] + module_counts["unresolved"],
        "allowed_conclusions": allowed,
        "forbidden_conclusions": forbidden,
        "unresolved_inputs": unresolved,
        "required_followup_experiments": followups,
        "limitations": gate_limitations(module_status, unresolved),
        "notes": "UNKNOWN, PARTIAL, and UNRESOLVED are preserved as data quality states; no missing evidence is imputed.",
    }


def gate_limitations(module_status: dict[str, str], unresolved: list[str]) -> list[str]:
    limitations = list(unresolved)
    for module, status in sorted(module_status.items()):
        if status in {"PARTIAL", "FAIL", "UNRESOLVED"}:
            limitations.append(f"{module} status is {status}")
    return sorted(set(limitations))


def load_normalized_tables(bundle: Path) -> dict[str, list[dict[str, Any]]]:
    names = (
        "experiments",
        "trials",
        "batches",
        "runs",
        "requests",
        "request_lifecycle",
        "worker_intervals",
        "scheduler_iterations",
        "resource_samples",
        "trajectories",
        "p1_request_attempts",
        "p2_requests",
        "request_id_aliases",
        "request_parse_audit",
        "request_parse_conservation",
        "profile_detection_audit",
    )
    return {name: read_bundle_table(bundle, "01_normalized", name) for name in names}


def build_integrity_issues(discovery: DiscoveryResult | None) -> list[dict[str, Any]]:
    if discovery is None:
        return [{"status": "PARTIAL", "issue_type": "discovery_unavailable", "details": "validate called without discovery context"}]
    rows: list[dict[str, Any]] = []
    metadata_only_paths: list[str] = []
    for missing in discovery.missing_files:
        rows.append({"status": missing.get("status") or "PARTIAL", "issue_type": "missing_file", **missing})
    for item in discovery.jsonl_integrity:
        if item.get("integrity_mode") == "metadata_only":
            metadata_only_paths.append(str(item.get("path")))
        for key in ("invalid_lines", "duplicate_event_ids", "sequence_regressions", "timestamp_regressions", "missing_required_fields"):
            value = integer_or_none(item.get(key)) or 0
            if value:
                rows.append(
                    {
                        "status": "FAIL" if key == "invalid_lines" else "PARTIAL",
                        "issue_type": key,
                        "path": item.get("path"),
                        "count": value,
                    }
                )
        if item.get("last_line_missing_newline"):
            rows.append({"status": "PARTIAL", "issue_type": "last_line_missing_newline", "path": item.get("path"), "count": 1})
    if metadata_only_paths:
        rows.append(
            {
                "status": "PARTIAL",
                "issue_type": "jsonl_integrity_metadata_only",
                "count": len(metadata_only_paths),
                "details": "full JSONL integrity scan was not requested",
                "paths_sample": metadata_only_paths[:20],
            }
        )
    if not rows:
        rows.append({"status": "PASS", "issue_type": "none", "details": "no blocking inventory issues observed"})
    return rows


def build_metric_coverage(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    metrics = {
        "runs.run_wall_s": tables.get("runs", []),
        "requests.server_e2e_s": tables.get("requests", []),
        "requests.queue_s": tables.get("requests", []),
        "requests.generation_tokens": tables.get("requests", []),
        "resources.aicore_util_pct": tables.get("resource_samples", []),
        "p2_requests.lifecycle_status": tables.get("p2_requests", []),
    }
    rows: list[dict[str, Any]] = []
    for metric, source_rows in metrics.items():
        table, field = metric.split(".", 1)
        denominator = len(source_rows)
        numerator = sum(1 for row in source_rows if row.get(field) not in {None, ""})
        rows.append(
            {
                "metric": metric,
                "table": table,
                "field": field,
                "non_null_count": numerator,
                "row_count": denominator,
                "coverage": numerator / denominator if denominator else None,
                "status": "PASS" if denominator and numerator else "PARTIAL",
            }
        )
    return rows


def validate_time_conservation(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in tables.get("runs", []):
        run_wall = float_or_none(run.get("run_wall_s"))
        model = float_or_none(run.get("model_union_s"))
        tool = float_or_none(run.get("tool_union_s"))
        overlap = float_or_none(run.get("model_tool_overlap_s")) or 0.0
        idle = float_or_none(run.get("idle_or_transition_s"))
        if run_wall is None or model is None or tool is None or idle is None:
            status = "PARTIAL"
            residual = None
            rel = None
        else:
            expected = model + tool - overlap + idle
            residual = run_wall - expected
            rel = relative_error(run_wall, expected)
            status = status_from_relative_error(
                rel,
                pass_threshold=cfg.validation.time_conservation_pass_relative_error,
                partial_threshold=cfg.validation.time_conservation_pass_relative_error * 2,
            )
        rows.append(
            {
                "run_id": run.get("run_id"),
                "run_wall_s": run_wall,
                "model_union_s": model,
                "tool_union_s": tool,
                "model_tool_overlap_s": overlap,
                "idle_or_transition_s": idle,
                "residual_s": residual,
                "relative_error": rel,
                "status": status,
            }
        )
    return rows or [{"status": "PARTIAL", "reason": "no runs"}]


def validate_lifecycle_conservation(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in tables.get("request_lifecycle", []):
        server = float_or_none(row.get("server_e2e_s"))
        parts = [
            float_or_none(row.get("queue_s")),
            float_or_none(row.get("scheduled_to_first_token_s")),
            float_or_none(row.get("first_token_to_engine_finished_s")),
            float_or_none(row.get("engine_finished_to_api_finished_s")),
        ]
        if server is None or any(part is None for part in parts):
            status = "PARTIAL"
            rel = None
            residual = None
        else:
            expected = sum(part for part in parts if part is not None)
            residual = server - expected
            rel = relative_error(server, expected)
            status = status_from_relative_error(
                rel,
                pass_threshold=cfg.validation.lifecycle_pass_relative_error,
                partial_threshold=cfg.validation.lifecycle_pass_relative_error * 2,
            )
        rows.append(
            {
                "request_attempt_id": row.get("request_attempt_id"),
                "run_id": row.get("run_id"),
                "server_e2e_s": server,
                "residual_s": residual,
                "relative_error": rel,
                "clock_domain_status": row.get("clock_domain_status"),
                "status": status,
            }
        )
    return rows or [{"status": "PARTIAL", "reason": "no request lifecycle rows"}]


def validate_little_law(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("requests", []):
        grouped[(row.get("trial_id"), row.get("batch_id"), row.get("configured_concurrency"))].append(row)
    rows: list[dict[str, Any]] = []
    for (trial_id, batch_id, concurrency), items in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        intervals = []
        e2e_values = []
        for item in items:
            start = ns_to_s(item.get("request_start"))
            end = ns_to_s(item.get("api_finished"))
            e2e = float_or_none(item.get("server_e2e_s"))
            if start is not None and end is not None and end > start:
                intervals.append((start, end))
                e2e_values.append(e2e if e2e is not None else end - start)
        if not intervals:
            rows.append({"trial_id": trial_id, "batch_id": batch_id, "configured_concurrency": concurrency, "status": "PARTIAL", "reason": "no complete request intervals"})
            continue
        window_start = min(start for start, _ in intervals)
        window_end = max(end for _, end in intervals)
        window_s = window_end - window_start
        series = concurrency_series(intervals)
        weighted = time_weighted_summary((row["start_s"], row["end_s"], row["value"]) for row in series)
        observed_l = weighted["mean"]
        lam = len(intervals) / window_s if window_s > 0 else None
        w = mean(e2e_values)
        predicted = lam * w if lam is not None and w is not None else None
        rel = relative_error(observed_l, predicted)
        rows.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "configured_concurrency": concurrency,
                "window_s": window_s,
                "request_count": len(intervals),
                "observed_time_weighted_inflight": observed_l,
                "lambda_requests_per_s": lam,
                "mean_e2e_s": w,
                "predicted_l": predicted,
                "relative_error": rel,
                "status": status_from_relative_error(
                    rel,
                    pass_threshold=cfg.validation.little_law_pass_relative_error,
                    partial_threshold=cfg.validation.little_law_partial_relative_error,
                ),
            }
        )
    return rows or [{"status": "PARTIAL", "reason": "no requests"}]


def validate_pipeline(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    phase_rows: list[dict[str, Any]] = []
    for batch in tables.get("batches", []):
        timings = parse_json_value(batch.get("phase_timings"), {})
        validation = parse_json_value(batch.get("pipeline_phase_validation"), {})
        if not timings:
            continue
        component_sum = sum(
            value
            for value in (
                float_or_none(timings.get("preprocessing_s")),
                float_or_none(timings.get("workload_phase_s")),
                float_or_none(timings.get("p2_index_s")),
                float_or_none(timings.get("analysis_phase_s")),
                float_or_none(timings.get("evidence_build_s")),
                float_or_none(timings.get("model_pack_s")),
                float_or_none(timings.get("finalization_s")),
            )
            if value is not None
        )
        pipeline_wall = float_or_none(timings.get("pipeline_wall_s"))
        phase_rows.append(
            {
                "trial_id": batch.get("trial_id"),
                "batch_id": batch.get("batch_id"),
                "observed_batch_wall_s": pipeline_wall,
                "worker_state_union_s": component_sum,
                "residual_s": validation.get("absolute_error_s")
                if validation.get("absolute_error_s") is not None
                else (pipeline_wall - component_sum if pipeline_wall is not None else None),
                "relative_error": validation.get("relative_error"),
                "status": validation.get("status") or "PARTIAL",
                "evidence_source": "batch_phase_events" if validation or batch.get("batch_phase_events_path") else "estimated_worker_intervals",
                "timestamp_quality": validation.get("evidence_source") or ("DIRECT" if validation or batch.get("batch_phase_events_path") else "ESTIMATED"),
            }
        )
    if phase_rows:
        return phase_rows
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("worker_intervals", []):
        grouped[(row.get("trial_id"), row.get("batch_id"))].append(row)
    rows: list[dict[str, Any]] = []
    for (trial_id, batch_id), items in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        starts = [ns_to_s(item.get("start_time")) for item in items]
        ends = [ns_to_s(item.get("end_time")) for item in items]
        starts = [item for item in starts if item is not None]
        ends = [item for item in ends if item is not None]
        if not starts or not ends:
            rows.append({"trial_id": trial_id, "batch_id": batch_id, "status": "PARTIAL", "reason": "missing worker interval bounds"})
            continue
        observed = max(ends) - min(starts)
        state_coverage = max(ends) - min(starts)
        residual = observed - state_coverage
        rel = relative_error(observed, state_coverage)
        rows.append(
            {
                "trial_id": trial_id,
                "batch_id": batch_id,
                "observed_batch_wall_s": observed,
                "worker_state_union_s": state_coverage,
                "residual_s": residual,
                "relative_error": rel,
                "status": "PARTIAL",
                "evidence_source": "estimated_worker_intervals",
                "timestamp_quality": "ESTIMATED",
                "reason": "direct_pipeline_phase_timestamps_unavailable",
            }
        )
    return rows or [{"status": "PARTIAL", "reason": "no worker intervals"}]


def validate_request_ids(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    alias_to_requests: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in tables.get("request_id_aliases", []):
        alias_type = str(row.get("alias_type") or "")
        alias_value = str(row.get("alias_value") or "")
        request_id = f"{row.get('run_id')}|{row.get('request_attempt_id')}"
        if alias_type and alias_value and request_id:
            alias_to_requests[(alias_type, alias_value)].add(request_id)
    rows: list[dict[str, Any]] = []
    for (alias_type, alias_value), request_ids in sorted(alias_to_requests.items()):
        if len(request_ids) > 1:
            rows.append(
                {
                    "status": "FAIL",
                    "issue_type": "alias_maps_to_multiple_requests",
                    "alias_type": alias_type,
                    "alias_value": alias_value,
                    "request_ids": sorted(request_ids),
                }
            )
    return rows or [{"status": "PASS", "issue_type": "none"}]


def validate_request_conservation(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    audits = tables.get("request_parse_audit", [])
    rows: list[dict[str, Any]] = []
    for audit in audits:
        raw = integer_or_none(audit.get("raw_request_attempt_candidates")) or 0
        explained = sum(
            integer_or_none(audit.get(field)) or 0
            for field in ("parsed_request_attempts", "duplicates_removed", "filtered_events", "unsupported_request_candidates", "parse_errors", "unknown_loss_count")
        )
        unknown = integer_or_none(audit.get("unknown_loss_count")) or 0
        rows.append(
            {
                "run_id": audit.get("run_id"),
                "raw_request_attempt_candidates": raw,
                "explained_candidates": explained,
                "residual": raw - explained,
                "unknown_loss_count": unknown,
                "status": "PASS" if raw == explained and unknown == 0 else "FAIL",
                "reason": None if raw == explained and unknown == 0 else "request_candidate_conservation_failed",
            }
        )
    return rows or [{"status": "PARTIAL", "reason": "no request parse audit rows"}]


def validate_profile_detection(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = tables.get("profile_detection_audit", [])
    if not rows:
        return [{"status": "PARTIAL", "reason": "profile detection audit unavailable"}]
    return [
        {
            "run_id": row.get("run_id"),
            "detected_profile": row.get("detected_profile"),
            "confidence": row.get("confidence"),
            "status": "PASS" if row.get("status") == "PASS" else "PARTIAL",
            "reason": row.get("fallback_reason"),
        }
        for row in rows
    ]


def validate_resource_sampling(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> list[dict[str, Any]]:
    samples = tables.get("resource_samples", [])
    count = len(samples)
    npu_samples = [row for row in samples if row.get("aicore_util_pct") not in {None, ""} or row.get("hbm_bytes") not in {None, ""}]
    rows = [
        {
            "scope": "all_resources",
            "sample_count": count,
            "minimum_required": cfg.validation.min_resource_sample_count,
            "status": "PASS" if count >= cfg.validation.min_resource_sample_count else "PARTIAL",
        },
        {
            "scope": "npu",
            "sample_count": len(npu_samples),
            "minimum_required": cfg.validation.min_resource_sample_count,
            "npu_mapping_status": npu_mapping_status(tables),
            "status": "PASS" if len(npu_samples) >= cfg.validation.min_resource_sample_count else "PARTIAL",
        },
    ]
    return rows


def p2_status(tables: dict[str, list[dict[str, Any]]], cfg: AnalysisConfig) -> str:
    requests = tables.get("requests", [])
    eligible = [row for row in requests if truthy(row.get("p2_eligible"))]
    matched = [row for row in eligible if truthy(row.get("p2_matched"))]
    complete = [row for row in matched if truthy(row.get("lifecycle_complete"))]
    if not eligible:
        return "PARTIAL"
    match_rate = len(matched) / len(eligible)
    lifecycle_rate = len(complete) / len(matched) if matched else 0.0
    match_threshold = cfg.validation.min_p2_match_rate
    lifecycle_threshold = cfg.validation.min_p2_lifecycle_rate
    if match_threshold is None:
        match_threshold = profile_quality_gate("min_p2_match_rate")
    if lifecycle_threshold is None:
        lifecycle_threshold = profile_quality_gate("min_p2_lifecycle_rate")
    if match_threshold is None or lifecycle_threshold is None:
        return "PASS" if len(matched) == len(eligible) and len(complete) == len(matched) else "PARTIAL" if matched else "FAIL"
    if match_rate >= match_threshold and lifecycle_rate >= lifecycle_threshold:
        return "PASS"
    if matched:
        return "PARTIAL"
    return "FAIL"


def npu_mapping_status(tables: dict[str, list[dict[str, Any]]]) -> str:
    batch_levels = {
        str(parse_json_value(row.get("npu_process_mapping"), {}).get("evidence_level") or "").upper()
        for row in tables.get("batches", [])
        if parse_json_value(row.get("npu_process_mapping"), {})
    }
    if batch_levels & {"LEVEL_1", "LEVEL_2"}:
        return "PASS"
    if "LEVEL_3" in batch_levels:
        return "PARTIAL"
    samples = tables.get("resource_samples", [])
    levels = {str(row.get("mapping_confidence") or "").upper() for row in samples if row.get("mapping_confidence") not in {None, ""}}
    if levels & {"LEVEL_1", "LEVEL_2"}:
        return "PASS"
    if "LEVEL_3" in levels:
        return "PARTIAL"
    if "LEVEL_4" in levels:
        return "UNRESOLVED"
    return "UNRESOLVED"


def p1_capture_status(tables: dict[str, list[dict[str, Any]]]) -> str:
    requests = tables.get("requests", [])
    trajectories = tables.get("trajectories", [])
    if not requests and not trajectories:
        return "PARTIAL"
    if trajectories and requests:
        return "PASS"
    if requests:
        return "PARTIAL"
    return "FAIL"


def token_timeseries_status(tables: dict[str, list[dict[str, Any]]]) -> str:
    rows = tables.get("scheduler_iterations", [])
    if any(row.get("actual_generated_tokens_delta") not in {None, ""} for row in rows):
        return "PASS"
    if rows:
        return "UNRESOLVED"
    return "PARTIAL"


def conclusion_policy(module_status: dict[str, str], tables: dict[str, list[dict[str, Any]]]) -> tuple[list[str], list[str], list[str], list[str]]:
    allowed = [
        "OBSERVED_FACT: inventory counts, parsed run counts, and table row counts may be reported.",
        "OBSERVED_FACT: task, request, run, batch, and pipeline levels must remain separate.",
    ]
    forbidden: list[str] = [
        "Do not equate configured concurrency with actual model or engine concurrency.",
        "Do not treat observability failure as task execution failure.",
        "Do not treat scheduled tokens as generated tokens.",
        "Do not write statistical association as causal mechanism.",
    ]
    unresolved: list[str] = []
    followups: list[str] = []
    if module_status.get("pipeline_reconciliation") != "PASS":
        forbidden.append("Do not make precise postprocessing overhead attribution until pipeline reconciliation passes.")
        unresolved.append("pipeline worker intervals are incomplete or only partially reconstructed")
    if module_status.get("lifecycle") != "PASS":
        forbidden.append("Do not make exact request-stage contribution claims until lifecycle conservation passes.")
        unresolved.append("request lifecycle fields are incomplete or cross-clock unavailable")
    if module_status.get("little_law") != "PASS":
        forbidden.append("Do not claim queueing model consistency until Little Law validation passes.")
    if module_status.get("npu_mapping") != "PASS":
        forbidden.append("Do not confirm physical NPU mapping or hardware bottleneck from LEVEL_3/4/NONE evidence.")
        followups.append("collect PID-to-NPU or rank-to-physical-device mapping evidence")
    if module_status.get("p1_capture") not in {"PASS"}:
        forbidden.append("Do not claim complete trajectory-to-P1 model request capture until P1 capture status passes.")
        followups.append("compare raw trajectory model attempts against P1 request attempts")
    if module_status.get("token_timeseries") != "PASS":
        forbidden.append("Do not report steady-state generated-token throughput from scheduled token counters.")
        followups.append("collect actual generated token deltas in scheduler or request lifecycle samples")
    if module_status.get("p2_audit") != "PASS":
        forbidden.append("Do not make strong vLLM scheduler/KV mechanism claims until P2 coverage passes.")
        followups.append("collect complete P2 lifecycle events for successful model requests")
    if tables.get("requests"):
        allowed.append("OBSERVED_FACT: request-level metrics with non-null coverage may be reported with evidence.")
    return sorted(set(allowed)), sorted(set(forbidden)), sorted(set(unresolved)), sorted(set(followups))


def inventory_status_from_discovery(discovery: DiscoveryResult | None) -> str:
    if discovery is None:
        return "PARTIAL"
    statuses = [row.get("status") for row in discovery.experiment_inventory]
    if "FAIL" in statuses:
        return "FAIL"
    if "PARTIAL" in statuses:
        return "PARTIAL"
    return "PASS"


def rows_status(rows: list[dict[str, Any]]) -> str:
    statuses = [str(row.get("status") or "PARTIAL") for row in rows]
    return combine_status(statuses)


def combine_status(statuses: Any, *, unresolved_ok: bool = False) -> str:
    values = list(statuses)
    if "FAIL" in values:
        return "FAIL"
    if "PARTIAL" in values:
        return "PARTIAL"
    if "UNRESOLVED" in values and not unresolved_ok:
        return "PARTIAL"
    return "PASS" if values else "PARTIAL"


def status_from_relative_error(value: float | None, *, pass_threshold: float, partial_threshold: float) -> str:
    if value is None:
        return "PARTIAL"
    if value <= pass_threshold:
        return "PASS"
    if value <= partial_threshold:
        return "PARTIAL"
    return "FAIL"


def ns_to_s(value: Any) -> float | None:
    parsed = integer_or_none(value)
    if parsed is None:
        return None
    return parsed / 1_000_000_000


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "passed", "success"}


def parse_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
