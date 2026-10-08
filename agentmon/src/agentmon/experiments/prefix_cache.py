from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "1.0"


def plan_prefix_cache_experiment(
    *,
    baseline_reports: list[dict[str, Any]],
    baseline_summaries: list[dict[str, Any]],
    variant_config: str | Path,
) -> dict[str, Any]:
    variant = variant_config_snapshot(variant_config)
    baseline_runs = [
        {
            "run_id": summary.get("run_id"),
            "label": summary.get("label"),
            "run_status": summary.get("run_status"),
            "task_success": summary.get("task_success"),
            "prefix_cache_status": summary.get("prefix_cache_status"),
            "p2_kv_prefix_cache_status": summary.get("p2_kv_prefix_cache_status"),
            "model_requests": summary.get("model_requests"),
            "p2_request_count": summary.get("p2_request_count"),
            "duration_seconds": summary.get("duration_seconds"),
            "request_ttft_mean": nested(summary, "p2_ttft_latency.mean"),
            "request_prefill_mean": nested(summary, "p2_prefill_latency.mean"),
            "request_decode_mean": nested(summary, "p2_decode_latency.mean"),
            "request_e2e_mean": nested(summary, "p2_e2e_latency.mean"),
            "kv_usage_ratio_max": summary.get("kv_cache_usage_ratio_max"),
            "p2_preemption_count": summary.get("p2_preemption_count"),
        }
        for summary in baseline_summaries
    ]
    checks = build_checks(baseline_reports, baseline_summaries, variant)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_type": "prefix-cache",
        "planner": "agentmon.prefix_cache_ab.v1",
        "status": "ready" if all(check["status"] == "pass" for check in checks) else "needs_attention",
        "mutates_service": False,
        "baseline_run_count": len(baseline_summaries),
        "baseline_runs": baseline_runs,
        "variant_config": variant,
        "checks": checks,
        "required_matrix": {
            "conditions": ["prefix_cache_disabled", "prefix_cache_enabled"],
            "cache_temperature": ["cold", "warm"],
            "minimum_repetitions_per_condition": 3,
            "same_task_set_required": True,
            "same_service_parameters_required_except_prefix_cache": True,
        },
        "metrics_to_compare": [
            "task_success",
            "request_ttft_mean",
            "request_prefill_mean",
            "request_decode_mean",
            "request_e2e_mean",
            "p2_cached_prompt_tokens",
            "p2_computed_prefill_tokens",
            "p2_recomputed_prompt_tokens",
            "kv_cache_usage_ratio_max",
            "p2_preemption_count",
        ],
        "notes": [
            "This planner does not restart or reconfigure vLLM.",
            "Run disabled and enabled conditions under the same model and serving limits.",
            "Separate cold-cache from warm-cache repetitions.",
        ],
    }


def variant_config_snapshot(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        return {"path": str(config_path), "exists": False, "sha256": None, "prefix_cache_hint": "unknown"}
    data = config_path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(config_path),
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "prefix_cache_hint": detect_prefix_cache_hint(text),
    }


def detect_prefix_cache_hint(text: str) -> str:
    for line in text.lower().splitlines():
        if "prefix" not in line or "cach" not in line:
            continue
        normalized = line.strip()
        if re.search(r"(?:^|\s)--(?:no|disable)-[^\s]*prefix[^\s]*cach[^\s]*", normalized):
            return "disabled"
        if re.search(r"(?:=|:)\s*[\"']?(?:false|disabled|off|0)\b", normalized):
            return "disabled"
        if re.search(r"(?:^|\s)--enable-[^\s]*prefix[^\s]*cach[^\s]*", normalized):
            return "enabled"
        if re.search(r"(?:=|:)\s*[\"']?(?:true|enabled|on|1)\b", normalized):
            return "enabled"
    return "unknown"


def build_checks(
    baseline_reports: list[dict[str, Any]],
    baseline_summaries: list[dict[str, Any]],
    variant: dict[str, Any],
) -> list[dict[str, Any]]:
    labels = {summary.get("label") for summary in baseline_summaries if summary.get("label") is not None}
    model_names = {
        nested(report, "manifest.model_name") or nested(report, "agent.model_name")
        for report in baseline_reports
        if nested(report, "manifest.model_name") or nested(report, "agent.model_name")
    }
    prefix_statuses = {summary.get("prefix_cache_status") for summary in baseline_summaries}
    return [
        check("baseline_runs_present", bool(baseline_summaries), "at least one baseline run is required"),
        check("variant_config_exists", bool(variant.get("exists")), "variant config file should exist"),
        check(
            "variant_mentions_prefix_cache",
            variant.get("prefix_cache_hint") in {"enabled", "disabled"},
            "variant config should explicitly mention prefix cache state",
        ),
        check("same_label", len(labels) <= 1, f"baseline labels: {sorted(str(item) for item in labels)}"),
        check(
            "same_model_when_known",
            len(model_names) <= 1,
            f"baseline model names: {sorted(str(item) for item in model_names)}",
        ),
        check(
            "baseline_prefix_cache_known",
            all(status not in {None, "unknown"} for status in prefix_statuses),
            f"baseline prefix statuses: {sorted(str(item) for item in prefix_statuses)}",
        ),
    ]


def check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "warn", "detail": detail}


def format_prefix_cache_plan_table(plan: dict[str, Any]) -> str:
    rows = [
        ("status", plan.get("status")),
        ("baseline_run_count", plan.get("baseline_run_count")),
        ("variant_config", nested(plan, "variant_config.path")),
        ("variant_prefix_cache_hint", nested(plan, "variant_config.prefix_cache_hint")),
        ("mutates_service", plan.get("mutates_service")),
    ]
    rows.extend((f"check.{item['name']}", item["status"]) for item in plan.get("checks", []))
    width = max(len(str(key)) for key, _value in rows)
    return "\n".join(f"{str(key).ljust(width)}  {value}" for key, value in rows)


def plan_to_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)


def nested(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
