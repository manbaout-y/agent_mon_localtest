from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import statistics
from typing import Any


SCHEMA_VERSION = "1.0"
PAIR_METRICS = (
    "p2_e2e_s",
    "p2_queue_s",
    "p2_exact_ttft_s",
    "p2_exact_prefill_s",
    "p2_exact_decode_s",
    "p2_proxy_ttft_s",
    "p2_proxy_decode_s",
    "p2_prompt_tokens",
    "p2_cached_prompt_tokens",
    "p2_generation_tokens",
    "p2_kv_usage_after",
)


def compare_batch_steps(
    baseline_dir: str | Path,
    target_dir: str | Path,
    *,
    pair_by: str = "task,step",
) -> dict[str, Any]:
    if pair_by not in {"task", "task,step"}:
        raise ValueError("pair-by must be task or task,step")
    baseline = load_batch(Path(baseline_dir))
    target = load_batch(Path(target_dir))
    checks = comparability_checks(baseline, target)
    baseline_rows = model_rows_with_tasks(baseline)
    target_rows = model_rows_with_tasks(target)
    if pair_by == "task":
        pairs = pair_task_aggregates(baseline_rows, target_rows)
    else:
        pairs = pair_step_rows(baseline_rows, target_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_type": "agentmon.batch_step_comparison",
        "baseline": str(baseline["root"]),
        "target": str(target["root"]),
        "pair_by": pair_by,
        "comparable": all(check["status"] != "error" for check in checks),
        "checks": checks,
        "pair_count": len(pairs),
        "paired_rows": pairs,
        "metric_delta_statistics": {
            metric: statistics_bundle(row.get(f"{metric}_delta") for row in pairs)
            for metric in PAIR_METRICS
        },
    }


def load_batch(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "batch-manifest.json")
    results = read_csv(root / "batch-results.tsv", delimiter="\t")
    step_path = locate_step_csv(root)
    steps = read_csv(step_path) if step_path else []
    task_by_run = {
        str(row.get("run_id")): str(row.get("task_id"))
        for row in results
        if row.get("run_id") not in {None, ""}
    }
    return {
        "root": root,
        "manifest": manifest,
        "results": results,
        "steps": steps,
        "task_by_run": task_by_run,
    }


def locate_step_csv(root: Path) -> Path | None:
    candidates = (
        root / "step-sequence" / "all-steps.csv",
        root / "all-steps.csv",
    )
    return next((path for path in candidates if path.is_file()), None)


def comparability_checks(baseline: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    left = as_dict(baseline.get("manifest"))
    right = as_dict(target.get("manifest"))
    left_comp = as_dict(left.get("comparability"))
    right_comp = as_dict(right.get("comparability"))
    fields = (
        "task_file_sha256",
        "task_package_sha256",
        "agentmon_source_sha256",
        "runner_sha256",
        "vllm_version",
        "vllm_ascend_version",
        "cann_version",
        "model_config_sha256",
        "p2_filter_mode",
    )
    checks: list[dict[str, Any]] = []
    left_tasks = sorted(left.get("task_ids") or [])
    right_tasks = sorted(right.get("task_ids") or [])
    checks.append(check("task_id_set", left_tasks, right_tasks, missing_is_error=True))
    for field in fields:
        checks.append(check(field, left_comp.get(field), right_comp.get(field), missing_is_error=False))
    return checks


def check(name: str, expected: Any, actual: Any, *, missing_is_error: bool) -> dict[str, Any]:
    if expected is not None and expected == actual:
        status = "pass"
    elif expected is None and actual is None:
        status = "error" if missing_is_error else "warning"
    else:
        status = "error"
    return {"name": name, "status": status, "expected": expected, "actual": actual}


def model_rows_with_tasks(batch: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in batch["steps"]:
        if raw.get("item_type") != "model" or raw.get("step_type") == "model_extra":
            continue
        row = dict(raw)
        row["task_id"] = batch["task_by_run"].get(str(row.get("run_id")), row.get("task_id"))
        output.append(row)
    return output


def pair_step_rows(baseline: list[dict[str, Any]], target: list[dict[str, Any]]) -> list[dict[str, Any]]:
    left = {step_key(row): row for row in baseline if step_key(row) is not None}
    right = {step_key(row): row for row in target if step_key(row) is not None}
    output: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        before = left.get(key)
        after = right.get(key)
        row = pair_row(key[0], key[1], before, after)
        row["model_call_index_in_step"] = key[2]
        output.append(row)
    return output


def pair_task_aggregates(baseline: list[dict[str, Any]], target: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = sorted(
        {str(row.get("task_id")) for row in baseline + target if row.get("task_id") not in {None, ""}}
    )
    output: list[dict[str, Any]] = []
    for task_id in tasks:
        before = aggregate_task([row for row in baseline if str(row.get("task_id")) == task_id])
        after = aggregate_task([row for row in target if str(row.get("task_id")) == task_id])
        output.append(pair_row(task_id, None, before, after))
    return output


def aggregate_task(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    result: dict[str, Any] = {"run_id": rows[0].get("run_id")}
    for metric in PAIR_METRICS:
        values = [value for row in rows if (value := number_or_none(row.get(metric))) is not None]
        result[metric] = sum(values) if values else None
    return result


def pair_row(
    task_id: str,
    step_index: int | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "task_id": task_id,
        "step_index": step_index,
        "pair_status": "paired" if before and after else "baseline_only" if before else "target_only",
        "baseline_run_id": before.get("run_id") if before else None,
        "target_run_id": after.get("run_id") if after else None,
        "baseline_model_call_id": before.get("model_call_id") if before else None,
        "target_model_call_id": after.get("model_call_id") if after else None,
    }
    for metric in PAIR_METRICS:
        left = number_or_none(before.get(metric)) if before else None
        right = number_or_none(after.get(metric)) if after else None
        result[f"baseline_{metric}"] = left
        result[f"target_{metric}"] = right
        result[f"{metric}_delta"] = right - left if left is not None and right is not None else None
    return result


def step_key(row: dict[str, Any]) -> tuple[str, int, int] | None:
    task = row.get("task_id")
    step = integer_or_none(row.get("step_index"))
    call_index = integer_or_none(row.get("model_call_index_in_step"))
    if call_index is None:
        call_index = integer_or_none(row.get("item_index")) or 0
    return (str(task), step, call_index) if task not in {None, ""} and step is not None else None


def statistics_bundle(values: Any) -> dict[str, Any]:
    cleaned = [value for raw in values if (value := number_or_none(raw)) is not None]
    if not cleaned:
        return {"count": 0, "mean": None, "median": None, "standard_deviation": None, "effect_size_dz": None}
    stdev = statistics.stdev(cleaned) if len(cleaned) >= 2 else None
    mean = statistics.fmean(cleaned)
    return {
        "count": len(cleaned),
        "mean": mean,
        "median": statistics.median(cleaned),
        "standard_deviation": stdev,
        "effect_size_dz": mean / stdev if stdev not in {None, 0} else None,
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def read_csv(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, "", "N/A", "null"}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def integer_or_none(value: Any) -> int | None:
    parsed = number_or_none(value)
    return int(parsed) if parsed is not None and parsed.is_integer() else None
