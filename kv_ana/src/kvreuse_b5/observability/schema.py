from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

import numpy as np


TRACE_SCHEMA_VERSION = "kvreuse-observability-v1"
OBSERVABILITY_RELEASE = "1.0.1"


class TraceAction(str, Enum):
    SELECT = "select"
    PROBE = "probe"
    RECOMPUTE = "recompute"
    REUSE = "reuse"
    CORRECT = "correct"
    REPLACE = "replace"
    RETIRE = "retire"
    SKIP = "skip"


class TraceSource(str, Enum):
    CACHED = "cached"
    FRESH = "fresh"
    ORACLE = "oracle"
    FULL_EXACT = "full_exact"
    APPROXIMATE_CONTEXT = "approximate_context"


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class KVTraceEvent:
    run_id: str
    method: str
    method_config_hash: str
    model_family: str
    condition: str
    state_kind: str
    pair_id: int | str
    step_index: int
    layer_id: int
    action: TraceAction | str
    schema_version: str = TRACE_SCHEMA_VERSION
    event_id: str | None = None
    selected_units: int = 0
    observed_units: int = 0
    recomputed_units: int = 0
    corrected_units: int = 0
    replaced_units: int = 0
    positions: tuple[int | float, ...] | None = None
    source: TraceSource | str = TraceSource.CACHED
    provider: str | None = None
    duration_ns: int | None = None
    baseline_sse: float | None = None
    after_sse: float | None = None
    correction_norm: float | None = None
    estimated_residual_mean_norm: float | None = None
    applied_correction_norm: float | None = None
    request_id: str | None = None
    agent_run_id: str | None = None
    agent_step_id: str | None = None
    model_request_id: str | None = None
    reuse_request_id: str | None = None
    chunk_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            action = self.action if isinstance(self.action, TraceAction) else TraceAction(self.action)
        except ValueError as exc:
            raise ValueError(f"invalid trace action {self.action!r}") from exc
        try:
            source = self.source if isinstance(self.source, TraceSource) else TraceSource(self.source)
        except ValueError as exc:
            raise ValueError(f"invalid trace source {self.source!r}") from exc
        counts = (
            self.selected_units, self.observed_units, self.recomputed_units,
            self.corrected_units, self.replaced_units,
        )
        if any(int(value) < 0 for value in counts):
            raise ValueError("trace unit counts must be non-negative")
        if self.duration_ns is not None and self.duration_ns < 0:
            raise ValueError("duration_ns must be non-negative")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "source", source)
        applied = self.applied_correction_norm
        if applied is None:
            applied = self.correction_norm
        if applied is not None:
            object.__setattr__(self, "applied_correction_norm", float(applied))
            object.__setattr__(self, "correction_norm", float(applied))
        if self.positions is not None:
            object.__setattr__(self, "positions", tuple(_json_value(value) for value in self.positions))
        object.__setattr__(self, "metadata", _json_value(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


def trace_schema_document() -> dict[str, Any]:
    nullable_runtime_ids = [
        "request_id", "agent_run_id", "agent_step_id", "model_request_id",
        "reuse_request_id", "chunk_id",
    ]
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "observability_release": OBSERVABILITY_RELEASE,
        "event_type": "KVTraceEvent",
        "actions": [item.value for item in TraceAction],
        "sources": [item.value for item in TraceSource],
        "nullable_runtime_ids": nullable_runtime_ids,
        "unit_definition": "one layer x position/state-row x state_kind vector",
        "cost_semantics": {
            "additive": "sum of work every time an action executes; may exceed total available units",
            "unique_layer_position": "deduplicated model/condition/state/pair/layer/position work",
            "latency": "not inferred from offline work units",
            "denominators": "position fractions use total_positions; layer-position fractions use total_layer_position_units",
        },
        "tensor_boundary": "events contain indices, counts, scalars, and small diagnostics only; never K/V tensors",
        "fields": list(KVTraceEvent.__dataclass_fields__),
    }
