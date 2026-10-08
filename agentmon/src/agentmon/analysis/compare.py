from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.io import float_or_none, read_bundle_table, read_json, write_json, write_markdown, write_table, write_table_manifest


def compare_bundles(
    *,
    bundle_paths: list[str | Path],
    output_root: str | Path,
    output_format: str = "parquet",
) -> dict[str, Any]:
    if len(bundle_paths) < 2:
        raise ValueError("analysis compare requires at least two --bundle values")
    bundles = [Path(path).expanduser().resolve() for path in bundle_paths]
    output = Path(output_root).expanduser().resolve()
    manifests = [read_json(bundle / "manifest" / "experiment_manifest.json", default={}) for bundle in bundles]
    ready = [read_json(bundle / "02_validation" / "analysis_ready.json", default={}) for bundle in bundles]
    throughput = [read_bundle_table(bundle, "03_analysis", "throughput_summary") for bundle in bundles]
    comparable, non_comparable = compare_dimensions(manifests)
    normalized_deltas = build_normalized_deltas(bundles, throughput)
    paired_deltas = build_paired_deltas(bundles)
    warnings = build_warnings(non_comparable, ready)
    artifacts = {
        "normalized_deltas": write_table(output, "normalized_deltas", normalized_deltas, output_format=output_format),
        "paired_deltas": write_table(output, "paired_deltas", paired_deltas, output_format=output_format),
        "warnings": write_table(output, "warnings", warnings, output_format=output_format),
    }
    summary = {
        "schema_version": "1.0",
        "bundle_count": len(bundles),
        "bundles": [str(bundle) for bundle in bundles],
        "comparable_dimensions": comparable,
        "non_comparable_dimensions": non_comparable,
        "normalized_delta_count": len(normalized_deltas),
        "paired_delta_count": len(paired_deltas),
        "warnings": warnings,
        "status": "PASS" if not non_comparable else "PARTIAL",
    }
    write_json(output / "comparison_summary.json", summary)
    write_markdown(output / "comparison_report.md", comparison_markdown(summary))
    write_table_manifest(output / "comparison_manifest.json", artifacts)
    return summary


def compare_dimensions(manifests: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    dimensions = [
        "task_set_sha256",
        "model_name",
        "serving_framework",
        "serving_framework_version",
        "tensor_parallel",
        "pipeline_parallel",
        "device_visibility",
        "agentmon_version",
    ]
    comparable: list[str] = []
    non_comparable: list[dict[str, Any]] = []
    for key in dimensions:
        values = [manifest.get(key) for manifest in manifests]
        compact = sorted({str(value) for value in values if value is not None and value != "" and value != []})
        if len(compact) <= 1:
            comparable.append(key)
        else:
            non_comparable.append({"dimension": key, "values": compact, "warning": "dimension differs across bundles"})
    return comparable, non_comparable


def comparison_key(row: dict[str, Any], *, paired: bool) -> tuple[str, ...]:
    concurrency = float_or_none(row.get("configured_concurrency"))
    key = (
        str(row.get("trial_id") or "trial-0"),
        str(concurrency) if concurrency is not None else "UNKNOWN",
    )
    return (*key, str(row.get("task_id") or "")) if paired else key


def comparison_index(table: list[dict[str, Any]], *, paired: bool) -> dict[tuple[str, ...], dict[str, Any]]:
    index: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in table:
        key = comparison_key(row, paired=paired)
        if key in index:
            raise ValueError(f"ambiguous comparison key {key}; provide distinct trial/concurrency metadata")
        index[key] = row
    return index


def build_normalized_deltas(bundles: list[Path], throughput_tables: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not throughput_tables or not throughput_tables[0]:
        return []
    baseline_by_key = comparison_index(throughput_tables[0], paired=False)
    rows: list[dict[str, Any]] = []
    for bundle_index, table in enumerate(throughput_tables[1:], start=1):
        for key, row in comparison_index(table, paired=False).items():
            base = baseline_by_key.get(key)
            if not base:
                continue
            for metric in ("task_throughput_per_hour", "request_throughput_per_second", "whole_workload_generation_tok_s"):
                rows.append(
                    {
                        "baseline_bundle": str(bundles[0]),
                        "target_bundle": str(bundles[bundle_index]),
                        "trial_id": key[0],
                        "configured_concurrency": row.get("configured_concurrency"),
                        "metric": metric,
                        "baseline_value": base.get(metric),
                        "target_value": row.get(metric),
                        "delta": delta(row.get(metric), base.get(metric)),
                        "relative_delta": relative_delta(row.get(metric), base.get(metric)),
                    }
                )
    return rows


def build_paired_deltas(bundles: list[Path]) -> list[dict[str, Any]]:
    if len(bundles) < 2:
        return []
    baseline_runs = read_bundle_table(bundles[0], "01_normalized", "runs")
    base_by_task = comparison_index([row for row in baseline_runs if row.get("task_id")], paired=True)
    rows: list[dict[str, Any]] = []
    for target in bundles[1:]:
        target_runs = [row for row in read_bundle_table(target, "01_normalized", "runs") if row.get("task_id")]
        for key, row in comparison_index(target_runs, paired=True).items():
            task_id = str(row.get("task_id") or "")
            base = base_by_task.get(key)
            if not base:
                continue
            rows.append(
                {
                    "task_id": task_id,
                    "trial_id": key[0],
                    "configured_concurrency": row.get("configured_concurrency"),
                    "baseline_run_id": base.get("run_id"),
                    "target_run_id": row.get("run_id"),
                    "run_wall_delta_s": delta(row.get("run_wall_s"), base.get("run_wall_s")),
                    "request_count_delta": delta(row.get("model_request_attempt_count"), base.get("model_request_attempt_count")),
                    "prompt_token_delta": delta(row.get("prompt_tokens_sum"), base.get("prompt_tokens_sum")),
                    "generation_token_delta": delta(row.get("generation_tokens_sum"), base.get("generation_tokens_sum")),
                    "conclusion_strength": "STATISTICAL_ASSOCIATION",
                }
            )
    return rows


def build_warnings(non_comparable: list[dict[str, Any]], ready: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "status": "PARTIAL",
            "warning_type": "non_comparable_dimension",
            "dimension": item.get("dimension"),
            "details": item.get("values"),
        }
        for item in non_comparable
    ]
    for index, item in enumerate(ready):
        if item.get("overall_status") != "PASS":
            rows.append(
                {
                    "status": "PARTIAL",
                    "warning_type": "bundle_not_fully_ready",
                    "bundle_index": index,
                    "overall_status": item.get("overall_status"),
                }
            )
    return rows


def delta(target: Any, baseline: Any) -> float | None:
    left = float_or_none(target)
    right = float_or_none(baseline)
    if left is None or right is None:
        return None
    return left - right


def relative_delta(target: Any, baseline: Any) -> float | None:
    left = float_or_none(target)
    right = float_or_none(baseline)
    if left is None or right in {None, 0.0}:
        return None
    return (left - right) / abs(right)


def comparison_markdown(summary: dict[str, Any]) -> str:
    return f"""# AgentMon Analysis Comparison

Status: `{summary['status']}`

- Bundles: {summary['bundle_count']}
- Comparable dimensions: {', '.join(summary['comparable_dimensions'])}
- Non-comparable dimensions: {len(summary['non_comparable_dimensions'])}
- Normalized deltas: {summary['normalized_delta_count']}
- Paired deltas: {summary['paired_delta_count']}

Do not write strong conclusions for non-comparable dimensions without additional controls.
"""
