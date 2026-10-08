from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.optimizer.registry import build_optimizer_strategy
from agentmon.optimizer.service import queue_snapshot_from_scheduler
from agentmon.optimizer.types import QueueSnapshot, RunRuntimeProfile


class ReplaySimulator:
    """Replay optimizer decisions from captured scheduler/runtime snapshots."""

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg
        self.strategy = build_optimizer_strategy(cfg)

    def replay_optimizer_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        queue = queue_snapshot_from_payload(payload)
        decision = self.strategy.evaluate(queue)
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.replay.optimizer_decision",
            "source_result_type": payload.get("result_type", "unknown"),
            "optimizer_decision": decision.to_dict(),
        }

    def replay_optimizer_snapshot_file(self, path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("replay snapshot must be a JSON object")
        return self.replay_optimizer_snapshot(payload)

    def replay_runtime_events_file(self, path: str | Path) -> dict[str, Any]:
        events = _read_jsonl(path)
        return replay_runtime_events(events)


def replay_runtime_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    queue_state_by_id: dict[str, dict[str, Any]] = {}
    daemon_passes: list[dict[str, Any]] = []
    optimizer_decisions: list[dict[str, Any]] = []
    control_events: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        source = str(event.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        queue_id = payload.get("queue_id")
        if isinstance(queue_id, str) and queue_id:
            row = queue_state_by_id.setdefault(
                queue_id,
                {"queue_id": queue_id, "state": "unknown", "events": []},
            )
            row["events"].append(event_type)
            task_id = payload.get("task_id")
            if isinstance(task_id, str):
                row["task_id"] = task_id
            row["state"] = _queue_state_for_event(event_type, payload, row["state"])
            if payload.get("lease_owner") is not None:
                row["lease_owner"] = payload.get("lease_owner")
        if event_type in {"runtime.daemon.pass_completed", "runtime.daemon.orphan_recovery_completed"}:
            daemon_passes.append(dict(payload))
        if event_type == "optimizer.decision":
            optimizer_decisions.append(dict(payload))
        if event_type.startswith("control."):
            control_events.append({"event_type": event_type, "payload": dict(payload)})
    return {
        "schema_version": "1.0",
        "result_type": "agentmon.replay.runtime_events",
        "event_count": len(events),
        "event_type_counts": event_type_counts,
        "source_counts": source_counts,
        "queue_state_by_id": queue_state_by_id,
        "daemon_passes": daemon_passes,
        "optimizer_decisions": optimizer_decisions,
        "control_events": control_events,
    }


def _queue_state_for_event(event_type: str, payload: dict[str, Any], current: str) -> str:
    if event_type == "runtime.queue.enqueued":
        return "queued"
    if event_type == "runtime.queue.running":
        return "running"
    if event_type == "runtime.queue.deferred":
        return "deferred"
    if event_type == "runtime.queue.failed":
        return "failed"
    if event_type == "runtime.queue.finished":
        state = payload.get("state")
        return str(state) if state else "finished"
    if event_type == "runtime.queue.orphan_recovered":
        state = payload.get("state")
        return str(state) if state else current
    return current


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def queue_snapshot_from_payload(payload: dict[str, Any]) -> QueueSnapshot:
    result_type = payload.get("result_type")
    if result_type == "agentmon.scheduler.snapshot":
        return queue_snapshot_from_scheduler(payload)
    if result_type == "agentmon.runtime.queue_snapshot" and isinstance(payload.get("scheduler"), dict):
        return queue_snapshot_from_scheduler(payload["scheduler"])
    if isinstance(payload.get("queue"), dict):
        return queue_snapshot_from_payload(payload["queue"])
    return _queue_snapshot_from_queue_like_payload(payload)


def _queue_snapshot_from_queue_like_payload(payload: dict[str, Any]) -> QueueSnapshot:
    runs = tuple(
        RunRuntimeProfile(
            run_id=str(row.get("run_id") or ""),
            task_id=str(row["task_id"]) if row.get("task_id") not in {None, ""} else None,
            state=str(row.get("state") or "unknown"),
            backend=str(row.get("backend") or "unknown"),
            duration_ns=row.get("duration_ns") if isinstance(row.get("duration_ns"), int) else None,
            classification=str(row.get("classification") or "unknown"),
        )
        for row in payload.get("runs", [])
        if isinstance(row, dict)
    )
    state_counts = _state_counts(payload, runs)
    running_count = int(payload.get("running_count") or state_counts.get("running", 0))
    return QueueSnapshot(
        running_count=running_count,
        max_running_runs=int(payload.get("max_running_runs") or 0),
        run_count=int(payload.get("run_count") or len(runs)),
        state_counts=state_counts,
        runs=runs,
        quality=str(payload.get("quality") or "replay_snapshot"),
    )


def _state_counts(payload: dict[str, Any], runs: tuple[RunRuntimeProfile, ...]) -> dict[str, int]:
    raw_counts = payload.get("state_counts")
    if isinstance(raw_counts, dict):
        return {
            str(key): int(value)
            for key, value in raw_counts.items()
            if isinstance(value, int)
        }
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.state] = counts.get(run.state, 0) + 1
    return counts
