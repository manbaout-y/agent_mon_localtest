from __future__ import annotations

import os
import uuid
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any

from .cost import CostAccountingRecord
from .schema import KVTraceEvent


class TraceLevel(str, Enum):
    OFF = "off"
    SUMMARY = "summary"
    DETAIL = "detail"


def _optional_sum(left: float | int | None, right: float | int | None) -> float | int | None:
    if right is None:
        return left
    return right if left is None else left + right


_POSITION_ARRAY_COUNTS = {
    "retire_positions": "retired_count",
    "newly_selected_positions": "newly_selected_count",
    "selected_before": "selected_before_count",
    "selected_after": "selected_after_count",
}


def _summary_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in metadata.items():
        if key == "detail":
            continue
        count_name = _POSITION_ARRAY_COUNTS.get(key)
        if count_name is not None and isinstance(value, list):
            output[count_name] = len(value)
            continue
        if isinstance(value, list) and "position" in key:
            output[f"{key}_count"] = len(value)
            continue
        output[key] = value
    return output


class TraceCollector:
    def __init__(
        self,
        level: TraceLevel | str = TraceLevel.SUMMARY,
        *,
        run_id: str | None = None,
        trace_dir: str | Path | None = None,
    ):
        self.level = level if isinstance(level, TraceLevel) else TraceLevel(level)
        self.run_id = run_id or uuid.uuid4().hex
        self.trace_dir = Path(trace_dir) if trace_dir is not None else None
        self._sequence = 0
        self._summary: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._details: list[KVTraceEvent] = []
        self._costs: dict[tuple[Any, ...], CostAccountingRecord] = {}

    @classmethod
    def from_env(cls, default_dir: str | Path, *, run_id: str | None = None) -> "TraceCollector":
        level = os.environ.get("KVREUSE_TRACE", TraceLevel.SUMMARY.value).strip().lower()
        trace_dir = os.environ.get("KVREUSE_TRACE_DIR") or str(default_dir)
        return cls(level, run_id=run_id, trace_dir=trace_dir)

    @property
    def enabled(self) -> bool:
        return self.level is not TraceLevel.OFF

    def record(self, event: KVTraceEvent) -> KVTraceEvent | None:
        if not self.enabled:
            return None
        if event.run_id != self.run_id:
            event = replace(event, run_id=self.run_id)
        if event.event_id is None:
            self._sequence += 1
            event = replace(event, event_id=f"{self.run_id}:{self._sequence}")
        key = (
            event.run_id, event.method, event.method_config_hash, event.model_family,
            event.condition, event.state_kind, str(event.pair_id), event.layer_id, event.action.value,
        )
        aggregate = self._summary.get(key)
        if aggregate is None:
            aggregate = event.to_dict()
            aggregate["positions"] = None
            aggregate["metadata"] = _summary_metadata(dict(event.metadata))
            aggregate["metadata"]["event_count"] = 1
            aggregate["metadata"]["unique_position_count"] = len(set(event.positions or ()))
            aggregate["_positions"] = set(event.positions or ())
            aggregate["_correction_norm_sq"] = (event.correction_norm or 0.0) ** 2
            aggregate["_applied_correction_norm_sq"] = (event.applied_correction_norm or 0.0) ** 2
            aggregate["_estimated_residual_mean_norm_sq"] = (event.estimated_residual_mean_norm or 0.0) ** 2
            aggregate["_correction_norm_present"] = event.correction_norm is not None
            aggregate["_applied_correction_norm_present"] = event.applied_correction_norm is not None
            aggregate["_estimated_residual_mean_norm_present"] = event.estimated_residual_mean_norm is not None
            self._summary[key] = aggregate
        else:
            for name in ("selected_units", "observed_units", "recomputed_units", "corrected_units", "replaced_units"):
                aggregate[name] += getattr(event, name)
            aggregate["duration_ns"] = _optional_sum(aggregate.get("duration_ns"), event.duration_ns)
            aggregate["baseline_sse"] = _optional_sum(aggregate.get("baseline_sse"), event.baseline_sse)
            aggregate["after_sse"] = _optional_sum(aggregate.get("after_sse"), event.after_sse)
            aggregate["_correction_norm_sq"] += (event.correction_norm or 0.0) ** 2
            aggregate["_applied_correction_norm_sq"] += (event.applied_correction_norm or 0.0) ** 2
            aggregate["_estimated_residual_mean_norm_sq"] += (event.estimated_residual_mean_norm or 0.0) ** 2
            aggregate["_correction_norm_present"] |= event.correction_norm is not None
            aggregate["_applied_correction_norm_present"] |= event.applied_correction_norm is not None
            aggregate["_estimated_residual_mean_norm_present"] |= event.estimated_residual_mean_norm is not None
            aggregate["_positions"].update(event.positions or ())
            aggregate["metadata"]["event_count"] += 1
            aggregate["metadata"]["unique_position_count"] = len(aggregate["_positions"])
        cost_key = (
            event.run_id, event.method, event.method_config_hash, event.model_family,
            event.condition, event.state_kind, str(event.pair_id),
        )
        cost = self._costs.get(cost_key)
        if cost is None:
            cost = CostAccountingRecord(
                event.run_id, event.method, event.method_config_hash, event.model_family,
                event.condition, event.state_kind, event.pair_id,
            )
            self._costs[cost_key] = cost
        cost.add(event)
        if self.level is TraceLevel.DETAIL:
            self._details.append(event)
        return event

    def summary_events(self) -> list[dict[str, Any]]:
        rows = []
        for key in sorted(self._summary, key=repr):
            row = dict(self._summary[key])
            row["correction_norm"] = row["_correction_norm_sq"] ** 0.5 if row["_correction_norm_present"] else None
            row["applied_correction_norm"] = row["_applied_correction_norm_sq"] ** 0.5 if row["_applied_correction_norm_present"] else None
            row["estimated_residual_mean_norm"] = row["_estimated_residual_mean_norm_sq"] ** 0.5 if row["_estimated_residual_mean_norm_present"] else None
            row.pop("_positions", None)
            row.pop("_correction_norm_sq", None)
            row.pop("_applied_correction_norm_sq", None)
            row.pop("_estimated_residual_mean_norm_sq", None)
            row.pop("_correction_norm_present", None)
            row.pop("_applied_correction_norm_present", None)
            row.pop("_estimated_residual_mean_norm_present", None)
            rows.append(row)
        return rows

    def detail_events(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self._details]

    def cost_rows(self) -> list[dict[str, Any]]:
        return [self._costs[key].to_row() for key in sorted(self._costs, key=repr)]

    def forensic_rows(self) -> list[dict[str, Any]]:
        from .forensic import build_forensic_rows

        return build_forensic_rows(self.summary_events(), self.cost_rows())
