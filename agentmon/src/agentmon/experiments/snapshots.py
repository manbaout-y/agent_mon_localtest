from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256
import heapq
import json
from math import isfinite
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Callable, Iterator

from agentmon.config import AgentMonConfig
from agentmon.optimizer.harness.schemas import DEFAULT_P2_SERVICE_EVENT_PROFILE, DEFAULT_STEP_CSV_PROFILE, merge_schema_profile
from agentmon.optimizer.types import ModelServiceSnapshot, QueueSnapshot, RunRuntimeProfile

from .evidence import (
    BatchExperimentData,
    COMPLETE,
    PARTIAL,
    UNKNOWN,
    first_float,
    first_int,
    float_or_none,
    int_or_none,
    iter_batch_results,
    iter_run_events,
    iter_step_rows,
)


STEP_CSV_PROFILE = DEFAULT_STEP_CSV_PROFILE
P2_SERVICE_EVENT_PROFILE = DEFAULT_P2_SERVICE_EVENT_PROFILE


@dataclass(frozen=True)
class OptimizerReplaySnapshot:
    snapshot_index: int
    timestamp_mono_ns: int | None
    timestamp_offset_s: float | None
    queue: QueueSnapshot
    model_service: ModelServiceSnapshot | None
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.optimizer_experiment.snapshot",
            "snapshot_index": self.snapshot_index,
            "timestamp_mono_ns": self.timestamp_mono_ns,
            "timestamp_offset_s": self.timestamp_offset_s,
            "queue": self.queue.to_dict(),
            "model_service": self.model_service.to_dict() if self.model_service else None,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class RunInterval:
    run_id: str
    task_id: str | None
    start_mono_ns: int | None
    end_mono_ns: int | None
    source: str
    terminal_state: str


@dataclass(frozen=True)
class ModelServiceSampleIndex:
    timestamps: tuple[int, ...]
    latest_samples: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ModelStepInterval:
    row_index: int
    start_mono_ns: int | None
    end_mono_ns: int | None
    running_requests: int | None
    waiting_requests: int | None
    queue_latency_s: float | None
    kv_usage_ratio: float | None


class ActiveMaximum:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int]] = []
        self._active: set[int] = set()

    def add(self, row_index: int, value: int | None) -> None:
        if value is None:
            return
        self._active.add(row_index)
        heapq.heappush(self._heap, (-value, row_index))

    def remove(self, row_index: int) -> None:
        self._active.discard(row_index)

    def maximum(self) -> int | None:
        while self._heap and self._heap[0][1] not in self._active:
            heapq.heappop(self._heap)
        return -self._heap[0][0] if self._heap else None


class DiskOrderStatistic:
    """Exact append-only percentile index with bounded Python residency.

    This is intentionally a stage-private derived SQLite file, not the optional
    AgentMon ``metrics.sqlite3`` query cache.  A deterministic treap stores
    subtree counts, allowing exact rank lookups without retaining every unique
    value or a Fenwick tree in the replay process.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute(
            "CREATE TABLE nodes (node_key TEXT PRIMARY KEY, value REAL NOT NULL, priority INTEGER NOT NULL, count INTEGER NOT NULL, subtree_size INTEGER NOT NULL, left_key TEXT, right_key TEXT)"
        )
        self.root_key: str | None = None
        self.count = 0
        self._percentile_cache: dict[float, float | None] = {}

    def add(self, value: float | None) -> None:
        if value is None:
            return
        numeric = float(value)
        if not isfinite(numeric):
            return
        node_key = numeric.hex()
        with self.connection:
            self.root_key = self._insert(self.root_key, node_key, numeric)
        self.count += 1
        self._percentile_cache.clear()

    def percentile(self, percentile_value: float) -> float | None:
        if self.count == 0:
            return None
        cached = self._percentile_cache.get(percentile_value)
        if cached is not None or percentile_value in self._percentile_cache:
            return cached
        rank = (self.count - 1) * percentile_value / 100.0
        lower = int(rank)
        upper = min(lower + 1, self.count - 1)
        lower_value = self._value_at_rank(lower)
        upper_value = self._value_at_rank(upper)
        value = lower_value + (upper_value - lower_value) * (rank - lower)
        self._percentile_cache[percentile_value] = value
        return value

    def close(self) -> None:
        self.connection.close()

    def _insert(self, root_key: str | None, node_key: str, value: float) -> str:
        if root_key is None:
            self.connection.execute(
                "INSERT INTO nodes(node_key, value, priority, count, subtree_size, left_key, right_key) VALUES (?, ?, ?, 1, 1, NULL, NULL)",
                (node_key, value, self._priority(node_key)),
            )
            return node_key
        root = self._node(root_key)
        if value == root["value"]:
            self.connection.execute(
                "UPDATE nodes SET count = count + 1, subtree_size = subtree_size + 1 WHERE node_key = ?",
                (root_key,),
            )
            return root_key
        if value < root["value"]:
            left_key = self._insert(root["left_key"], node_key, value)
            self._set_child(root_key, "left_key", left_key)
            if self._node(left_key)["priority"] < root["priority"]:
                root_key = self._rotate_right(root_key)
        else:
            right_key = self._insert(root["right_key"], node_key, value)
            self._set_child(root_key, "right_key", right_key)
            if self._node(right_key)["priority"] < root["priority"]:
                root_key = self._rotate_left(root_key)
        self._update_size(root_key)
        return root_key

    def _rotate_right(self, root_key: str) -> str:
        root = self._node(root_key)
        left_key = root["left_key"]
        assert left_key is not None
        left = self._node(left_key)
        self._set_child(root_key, "left_key", left["right_key"])
        self._set_child(left_key, "right_key", root_key)
        self._update_size(root_key)
        self._update_size(left_key)
        return left_key

    def _rotate_left(self, root_key: str) -> str:
        root = self._node(root_key)
        right_key = root["right_key"]
        assert right_key is not None
        right = self._node(right_key)
        self._set_child(root_key, "right_key", right["left_key"])
        self._set_child(right_key, "left_key", root_key)
        self._update_size(root_key)
        self._update_size(right_key)
        return right_key

    def _value_at_rank(self, rank: int) -> float:
        key = self.root_key
        while key is not None:
            node = self._node(key)
            left_size = self._size(node["left_key"])
            if rank < left_size:
                key = node["left_key"]
            elif rank < left_size + node["count"]:
                return float(node["value"])
            else:
                rank -= left_size + node["count"]
                key = node["right_key"]
        raise RuntimeError("order-statistic rank is outside the index")

    def _node(self, key: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT node_key, value, priority, count, subtree_size, left_key, right_key FROM nodes WHERE node_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("order-statistic node is missing")
        return {
            "node_key": row[0], "value": float(row[1]), "priority": int(row[2]),
            "count": int(row[3]), "subtree_size": int(row[4]), "left_key": row[5], "right_key": row[6],
        }

    def _size(self, key: str | None) -> int:
        if key is None:
            return 0
        return int(self._node(key)["subtree_size"])

    def _set_child(self, key: str, column: str, value: str | None) -> None:
        self.connection.execute(f"UPDATE nodes SET {column} = ? WHERE node_key = ?", (value, key))

    def _update_size(self, key: str) -> None:
        node = self._node(key)
        size = node["count"] + self._size(node["left_key"]) + self._size(node["right_key"])
        self.connection.execute("UPDATE nodes SET subtree_size = ? WHERE node_key = ?", (size, key))

    @staticmethod
    def _priority(node_key: str) -> int:
        return int.from_bytes(sha256(node_key.encode("ascii")).digest()[:8], "big") & ((1 << 63) - 1)


def build_snapshot_timeline(
    data: BatchExperimentData,
    cfg: AgentMonConfig,
    schema_profile: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
) -> list[OptimizerReplaySnapshot]:
    return list(iter_snapshot_timeline(data, cfg, schema_profile, progress_callback=progress_callback))


def iter_snapshot_timeline(
    data: BatchExperimentData,
    cfg: AgentMonConfig,
    schema_profile: dict[str, Any] | None = None,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    work_dir: str | Path | None = None,
    memory_budget_bytes: int = 32 * 1024 * 1024,
) -> Iterator[OptimizerReplaySnapshot]:
    """Build replay snapshots as a one-pass stream.

    P2 samples are sorted into bounded derived parts before they are merged.
    The raw JSONL remains the authority; the parts are disposable stage work.
    """
    resolved_profile = merge_schema_profile(schema_profile)
    step_profile = resolved_profile["step_csv_profile"]
    run_intervals = build_run_intervals(data, step_profile)
    model_intervals = build_model_step_intervals(data, step_profile)
    max_running_runs, capacity_source = scheduler_capacity(data, cfg)
    run_starts = group_intervals_by_time(run_intervals, "start_mono_ns")
    run_ends = group_intervals_by_time(run_intervals, "end_mono_ns")
    model_starts = group_model_intervals_by_time(model_intervals, "start_mono_ns")
    model_ends = group_model_intervals_by_time(model_intervals, "end_mono_ns")
    active_runs: dict[str, RunInterval] = {}
    active_models: dict[int, ModelStepInterval] = {}
    active_running = ActiveMaximum()
    active_waiting = ActiveMaximum()
    completed_model_count = 0
    completed_kv_max: float | None = None
    latest_service: dict[str, Any] = {}
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        temporary_root = tempfile.TemporaryDirectory(prefix="agentmon-snapshot-stream.")
        sample_work_dir = Path(temporary_root.name)
    else:
        sample_work_dir = Path(work_dir).expanduser().resolve()
    completed_queue = DiskOrderStatistic(sample_work_dir / "queue-percentile.sqlite3")
    static_timepoints = iter_static_timepoints(
        data,
        run_intervals,
        work_dir=sample_work_dir / "static-timepoint-parts",
        memory_budget_bytes=memory_budget_bytes,
    )
    sample_iter = iter_model_service_event_samples(
        data,
        resolved_profile["p2_service_event_profile"],
        work_dir=sample_work_dir / "service-sample-parts",
        memory_budget_bytes=memory_budget_bytes,
    )
    next_sample = next(sample_iter, None)
    next_static_timestamp = next(static_timepoints, None)
    base_time: int | None = None
    index = 0
    try:
        while next_static_timestamp is not None or next_sample is not None:
            static_timestamp = next_static_timestamp
            service_timestamp = int_or_none(next_sample.get("timestamp_mono_ns")) if next_sample else None
            timestamp = service_timestamp if static_timestamp is None else static_timestamp if service_timestamp is None else min(static_timestamp, service_timestamp)
            if timestamp is None:
                break
            if static_timestamp == timestamp:
                next_static_timestamp = next(static_timepoints, None)
            if base_time is None:
                base_time = timestamp
            for interval in run_ends.get(timestamp, ()):
                active_runs.pop(interval.run_id, None)
            for interval in run_starts.get(timestamp, ()):
                if interval.end_mono_ns is not None and interval.end_mono_ns > timestamp:
                    active_runs[interval.run_id] = interval
            active = sorted(active_runs.values(), key=lambda item: (item.start_mono_ns is None, item.start_mono_ns or 0, item.run_id))
            for interval in model_ends.get(timestamp, ()):
                active_models.pop(interval.row_index, None)
                active_running.remove(interval.row_index)
                active_waiting.remove(interval.row_index)
                completed_queue.add(interval.queue_latency_s)
                completed_model_count += 1
                if interval.kv_usage_ratio is not None:
                    completed_kv_max = interval.kv_usage_ratio if completed_kv_max is None else max(completed_kv_max, interval.kv_usage_ratio)
            for interval in model_starts.get(timestamp, ()):
                if interval.end_mono_ns is not None and interval.end_mono_ns > timestamp:
                    active_models[interval.row_index] = interval
                    active_running.add(interval.row_index, interval.running_requests)
                    active_waiting.add(interval.row_index, interval.waiting_requests)
            while next_sample is not None:
                sample_timestamp = int_or_none(next_sample.get("timestamp_mono_ns"))
                if sample_timestamp is None or sample_timestamp > timestamp:
                    break
                merge_model_service_sample(latest_service, next_sample, sample_timestamp)
                next_sample = next(sample_iter, None)
            queue = QueueSnapshot(
                running_count=len(active),
                max_running_runs=max_running_runs,
                run_count=len(run_intervals),
                state_counts={"running": len(active)},
                runs=tuple(
                    RunRuntimeProfile(
                        run_id=interval.run_id,
                        task_id=interval.task_id,
                        state="running",
                        backend="offline_replay",
                        duration_ns=timestamp - interval.start_mono_ns if interval.start_mono_ns is not None else None,
                        classification=UNKNOWN,
                    )
                    for interval in active
                ),
                quality=COMPLETE if active else PARTIAL,
            )
            model_service = model_service_snapshot_from_state(
                active_model_count=len(active_models),
                completed_model_count=completed_model_count,
                active_running_requests=active_running.maximum(),
                active_waiting_requests=active_waiting.maximum(),
                completed_queue_p95_s=completed_queue.percentile(95),
                completed_kv_max=completed_kv_max,
                latest_service=latest_service or None,
            )
            yield OptimizerReplaySnapshot(
                snapshot_index=index,
                timestamp_mono_ns=timestamp,
                timestamp_offset_s=(timestamp - base_time) / 1_000_000_000 if base_time is not None else None,
                queue=queue,
                model_service=model_service,
                evidence={
                    "snapshot_source": "batch_results_and_step_csv",
                    "scheduler_capacity_source": capacity_source,
                    "active_run_interval_source_counts": source_counts([item.source for item in active]),
                    "model_service_quality": model_service.quality,
                    "model_service_evidence_source": model_service_evidence_source(model_service),
                },
            )
            index += 1
            if progress_callback:
                progress_callback(index, None)
    finally:
        completed_queue.close()
        if temporary_root is not None:
            temporary_root.cleanup()


def build_model_step_intervals(
    data: BatchExperimentData,
    step_profile: dict[str, Any],
) -> list[ModelStepInterval]:
    intervals: list[ModelStepInterval] = []
    for row_index, row in enumerate(iter_step_rows(data)):
        if row.get("item_type") != "model":
            continue
        intervals.append(
            ModelStepInterval(
                row_index=row_index,
                start_mono_ns=int_or_none(row.get("item_start_mono_ns")),
                end_mono_ns=int_or_none(row.get("item_end_mono_ns")),
                running_requests=first_int(
                    *(row.get(field) for field in step_profile["scheduler_running_request_fields"])
                ),
                waiting_requests=first_int(
                    *(row.get(field) for field in step_profile["scheduler_waiting_request_fields"])
                ),
                queue_latency_s=first_float(
                    *(row.get(field) for field in step_profile["model_phase_duration_fields"]["model_queue"])
                ),
                kv_usage_ratio=first_float(
                    *(row.get(field) for field in step_profile["kv_usage_ratio_fields"])
                ),
            )
        )
    return intervals


def group_intervals_by_time(
    intervals: list[RunInterval],
    field_name: str,
) -> dict[int, tuple[RunInterval, ...]]:
    grouped: dict[int, list[RunInterval]] = {}
    for interval in intervals:
        value = getattr(interval, field_name)
        if value is not None:
            grouped.setdefault(value, []).append(interval)
    return {key: tuple(value) for key, value in grouped.items()}


def group_model_intervals_by_time(
    intervals: list[ModelStepInterval],
    field_name: str,
) -> dict[int, tuple[ModelStepInterval, ...]]:
    grouped: dict[int, list[ModelStepInterval]] = {}
    for interval in intervals:
        value = getattr(interval, field_name)
        if value is not None:
            grouped.setdefault(value, []).append(interval)
    return {key: tuple(value) for key, value in grouped.items()}


def model_service_snapshot_from_state(
    *,
    active_model_count: int,
    completed_model_count: int,
    active_running_requests: int | None,
    active_waiting_requests: int | None,
    completed_queue_p95_s: float | None,
    completed_kv_max: float | None,
    latest_service: dict[str, Any] | None,
) -> ModelServiceSnapshot:
    running_requests = active_running_requests
    waiting_requests = active_waiting_requests
    if latest_service:
        if "running_requests" in latest_service:
            running_requests = int_or_none(latest_service.get("running_requests"))
        if "waiting_requests" in latest_service:
            waiting_requests = int_or_none(latest_service.get("waiting_requests"))
    quality = COMPLETE if active_model_count and (running_requests is not None or waiting_requests is not None) else PARTIAL
    if latest_service:
        quality = PARTIAL if latest_service.get("_conflicted_fields") else "raw_p2_events"
    if not active_model_count and not completed_model_count:
        quality = "raw_p2_events" if latest_service and not latest_service.get("_conflicted_fields") else PARTIAL if latest_service else UNKNOWN
    return ModelServiceSnapshot(
        service_id="runtime.default",
        provider="offline_observed",
        running_requests=running_requests,
        waiting_requests=waiting_requests,
        queue_latency_p95_s=completed_queue_p95_s,
        kv_usage_ratio=(
            float_or_none(latest_service.get("kv_usage_ratio"))
            if latest_service and "kv_usage_ratio" in latest_service
            else completed_kv_max
        ),
        quality=quality,
    )


def model_service_evidence_source(model_service: ModelServiceSnapshot | None) -> str:
    if model_service is None or model_service.quality == UNKNOWN:
        return UNKNOWN
    if model_service.quality == "raw_p2_events":
        return "raw_p2_events"
    return "step_csv_projection"


def build_run_intervals(data: BatchExperimentData, step_profile: dict[str, Any] | None = None) -> list[RunInterval]:
    step_profile = step_profile or STEP_CSV_PROFILE
    by_run: dict[str, RunInterval] = {}
    for row in iter_batch_results(data):
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        by_run[run_id] = RunInterval(
            run_id=run_id,
            task_id=str(row.get("task_id") or "") or None,
            start_mono_ns=first_int(*(row.get(field) for field in step_profile["run_start_fields"])),
            end_mono_ns=first_int(*(row.get(field) for field in step_profile["run_end_fields"])),
            source="batch-results.tsv",
            terminal_state=str(row.get("status") or row.get("execution_success") or UNKNOWN),
        )
    step_bounds: dict[str, dict[str, int]] = {}
    task_ids: dict[str, str] = {}
    for row in iter_step_rows(data):
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        start = int_or_none(row.get("item_start_mono_ns"))
        end = int_or_none(row.get("item_end_mono_ns"))
        if start is None or end is None or end < start:
            continue
        bounds = step_bounds.setdefault(run_id, {"start": start, "end": end})
        bounds["start"] = min(bounds["start"], start)
        bounds["end"] = max(bounds["end"], end)
        if row.get("task_id"):
            task_ids[run_id] = str(row["task_id"])
    for run_id, bounds in step_bounds.items():
        current = by_run.get(run_id)
        if current is None:
            by_run[run_id] = RunInterval(
                run_id=run_id,
                task_id=task_ids.get(run_id),
                start_mono_ns=bounds["start"],
                end_mono_ns=bounds["end"],
                source="step_csv_interval_bounds",
                terminal_state=UNKNOWN,
            )
            continue
        by_run[run_id] = RunInterval(
            run_id=run_id,
            task_id=current.task_id or task_ids.get(run_id),
            start_mono_ns=current.start_mono_ns if current.start_mono_ns is not None else bounds["start"],
            end_mono_ns=current.end_mono_ns if current.end_mono_ns is not None else bounds["end"],
            source=current.source if current.start_mono_ns is not None and current.end_mono_ns is not None else "batch_results_with_step_csv_fill",
            terminal_state=current.terminal_state,
        )
    return sorted(by_run.values(), key=lambda item: (item.start_mono_ns is None, item.start_mono_ns or 0, item.run_id))


def step_item_timepoints(data: BatchExperimentData) -> list[int]:
    return list(iter_step_item_timepoints(data))


def iter_step_item_timepoints(data: BatchExperimentData) -> Iterator[int]:
    for row in iter_step_rows(data):
        start = int_or_none(row.get("item_start_mono_ns"))
        end = int_or_none(row.get("item_end_mono_ns"))
        if start is not None:
            yield start
        if end is not None:
            yield end


def iter_static_timepoints(
    data: BatchExperimentData,
    run_intervals: list[RunInterval],
    *,
    work_dir: str | Path,
    memory_budget_bytes: int,
) -> Iterator[int]:
    """Externally sort and de-duplicate replay boundary timestamps.

    This removes the replay-local ``sorted(set(...))`` allocation.  The
    compatibility data adapter may still hold step rows for M1 consumers; the
    temporary parts here are only derived ordering state for M2.
    """
    if int(memory_budget_bytes) <= 0:
        raise ValueError("memory_budget_bytes must be greater than zero")
    root = Path(work_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    buffer: list[int] = []
    buffered_bytes = 0
    part_index = 0

    def flush() -> None:
        nonlocal buffer, buffered_bytes, part_index
        if not buffer:
            return
        path = root / f"timestamps-{part_index:06d}.txt"
        temporary = path.with_name(path.name + ".partial")
        with temporary.open("w", encoding="utf-8") as handle:
            previous: int | None = None
            for timestamp in sorted(buffer):
                if timestamp != previous:
                    handle.write(f"{timestamp}\n")
                    previous = timestamp
        temporary.replace(path)
        parts.append(path)
        part_index += 1
        buffer = []
        buffered_bytes = 0

    def add(timestamp: int | None) -> None:
        nonlocal buffered_bytes
        if timestamp is None:
            return
        buffer.append(timestamp)
        buffered_bytes += len(str(timestamp).encode("ascii")) + 1
        if buffered_bytes >= int(memory_budget_bytes):
            flush()

    for interval in run_intervals:
        add(interval.start_mono_ns)
        add(interval.end_mono_ns)
    for row in iter_step_rows(data):
        add(int_or_none(row.get("item_start_mono_ns")))
        add(int_or_none(row.get("item_end_mono_ns")))
    flush()
    yield from iter_merged_unique_timestamp_parts(parts)


def iter_merged_unique_timestamp_parts(parts: list[Path]) -> Iterator[int]:
    handles = [path.open("r", encoding="utf-8") for path in parts]
    heap: list[tuple[int, int]] = []
    try:
        for index, handle in enumerate(handles):
            line = handle.readline()
            if line:
                heapq.heappush(heap, (int(line), index))
        previous: int | None = None
        while heap:
            timestamp, index = heapq.heappop(heap)
            if timestamp != previous:
                yield timestamp
                previous = timestamp
            line = handles[index].readline()
            if line:
                heapq.heappush(heap, (int(line), index))
    finally:
        for handle in handles:
            handle.close()


def active_runs_at(intervals: list[RunInterval], timestamp_mono_ns: int) -> list[RunInterval]:
    active: list[RunInterval] = []
    for interval in intervals:
        if interval.start_mono_ns is None or interval.end_mono_ns is None:
            continue
        if interval.start_mono_ns <= timestamp_mono_ns < interval.end_mono_ns:
            active.append(interval)
    return active


def scheduler_capacity(data: BatchExperimentData, cfg: AgentMonConfig) -> tuple[int, str]:
    manifest_capacity = first_int(data.manifest.get("concurrency"), data.summary.get("configured_concurrency"))
    if manifest_capacity is not None:
        return manifest_capacity, "batch_manifest_or_summary"
    return cfg.scheduler.max_running_runs, "agentmon_config"


def model_service_snapshot_at(
    data: BatchExperimentData,
    timestamp_mono_ns: int,
    *,
    step_profile: dict[str, Any] | None = None,
    model_service_samples: list[dict[str, Any]] | None = None,
    model_service_sample_index: ModelServiceSampleIndex | None = None,
) -> ModelServiceSnapshot:
    step_profile = step_profile or STEP_CSV_PROFILE
    active_model_rows = [
        row for row in iter_step_rows(data)
        if row.get("item_type") == "model" and row_active_at(row, timestamp_mono_ns)
    ]
    completed_model_rows = [
        row for row in iter_step_rows(data)
        if row.get("item_type") == "model"
        and (int_or_none(row.get("item_end_mono_ns")) is not None)
        and int_or_none(row.get("item_end_mono_ns")) <= timestamp_mono_ns
    ]
    running_requests = max_optional_int(active_model_rows, (
        *step_profile["scheduler_running_request_fields"],
    ))
    waiting_requests = max_optional_int(active_model_rows, (
        *step_profile["scheduler_waiting_request_fields"],
    ))
    latest_service = (
        latest_model_service_sample_from_index(model_service_sample_index, timestamp_mono_ns)
        if model_service_sample_index is not None
        else latest_model_service_sample(model_service_samples or [], timestamp_mono_ns)
    )
    if latest_service:
        if "running_requests" in latest_service:
            running_requests = int_or_none(latest_service.get("running_requests"))
        if "waiting_requests" in latest_service:
            waiting_requests = int_or_none(latest_service.get("waiting_requests"))
    queue_values = [
        value
        for value in (
            first_float(*(row.get(field) for field in step_profile["model_phase_duration_fields"]["model_queue"]))
            for row in completed_model_rows
        )
        if value is not None
    ]
    kv_values = [
        value
        for value in (
            first_float(
                *(row.get(field) for field in step_profile["kv_usage_ratio_fields"]),
            )
            for row in completed_model_rows
        )
        if value is not None
    ]
    quality = COMPLETE if active_model_rows and (running_requests is not None or waiting_requests is not None) else PARTIAL
    if latest_service:
        quality = PARTIAL if latest_service.get("_conflicted_fields") else "raw_p2_events"
    if not active_model_rows and not completed_model_rows:
        quality = (
            PARTIAL
            if latest_service and latest_service.get("_conflicted_fields")
            else "raw_p2_events"
            if latest_service
            else UNKNOWN
        )
    return ModelServiceSnapshot(
        service_id="runtime.default",
        provider="offline_observed",
        running_requests=running_requests,
        waiting_requests=waiting_requests,
        queue_latency_p95_s=percentile(queue_values, 95) if queue_values else None,
        kv_usage_ratio=(
            float_or_none(latest_service.get("kv_usage_ratio"))
            if latest_service and "kv_usage_ratio" in latest_service
            else max(kv_values) if kv_values else None
        ),
        quality=quality,
    )


def build_model_service_event_samples(
    data: BatchExperimentData,
    p2_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return list(iter_model_service_event_samples(data, p2_profile))


def iter_model_service_event_samples(
    data: BatchExperimentData,
    p2_profile: dict[str, Any] | None = None,
    *,
    work_dir: str | Path | None = None,
    memory_budget_bytes: int = 32 * 1024 * 1024,
) -> Iterator[dict[str, Any]]:
    """Normalize and externally merge P2 service samples.

    The work directory contains only deterministic derived records and may be
    discarded after a completed stage. The in-memory buffer is byte-budgeted.
    """
    p2_profile = p2_profile or P2_SERVICE_EVENT_PROFILE
    event_sources = set(p2_profile.get("event_sources") or ())
    scheduler_types = set(p2_profile["scheduler_event_types"])
    kv_types = set(p2_profile["kv_event_types"])
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        temporary_root = tempfile.TemporaryDirectory(prefix="agentmon-service-samples.")
        root = Path(temporary_root.name)
    else:
        root = Path(work_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    buffer: list[dict[str, Any]] = []
    buffered_bytes = 0
    if int(memory_budget_bytes) <= 0:
        raise ValueError("memory_budget_bytes must be greater than zero")
    # A single input record can exceed the requested budget.  That is recorded
    # as an unavoidable record-size exception, not hidden by silently raising
    # the caller's budget to an arbitrary floor.
    budget = int(memory_budget_bytes)
    try:
        for run_data, event, _ in iter_run_events(data):
            if event_sources and event.get("source") not in event_sources:
                continue
            event_type = str(event.get("event_type") or "")
            if event_type not in scheduler_types and event_type not in kv_types:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            timestamp = int_or_none(event.get("monotonic_time_ns"))
            if timestamp is None:
                timestamp = first_int(*(payload.get(field) for field in ("timestamp_mono_ns", "monotonic_time_ns")))
            if timestamp is None:
                continue
            sample = {
                "timestamp_mono_ns": timestamp,
                "event_type": event_type,
                "event_id": event.get("event_id"),
                "run_id": run_data.run_id,
                "service_id": payload.get("service_id") or event.get("service_id") or "runtime.default",
                "running_requests": first_int(*(payload.get(field) for field in p2_profile["running_request_fields"])),
                "waiting_requests": first_int(*(payload.get(field) for field in p2_profile["waiting_request_fields"])),
                "kv_usage_ratio": first_float(*(payload.get(field) for field in p2_profile["kv_usage_ratio_fields"])),
                "quality": event.get("quality") or "raw_p2_events",
            }
            buffer.append(sample)
            buffered_bytes += len(json.dumps(sample, ensure_ascii=True, sort_keys=True)) + 1
            if buffered_bytes >= budget:
                parts.append(write_sorted_sample_part(root, len(parts), buffer))
                buffer = []
                buffered_bytes = 0
        if buffer:
            parts.append(write_sorted_sample_part(root, len(parts), buffer))
        yield from merge_sorted_sample_parts(parts)
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()


def write_sorted_sample_part(root: Path, index: int, samples: list[dict[str, Any]]) -> Path:
    path = root / f"part-{index:06d}.jsonl"
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for sample in sorted(samples, key=canonical_sample_sort_key):
            handle.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)
    return path


def merge_sorted_sample_parts(parts: list[Path]) -> Iterator[dict[str, Any]]:
    handles = [path.open("r", encoding="utf-8") for path in parts]
    heap: list[tuple[tuple[Any, ...], int, dict[str, Any]]] = []
    try:
        for index, handle in enumerate(handles):
            sample = read_next_sample(handle)
            if sample is not None:
                heapq.heappush(heap, (canonical_sample_sort_key(sample), index, sample))
        current_timestamp: int | None = None
        current_rows: list[dict[str, Any]] = []
        while heap:
            _, index, sample = heapq.heappop(heap)
            replacement = read_next_sample(handles[index])
            if replacement is not None:
                heapq.heappush(heap, (canonical_sample_sort_key(replacement), index, replacement))
            timestamp = int_or_none(sample.get("timestamp_mono_ns"))
            if current_timestamp is not None and timestamp != current_timestamp:
                yield from deduplicated_timestamp_samples(current_rows)
                current_rows = []
            current_timestamp = timestamp
            current_rows.append(sample)
        if current_rows:
            yield from deduplicated_timestamp_samples(current_rows)
    finally:
        for handle in handles:
            handle.close()


def deduplicated_timestamp_samples(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduplicated: list[dict[str, Any]] = []
    for row in rows:
        identity = model_service_sample_identity(row)
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(row)
    annotate_service_sample_conflicts(deduplicated)
    yield from deduplicated


def canonical_sample_sort_key(sample: dict[str, Any]) -> tuple[Any, ...]:
    return (*model_service_sample_sort_key(sample), *model_service_sample_identity(sample))


def read_next_sample(handle: Any) -> dict[str, Any] | None:
    line = handle.readline()
    if not line:
        return None
    value = json.loads(line)
    return value if isinstance(value, dict) else None


def model_service_sample_identity(sample: dict[str, Any]) -> tuple[Any, ...]:
    event_id = sample.get("event_id")
    return (
        "event_id_and_observed_values" if event_id else "observed_values",
        str(event_id or ""),
        int_or_none(sample.get("timestamp_mono_ns")),
        str(sample.get("event_type") or ""),
        str(sample.get("service_id") or "runtime.default"),
        int_or_none(sample.get("running_requests")),
        int_or_none(sample.get("waiting_requests")),
        float_or_none(sample.get("kv_usage_ratio")),
    )


def annotate_service_sample_conflicts(samples: list[dict[str, Any]]) -> None:
    by_timestamp: dict[int, list[dict[str, Any]]] = {}
    for sample in samples:
        timestamp = int_or_none(sample.get("timestamp_mono_ns"))
        if timestamp is not None:
            by_timestamp.setdefault(timestamp, []).append(sample)
    for rows in by_timestamp.values():
        conflicts = {
            field
            for field in ("running_requests", "waiting_requests", "kv_usage_ratio")
            if len({row.get(field) for row in rows if row.get(field) is not None}) > 1
        }
        if conflicts:
            for row in rows:
                row["conflicted_fields"] = tuple(sorted(conflicts))


def build_model_service_sample_index(samples: list[dict[str, Any]]) -> ModelServiceSampleIndex:
    timestamps: list[int] = []
    latest_samples: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}
    for sample in sorted(samples, key=model_service_sample_sort_key):
        sample_timestamp = int_or_none(sample.get("timestamp_mono_ns"))
        if sample_timestamp is None:
            continue
        merge_model_service_sample(latest, sample, sample_timestamp)
        timestamps.append(sample_timestamp)
        latest_samples.append(dict(latest))
    return ModelServiceSampleIndex(tuple(timestamps), tuple(latest_samples))


def model_service_sample_sort_key(sample: dict[str, Any]) -> tuple[int, str, str, str]:
    timestamp = int_or_none(sample.get("timestamp_mono_ns"))
    return (
        timestamp if timestamp is not None else -1,
        str(sample.get("event_type") or ""),
        str(sample.get("service_id") or "runtime.default"),
        str(sample.get("run_id") or ""),
    )


def latest_model_service_sample_from_index(
    sample_index: ModelServiceSampleIndex,
    timestamp_mono_ns: int,
) -> dict[str, Any] | None:
    position = bisect_right(sample_index.timestamps, timestamp_mono_ns) - 1
    if position < 0:
        return None
    return dict(sample_index.latest_samples[position])


def latest_model_service_sample(
    samples: list[dict[str, Any]],
    timestamp_mono_ns: int,
) -> dict[str, Any] | None:
    latest: dict[str, Any] = {}
    for sample in samples:
        sample_timestamp = int_or_none(sample.get("timestamp_mono_ns"))
        if sample_timestamp is None or sample_timestamp > timestamp_mono_ns:
            continue
        merge_model_service_sample(latest, sample, sample_timestamp)
    return latest or None


def merge_model_service_sample(
    latest: dict[str, Any],
    sample: dict[str, Any],
    sample_timestamp: int,
) -> None:
    conflicts = set(str(key) for key in latest.get("_conflicted_fields", ()))
    for key in sample.get("conflicted_fields", ()):
        field = str(key)
        latest[field] = None
        conflicts.add(field)
    for key in ("running_requests", "waiting_requests", "kv_usage_ratio"):
        if key in sample.get("conflicted_fields", ()):
            continue
        if sample.get(key) is not None:
            latest[key] = sample[key]
            conflicts.discard(key)
    if conflicts:
        latest["_conflicted_fields"] = tuple(sorted(conflicts))
    else:
        latest.pop("_conflicted_fields", None)
    latest["timestamp_mono_ns"] = sample_timestamp


def row_active_at(row: dict[str, str], timestamp_mono_ns: int) -> bool:
    start = int_or_none(row.get("item_start_mono_ns"))
    end = int_or_none(row.get("item_end_mono_ns"))
    return start is not None and end is not None and start <= timestamp_mono_ns < end


def max_optional_int(rows: list[dict[str, str]], keys: tuple[str, ...]) -> int | None:
    values: list[int] = []
    for row in rows:
        for key in keys:
            value = int_or_none(row.get(key))
            if value is not None:
                values.append(value)
                break
    return max(values) if values else None


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def source_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
