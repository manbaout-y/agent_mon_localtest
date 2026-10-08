from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .collector import TraceCollector, TraceLevel
from .schema import KVTraceEvent, TraceAction, TraceSource


def collector_from_context(context: Any) -> TraceCollector | None:
    collector = context.metadata.get("observability_collector")
    return collector if isinstance(collector, TraceCollector) and collector.enabled else None


def source_from_provider(provider: Any) -> TraceSource:
    semantics = getattr(provider, "semantics_variant", None)
    if semantics == "approximate_context":
        return TraceSource.APPROXIMATE_CONTEXT
    if semantics == "full_exact" or getattr(provider.capabilities, "exact", False):
        return TraceSource.FULL_EXACT
    if getattr(provider.capabilities, "oracle", False):
        return TraceSource.ORACLE
    return TraceSource.FRESH


def _metadata(context: Any, state_kind: str, total_available_units: int, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(metadata or {})
    value["total_available_units"] = int(total_available_units)
    state = context.states[state_kind]
    value["total_positions"] = int(state.shape[1])
    value["total_layer_position_units"] = int(np.prod(state.shape[:2]))
    value["available_scope"] = "sample"
    if context.metadata.get("trace_level") == TraceLevel.DETAIL.value:
        value["trace_level"] = TraceLevel.DETAIL.value
    return value


def emit_recipe_event(
    context: Any,
    recipe: Any,
    *,
    step_index: int,
    layer_index: int,
    state_kind: str,
    action: TraceAction | str,
    positions: Sequence[int | float] | np.ndarray | None = None,
    selected_units: int = 0,
    observed_units: int = 0,
    recomputed_units: int = 0,
    corrected_units: int = 0,
    replaced_units: int = 0,
    source: TraceSource | str = TraceSource.CACHED,
    provider: str | None = None,
    baseline_sse: float | None = None,
    after_sse: float | None = None,
    correction_norm: float | None = None,
    estimated_residual_mean_norm: float | None = None,
    applied_correction_norm: float | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    collector = collector_from_context(context)
    if collector is None:
        return
    layers = context.metadata.get("layer_ids") or []
    layer_id = int(layers[layer_index]) if layer_index < len(layers) else int(layer_index)
    total = int(np.prod(context.states[state_kind].shape[:2]))
    links = context.metadata
    event_metadata = dict(metadata or {})
    event_metadata.setdefault("variant", recipe.spec.variant_id)
    event_metadata.setdefault(
        "experiment_class",
        recipe.spec.parameters.get("experiment_class", "PAPER_RECIPE"),
    )
    collector.record(KVTraceEvent(
        run_id=collector.run_id,
        method=recipe.spec.recipe_id,
        method_config_hash=recipe.spec.recipe_variant_hash,
        model_family=str(links.get("model_family", "unknown")),
        condition=str(links.get("condition", "unknown")),
        state_kind=state_kind,
        pair_id=links.get("pair_id", "unknown"),
        step_index=int(step_index),
        layer_id=layer_id,
        action=action,
        selected_units=int(selected_units),
        observed_units=int(observed_units),
        recomputed_units=int(recomputed_units),
        corrected_units=int(corrected_units),
        replaced_units=int(replaced_units),
        positions=None if positions is None else tuple(np.asarray(positions).tolist()),
        source=source,
        provider=provider,
        baseline_sse=baseline_sse,
        after_sse=after_sse,
        correction_norm=correction_norm,
        estimated_residual_mean_norm=estimated_residual_mean_norm,
        applied_correction_norm=applied_correction_norm,
        request_id=links.get("request_id"),
        agent_run_id=links.get("agent_run_id"),
        agent_step_id=links.get("agent_step_id"),
        model_request_id=links.get("model_request_id"),
        reuse_request_id=links.get("reuse_request_id"),
        chunk_id=links.get("chunk_id"),
        metadata=_metadata(context, state_kind, total, event_metadata),
    ))
