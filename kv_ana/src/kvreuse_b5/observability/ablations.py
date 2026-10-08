from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from kvreuse_b5.providers import FreshProbeProvider
from kvreuse_b5.recipes.agentkvshift import _distribution_stats, _recovery, agentkvshift_spec
from kvreuse_b5.recipes.contract import (
    ExecutableRecipe,
    RecipeCostRecord,
    RecipeExecutionContext,
    RecipeExecutionStatus,
    RecipeResult,
    trace_entry,
)

from .events import collector_from_context, emit_recipe_event, source_from_provider
from .schema import TraceAction


class AgentKVShiftForensicAblation(ExecutableRecipe):
    """Non-tournament diagnostic variants of the frozen AgentKVShift recipe.

    ``probe_only`` retains the exact checkpoint layer and exact later-layer probe
    writes, but applies no mean-shift correction. ``shift_only`` retains the
    exact checkpoint layer and uses later fresh probes to estimate the same
    weighted mean shift, materializes that pre-replacement state, and disables
    later exact probe writes.
    """

    VALID_VARIANTS = {"probe_only", "shift_only"}

    def __init__(
        self,
        variant: str,
        probe_semantics_variant: str | None = None,
        **parameters: Any,
    ):
        if variant not in self.VALID_VARIANTS:
            raise ValueError(f"unknown AgentKVShift forensic variant {variant!r}")
        official = agentkvshift_spec(probe_semantics_variant, **parameters)
        forensic_parameters = dict(official.parameters)
        forensic_parameters.update({
            "experiment_class": "FORENSIC_ABLATION",
            "forensic_variant": variant,
        })
        spec = replace(
            official,
            recipe_id=f"agentkvshift/forensic_{variant}",
            variant_id=variant,
            parameters=forensic_parameters,
            execution_status="FORENSIC_ABLATION",
        )
        super().__init__(spec)
        self.variant = variant

    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        self.validate_context(context)
        provider = context.providers["PARTIAL_FORWARD"]
        if not isinstance(provider, FreshProbeProvider):
            raise TypeError("AgentKVShift forensic ablation requires FreshProbeProvider")
        configured = self.spec.parameters["probe_semantics_variant"]
        if configured not in {"full_exact", "approximate_context"}:
            raise ValueError("probe_semantics_variant must explicitly be full_exact or approximate_context")
        if provider.semantics_variant != configured:
            raise ValueError(f"provider semantics {provider.semantics_variant!r} != configured {configured!r}")

        states = context.copy_states()
        cost = RecipeCostRecord()
        trace = []
        observer = collector_from_context(context)
        targets = context.metadata.get("observability_targets") if observer is not None else None
        provider_name = getattr(provider.capabilities, "source", provider.capability)
        common = {
            "provider_oracle": bool(provider.capabilities.oracle),
            "provider_uses_full_target": bool(provider.capabilities.uses_full_target),
            "probe_source_semantics": configured,
            "forensic_variant": self.variant,
            "experiment_class": "FORENSIC_ABLATION",
        }

        checkpoint = provider.checkpoint_states(current=states)
        cost.add(checkpoint.cost)
        positions: dict[str, np.ndarray] = {}
        weights: dict[str, np.ndarray] = {}
        for kind in ("K", "V"):
            base = states[kind][0].copy()
            residual = np.asarray(checkpoint.value[kind]) - base
            divergence = np.linalg.norm(residual, axis=-1)
            count = max(1, math.ceil(len(divergence) * float(self.spec.parameters["probe_ratio"])))
            positions[kind] = np.argsort(-divergence, kind="stable")[:count]
            weights[kind] = np.minimum(divergence, float(self.spec.parameters["clip"]))
            states[kind][0] = checkpoint.value[kind]
            all_positions = np.arange(len(divergence), dtype=int)
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.SELECT, positions=positions[kind], selected_units=len(positions[kind]),
                source=source_from_provider(provider), provider=provider_name,
                metadata={
                    **common,
                    "selector": "layer0_divergence",
                    **_distribution_stats(divergence, "selector"),
                    **_distribution_stats(weights[kind], "weight"),
                    "selector_degenerate": len(np.unique(divergence)) == 1,
                    "selected_count": len(positions[kind]),
                    "selected_position_fraction": len(positions[kind]) / len(divergence),
                    **({"detail": {"selector_score_by_position": divergence.tolist()}}
                       if observer is not None and observer.level.value == "detail" else {}),
                },
            )
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.RECOMPUTE, positions=all_positions,
                observed_units=len(all_positions), recomputed_units=len(all_positions),
                source=source_from_provider(provider), provider=provider_name, metadata=common,
            )
            before = after = None
            if targets is not None:
                target = np.asarray(targets[kind][0])
                before = float(np.sum((target - base) ** 2))
                after = float(np.sum((target - states[kind][0]) ** 2))
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.REPLACE, positions=all_positions, replaced_units=len(all_positions),
                source=source_from_provider(provider), provider=provider_name,
                baseline_sse=before, after_sse=after,
                metadata={
                    **common,
                    "exact_probe_replacement": True,
                    "replacement_norm": float(np.linalg.norm(states[kind][0] - base)),
                    "sse_before_layer_repair": before,
                    "sse_after_nonprobe_correction": before,
                    "sse_after_probe_replacement": after,
                    "correction_only_recovery": 0.0 if before is not None and before > 0 else None,
                    "replacement_incremental_recovery": _recovery(before, after),
                    "total_layer_recovery": _recovery(before, after),
                },
            )

        cost.selected_units += sum(len(value) for value in positions.values())
        cost.updated_units += sum(value.shape[1] for value in checkpoint.value.values())
        trace.append(trace_entry(
            0, self.spec.online_steps[0], input_scope={"layer": 0},
            output_scope={"probes": {kind: value.tolist() for kind, value in positions.items()}},
            selected_units=sum(len(value) for value in positions.values()), state_updated=True,
            cost=checkpoint.cost,
        ))

        for layer in range(1, states["K"].shape[0]):
            fresh = provider.probe_states(layer=layer, positions_by_kind=positions, current=states)
            cost.add(fresh.cost)
            trace.append(trace_entry(
                len(trace), self.spec.online_steps[1],
                input_scope={"layer": layer, "positions": {kind: value.tolist() for kind, value in positions.items()}},
                output_scope={"kinds": ["K", "V"]},
                selected_units=sum(len(value) for value in positions.values()), cost=fresh.cost,
            ))
            for kind in ("K", "V"):
                pos = positions[kind]
                base = states[kind][layer].copy()
                fresh_values = np.asarray(fresh.value[kind])
                mu = np.mean(fresh_values - base[pos], axis=0)
                shifted = base + weights[kind][:, None] * mu
                non_probe = np.setdiff1d(np.arange(len(base), dtype=int), pos, assume_unique=False)
                emit_recipe_event(
                    context, self, step_index=1, layer_index=layer, state_kind=kind,
                    action=TraceAction.PROBE, positions=pos, observed_units=len(pos), recomputed_units=len(pos),
                    source=source_from_provider(provider), provider=provider_name, metadata=common,
                )

                if self.variant == "probe_only":
                    corrected = base.copy()
                    corrected[pos] = fresh_values
                    after_correction_state = base
                    correction_positions = np.asarray([], dtype=int)
                else:
                    corrected = shifted
                    after_correction_state = shifted
                    correction_positions = np.arange(len(base), dtype=int)
                states[kind][layer] = corrected
                cost.updated_units += len(pos) if self.variant == "probe_only" else len(corrected)

                before_sse = correction_sse = final_sse = None
                if targets is not None:
                    target = np.asarray(targets[kind][layer])
                    before_sse = float(np.sum((target - base) ** 2))
                    correction_sse = float(np.sum((target - after_correction_state) ** 2))
                    final_sse = float(np.sum((target - corrected) ** 2))

                if self.variant == "shift_only":
                    applied = float(np.linalg.norm(shifted - base))
                    emit_recipe_event(
                        context, self, step_index=2, layer_index=layer, state_kind=kind,
                        action=TraceAction.CORRECT, positions=correction_positions,
                        corrected_units=len(correction_positions), source=source_from_provider(provider),
                        provider=provider_name, baseline_sse=before_sse, after_sse=final_sse,
                        correction_norm=applied,
                        estimated_residual_mean_norm=float(np.linalg.norm(mu)),
                        applied_correction_norm=applied,
                        metadata={
                            **common,
                            "estimated_residual_mean_norm": float(np.linalg.norm(mu)),
                            "applied_correction_norm": applied,
                            "exact_probe_replacement": False,
                            "sse_before_layer_repair": before_sse,
                            "sse_after_nonprobe_correction": correction_sse,
                            "sse_after_probe_replacement": final_sse,
                            "correction_only_recovery": _recovery(before_sse, correction_sse),
                            "replacement_incremental_recovery": 0.0 if before_sse is not None and before_sse > 0 else None,
                            "total_layer_recovery": _recovery(before_sse, final_sse),
                        },
                    )
                else:
                    emit_recipe_event(
                        context, self, step_index=2, layer_index=layer, state_kind=kind,
                        action=TraceAction.REPLACE, positions=pos, replaced_units=len(pos),
                        source=source_from_provider(provider), provider=provider_name,
                        baseline_sse=correction_sse, after_sse=final_sse,
                        metadata={
                            **common,
                            "exact_probe_replacement": True,
                            "replacement_norm": float(np.linalg.norm(corrected[pos] - base[pos])),
                            "sse_before_layer_repair": before_sse,
                            "sse_after_nonprobe_correction": correction_sse,
                            "sse_after_probe_replacement": final_sse,
                            "correction_only_recovery": 0.0 if before_sse is not None and before_sse > 0 else None,
                            "replacement_incremental_recovery": _recovery(before_sse, final_sse),
                            "total_layer_recovery": _recovery(before_sse, final_sse),
                        },
                    )
            trace.append(trace_entry(
                len(trace), self.spec.online_steps[2], input_scope={"layer": layer},
                output_scope={"updated_kinds": ["K", "V"], "forensic_variant": self.variant},
                selected_units=sum(len(value) for value in positions.values()), state_updated=True,
            ))

        return RecipeResult(
            self.spec.recipe_id,
            RecipeExecutionStatus.EXECUTED,
            states,
            trace,
            cost,
            {
                "probe_positions": {kind: value.tolist() for kind, value in positions.items()},
                "probe_semantics": configured,
                "experiment_class": "FORENSIC_ABLATION",
                "forensic_variant": self.variant,
            },
        )
