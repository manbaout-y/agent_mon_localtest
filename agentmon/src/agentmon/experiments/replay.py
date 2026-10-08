from __future__ import annotations

from copy import deepcopy
import csv
from collections import Counter, deque
import json
from pathlib import Path
from hashlib import sha256
import sqlite3
from typing import Any, Callable, TextIO

from agentmon.config import AgentMonConfig
from agentmon.optimizer.registry import build_optimizer_strategy, optimizer_strategy_catalog
from agentmon.optimizer.registry import OptimizerStrategy
from agentmon.optimizer.service import evaluate_strategy
from agentmon.optimizer.types import OptimizerDecision
from agentmon.optimizer.harness.audit import audit_decision_timeline
from agentmon.optimizer.harness.schemas import FORBIDDEN_CONCLUSIONS, SCHEMA_VERSION

from .evidence import BatchExperimentData, first_float, int_or_none, iter_step_rows
from .prediction import association_direction, build_prediction_variable_validation, number_or_none
from .snapshots import OptimizerReplaySnapshot, build_snapshot_timeline, iter_snapshot_timeline


def run_optimizer_advice_replay(
    data: BatchExperimentData,
    cfg: AgentMonConfig,
    strategies: list[str],
    schema_profile: dict[str, Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    selected = strategies or [cfg.optimizer.strategy]
    validate_strategy_names(selected)
    snapshots = build_snapshot_timeline(
        data,
        cfg,
        schema_profile,
        progress_callback=(
            (lambda current, total: progress_callback("snapshot_build", current, total))
            if progress_callback
            else None
        ),
    )
    replay_strategies = build_replay_strategies(selected, cfg)
    decision_rows: list[dict[str, Any]] = []
    decision_records: list[tuple[OptimizerReplaySnapshot, OptimizerDecision, dict[str, Any]]] = []
    decision_total = len(snapshots) * len(selected)
    decision_current = 0
    for snapshot in snapshots:
        for strategy_name in selected:
            decision = evaluate_strategy(
                replay_strategies[strategy_name],
                snapshot.queue,
                snapshot.model_service,
            )
            row = decision_timeline_row(snapshot, decision)
            decision_rows.append(row)
            decision_records.append((snapshot, decision, row))
            decision_current += 1
            if progress_callback:
                progress_callback("decision_replay", decision_current, decision_total)
    summary = summarize_strategy_decisions(decision_rows, snapshots)
    audit = audit_decision_timeline(decision_rows)
    prediction_validation = build_prediction_variable_validation(data, snapshots, schema_profile)
    return {
        "snapshots": snapshots,
        "decision_timeline": decision_rows,
        "decision_records": decision_records,
        "strategy_summary": summary,
        "risk_audit": audit,
        "prediction_variable_validation": prediction_validation,
    }


def run_optimizer_advice_replay_streaming(
    data: BatchExperimentData,
    cfg: AgentMonConfig,
    strategies: list[str],
    output_dir: str | Path,
    *,
    schema_profile: dict[str, Any] | None = None,
    work_dir: str | Path | None = None,
    progress_callback: Callable[[str, int, int | None], None] | None = None,
    memory_budget_bytes: int = 32 * 1024 * 1024,
    input_identity: str | None = None,
    source_tree_sha256: str | None = None,
    replay_identity: str | None = None,
) -> dict[str, Any]:
    """Write advice replay outputs while retaining only current stream state."""
    if int(memory_budget_bytes) <= 0:
        raise ValueError("memory_budget_bytes must be greater than zero")
    selected = strategies or [cfg.optimizer.strategy]
    validate_strategy_names(selected)
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage_root = Path(work_dir).expanduser().resolve() if work_dir else root / "stages" / "m2_decision_replay"
    parts_root = stage_root / "parts"
    parts_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "optimizer_decision_timeline": root / "optimizer_decision_timeline.csv",
        "optimizer_decisions": root / "optimizer_decisions.jsonl",
        "optimizer_strategy_summary": root / "optimizer_strategy_summary.json",
        "optimizer_risk_audit": root / "optimizer_risk_audit.json",
        "prediction_variable_validation": root / "prediction_variable_validation.json",
        "prediction_variable_validation_table": root / "prediction_variable_validation.csv",
        "snapshot_manifest": stage_root / "snapshot_manifest.json",
        "checkpoint": stage_root / "checkpoint.json",
    }
    timeline_tmp = paths["optimizer_decision_timeline"].with_name(paths["optimizer_decision_timeline"].name + ".partial")
    decisions_tmp = paths["optimizer_decisions"].with_name(paths["optimizer_decisions"].name + ".partial")
    replay_strategies = build_replay_strategies(selected, cfg)
    summary = OnlineStrategySummary(selected)
    audit = OnlineRiskAudit(selected, parts_root)
    prediction: OnlinePredictionValidation | None = None
    snapshot_parts: list[dict[str, Any]] = []
    snapshot_handle: TextIO | None = None
    snapshot_part_index = 0
    snapshot_part_bytes = 0
    snapshot_part_budget = int(memory_budget_bytes)
    snapshot_count = 0
    decision_count = 0
    try:
        prediction = OnlinePredictionValidation(data, schema_profile, parts_root / "prediction-observations.sqlite3")
        with timeline_tmp.open("w", encoding="utf-8", newline="") as timeline_handle, decisions_tmp.open("w", encoding="utf-8") as decisions_handle:
            writer = csv.DictWriter(timeline_handle, fieldnames=DECISION_TIMELINE_FIELDS)
            writer.writeheader()
            for snapshot in iter_snapshot_timeline(
                data,
                cfg,
                schema_profile,
                work_dir=stage_root / "snapshot_work",
                memory_budget_bytes=memory_budget_bytes,
                progress_callback=(lambda current, total: progress_callback("snapshot_build", current, total)) if progress_callback else None,
            ):
                if snapshot_handle is None or snapshot_part_bytes >= snapshot_part_budget:
                    if snapshot_handle is not None:
                        snapshot_handle.close()
                        snapshot_parts.append(snapshot_part_metadata(parts_root / f"snapshot-{snapshot_part_index:06d}.jsonl"))
                        snapshot_part_index += 1
                    snapshot_path = parts_root / f"snapshot-{snapshot_part_index:06d}.jsonl"
                    snapshot_handle = snapshot_path.open("w", encoding="utf-8")
                    snapshot_part_bytes = 0
                snapshot_json = json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True)
                snapshot_handle.write(snapshot_json + "\n")
                snapshot_part_bytes += len(snapshot_json.encode("utf-8")) + 1
                snapshot_count += 1
                prediction.observe(snapshot)
                for strategy_name in selected:
                    decision = evaluate_strategy(replay_strategies[strategy_name], snapshot.queue, snapshot.model_service)
                    row = decision_timeline_row(snapshot, decision)
                    writer.writerow(row)
                    decisions_handle.write(json.dumps({
                        "schema_version": SCHEMA_VERSION,
                        "result_type": "agentmon.optimizer_experiment.decision",
                        "snapshot": snapshot.to_dict(),
                        "optimizer_decision": decision.to_dict(),
                        "advice_labels": {"would_defer": row["would_defer"], "would_limit": row["would_limit"], "would_relax": row["would_relax"]},
                        "limitations": list(FORBIDDEN_CONCLUSIONS),
                    }, ensure_ascii=False, sort_keys=True) + "\n")
                    summary.observe(row)
                    audit.observe(row)
                    decision_count += 1
                    if progress_callback:
                        progress_callback("decision_replay", decision_count, None)
                atomic_json(paths["checkpoint"], {
                    "schema_version": "2.0",
                    "snapshot_count": snapshot_count,
                    "decision_count": decision_count,
                    "last_snapshot_index": snapshot.snapshot_index,
                    "state": "RUNNING",
                    "input_identity": input_identity,
                    "source_tree_sha256": source_tree_sha256,
                    "replay_identity": replay_identity,
                })
        if snapshot_handle is not None:
            snapshot_handle.close()
            snapshot_parts.append(snapshot_part_metadata(parts_root / f"snapshot-{snapshot_part_index:06d}.jsonl"))
        timeline_tmp.replace(paths["optimizer_decision_timeline"])
        decisions_tmp.replace(paths["optimizer_decisions"])
        strategy_summary = summary.finalize(snapshot_count)
        risk_audit = audit.finalize(paths["optimizer_risk_audit"])
        prediction_validation = prediction.finalize()
        write_json(paths["optimizer_strategy_summary"], strategy_summary)
        write_json(paths["prediction_variable_validation"], prediction_validation)
        write_csv(paths["prediction_variable_validation_table"], prediction_validation["rows"])
        atomic_json(paths["snapshot_manifest"], {
            "schema_version": "2.0",
            "result_type": "agentmon.optimizer_experiment.snapshot_stream",
            "snapshot_count": snapshot_count,
            "parts": snapshot_parts,
            "streaming_contract": {
                "memory_budget_bytes": int(memory_budget_bytes),
                "snapshot_part_byte_budget": snapshot_part_budget,
                "static_timepoint_ordering": "external_merge_unique_parts",
                "queue_percentile_backend": "stage_private_sqlite_treap_exact",
                "prediction_observation_backend": "stage_private_sqlite_ordered_index",
                "step_input_adapter": "materialized_compatibility" if data.step_rows_materialized else "source_only_csv_iterator",
                "metrics_sqlite_used": False,
                "max_resident_snapshot_count": 1,
                "max_resident_decision_group_count": 1,
                "strategy_count": len(selected),
                "limitations": [
                    "The snapshot object itself contains the currently active runs.",
                    "Step CSV compatibility input may still be materialized by the M1 adapter.",
                ],
            },
        })
        artifact_hashes = {
            key: {"path": str(path), "sha256": file_sha256(path)}
            for key, path in paths.items()
            if key != "checkpoint" and path.is_file()
        }
        atomic_json(paths["checkpoint"], {
            "schema_version": "2.0",
            "snapshot_count": snapshot_count,
            "decision_count": decision_count,
            "state": "COMPLETE",
            "snapshot_manifest_sha256": file_sha256(paths["snapshot_manifest"]),
            "input_identity": input_identity,
            "source_tree_sha256": source_tree_sha256,
            "replay_identity": replay_identity,
            "artifacts": artifact_hashes,
        })
        return {
            "strategy_summary": strategy_summary,
            "risk_audit": risk_audit,
            "prediction_variable_validation": prediction_validation,
            "paths": {key: str(value) for key, value in paths.items()},
            "snapshot_count": snapshot_count,
            "decision_count": decision_count,
            "streaming": True,
            "replay_identity": replay_identity,
        }
    finally:
        if snapshot_handle is not None and not snapshot_handle.closed:
            snapshot_handle.close()
        audit.close()
        if prediction is not None:
            prediction.close()


DECISION_TIMELINE_FIELDS = (
    "snapshot_index", "timestamp_mono_ns", "timestamp_offset_s", "strategy", "mode", "admission_pressure",
    "running_count", "max_running_runs", "run_count", "model_running_requests", "model_waiting_requests",
    "model_queue_latency_p95_s", "model_kv_usage_ratio", "model_service_quality", "target_runtime_default",
    "would_defer", "would_limit", "would_relax", "risk_flags", "reason", "confidence", "evidence_source", "data_quality",
)


class OnlineStrategySummary:
    def __init__(self, strategies: list[str]) -> None:
        self.rows = {name: {"decision_count": 0, "pressure_counts": Counter(), "would_defer_count": 0, "would_limit_count": 0, "would_relax_count": 0, "risk_flag_counts": Counter()} for name in strategies}

    def observe(self, row: dict[str, Any]) -> None:
        current = self.rows[str(row["strategy"])]
        current["decision_count"] += 1
        current["pressure_counts"][str(row["admission_pressure"])] += 1
        for field in ("would_defer", "would_limit", "would_relax"):
            if row.get(field):
                current[f"{field}_count"] += 1
        for flag in str(row.get("risk_flags") or "").split(";"):
            if flag:
                current["risk_flag_counts"][flag] += 1

    def finalize(self, snapshot_count: int) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "result_type": "agentmon.optimizer_experiment.strategy_summary",
            "snapshot_count": snapshot_count,
            "decision_count": sum(int(row["decision_count"]) for row in self.rows.values()),
            "strategies": [
                {
                    "strategy": name,
                    "decision_count": item["decision_count"],
                    "pressure_counts": dict(sorted(item["pressure_counts"].items())),
                    "would_defer_count": item["would_defer_count"],
                    "would_limit_count": item["would_limit_count"],
                    "would_relax_count": item["would_relax_count"],
                    "risk_flag_counts": dict(sorted(item["risk_flag_counts"].items())),
                }
                for name, item in sorted(self.rows.items())
            ],
            "limitations": ["Counts describe offline advice replay only.", *FORBIDDEN_CONCLUSIONS],
        }


class OnlineRiskAudit:
    def __init__(self, strategies: list[str], parts_root: Path) -> None:
        self.state = {name: {"decision_count": 0, "pressure_counts": Counter(), "previous": None, "recent": deque(maxlen=2), "transitions": 0, "would_defer": 0, "would_limit": 0, "would_relax": 0, "current_defer": 0, "current_limit": 0, "max_defer": 0, "max_limit": 0, "risk_flags": Counter()} for name in strategies}
        self.pattern_paths = {name: parts_root / f"risk-patterns-{name}.jsonl" for name in strategies}
        self.pattern_handles = {name: path.open("w", encoding="utf-8") for name, path in self.pattern_paths.items()}

    def observe(self, row: dict[str, Any]) -> None:
        name = str(row["strategy"])
        state = self.state[name]
        pressure = str(row.get("admission_pressure") or "UNKNOWN")
        if state["previous"] is not None and state["previous"] != pressure:
            state["transitions"] += 1
        recent = state["recent"]
        if len(recent) == 2 and recent[0] == pressure and recent[0] != recent[1]:
            self.pattern_handles[name].write(json.dumps({"start_snapshot_index": int(row["snapshot_index"]) - 2, "pattern": [recent[0], recent[1], pressure]}, ensure_ascii=False, sort_keys=True) + "\n")
        recent.append(pressure)
        state["previous"] = pressure
        state["decision_count"] += 1
        state["pressure_counts"][pressure] += 1
        for source, current_key, max_key in (("would_defer", "current_defer", "max_defer"), ("would_limit", "current_limit", "max_limit")):
            if row.get(source):
                state[source] += 1
                state[current_key] += 1
                state[max_key] = max(state[max_key], state[current_key])
            else:
                state[current_key] = 0
        if row.get("would_relax"):
            state["would_relax"] += 1
        for flag in str(row.get("risk_flags") or "").split(";"):
            if flag:
                state["risk_flags"][flag] += 1

    def finalize(self, path: Path) -> dict[str, Any]:
        self.close()
        summary_rows = [self.summary(name) for name in sorted(self.state)]
        temporary = path.with_name(path.name + ".partial")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write('{"audit_mode":"offline_advice_replay","global_limitations":')
            handle.write(json.dumps(["Advice replay does not prove online runtime benefit.", "would_defer and would_limit are inferred from decision output and are not enforced actions.", "Starvation risk is unresolved without a policy threshold and enforced queue history.", *FORBIDDEN_CONCLUSIONS], ensure_ascii=False))
            handle.write(',"result_type":"agentmon.optimizer_experiment.risk_audit","schema_version":')
            handle.write(json.dumps(SCHEMA_VERSION))
            handle.write(',"strategy_audits":[')
            for index, row in enumerate(summary_rows):
                if index:
                    handle.write(",")
                handle.write(json.dumps({key: value for key, value in row.items() if key != "alternating_pressure_patterns"}, ensure_ascii=False, sort_keys=True)[:-1])
                handle.write(',"alternating_pressure_patterns":[')
                pattern_path = self.pattern_paths[row["strategy"]]
                first = True
                if pattern_path.is_file():
                    with pattern_path.open("r", encoding="utf-8") as patterns:
                        for line in patterns:
                            if not first:
                                handle.write(",")
                            handle.write(line.strip())
                            first = False
                handle.write("]}")
            handle.write("]}\n")
        temporary.replace(path)
        return {"schema_version": SCHEMA_VERSION, "result_type": "agentmon.optimizer_experiment.risk_audit", "strategy_audits": summary_rows, "global_limitations": ["Advice replay does not prove online runtime benefit.", *FORBIDDEN_CONCLUSIONS]}

    def summary(self, name: str) -> dict[str, Any]:
        state = self.state[name]
        count = state["decision_count"]
        defer = state["would_defer"]
        limit = state["would_limit"]
        return {
            "strategy": name,
            "decision_count": count,
            "pressure_counts": dict(sorted(state["pressure_counts"].items())),
            "pressure_transition_count": state["transitions"],
            "alternating_pressure_patterns": [],
            "oscillation_status": "ALTERNATING_PATTERN_OBSERVED" if self.pattern_paths[name].is_file() and self.pattern_paths[name].stat().st_size else "PRESSURE_TRANSITIONS_OBSERVED" if state["transitions"] else "NO_PRESSURE_TRANSITIONS_OBSERVED",
            "would_defer_count": defer,
            "would_limit_count": limit,
            "would_relax_count": state["would_relax"],
            "max_consecutive_would_defer_snapshots": state["max_defer"],
            "max_consecutive_would_limit_snapshots": state["max_limit"],
            "risk_flag_counts": dict(sorted(state["risk_flags"].items())),
            "starvation_risk_status": "UNKNOWN" if not count else "PERSISTENT_WOULD_DEFER_OBSERVED" if defer == count else "UNRESOLVED" if defer else "NO_WOULD_DEFER_OBSERVED",
            "over_limit_status": "UNKNOWN" if not count else "PERSISTENT_WOULD_LIMIT_OBSERVED" if limit == count else "INTERMITTENT_WOULD_LIMIT_OBSERVED" if limit else "NO_WOULD_LIMIT_OBSERVED",
        }

    def close(self) -> None:
        for handle in self.pattern_handles.values():
            if not handle.closed:
                handle.close()


class FutureModelObservationIndex:
    """Disk-backed next-observation cursor for streaming prediction validation."""

    def __init__(self, data: BatchExperimentData, profile: dict[str, Any], path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute(
            "CREATE TABLE observations (start_mono_ns INTEGER NOT NULL, ordinal INTEGER NOT NULL, queue_s REAL, ttft_s REAL, decode_s REAL, PRIMARY KEY(start_mono_ns, ordinal))"
        )
        for ordinal, row in enumerate(iter_step_rows(data)):
            if row.get("item_type") != "model":
                continue
            start = int_or_none(row.get("item_start_mono_ns"))
            if start is None:
                continue
            self.connection.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?)",
                (
                    start,
                    ordinal,
                    first_float(*(row.get(field) for field in profile["model_phase_duration_fields"]["model_queue"])),
                    first_float(*(row.get(field) for field in profile["request_ttft_fields"])),
                    first_float(*(row.get(field) for field in profile["model_phase_duration_fields"]["model_decode"])),
                ),
            )
        self.connection.commit()

    def next_at_or_after(self, timestamp_mono_ns: int | None) -> dict[str, float | int | None] | None:
        if timestamp_mono_ns is None:
            return None
        row = self.connection.execute(
            "SELECT start_mono_ns, queue_s, ttft_s, decode_s FROM observations WHERE start_mono_ns >= ? ORDER BY start_mono_ns, ordinal LIMIT 1",
            (timestamp_mono_ns,),
        ).fetchone()
        if row is None:
            return None
        return {"start_mono_ns": int(row[0]), "queue_s": row[1], "ttft_s": row[2], "decode_s": row[3]}

    def close(self) -> None:
        self.connection.close()


class OnlinePredictionValidation:
    def __init__(self, data: BatchExperimentData, schema_profile: dict[str, Any] | None, index_path: str | Path) -> None:
        profile = merge_prediction_profile(schema_profile)
        self.future_index = FutureModelObservationIndex(data, profile, index_path)
        self.snapshot_count = 0
        self.candidates = {
            "queue.running_count": lambda snapshot: snapshot.queue.running_count,
            "queue.capacity_utilization": lambda snapshot: snapshot.queue.running_count / snapshot.queue.max_running_runs if snapshot.queue.max_running_runs > 0 else None,
            "model.running_requests": lambda snapshot: snapshot.model_service.running_requests if snapshot.model_service else None,
            "model.waiting_requests": lambda snapshot: snapshot.model_service.waiting_requests if snapshot.model_service else None,
            "model.kv_usage_ratio": lambda snapshot: snapshot.model_service.kv_usage_ratio if snapshot.model_service else None,
        }
        self.outcomes = {"next_request.queue_s": "queue_s", "next_request.ttft_s": "ttft_s", "next_request.decode_s": "decode_s"}
        self.stats = {(candidate, outcome): OnlineCovariance() for candidate in self.candidates for outcome in self.outcomes}
        self.x_counts = Counter()
        self.y_counts = Counter()

    def observe(self, snapshot: OptimizerReplaySnapshot) -> None:
        timestamp = snapshot.timestamp_mono_ns
        future = self.future_index.next_at_or_after(timestamp)
        self.snapshot_count += 1
        outcome_values = {
            outcome_name: number_or_none(future.get(field)) if future else None
            for outcome_name, field in self.outcomes.items()
        }
        for outcome_name, y_value in outcome_values.items():
            if y_value is not None:
                self.y_counts[outcome_name] += 1
        for candidate_name, extractor in self.candidates.items():
            x_value = number_or_none(extractor(snapshot))
            if x_value is not None:
                self.x_counts[candidate_name] += 1
            for outcome_name, y_value in outcome_values.items():
                if x_value is not None and y_value is not None:
                    self.stats[(candidate_name, outcome_name)].add(x_value, y_value)

    def finalize(self) -> dict[str, Any]:
        rows = []
        for candidate_name in self.candidates:
            for outcome_name in self.outcomes:
                stats = self.stats[(candidate_name, outcome_name)]
                correlation = stats.pearson()
                rows.append({"candidate_variable": candidate_name, "future_outcome": outcome_name, "snapshot_count": self.snapshot_count, "candidate_observed_count": self.x_counts[candidate_name], "outcome_observed_count": self.y_counts[outcome_name], "paired_observation_count": stats.count, "association_direction": association_direction(correlation), "pearson_correlation": correlation, "validation_method": "next_future_model_request", "data_quality": "COMPLETE" if stats.count >= 2 else "PARTIAL" if stats.count else "UNKNOWN", "evidence_source": "snapshot_timeline;step_csv", "forbidden_conclusion": "Do not treat lagged association as causal prediction quality."})
        return {"schema_version": SCHEMA_VERSION, "result_type": "agentmon.optimizer_experiment.prediction_variable_validation", "validation_mode": "lagged_observation_pairs", "rows": rows, "summary": {"candidate_count": len(rows), "validated_count": sum(1 for row in rows if row["data_quality"] == "COMPLETE"), "partial_count": sum(1 for row in rows if row["data_quality"] == "PARTIAL"), "unknown_count": sum(1 for row in rows if row["data_quality"] == "UNKNOWN")}, "limitations": ["This validates candidate variable availability and lagged association only.", "It does not train a predictor or claim causal control effect.", *FORBIDDEN_CONCLUSIONS]}

    def close(self) -> None:
        self.future_index.close()


class OnlineCovariance:
    def __init__(self) -> None:
        self.count = 0
        self.mean_x = 0.0
        self.mean_y = 0.0
        self.co_moment = 0.0
        self.m2_x = 0.0
        self.m2_y = 0.0

    def add(self, x_value: float, y_value: float) -> None:
        self.count += 1
        dx = x_value - self.mean_x
        self.mean_x += dx / self.count
        dy = y_value - self.mean_y
        self.mean_y += dy / self.count
        self.co_moment += dx * (y_value - self.mean_y)
        self.m2_x += dx * (x_value - self.mean_x)
        self.m2_y += dy * (y_value - self.mean_y)

    def pearson(self) -> float | None:
        if self.count < 2 or self.m2_x == 0 or self.m2_y == 0:
            return None
        return self.co_moment / ((self.m2_x * self.m2_y) ** 0.5)


def merge_prediction_profile(schema_profile: dict[str, Any] | None) -> dict[str, Any]:
    from agentmon.optimizer.harness.schemas import merge_schema_profile

    return merge_schema_profile(schema_profile)["step_csv_profile"]


def snapshot_part_metadata(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        row_count = sum(1 for _ in handle)
    return {"path": str(path), "sha256": file_sha256(path), "row_count": row_count}


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_replay_outputs(outputs: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "optimizer_decision_timeline": root / "optimizer_decision_timeline.csv",
        "optimizer_decisions": root / "optimizer_decisions.jsonl",
        "optimizer_strategy_summary": root / "optimizer_strategy_summary.json",
        "optimizer_risk_audit": root / "optimizer_risk_audit.json",
        "prediction_variable_validation": root / "prediction_variable_validation.json",
        "prediction_variable_validation_table": root / "prediction_variable_validation.csv",
    }
    write_csv(paths["optimizer_decision_timeline"], outputs["decision_timeline"])
    with paths["optimizer_decisions"].open("w", encoding="utf-8") as handle:
        for snapshot, decision, row in outputs["decision_records"]:
            handle.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "result_type": "agentmon.optimizer_experiment.decision",
                        "snapshot": snapshot.to_dict(),
                        "optimizer_decision": decision.to_dict(),
                        "advice_labels": {
                            "would_defer": row["would_defer"],
                            "would_limit": row["would_limit"],
                            "would_relax": row["would_relax"],
                        },
                        "limitations": list(FORBIDDEN_CONCLUSIONS),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    write_json(paths["optimizer_strategy_summary"], outputs["strategy_summary"])
    write_json(paths["optimizer_risk_audit"], outputs["risk_audit"])
    write_json(paths["prediction_variable_validation"], outputs["prediction_variable_validation"])
    write_csv(paths["prediction_variable_validation_table"], outputs["prediction_variable_validation"].get("rows", []))
    return paths


def evaluate_replay_strategy(
    strategy_name: str,
    cfg: AgentMonConfig,
    snapshot: OptimizerReplaySnapshot,
) -> OptimizerDecision:
    strategy_cfg = deepcopy(cfg)
    strategy_cfg.optimizer.strategy = strategy_name
    strategy_cfg.optimizer.mode = "advise"
    strategy = build_optimizer_strategy(strategy_cfg)
    return evaluate_strategy(strategy, snapshot.queue, snapshot.model_service)


def build_replay_strategies(
    strategy_names: list[str],
    cfg: AgentMonConfig,
) -> dict[str, OptimizerStrategy]:
    strategies: dict[str, OptimizerStrategy] = {}
    for strategy_name in strategy_names:
        strategy_cfg = deepcopy(cfg)
        strategy_cfg.optimizer.strategy = strategy_name
        strategy_cfg.optimizer.mode = "advise"
        strategies[strategy_name] = build_optimizer_strategy(strategy_cfg)
    return strategies


def decision_timeline_row(
    snapshot: OptimizerReplaySnapshot,
    decision: OptimizerDecision,
) -> dict[str, Any]:
    running_count = snapshot.queue.running_count
    target_default = decision.target_concurrency.get("runtime.default")
    would_defer = decision.admission_pressure in {"moderate", "high"}
    would_limit = (
        decision.admission_pressure == "high"
        or (target_default is not None and target_default < running_count)
        or any(flag.endswith("_high") for flag in decision.risk_flags)
    )
    would_relax = decision.admission_pressure == "none" and (
        target_default is None or running_count < target_default
    )
    return {
        "snapshot_index": snapshot.snapshot_index,
        "timestamp_mono_ns": snapshot.timestamp_mono_ns,
        "timestamp_offset_s": snapshot.timestamp_offset_s,
        "strategy": decision.strategy,
        "mode": decision.mode,
        "admission_pressure": decision.admission_pressure,
        "running_count": running_count,
        "max_running_runs": snapshot.queue.max_running_runs,
        "run_count": snapshot.queue.run_count,
        "model_running_requests": snapshot.model_service.running_requests if snapshot.model_service else None,
        "model_waiting_requests": snapshot.model_service.waiting_requests if snapshot.model_service else None,
        "model_queue_latency_p95_s": snapshot.model_service.queue_latency_p95_s if snapshot.model_service else None,
        "model_kv_usage_ratio": snapshot.model_service.kv_usage_ratio if snapshot.model_service else None,
        "model_service_quality": snapshot.model_service.quality if snapshot.model_service else None,
        "target_runtime_default": target_default,
        "would_defer": would_defer,
        "would_limit": would_limit,
        "would_relax": would_relax,
        "risk_flags": ";".join(decision.risk_flags),
        "reason": decision.reason,
        "confidence": decision.confidence,
        "evidence_source": snapshot.evidence.get("snapshot_source"),
        "data_quality": snapshot.evidence.get("model_service_quality"),
    }


def summarize_strategy_decisions(
    decision_rows: list[dict[str, Any]],
    snapshots: list[OptimizerReplaySnapshot],
) -> dict[str, Any]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in decision_rows:
        by_strategy.setdefault(str(row["strategy"]), []).append(row)
    strategy_rows: list[dict[str, Any]] = []
    for strategy, rows in sorted(by_strategy.items()):
        pressure_counts = count_values(str(row["admission_pressure"]) for row in rows)
        risk_counts = count_values(
            flag
            for row in rows
            for flag in str(row.get("risk_flags") or "").split(";")
            if flag
        )
        strategy_rows.append(
            {
                "strategy": strategy,
                "decision_count": len(rows),
                "pressure_counts": pressure_counts,
                "would_defer_count": sum(1 for row in rows if row.get("would_defer")),
                "would_limit_count": sum(1 for row in rows if row.get("would_limit")),
                "would_relax_count": sum(1 for row in rows if row.get("would_relax")),
                "risk_flag_counts": risk_counts,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.strategy_summary",
        "snapshot_count": len(snapshots),
        "decision_count": len(decision_rows),
        "strategies": strategy_rows,
        "limitations": [
            "Counts describe offline advice replay only.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def validate_strategy_names(strategies: list[str]) -> None:
    available = {row["name"] for row in optimizer_strategy_catalog()}
    unknown = [name for name in strategies if name not in available]
    if unknown:
        raise ValueError(f"unknown optimizer strategy: {', '.join(unknown)}")


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


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
