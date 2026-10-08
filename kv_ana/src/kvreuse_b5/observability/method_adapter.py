from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from kvreuse_b5.data.types import StatePair

from .collector import TraceCollector
from .schema import KVTraceEvent, TraceAction, TraceSource


def _row_positions(pair: StatePair, indices: list[int] | tuple[int, ...] | np.ndarray | None) -> tuple[int | float, ...]:
    if indices is None:
        return ()
    rows = np.asarray(indices, dtype=int)
    if len(rows) == 0:
        return ()
    return tuple(pair.positions[rows].tolist())


def observe_method_record(
    collector: TraceCollector,
    pair: StatePair,
    method: Any,
    method_config_hash: str,
    record: Mapping[str, Any],
    *,
    corrected: np.ndarray | None = None,
) -> None:
    if not collector.enabled:
        return
    diagnostics = dict(record.get("diagnostics") or {})
    common = {
        "run_id": collector.run_id,
        "method": method.name,
        "method_config_hash": method_config_hash,
        "model_family": pair.model_family,
        "condition": pair.condition,
        "state_kind": pair.state_kind,
        "pair_id": pair.pair_id,
        "step_index": 0,
        "layer_id": pair.layer_id,
        "metadata": {
            "total_available_units": pair.total_units,
            "total_positions": pair.total_units,
            "total_layer_position_units": pair.total_units,
            "available_scope": "layer",
            "method_variant": method.variant,
        },
    }
    if record.get("status") not in {"OK", "ZERO_RESIDUAL", "ZERO_BASELINE_DEGRADED"}:
        values = {**common, "metadata": {**common["metadata"], "status": record.get("status"), "error": record.get("error")}}
        collector.record(KVTraceEvent(**values, action=TraceAction.SKIP, source=TraceSource.CACHED))
        return
    if not method.materializes_state:
        values = {**common, "metadata": {**common["metadata"], "metric_only_reference": True}}
        collector.record(KVTraceEvent(**values, action=TraceAction.SKIP, source=TraceSource.ORACLE, baseline_sse=record.get("baseline_sse"), after_sse=record.get("corrected_sse")))
        return
    selected = diagnostics.get("selected_indices")
    selected_positions = _row_positions(pair, selected)
    if selected is not None:
        collector.record(KVTraceEvent(**common, action=TraceAction.SELECT, positions=selected_positions, selected_units=len(selected_positions), source=TraceSource.ORACLE if method.capabilities.oracle else TraceSource.CACHED))
    probes = diagnostics.get("probe_indices")
    probe_positions = _row_positions(pair, probes)
    if probes is not None:
        collector.record(KVTraceEvent(**common, action=TraceAction.PROBE, positions=probe_positions, observed_units=len(probe_positions), recomputed_units=len(probe_positions), source=TraceSource.FULL_EXACT, provider="saved_probe_target"))
    detector_units = int(record.get("detector_recomputed_units") or 0)
    if detector_units:
        values = {**common, "metadata": {**common["metadata"], "unique_positions_unavailable": True}}
        collector.record(KVTraceEvent(**values, action=TraceAction.RECOMPUTE, positions=None, observed_units=detector_units, recomputed_units=detector_units, source=TraceSource.FULL_EXACT, provider="detector_exact_recompute"))
    repaired = int(record.get("exact_repaired_units") or 0)
    if repaired:
        replacement_positions = selected_positions[:repaired] if selected_positions else tuple(pair.positions[:repaired].tolist())
        collector.record(KVTraceEvent(**common, action=TraceAction.REPLACE, positions=replacement_positions, recomputed_units=repaired, replaced_units=repaired, source=TraceSource.FULL_EXACT, provider="saved_exact_target"))
    baseline = record.get("baseline_sse")
    after = record.get("corrected_sse")
    if method.name == "no_correction":
        collector.record(KVTraceEvent(**common, action=TraceAction.REUSE, positions=tuple(pair.positions.tolist()), source=TraceSource.CACHED, baseline_sse=baseline, after_sse=after))
    elif repaired == 0:
        correction_norm = float(np.linalg.norm(np.asarray(corrected) - pair.a)) if corrected is not None else None
        source = TraceSource.ORACLE if method.capabilities.uses_full_target else TraceSource.CACHED
        collector.record(KVTraceEvent(**common, action=TraceAction.CORRECT, positions=tuple(pair.positions.tolist()), corrected_units=pair.total_units, source=source, baseline_sse=baseline, after_sse=after, correction_norm=correction_norm))
