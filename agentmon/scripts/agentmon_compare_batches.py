#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics
import sys
from typing import Any, Iterable, Sequence


METRICS = (
    "run_model_e2e_sum_s",
    "run_tool_duration_sum_s",
    "prompt_tokens",
    "generation_tokens",
    "p2_request_count",
)

METRIC_ALIASES = {
    "run_model_e2e_sum_s": "model_time_s",
    "run_tool_duration_sum_s": "tool_time_s",
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        comparison = compare_batches(
            [Path(path).expanduser().resolve() for path in args.batch_roots],
            expected_task_count=args.expected_task_count,
        )
    except Exception as exc:
        print(f"agentmon batch comparison error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_table(comparison))
    return 1 if args.strict and not comparison["comparable"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and compare AgentMon batch experiments")
    parser.add_argument("batch_roots", nargs="+", help="batch directories containing batch-manifest.json")
    parser.add_argument("--expected-task-count", type=int)
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when comparability checks fail")
    return parser


def compare_batches(batch_roots: list[Path], *, expected_task_count: int | None = None) -> dict[str, Any]:
    if not batch_roots:
        raise ValueError("at least one batch root is required")
    batches = [load_batch(path) for path in batch_roots]
    baseline = batches[0]
    checks: list[dict[str, Any]] = []
    for batch in batches:
        checks.extend(compare_to_baseline(baseline, batch, expected_task_count=expected_task_count))
    errors = [check for check in checks if check["status"] == "error"]
    warnings = [check for check in checks if check["status"] == "warning"]
    return {
        "schema_version": "1.0",
        "comparison_type": "agentmon.batch_comparison",
        "baseline": str(baseline["root"]),
        "batch_count": len(batches),
        "comparable": not errors,
        "checks": checks,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "batch_statistics": [batch_statistics(batch) for batch in batches],
        "paired_comparisons": [paired_comparison(baseline, batch) for batch in batches[1:]],
        "repeat_statistics": repeat_statistics(batches),
    }


def load_batch(root: Path) -> dict[str, Any]:
    manifest_path = root / "batch-manifest.json"
    results_path = root / "batch-results.tsv"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results: list[dict[str, str]] = []
    if results_path.is_file():
        with results_path.open("r", encoding="utf-8", newline="") as handle:
            results = list(csv.DictReader(handle, delimiter="\t"))
    return {"root": root, "manifest": manifest, "results": results}


def compare_to_baseline(
    baseline: dict[str, Any],
    batch: dict[str, Any],
    *,
    expected_task_count: int | None,
) -> list[dict[str, Any]]:
    base_manifest = as_dict(baseline["manifest"])
    manifest = as_dict(batch["manifest"])
    base_comp = as_dict(base_manifest.get("comparability"))
    comp = as_dict(manifest.get("comparability"))
    batch_name = str(batch["root"])
    checks: list[dict[str, Any]] = []

    def check(
        name: str,
        expected: Any,
        actual: Any,
        *,
        mismatch_severity: str = "error",
        missing_severity: str = "warning",
    ) -> None:
        if expected == actual and actual is not None:
            status = "pass"
        elif expected is None and actual is None:
            status = missing_severity
        else:
            status = mismatch_severity
        checks.append(
            {
                "batch": batch_name,
                "check": name,
                "status": status,
                "expected": expected,
                "actual": actual,
            }
        )

    check("task_id_set", sorted(base_manifest.get("task_ids") or []), sorted(manifest.get("task_ids") or []))
    check(
        "task_file_sha256",
        base_manifest.get("task_file_sha256"),
        manifest.get("task_file_sha256"),
        missing_severity="error",
    )
    for key in (
        "task_package_sha256",
        "agentmon_version",
        "agentmon_git_commit",
        "agentmon_source_sha256",
        "runner_version",
        "runner_sha256",
        "vllm_version",
        "vllm_ascend_version",
        "cann_version",
        "mini_swe_agent_version",
        "model_path",
        "model_config_sha256",
        "vllm_launch_command_sha256",
        "vllm_launch_settings",
        "scheduler_environment",
        "temperature",
        "seed",
        "p2_filter_mode",
        "p2_quality",
        "include_p2_window_extras",
        "p2_quality_thresholds",
        "p1_mode",
        "metadata",
    ):
        check(key, base_comp.get(key), comp.get(key))
    if expected_task_count is not None:
        check("expected_task_count", expected_task_count, manifest.get("task_count"), missing_severity="error")
    result_task_ids = sorted(row.get("task_id") for row in batch["results"] if row.get("task_id"))
    manifest_task_ids = sorted(manifest.get("task_ids") or [])
    check("result_task_id_set", manifest_task_ids, result_task_ids, missing_severity="error")
    return checks


def batch_statistics(batch: dict[str, Any]) -> dict[str, Any]:
    results = batch["results"]
    stats = {
        "batch": str(batch["root"]),
        "task_count": len(results),
        "execution_success_count": sum(parse_bool(row.get("execution_success")) is True for row in results),
        "monitoring_success_count": sum(parse_bool(row.get("monitoring_success")) is True for row in results),
        "judge_success_count": sum(parse_bool(row.get("judge_success")) is True for row in results),
    }
    stats["metrics"] = {
        metric: metric_statistics(metric_value(row, metric) for row in results) for metric in METRICS
    }
    return stats


def paired_comparison(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base_by_task = {row.get("task_id"): row for row in baseline["results"] if row.get("task_id")}
    candidate_by_task = {row.get("task_id"): row for row in candidate["results"] if row.get("task_id")}
    paired_ids = sorted(set(base_by_task) & set(candidate_by_task))
    metrics: dict[str, Any] = {}
    for metric in METRICS:
        deltas = []
        for task_id in paired_ids:
            before = metric_value(base_by_task[task_id], metric)
            after = metric_value(candidate_by_task[task_id], metric)
            if before is not None and after is not None:
                deltas.append(after - before)
        metrics[metric] = metric_statistics(deltas, effect_size_against_zero=True)
    return {
        "baseline": str(baseline["root"]),
        "candidate": str(candidate["root"]),
        "paired_task_count": len(paired_ids),
        "paired_task_ids": paired_ids,
        "delta_candidate_minus_baseline": metrics,
    }


def metric_statistics(
    values: Iterable[float | None],
    *,
    effect_size_against_zero: bool = False,
) -> dict[str, Any]:
    cleaned = [value for value in values if value is not None and math.isfinite(value)]
    if not cleaned:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p50": None,
            "p95": None,
            "standard_deviation": None,
            "ci95_low": None,
            "ci95_high": None,
            "bootstrap_ci95_low": None,
            "bootstrap_ci95_high": None,
            "effect_size_dz": None,
        }
    mean = statistics.fmean(cleaned)
    if len(cleaned) >= 2:
        margin = 1.96 * statistics.stdev(cleaned) / math.sqrt(len(cleaned))
        ci_low = mean - margin
        ci_high = mean + margin
    else:
        ci_low = None
        ci_high = None
    bootstrap_low, bootstrap_high = bootstrap_mean_ci(cleaned)
    stdev = statistics.stdev(cleaned) if len(cleaned) >= 2 else None
    return {
        "count": len(cleaned),
        "mean": mean,
        "median": percentile(cleaned, 50),
        "p50": percentile(cleaned, 50),
        "p95": percentile(cleaned, 95),
        "standard_deviation": stdev,
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "bootstrap_ci95_low": bootstrap_low,
        "bootstrap_ci95_high": bootstrap_high,
        "effect_size_dz": (
            mean / stdev if effect_size_against_zero and stdev not in {None, 0} else None
        ),
    }


def bootstrap_mean_ci(values: list[float], *, samples: int = 1000) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    seed = sum(int(abs(value) * 1_000_000) for value in values) ^ len(values)
    rng = random.Random(seed)
    means = [
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    ]
    return percentile(means, 2.5), percentile(means, 97.5)


def repeat_statistics(batches: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "repeat_count": len(batches),
        "metrics": {},
        "method": "batch means for between-batch variance; mean task variance for within-batch variance",
    }
    for metric in METRICS:
        per_batch_values = [
            [value for row in batch["results"] if (value := metric_value(row, metric)) is not None]
            for batch in batches
        ]
        means = [statistics.fmean(values) for values in per_batch_values if values]
        within_variances = [statistics.variance(values) for values in per_batch_values if len(values) >= 2]
        result["metrics"][metric] = {
            "batch_mean_statistics": metric_statistics(means),
            "between_batch_variance": statistics.variance(means) if len(means) >= 2 else None,
            "within_batch_variance": statistics.fmean(within_variances) if within_variances else None,
            "valid_batch_count": len(means),
        }
    return result


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def format_table(comparison: dict[str, Any]) -> str:
    lines = [
        f"Comparable: {comparison['comparable']}",
        f"Batches: {comparison['batch_count']} errors={comparison['error_count']} warnings={comparison['warning_count']}",
        "",
        "Checks",
    ]
    for check in comparison["checks"]:
        if check["status"] != "pass":
            lines.append(
                f"- [{check['status']}] {check['check']} batch={check['batch']} "
                f"expected={check['expected']} actual={check['actual']}"
            )
    if all(check["status"] == "pass" for check in comparison["checks"]):
        lines.append("- all checks passed")
    lines.extend(["", "Batch statistics"])
    for stats in comparison["batch_statistics"]:
        lines.append(
            f"- {stats['batch']}: tasks={stats['task_count']} execution={stats['execution_success_count']} "
            f"monitoring={stats['monitoring_success_count']} judge={stats['judge_success_count']}"
        )
    return "\n".join(lines)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def to_float(value: Any) -> float | None:
    if value in {None, "", "N/A", "null", "None"}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def metric_value(row: dict[str, Any], metric: str) -> float | None:
    value = to_float(row.get(metric))
    if value is not None:
        return value
    alias = METRIC_ALIASES.get(metric)
    return to_float(row.get(alias)) if alias else None


def parse_bool(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


if __name__ == "__main__":
    raise SystemExit(main())
