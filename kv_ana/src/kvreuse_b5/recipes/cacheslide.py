from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from kvreuse_b5.providers import PartialForwardProvider
from kvreuse_b5.observability.events import collector_from_context, emit_recipe_event, source_from_provider
from kvreuse_b5.observability.schema import TraceAction, TraceSource

from .contract import *


@dataclass(frozen=True)
class CacheSlidePositionModel:
    name: str = "CCPE"
    requires_training: bool = True
    requires_model_modification: bool = True
    evidence_refs: tuple[str, ...] = ("P2",)


@dataclass(frozen=True)
class CacheSlideRuntimeSpec:
    name: str = "SLIDE"
    executable_here: bool = False
    evidence_refs: tuple[str, ...] = ("P4", "C2")


def cacheslide_spec(variant: str = "paper_spec", **parameters: Any) -> PaperRecipeSpec:
    if variant not in {"paper_spec", "pinned_code"}: raise ValueError("CacheSlide variant must be paper_spec or pinned_code")
    params = {"top_k_ratio": 0.26, "gating_interval": 4, "cksim_threshold": 0.12, "cksim_rule": None, "ccpe_executed": False, "slide_executed": False}
    params.update(parameters)
    fidelity = FidelityLevel.F1_ALGORITHMIC_REPRODUCTION if variant == "paper_spec" else FidelityLevel.F3_INTERFACE_ONLY
    return PaperRecipeSpec(
        recipe_id=f"cacheslide/wca_{variant}", paper_id="13", variant_id=f"wca_{variant}", fidelity_level=fidelity,
        paper_spec_path="literature_fetch_pack_v3/literature/paper_specs/batch1/13_cacheslide.yaml",
        paper_spec_source_hash="90c6bd73d9cea10da1af4d6c61b3948fcc8c98b9532e1c8db2b62bd3022dc377",
        capabilities=RecipeCapabilities(("PARTIAL_FORWARD",)), required_providers=("PARTIAL_FORWARD",),
        offline_steps=(RecipeStep("ccpe_training", "paper_mechanism", None, ("P2",)),),
        online_steps=(
            RecipeStep("full_layer1_k_distance_detection", "paper_mechanism", "PARTIAL_FORWARD", ("P3",)),
            RecipeStep("later_layer_weighted_cache_adaptation", "paper_mechanism", "PARTIAL_FORWARD", ("P3",)),
            RecipeStep("periodic_cksim_gating", "paper_mechanism", "PARTIAL_FORWARD", ("P3",)),
            RecipeStep("slide_page_runtime", "system_optimization", "CACHE_RUNTIME", ("P4", "C2")),
        ),
        selection_granularity="token x layer rotating top-k", budget_semantics="full layer 1 plus top-k fraction later",
        position_semantics="CCPE trained position ranges; not RoPE shift",
        state_update_semantics="selected fresh/reused K/V weighted fusion; periodic selected-set rotation",
        evidence_ids=("P1", "P2", "P3", "P4", "P5", "C1", "C2", "R1"), unresolved_fields=("exact pinned-code WCA symbol", "CKSim inequality wording"), runtime_dependencies=("TRAINING", "MODEL_MODIFICATION", "CACHE_RUNTIME", "SYSTEM_RUNTIME"), parameters=params,
    )


class CacheSlideWCARecipe(ExecutableRecipe):
    def __init__(self, variant: str = "paper_spec", **parameters: Any):
        if variant != "paper_spec":
            raise ValueError("pinned_code CacheSlide has no auditable complete WCA implementation")
        super().__init__(cacheslide_spec(variant, **parameters))

    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        self.validate_context(context)
        rule = self.spec.parameters["cksim_rule"]
        if rule not in {"retire_if_similarity_gte", "retire_if_similarity_lte"}:
            raise ValueError("cksim_rule must explicitly resolve the paper's CKSim inequality as retire_if_similarity_gte or retire_if_similarity_lte")
        provider = context.providers["PARTIAL_FORWARD"]
        if not isinstance(provider, PartialForwardProvider): raise TypeError("CacheSlide WCA requires PartialForwardProvider")
        states = context.copy_states(); cost = RecipeCostRecord(); trace = []
        observer = collector_from_context(context)
        target_states = context.metadata.get("observability_targets") if observer is not None else None
        provider_name = getattr(provider.capabilities, "source", provider.capability)
        provider_metadata = {
            "provider_oracle": bool(provider.capabilities.oracle),
            "provider_uses_full_target": bool(provider.capabilities.uses_full_target),
            "wca_component_only": True,
            "canonical_forensic_variant": rule == "retire_if_similarity_lte",
            "sensitivity_variant": rule == "retire_if_similarity_gte",
            "forensic_variant": f"wca_{rule}",
        }
        layers, tokens = states["K"].shape[:2]
        score = provider.score(layer=0, kinds=("K",), current=states); cost.add(score.cost)
        full = np.arange(tokens); fresh = provider.recompute(layer=0, positions=full, kinds=("K", "V"), current=states); cost.add(fresh.cost)
        layer1_scores = np.asarray(score.value)
        count = max(1, math.ceil(tokens * float(self.spec.parameters["top_k_ratio"])))
        selected = np.argsort(-layer1_scores, kind="stable")[:count]
        layer0_base = {kind: states[kind][0].copy() for kind in ("K", "V")} if observer is not None else {}
        for kind in ("K", "V"):
            states[kind][0] = fresh.value[kind]
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.SELECT, positions=selected, selected_units=len(selected),
                source=source_from_provider(provider), provider=provider_name,
                metadata={
                    **provider_metadata,
                    "selection_phase": "initial",
                    **({"detail": {"k_distance_score_by_position": layer1_scores.tolist()}} if observer is not None and observer.level.value == "detail" else {}),
                },
            )
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.RECOMPUTE, positions=full, observed_units=len(full), recomputed_units=len(full),
                source=source_from_provider(provider), provider=provider_name, metadata=provider_metadata,
            )
            baseline = after = None
            if target_states is not None:
                target = np.asarray(target_states[kind][0])
                baseline = float(np.sum((target - layer0_base[kind]) ** 2))
                after = float(np.sum((target - states[kind][0]) ** 2))
            emit_recipe_event(
                context, self, step_index=0, layer_index=0, state_kind=kind,
                action=TraceAction.REPLACE, positions=full, replaced_units=len(full),
                source=source_from_provider(provider), provider=provider_name,
                baseline_sse=baseline, after_sse=after,
                metadata={
                    **provider_metadata,
                    "exact_fresh_replacement": True,
                    "replacement_norm": float(np.linalg.norm(states[kind][0] - layer0_base[kind])) if observer is not None else None,
                },
            )
        cost.updated_units += tokens * 2
        trace.append(trace_entry(0, self.spec.online_steps[0], input_scope={"layer": 0}, output_scope={"selected": selected.tolist()}, selected_units=tokens, state_updated=True, cost={**score.cost, **fresh.cost}))
        interval = int(self.spec.parameters["gating_interval"])
        retired: set[int] = set()
        initial_order = np.argsort(-layer1_scores, kind="stable")
        for layer in range(1, layers):
            if len(selected) == 0:
                for kind in ("K", "V"):
                    emit_recipe_event(
                        context, self, step_index=1, layer_index=layer, state_kind=kind,
                        action=TraceAction.SKIP, source=TraceSource.CACHED,
                        metadata={**provider_metadata, "reason": "no_selected_positions"},
                    )
                break
            out = provider.recompute(layer=layer, positions=selected, kinds=("K", "V"), current=states); cost.add(out.cost)
            current_selected = selected.copy()
            k_similarity = None
            for kind in ("K", "V"):
                base = states[kind][layer, selected].copy(); fresh_values = np.asarray(out.value[kind])
                distance = np.linalg.norm(fresh_values - base, axis=-1)
                alpha = distance / max(float(distance.max()), 1e-12)
                states[kind][layer, selected] = alpha[:, None] * fresh_values + (1.0 - alpha[:, None]) * base
                emit_recipe_event(
                    context, self, step_index=1, layer_index=layer, state_kind=kind,
                    action=TraceAction.RECOMPUTE, positions=current_selected,
                    observed_units=len(current_selected), recomputed_units=len(current_selected),
                    source=source_from_provider(provider), provider=provider_name, metadata=provider_metadata,
                )
                baseline = after = None
                if target_states is not None:
                    target = np.asarray(target_states[kind][layer, current_selected])
                    baseline = float(np.sum((target - base) ** 2))
                    after = float(np.sum((target - states[kind][layer, current_selected]) ** 2))
                emit_recipe_event(
                    context, self, step_index=1, layer_index=layer, state_kind=kind,
                    action=TraceAction.CORRECT, positions=current_selected, corrected_units=len(current_selected),
                    source=source_from_provider(provider), provider=provider_name,
                    baseline_sse=baseline, after_sse=after,
                    correction_norm=float(np.linalg.norm(states[kind][layer, current_selected] - base)),
                    applied_correction_norm=float(np.linalg.norm(states[kind][layer, current_selected] - base)),
                    metadata={
                        **provider_metadata,
                        "adaptation": "weighted_cache_adaptation",
                        **({"detail": {"fusion_weight_by_position": alpha.tolist()}} if observer is not None and observer.level.value == "detail" else {}),
                    },
                )
                if kind == "K":
                    numer = np.sum(fresh_values * base, axis=-1)
                    denom = np.linalg.norm(fresh_values, axis=-1) * np.linalg.norm(base, axis=-1)
                    k_similarity = np.where(denom > 0, numer / np.maximum(denom, 1e-12), np.where(np.linalg.norm(fresh_values - base, axis=-1) == 0, 1.0, 0.0))
            cost.selected_units += len(selected); cost.updated_units += len(selected) * 2
            trace.append(trace_entry(len(trace), self.spec.online_steps[1], input_scope={"layer": layer, "selected": current_selected.tolist()}, output_scope={"fused": current_selected.tolist()}, selected_units=len(selected), state_updated=True, cost=out.cost))
            if interval > 0 and layer % interval == 0:
                assert k_similarity is not None
                retire_mask = k_similarity >= float(self.spec.parameters["cksim_threshold"]) if rule == "retire_if_similarity_gte" else k_similarity <= float(self.spec.parameters["cksim_threshold"])
                retired.update(int(pos) for pos in current_selected[retire_mask])
                kept = [int(pos) for pos in current_selected[~retire_mask]]
                for candidate in initial_order:
                    candidate = int(candidate)
                    if len(kept) >= count: break
                    if candidate not in kept and candidate not in retired: kept.append(candidate)
                selected = np.asarray(kept, dtype=int)
                retired_now = current_selected[retire_mask]
                newly_selected = np.asarray(sorted(set(selected.tolist()) - set(current_selected.tolist())), dtype=int)
                for kind in ("K", "V"):
                    emit_recipe_event(
                        context, self, step_index=2, layer_index=layer, state_kind=kind,
                        action=TraceAction.RETIRE, positions=retired_now,
                        source=TraceSource.CACHED, provider=provider_name,
                        metadata={
                            **provider_metadata,
                            "selected_before_count": len(current_selected),
                            "selected_after_count": len(selected),
                            "retired_count": len(retired_now),
                            "cksim_rule": rule,
                            **({"detail": {
                                "selected_before": current_selected.tolist(),
                                "selected_after": selected.tolist(),
                                "retire_positions": retired_now.tolist(),
                                "cksim_by_position": k_similarity.tolist(),
                            }} if observer is not None and observer.level.value == "detail" else {}),
                        },
                    )
                    emit_recipe_event(
                        context, self, step_index=2, layer_index=layer, state_kind=kind,
                        action=TraceAction.SELECT, positions=newly_selected, selected_units=len(newly_selected),
                        source=TraceSource.CACHED, provider=provider_name,
                        metadata={
                            **provider_metadata,
                            "selection_phase": "gating_replacement",
                            "selected_before_count": len(current_selected),
                            "selected_after_count": len(selected),
                            "newly_selected_count": len(newly_selected),
                            **({"detail": {
                                "selected_before": current_selected.tolist(),
                                "selected_after": selected.tolist(),
                                "newly_selected_positions": newly_selected.tolist(),
                            }} if observer is not None and observer.level.value == "detail" else {}),
                        },
                    )
                trace.append(trace_entry(len(trace), self.spec.online_steps[2], input_scope={"layer": layer, "previous": current_selected.tolist(), "cksim": k_similarity.tolist(), "rule": rule}, output_scope={"selected": selected.tolist(), "retired": sorted(retired)}, selected_units=len(selected)))
        return RecipeResult(self.spec.recipe_id, RecipeExecutionStatus.EXECUTED, states, trace, cost, {"ccpe": "contract only", "wca": "executed", "slide": "runtime spec only", "selected": selected.tolist()})
