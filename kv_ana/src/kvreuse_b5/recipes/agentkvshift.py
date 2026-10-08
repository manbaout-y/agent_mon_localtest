from __future__ import annotations

import math
from typing import Any

import numpy as np

from kvreuse_b5.providers import FreshProbeProvider
from kvreuse_b5.observability.events import collector_from_context, emit_recipe_event, source_from_provider
from kvreuse_b5.observability.schema import TraceAction

from .contract import *


def _distribution_stats(values: np.ndarray, prefix: str) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    _, frequencies = np.unique(array, return_counts=True)
    unique_count = int(len(frequencies))
    tied_units = int(np.sum(frequencies[frequencies > 1]))
    nonzero = int(np.count_nonzero(array))
    return {
        f"{prefix}_min": float(array.min()),
        f"{prefix}_max": float(array.max()),
        f"{prefix}_mean": float(array.mean()),
        f"{prefix}_nonzero_count": nonzero,
        f"{prefix}_zero_count": int(len(array) - nonzero),
        **({
            f"{prefix}_unique_value_count": unique_count,
            f"{prefix}_tie_count": tied_units,
            f"{prefix}_all_equal": unique_count == 1,
        } if prefix == "selector" else {}),
    }


def _recovery(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0:
        return None
    return (before - after) / before


def agentkvshift_spec(probe_semantics_variant: str | None, **parameters: Any) -> PaperRecipeSpec:
    params = {"probe_ratio": 0.25, "clip": 1.0, "probe_semantics_variant": probe_semantics_variant}
    params.update(parameters)
    return PaperRecipeSpec(
        recipe_id="agentkvshift/paper_spec", paper_id="14", variant_id="paper_spec",
        fidelity_level=FidelityLevel.F1_ALGORITHMIC_REPRODUCTION,
        paper_spec_path="literature_fetch_pack_v3/literature/paper_specs/batch1/14_agentkvshift.yaml",
        paper_spec_source_hash="1454cb05c48a1a35ac7112faa174a79dc736293b6b570d393a51c9ad4f2ff5e2",
        capabilities=RecipeCapabilities(("PARTIAL_FORWARD",)), required_providers=("PARTIAL_FORWARD",), offline_steps=(),
        online_steps=(
            RecipeStep("checkpoint_divergence_and_probe_selection", "paper_mechanism", "PARTIAL_FORWARD", ("P3",)),
            RecipeStep("fresh_probe_recomputation", "paper_mechanism", "PARTIAL_FORWARD", ("P3",)),
            RecipeStep("kv_separate_weighted_mean_shift", "paper_mechanism", None, ("P3",)),
        ),
        selection_granularity="per memory chunk; K/V-specific tokens", budget_semantics="b=ceil(r*n) per K and V plus full checkpoint layer",
        position_semantics="position compatibility assumed; no paper RoPE correction",
        state_update_semantics="exact probes; clipped layer-1 divergence weighted per-layer residual mean for non-probes",
        evidence_ids=("P1", "P2", "P3", "P4", "P5"), unresolved_fields=("probe_fresh_state_semantics",), parameters=params,
    )


class AgentKVShiftRecipe(ExecutableRecipe):
    def __init__(self, probe_semantics_variant: str | None = None, **parameters: Any):
        super().__init__(agentkvshift_spec(probe_semantics_variant, **parameters))

    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        self.validate_context(context)
        provider = context.providers["PARTIAL_FORWARD"]
        if not isinstance(provider, FreshProbeProvider):
            raise TypeError("AgentKVShift requires FreshProbeProvider")
        configured = self.spec.parameters["probe_semantics_variant"]
        if configured not in {"full_exact", "approximate_context"}:
            raise ValueError("probe_semantics_variant must explicitly be full_exact or approximate_context")
        if provider.semantics_variant != configured:
            raise ValueError(f"provider semantics {provider.semantics_variant!r} != configured {configured!r}")
        states = context.copy_states(); cost = RecipeCostRecord(); trace = []
        observer = collector_from_context(context)
        target_states = context.metadata.get("observability_targets") if observer is not None else None
        provider_name = getattr(provider.capabilities, "source", provider.capability)
        provider_metadata = {
            "provider_oracle": bool(provider.capabilities.oracle),
            "provider_uses_full_target": bool(provider.capabilities.uses_full_target),
            "probe_source_semantics": configured,
        }
        checkpoint = provider.checkpoint_states(current=states); cost.add(checkpoint.cost)
        positions: dict[str, np.ndarray] = {}; weights: dict[str, np.ndarray] = {}
        for kind in ("K", "V"):
            layer_base = states[kind][0].copy() if observer is not None else None
            residual = np.asarray(checkpoint.value[kind]) - states[kind][0]
            divergence = np.linalg.norm(residual, axis=-1)
            count = max(1, math.ceil(len(divergence) * float(self.spec.parameters["probe_ratio"])))
            positions[kind] = np.argsort(-divergence, kind="stable")[:count]
            weights[kind] = np.minimum(divergence, float(self.spec.parameters["clip"]))
            states[kind][0] = checkpoint.value[kind]
            full_positions = np.arange(len(divergence), dtype=int)
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.SELECT, positions=positions[kind], selected_units=len(positions[kind]),
                source=source_from_provider(provider), provider=provider_name,
                metadata={
                    **provider_metadata,
                    "selector": "layer0_divergence",
                    **_distribution_stats(divergence, "selector"),
                    **_distribution_stats(weights[kind], "weight"),
                    "selector_degenerate": len(np.unique(divergence)) == 1,
                    "selected_count": len(positions[kind]),
                    "selected_position_fraction": len(positions[kind]) / len(divergence),
                    **({"detail": {"selector_score_by_position": divergence.tolist()}} if observer is not None and observer.level.value == "detail" else {}),
                },
            )
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.RECOMPUTE, positions=full_positions,
                observed_units=len(full_positions), recomputed_units=len(full_positions),
                source=source_from_provider(provider), provider=provider_name, metadata=provider_metadata,
            )
            baseline = after = None
            if target_states is not None:
                target = np.asarray(target_states[kind][0])
                baseline = float(np.sum((target - layer_base) ** 2))
                after = float(np.sum((target - states[kind][0]) ** 2))
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.REPLACE, positions=full_positions, replaced_units=len(full_positions),
                source=source_from_provider(provider), provider=provider_name,
                baseline_sse=baseline, after_sse=after,
                metadata={
                    **provider_metadata,
                    "exact_probe_replacement": True,
                    "replacement_norm": float(np.linalg.norm(states[kind][0] - layer_base)) if layer_base is not None else None,
                    "sse_before_layer_repair": baseline,
                    "sse_after_nonprobe_correction": baseline,
                    "sse_after_probe_replacement": after,
                    "correction_only_recovery": 0.0 if baseline is not None and baseline > 0 else None,
                    "replacement_incremental_recovery": _recovery(baseline, after),
                    "total_layer_recovery": _recovery(baseline, after),
                },
            )
        cost.selected_units += sum(len(v) for v in positions.values()); cost.updated_units += sum(v.shape[1] for v in checkpoint.value.values())
        trace.append(trace_entry(0, self.spec.online_steps[0], input_scope={"layer": 0}, output_scope={"probes": {k: v.tolist() for k, v in positions.items()}}, selected_units=sum(len(v) for v in positions.values()), state_updated=True, cost=checkpoint.cost))
        for layer in range(1, states["K"].shape[0]):
            fresh = provider.probe_states(layer=layer, positions_by_kind=positions, current=states); cost.add(fresh.cost)
            trace.append(trace_entry(len(trace), self.spec.online_steps[1], input_scope={"layer": layer, "positions": {k: v.tolist() for k, v in positions.items()}}, output_scope={"kinds": ["K", "V"]}, selected_units=sum(len(v) for v in positions.values()), cost=fresh.cost))
            for kind in ("K", "V"):
                pos = positions[kind]
                base = states[kind][layer].copy()
                mu = np.mean(np.asarray(fresh.value[kind]) - base[pos], axis=0)
                shifted = base + weights[kind][:, None] * mu
                corrected = shifted.copy()
                corrected[pos] = fresh.value[kind]
                states[kind][layer] = corrected
                cost.updated_units += len(corrected)
                non_probe = np.setdiff1d(np.arange(len(base), dtype=int), pos, assume_unique=False)
                emit_recipe_event(
                    context, self, step_index=1, layer_index=layer, state_kind=kind,
                    action=TraceAction.PROBE, positions=pos, observed_units=len(pos), recomputed_units=len(pos),
                    source=source_from_provider(provider), provider=provider_name, metadata=provider_metadata,
                )
                baseline = after_nonprobe = after = None
                nonprobe_state = base.copy()
                nonprobe_state[non_probe] = shifted[non_probe]
                if target_states is not None:
                    target = np.asarray(target_states[kind][layer])
                    baseline = float(np.sum((target - base) ** 2))
                    after_nonprobe = float(np.sum((target - nonprobe_state) ** 2))
                    after = float(np.sum((target - corrected) ** 2))
                emit_recipe_event(
                    context, self, step_index=2, layer_index=layer, state_kind=kind,
                    action=TraceAction.CORRECT, positions=non_probe, corrected_units=len(non_probe),
                    source=source_from_provider(provider), provider=provider_name,
                    baseline_sse=baseline, after_sse=after_nonprobe,
                    correction_norm=float(np.linalg.norm(nonprobe_state - base)),
                    estimated_residual_mean_norm=float(np.linalg.norm(mu)),
                    applied_correction_norm=float(np.linalg.norm(nonprobe_state - base)),
                    metadata={
                        **provider_metadata,
                        "estimated_residual_mean_norm": float(np.linalg.norm(mu)),
                        "applied_correction_norm": float(np.linalg.norm(nonprobe_state - base)),
                        "non_probe_corrected_count": len(non_probe),
                    },
                )
                emit_recipe_event(
                    context, self, step_index=2, layer_index=layer, state_kind=kind,
                    action=TraceAction.REPLACE, positions=pos, replaced_units=len(pos),
                    source=source_from_provider(provider), provider=provider_name,
                    baseline_sse=after_nonprobe, after_sse=after,
                    metadata={
                        **provider_metadata,
                        "exact_probe_replacement": True,
                        "replacement_norm": float(np.linalg.norm(corrected[pos] - nonprobe_state[pos])),
                        "sse_before_layer_repair": baseline,
                        "sse_after_nonprobe_correction": after_nonprobe,
                        "sse_after_probe_replacement": after,
                        "correction_only_recovery": _recovery(baseline, after_nonprobe),
                        "replacement_incremental_recovery": ((after_nonprobe - after) / baseline) if baseline is not None and after_nonprobe is not None and after is not None and baseline > 0 else None,
                        "total_layer_recovery": _recovery(baseline, after),
                    },
                )
            trace.append(trace_entry(len(trace), self.spec.online_steps[2], input_scope={"layer": layer}, output_scope={"updated_kinds": ["K", "V"]}, selected_units=sum(len(v) for v in positions.values()), state_updated=True))
        return RecipeResult(self.spec.recipe_id, RecipeExecutionStatus.EXECUTED, states, trace, cost, {"probe_positions": {k: v.tolist() for k, v in positions.items()}, "probe_semantics": configured})
