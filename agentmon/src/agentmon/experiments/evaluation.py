from __future__ import annotations

import csv
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable

from agentmon.optimizer.harness.schemas import (
    COMPLETE,
    DEFAULT_MECHANISM_PROFILE,
    DEFAULT_STEP_CSV_PROFILE,
    FORBIDDEN_CONCLUSIONS,
    PARTIAL,
    SCHEMA_VERSION,
    UNKNOWN,
    UNRESOLVED,
    merge_schema_profile,
)

from .evidence import (
    BatchExperimentData,
    first_float,
    first_int,
    float_or_none,
    int_or_none,
    iter_batch_results,
    iter_step_rows,
    run_wall_from_result,
)


STEP_CSV_PROFILE = DEFAULT_STEP_CSV_PROFILE


def build_mechanism_evaluation(
    data: BatchExperimentData,
    evidence_outputs: dict[str, Any],
    replay_outputs: dict[str, Any],
    *,
    baseline_strategy: str | None = None,
    mechanism_profile: dict[str, Any] | None = None,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = merge_mechanism_profile(mechanism_profile)
    resolved_schema_profile = merge_schema_profile(schema_profile)
    timeline = list(replay_outputs.get("decision_timeline") or [])
    strategy_rows = list(replay_outputs.get("strategy_summary", {}).get("strategies") or [])
    selected_baseline = select_baseline_strategy(strategy_rows, baseline_strategy)
    ablation_rows = build_ablation_rows(timeline, selected_baseline)
    contribution_rows = build_contribution_rows(evidence_outputs, timeline, profile)
    conflict_audit = build_conflict_audit(timeline, profile)
    comparison = build_mechanism_comparison(
        data,
        evidence_outputs,
        replay_outputs,
        ablation_rows,
        conflict_audit,
        selected_baseline,
        profile,
        resolved_schema_profile,
    )
    return {
        "mechanism_comparison": comparison,
        "ablation_table": ablation_rows,
        "contribution_decomposition": contribution_rows,
        "conflict_audit": conflict_audit,
        "evaluation_report": build_evaluation_report(comparison, ablation_rows, contribution_rows, conflict_audit),
    }


def build_mechanism_evaluation_from_timeline(
    data: BatchExperimentData,
    evidence_outputs: dict[str, Any],
    timeline_path: str | Path,
    strategy_summary: dict[str, Any],
    *,
    baseline_strategy: str | None = None,
    mechanism_profile: dict[str, Any] | None = None,
    schema_profile: dict[str, Any] | None = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate a replay CSV with only one snapshot's decisions resident."""
    profile = merge_mechanism_profile(mechanism_profile)
    selected_baseline = select_baseline_strategy(list(strategy_summary.get("strategies") or []), baseline_strategy)
    counts: dict[str, dict[str, int]] = {}
    advice_components: dict[tuple[str, str], dict[str, Any]] = {}
    conflict_root = Path(work_dir).expanduser().resolve() if work_dir else Path(timeline_path).expanduser().resolve().parent / "stages" / "m3_mechanism_evaluation"
    conflict_root.mkdir(parents=True, exist_ok=True)
    conflict_jsonl = conflict_root / "conflicts.jsonl"
    conflict_count = 0
    fused_pressure_counts: Counter[str] = Counter()
    snapshot_group_count = 0
    current_index: int | None = None
    current_rows: list[dict[str, Any]] = []

    def flush_snapshot(handle) -> None:
        nonlocal conflict_count, snapshot_group_count
        if not current_rows:
            return
        pressures = [str(row.get("admission_pressure") or UNKNOWN) for row in current_rows]
        fused_pressure_counts[fuse_pressure(pressures, profile)] += 1
        snapshot_group_count += 1
        if len(current_rows) < 2:
            return
        pressure_values = sorted(set(pressures))
        defer_values = sorted({bool(row.get("would_defer")) for row in current_rows})
        limit_values = sorted({bool(row.get("would_limit")) for row in current_rows})
        relax_values = sorted({bool(row.get("would_relax")) for row in current_rows})
        targets = sorted({str(row.get("target_runtime_default")) for row in current_rows})
        conflict_types = []
        if len(pressure_values) > 1:
            conflict_types.append("pressure_disagreement")
        if len(defer_values) > 1:
            conflict_types.append("defer_relax_disagreement")
        if len(limit_values) > 1:
            conflict_types.append("limit_disagreement")
        if len(relax_values) > 1:
            conflict_types.append("relax_disagreement")
        if len(targets) > 1:
            conflict_types.append("target_concurrency_disagreement")
        if conflict_types:
            handle.write(json.dumps({
                "snapshot_index": current_index,
                "timestamp_mono_ns": current_rows[0].get("timestamp_mono_ns"),
                "conflict_types": conflict_types,
                "strategies": [row.get("strategy", UNKNOWN) for row in current_rows],
                "pressures": pressure_values,
                "would_defer_values": defer_values,
                "would_limit_values": limit_values,
                "would_relax_values": relax_values,
                "target_runtime_default_values": targets,
                "fusion_policy": profile.get("fusion_policy", UNKNOWN),
                "fused_admission_pressure": fuse_pressure(pressures, profile),
                "evidence_source": "optimizer_decision_timeline.csv",
                "data_quality": PARTIAL,
            }, ensure_ascii=False, sort_keys=True) + "\n")
            conflict_count += 1

    with conflict_jsonl.open("w", encoding="utf-8") as conflict_handle, Path(timeline_path).open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = normalize_timeline_row(raw)
            strategy = str(row.get("strategy") or UNKNOWN)
            current = counts.setdefault(strategy, {"decision_count": 0, "high_pressure_count": 0, "moderate_pressure_count": 0, "none_pressure_count": 0, "would_defer_count": 0, "would_limit_count": 0, "would_relax_count": 0})
            current["decision_count"] += 1
            pressure = str(row.get("admission_pressure") or UNKNOWN)
            if pressure in {"high", "moderate", "none"}:
                current[f"{pressure}_pressure_count"] += 1
            for field in ("would_defer", "would_limit", "would_relax"):
                if row.get(field):
                    current[f"{field}_count"] += 1
            for flag in str(row.get("risk_flags") or "").split(";"):
                if not flag:
                    continue
                component = component_for_risk_flag(flag, as_dict(profile.get("risk_flag_component_map")))
                item = advice_components.setdefault((strategy, component), {"strategy": strategy, "component": component, "evidence_kind": "advice_risk_flag", "observed_duration_s": None, "share_of_observed_duration_pct": None, "advice_flag_count": 0, "risk_flags": set(), "evidence_source": "optimizer_decision_timeline.csv", "data_quality": PARTIAL, "limitation": "Advice risk flags identify candidate mechanisms, not measured contribution."})
                item["advice_flag_count"] += 1
                item["risk_flags"].add(flag)
            snapshot_index = int(row.get("snapshot_index") or 0)
            if current_index is not None and snapshot_index != current_index:
                flush_snapshot(conflict_handle)
                current_rows = []
            current_index = snapshot_index
            current_rows.append(row)
        flush_snapshot(conflict_handle)
    baseline_counts = counts.get(selected_baseline or "", {key: 0 for key in ("would_defer_count", "would_limit_count", "high_pressure_count")})
    ablation_rows = [
        {
            "mechanism_id": f"optimizer_strategy:{strategy}", "ablation_type": "offline_advice_proxy", "baseline_strategy": selected_baseline or UNKNOWN,
            "strategy": strategy, **item,
            "delta_would_defer_vs_baseline": item["would_defer_count"] - baseline_counts["would_defer_count"] if selected_baseline is not None else None,
            "delta_would_limit_vs_baseline": item["would_limit_count"] - baseline_counts["would_limit_count"] if selected_baseline is not None else None,
            "delta_high_pressure_vs_baseline": item["high_pressure_count"] - baseline_counts["high_pressure_count"] if selected_baseline is not None else None,
            "evidence_source": "optimizer_decision_timeline.csv", "data_quality": COMPLETE if item["decision_count"] else UNKNOWN,
            "forbidden_conclusion": "Do not interpret advice deltas as runtime speedup.",
        }
        for strategy, item in sorted(counts.items())
    ]
    contribution_rows = [
        {"strategy": "observed_workload", "component": item.get("component", UNKNOWN), "evidence_kind": "observed_duration", "observed_duration_s": item.get("observed_duration_s"), "share_of_observed_duration_pct": item.get("share_of_observed_duration_pct"), "advice_flag_count": None, "evidence_source": ";".join(item.get("evidence_sources") or []), "data_quality": item.get("data_quality", PARTIAL), "limitation": "Observed duration is not causal contribution."}
        for item in evidence_outputs.get("bottleneck_attribution", {}).get("components", [])
    ]
    for item in sorted(advice_components.values(), key=lambda value: (value["strategy"], value["component"])):
        item["risk_flags"] = ";".join(sorted(item["risk_flags"]))
        contribution_rows.append(item)
    if not contribution_rows:
        contribution_rows = build_contribution_rows(evidence_outputs, [], profile)
    conflict_audit = {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.conflict_audit",
        "conflict_count": conflict_count,
        "conflicting_snapshot_count": conflict_count,
        "conflicts": [],
        "fusion_summary": {
            "fusion_policy": profile.get("fusion_policy", UNKNOWN),
            "fused_pressure_counts": dict(sorted(fused_pressure_counts.items())),
            "input_snapshot_count": snapshot_group_count,
            "conflicting_snapshot_count": conflict_count,
            "data_quality": COMPLETE if snapshot_group_count else UNKNOWN,
        },
        "streaming_contract": {
            "max_resident_decision_group_count": 1,
            "max_resident_fused_pressure_values": 0,
            "fused_pressure_counter_key_count": len(fused_pressure_counts),
            "conflict_details_storage": "jsonl_part",
        },
        "limitations": [
            "Conflict audit compares advice outputs only.",
            "Fusion policy is advisory and is not an online control-plane arbitration.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }
    replay_outputs = {"strategy_summary": strategy_summary, "decision_timeline": True}
    comparison = build_mechanism_comparison(data, evidence_outputs, replay_outputs, ablation_rows, conflict_audit, selected_baseline, profile, merge_schema_profile(schema_profile))
    return {"mechanism_comparison": comparison, "ablation_table": ablation_rows, "contribution_decomposition": contribution_rows, "conflict_audit": conflict_audit, "conflict_jsonl": conflict_jsonl, "evaluation_report": build_evaluation_report(comparison, ablation_rows, contribution_rows, conflict_audit)}


def normalize_timeline_row(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(row)
    result["snapshot_index"] = int(row.get("snapshot_index") or 0)
    for field in ("would_defer", "would_limit", "would_relax"):
        result[field] = truthy(row.get(field))
    return result


def write_evaluation_outputs(outputs: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "mechanism_comparison": root / "mechanism_comparison.json",
        "ablation_table": root / "ablation_table.csv",
        "contribution_decomposition": root / "contribution_decomposition.csv",
        "conflict_audit": root / "conflict_audit.json",
        "evaluation_report": root / "evaluation_report.md",
    }
    write_json(paths["mechanism_comparison"], outputs["mechanism_comparison"])
    write_csv(paths["ablation_table"], outputs["ablation_table"])
    write_csv(paths["contribution_decomposition"], outputs["contribution_decomposition"])
    conflict_jsonl = outputs.get("conflict_jsonl")
    if conflict_jsonl:
        write_conflict_audit_from_jsonl(paths["conflict_audit"], outputs["conflict_audit"], Path(conflict_jsonl))
    else:
        write_json(paths["conflict_audit"], outputs["conflict_audit"])
    paths["evaluation_report"].write_text(str(outputs["evaluation_report"]), encoding="utf-8")
    return paths


def write_conflict_audit_from_jsonl(path: Path, summary: dict[str, Any], source: Path) -> None:
    temporary = path.with_name(path.name + ".partial")
    without_conflicts = {key: value for key, value in summary.items() if key != "conflicts"}
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write("{")
        first = True
        for key in sorted(without_conflicts):
            if not first:
                handle.write(",")
            handle.write(json.dumps(key, ensure_ascii=False))
            handle.write(":")
            handle.write(json.dumps(without_conflicts[key], ensure_ascii=False, sort_keys=True))
            first = False
        if not first:
            handle.write(",")
        handle.write('"conflicts":[')
        first_conflict = True
        if source.is_file():
            with source.open("r", encoding="utf-8") as rows:
                for line in rows:
                    if not first_conflict:
                        handle.write(",")
                    handle.write(line.strip())
                    first_conflict = False
        handle.write("]}\n")
    temporary.replace(path)


def build_mechanism_comparison(
    data: BatchExperimentData,
    evidence_outputs: dict[str, Any],
    replay_outputs: dict[str, Any],
    ablation_rows: list[dict[str, Any]],
    conflict_audit: dict[str, Any],
    baseline_strategy: str | None,
    profile: dict[str, Any],
    schema_profile: dict[str, Any],
) -> dict[str, Any]:
    workload_summary = build_observed_workload_summary(data, schema_profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.mechanism_comparison",
        "evaluation_mode": "offline_advice_proxy",
        "baseline_strategy": baseline_strategy or UNKNOWN,
        "fusion_policy": profile.get("fusion_policy") or UNKNOWN,
        "observed_workload_summary": workload_summary,
        "strategy_mechanisms": build_strategy_mechanisms(replay_outputs),
        "ablation_proxy_summary": summarize_ablation_rows(ablation_rows),
        "fusion_summary": conflict_audit.get("fusion_summary", {}),
        "data_quality": {
            "evidence": evidence_outputs.get("data_quality", {}).get("overall_status", UNKNOWN),
            "replay": COMPLETE if replay_outputs.get("decision_timeline") else PARTIAL,
            "evaluation": "PROXY_ONLY",
        },
        "limitations": [
            "Mechanism comparison uses offline advice proxy metrics unless explicit variant batches are supplied in a future stage.",
            "Ablation rows compare strategy advice behavior, not observed online speedup.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def build_observed_workload_summary(
    data: BatchExperimentData,
    schema_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step_profile = merge_schema_profile(schema_profile)["step_csv_profile"]
    results = list(iter_batch_results(data))
    run_wall_values = [value for value in (run_wall_from_result(row, step_profile) for row in results) if value is not None]
    starts = [first_int(*(row.get(field) for field in step_profile["run_start_fields"])) for row in results]
    ends = [first_int(*(row.get(field) for field in step_profile["run_end_fields"])) for row in results]
    valid_starts = [value for value in starts if value is not None]
    valid_ends = [value for value in ends if value is not None]
    makespan_s = (
        (max(valid_ends) - min(valid_starts)) / 1_000_000_000
        if valid_starts and valid_ends and max(valid_ends) >= min(valid_starts)
        else None
    )
    completed_rows = [
        row for row in results
        if str(row.get("execution_success") or row.get("status") or "").lower() in {"true", "success", "succeeded", "passed"}
    ]
    model_rows = []
    tool_rows = []
    for row in iter_step_rows(data):
        if row.get("item_type") == "model":
            model_rows.append(row)
        elif row.get("item_type") == "tool":
            tool_rows.append(row)
    return {
        "task_level": {
            "attempted_count": len(results),
            "completed_count": len(completed_rows),
            "makespan_s": makespan_s,
            "throughput_tasks_per_s": len(completed_rows) / makespan_s if makespan_s and makespan_s > 0 else None,
            "run_wall_s": numeric_summary(run_wall_values),
            "data_quality": COMPLETE if run_wall_values else PARTIAL,
        },
        "request_level": {
            "model_call_count": len(model_rows),
            "queue_s": summarize_first_fields(model_rows, step_profile["model_phase_duration_fields"]["model_queue"]),
            "ttft_s": summarize_first_fields(model_rows, step_profile["request_ttft_fields"]),
            "tpot_s": summarize_first_fields(model_rows, step_profile["request_tpot_fields"]),
            "decode_s": summarize_first_fields(model_rows, step_profile["model_phase_duration_fields"]["model_decode"]),
        },
        "workload_normalized_tokens": {
            "actual_prefill_tokens": summarize_first_fields(model_rows, step_profile["actual_prefill_token_fields"]),
            "generation_tokens": summarize_first_fields(model_rows, step_profile["generation_token_fields"]),
            "normalization_basis": "observed_model_call_rows",
        },
        "resource_utilization": {
            "accelerator_util_pct": summarize_first_fields(model_rows, step_profile["resource_utilization_fields"]),
            "accelerator_memory_bytes": summarize_first_fields(model_rows, step_profile["resource_memory_fields"]),
            "data_quality": COMPLETE if any_has_value(model_rows, step_profile["resource_utilization_fields"]) else UNKNOWN,
        },
        "tool_quality": build_tool_quality_summary(tool_rows, step_profile),
    }


def build_strategy_mechanisms(replay_outputs: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in replay_outputs.get("strategy_summary", {}).get("strategies", []):
        rows.append(
            {
                "mechanism_id": f"optimizer_strategy:{row.get('strategy', UNKNOWN)}",
                "mechanism_type": "optimizer_strategy",
                "strategy": row.get("strategy", UNKNOWN),
                "decision_count": row.get("decision_count", 0),
                "advice_proxy_metrics": {
                    "pressure_counts": row.get("pressure_counts", {}),
                    "would_defer_count": row.get("would_defer_count", 0),
                    "would_limit_count": row.get("would_limit_count", 0),
                    "would_relax_count": row.get("would_relax_count", 0),
                    "risk_flag_counts": row.get("risk_flag_counts", {}),
                },
                "data_quality": COMPLETE,
            }
        )
    return rows


def build_ablation_rows(
    decision_rows: list[dict[str, Any]],
    baseline_strategy: str | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in decision_rows:
        grouped.setdefault(str(row.get("strategy") or UNKNOWN), []).append(row)
    baseline = proxy_counts(grouped.get(baseline_strategy or "", []))
    rows: list[dict[str, Any]] = []
    for strategy, strategy_decisions in sorted(grouped.items()):
        counts = proxy_counts(strategy_decisions)
        rows.append(
            {
                "mechanism_id": f"optimizer_strategy:{strategy}",
                "ablation_type": "offline_advice_proxy",
                "baseline_strategy": baseline_strategy or UNKNOWN,
                "strategy": strategy,
                "decision_count": counts["decision_count"],
                "high_pressure_count": counts["high_pressure_count"],
                "moderate_pressure_count": counts["moderate_pressure_count"],
                "none_pressure_count": counts["none_pressure_count"],
                "would_defer_count": counts["would_defer_count"],
                "would_limit_count": counts["would_limit_count"],
                "would_relax_count": counts["would_relax_count"],
                "delta_would_defer_vs_baseline": (
                    counts["would_defer_count"] - baseline["would_defer_count"]
                    if baseline_strategy is not None
                    else None
                ),
                "delta_would_limit_vs_baseline": (
                    counts["would_limit_count"] - baseline["would_limit_count"]
                    if baseline_strategy is not None
                    else None
                ),
                "delta_high_pressure_vs_baseline": (
                    counts["high_pressure_count"] - baseline["high_pressure_count"]
                    if baseline_strategy is not None
                    else None
                ),
                "evidence_source": "optimizer_decision_timeline.csv",
                "data_quality": COMPLETE if strategy_decisions else UNKNOWN,
                "forbidden_conclusion": "Do not interpret advice deltas as runtime speedup.",
            }
        )
    return rows


def build_contribution_rows(
    evidence_outputs: dict[str, Any],
    decision_rows: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in evidence_outputs.get("bottleneck_attribution", {}).get("components", []):
        rows.append(
            {
                "strategy": "observed_workload",
                "component": component.get("component", UNKNOWN),
                "evidence_kind": "observed_duration",
                "observed_duration_s": component.get("observed_duration_s"),
                "share_of_observed_duration_pct": component.get("share_of_observed_duration_pct"),
                "advice_flag_count": None,
                "evidence_source": ";".join(component.get("evidence_sources") or []),
                "data_quality": component.get("data_quality", PARTIAL),
                "limitation": "Observed duration is not causal contribution.",
            }
        )
    risk_map = as_dict(profile.get("risk_flag_component_map"))
    by_strategy_component: dict[tuple[str, str], dict[str, Any]] = {}
    for row in decision_rows:
        strategy = str(row.get("strategy") or UNKNOWN)
        for flag in str(row.get("risk_flags") or "").split(";"):
            if not flag:
                continue
            component = component_for_risk_flag(flag, risk_map)
            key = (strategy, component)
            current = by_strategy_component.setdefault(
                key,
                {
                    "strategy": strategy,
                    "component": component,
                    "evidence_kind": "advice_risk_flag",
                    "observed_duration_s": None,
                    "share_of_observed_duration_pct": None,
                    "advice_flag_count": 0,
                    "risk_flags": set(),
                    "evidence_source": "optimizer_decision_timeline.csv",
                    "data_quality": PARTIAL,
                    "limitation": "Advice risk flags identify candidate mechanisms, not measured contribution.",
                },
            )
            current["advice_flag_count"] += 1
            current["risk_flags"].add(flag)
    for row in sorted(by_strategy_component.values(), key=lambda item: (item["strategy"], item["component"])):
        row["risk_flags"] = ";".join(sorted(row["risk_flags"]))
        rows.append(row)
    if not rows:
        rows.append(
            {
                "strategy": UNKNOWN,
                "component": UNRESOLVED,
                "evidence_kind": UNKNOWN,
                "observed_duration_s": None,
                "share_of_observed_duration_pct": None,
                "advice_flag_count": None,
                "evidence_source": UNKNOWN,
                "data_quality": UNKNOWN,
                "limitation": "No observed component or advice evidence was available.",
            }
        )
    return rows


def build_conflict_audit(
    decision_rows: list[dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in decision_rows:
        snapshot_index = int(row.get("snapshot_index") or 0)
        grouped.setdefault(snapshot_index, []).append(row)
    conflicts: list[dict[str, Any]] = []
    fused_pressures: list[str] = []
    for snapshot_index, rows in sorted(grouped.items()):
        if len(rows) < 2:
            fused_pressures.append(str(rows[0].get("admission_pressure") or UNKNOWN) if rows else UNKNOWN)
            continue
        pressures = sorted({str(row.get("admission_pressure") or UNKNOWN) for row in rows})
        defer_values = sorted({bool(row.get("would_defer")) for row in rows})
        limit_values = sorted({bool(row.get("would_limit")) for row in rows})
        relax_values = sorted({bool(row.get("would_relax")) for row in rows})
        targets = sorted({str(row.get("target_runtime_default")) for row in rows})
        fused = fuse_pressure([str(row.get("admission_pressure") or UNKNOWN) for row in rows], profile)
        fused_pressures.append(fused)
        conflict_types = []
        if len(pressures) > 1:
            conflict_types.append("pressure_disagreement")
        if len(defer_values) > 1:
            conflict_types.append("defer_relax_disagreement")
        if len(limit_values) > 1:
            conflict_types.append("limit_disagreement")
        if len(relax_values) > 1:
            conflict_types.append("relax_disagreement")
        if len(targets) > 1:
            conflict_types.append("target_concurrency_disagreement")
        if conflict_types:
            conflicts.append(
                {
                    "snapshot_index": snapshot_index,
                    "timestamp_mono_ns": rows[0].get("timestamp_mono_ns"),
                    "conflict_types": conflict_types,
                    "strategies": [row.get("strategy", UNKNOWN) for row in rows],
                    "pressures": pressures,
                    "would_defer_values": defer_values,
                    "would_limit_values": limit_values,
                    "would_relax_values": relax_values,
                    "target_runtime_default_values": targets,
                    "fusion_policy": profile.get("fusion_policy", UNKNOWN),
                    "fused_admission_pressure": fused,
                    "evidence_source": "optimizer_decision_timeline.csv",
                    "data_quality": PARTIAL,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.conflict_audit",
        "conflict_count": len(conflicts),
        "conflicting_snapshot_count": len({row["snapshot_index"] for row in conflicts}),
        "conflicts": conflicts,
        "fusion_summary": {
            "fusion_policy": profile.get("fusion_policy", UNKNOWN),
            "fused_pressure_counts": count_values(fused_pressures),
            "input_snapshot_count": len(grouped),
            "conflicting_snapshot_count": len({row["snapshot_index"] for row in conflicts}),
            "data_quality": COMPLETE if grouped else UNKNOWN,
        },
        "limitations": [
            "Conflict audit compares advice outputs only.",
            "Fusion policy is advisory and is not an online control-plane arbitration.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def build_evaluation_report(
    comparison: dict[str, Any],
    ablation_rows: list[dict[str, Any]],
    contribution_rows: list[dict[str, Any]],
    conflict_audit: dict[str, Any],
) -> str:
    workload = comparison.get("observed_workload_summary", {})
    task_level = workload.get("task_level", {})
    request_level = workload.get("request_level", {})
    lines = [
        "# AgentMon Optimizer Mechanism Evaluation Report",
        "",
        f"- Evaluation mode: `{comparison.get('evaluation_mode')}`",
        f"- Baseline strategy: `{comparison.get('baseline_strategy')}`",
        f"- Fusion policy: `{comparison.get('fusion_policy')}`",
        f"- Evidence quality: `{comparison.get('data_quality', {}).get('evidence')}`",
        "",
        "## Observed Workload",
        "",
        f"- Attempted tasks: `{task_level.get('attempted_count')}`",
        f"- Completed tasks: `{task_level.get('completed_count')}`",
        f"- Makespan seconds: `{task_level.get('makespan_s')}`",
        f"- Throughput tasks/s: `{task_level.get('throughput_tasks_per_s')}`",
        f"- Model calls: `{request_level.get('model_call_count')}`",
        "",
        "## Advice Proxy Ablation",
        "",
    ]
    for row in ablation_rows:
        lines.append(
            f"- `{row['strategy']}`: high={row['high_pressure_count']}, "
            f"would_defer={row['would_defer_count']}, would_limit={row['would_limit_count']}"
        )
    lines.extend(["", "## Contribution Decomposition", ""])
    for row in contribution_rows[:20]:
        lines.append(
            f"- `{row.get('strategy')}` / `{row.get('component')}`: "
            f"{row.get('evidence_kind')} quality `{row.get('data_quality')}`"
        )
    lines.extend(
        [
            "",
            "## Conflict Audit",
            "",
            f"- Conflicts: `{conflict_audit.get('conflict_count')}`",
            f"- Conflicting snapshots: `{conflict_audit.get('conflicting_snapshot_count')}`",
            "",
            "## Required Cautions",
            "",
            "- This report does not claim causal speedup.",
            "- Online enforce remains out of scope for this offline harness stage.",
            *[f"- {item}" for item in FORBIDDEN_CONCLUSIONS],
            "",
        ]
    )
    return "\n".join(lines)


def select_baseline_strategy(strategy_rows: list[dict[str, Any]], requested: str | None) -> str | None:
    names = [str(row.get("strategy") or "") for row in strategy_rows if row.get("strategy")]
    if requested:
        if requested not in names:
            raise ValueError(f"baseline strategy not present in replay results: {requested}")
        return requested
    return names[0] if names else None


def proxy_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "decision_count": len(rows),
        "high_pressure_count": sum(1 for row in rows if row.get("admission_pressure") == "high"),
        "moderate_pressure_count": sum(1 for row in rows if row.get("admission_pressure") == "moderate"),
        "none_pressure_count": sum(1 for row in rows if row.get("admission_pressure") == "none"),
        "would_defer_count": sum(1 for row in rows if row.get("would_defer")),
        "would_limit_count": sum(1 for row in rows if row.get("would_limit")),
        "would_relax_count": sum(1 for row in rows if row.get("would_relax")),
    }


def summarize_ablation_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "strategies": [row.get("strategy", UNKNOWN) for row in rows],
        "total_would_defer_count": sum(int(row.get("would_defer_count") or 0) for row in rows),
        "total_would_limit_count": sum(int(row.get("would_limit_count") or 0) for row in rows),
        "data_quality": COMPLETE if rows else UNKNOWN,
    }


def component_for_risk_flag(flag: str, risk_map: dict[str, Any]) -> str:
    for prefix, component in sorted(risk_map.items(), key=lambda item: len(str(item[0])), reverse=True):
        if flag.startswith(str(prefix)):
            return str(component)
    return f"risk_flag:{flag}"


def fuse_pressure(pressures: list[str], profile: dict[str, Any]) -> str:
    order = as_dict(profile.get("pressure_order"))
    if not pressures:
        return UNKNOWN
    return max(pressures, key=lambda pressure: int(order.get(pressure, -1)))


def summarize_first_fields(rows: list[dict[str, str]], fields: Iterable[str]) -> dict[str, Any]:
    values = [
        value
        for value in (first_float(*(row.get(field) for field in fields)) for row in rows)
        if value is not None
    ]
    summary = numeric_summary(values)
    summary["data_quality"] = COMPLETE if values else UNKNOWN
    return summary


def numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "observed_count": 0,
            "mean": None,
            "p50": None,
            "p95": None,
            "max": None,
            "data_quality": UNKNOWN,
        }
    return {
        "observed_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values),
        "data_quality": COMPLETE,
    }


def build_tool_quality_summary(
    tool_rows: list[dict[str, str]],
    step_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step_profile = step_profile or STEP_CSV_PROFILE
    timeout_count = 0
    error_count = 0
    observed_timeout = False
    observed_error = False
    for row in tool_rows:
        timeout_value = first_value(*(row.get(field) for field in step_profile["tool_timeout_fields"]))
        if timeout_value is not None:
            observed_timeout = True
            if truthy(timeout_value):
                timeout_count += 1
        returncode = int_or_none(row.get("returncode"))
        status = str(row.get("status") or "").lower()
        exception_type = row.get("exception_type")
        if returncode is not None or status or exception_type:
            observed_error = True
        if (returncode is not None and returncode != 0) or status in {"error", "failed", "timeout"} or bool(exception_type):
            error_count += 1
    return {
        "tool_call_count": len(tool_rows),
        "timeout_count": timeout_count,
        "error_count": error_count,
        "timeout_rate": timeout_count / len(tool_rows) if tool_rows else None,
        "error_rate": error_count / len(tool_rows) if tool_rows else None,
        "timeout_data_quality": COMPLETE if observed_timeout else UNKNOWN,
        "error_data_quality": COMPLETE if observed_error else UNKNOWN,
    }


def merge_mechanism_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    merged = {
        "fusion_policy": DEFAULT_MECHANISM_PROFILE["fusion_policy"],
        "pressure_order": dict(DEFAULT_MECHANISM_PROFILE["pressure_order"]),
        "risk_flag_component_map": dict(DEFAULT_MECHANISM_PROFILE["risk_flag_component_map"]),
    }
    if not profile:
        return merged
    for key, value in profile.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def any_has_value(rows: list[dict[str, str]], fields: Iterable[str]) -> bool:
    return any(first_value(*(row.get(field) for field in fields)) is not None for row in rows)


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "timeout", "timed_out"}


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in {None, ""}:
            return value
    return None


def count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
