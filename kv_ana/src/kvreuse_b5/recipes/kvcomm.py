from __future__ import annotations

from typing import Any

import numpy as np

from kvreuse_b5.providers import AnchorStateProvider

from .contract import (
    ExecutableRecipe,
    FidelityLevel,
    PaperRecipeSpec,
    RecipeCapabilities,
    RecipeCostRecord,
    RecipeExecutionContext,
    RecipeExecutionStatus,
    RecipeResult,
    RecipeStep,
    trace_entry,
)


def kvcomm_spec(variant: str = "paper_spec", **parameters: Any) -> PaperRecipeSpec:
    if variant not in {"paper_spec", "pinned_code"}:
        raise ValueError("KVCOMM variant must be paper_spec or pinned_code")
    defaults = {"temperature": 1.0, "entropy_threshold": 1.0, "top_p": 1.0, "preserve_layer0": variant == "pinned_code", "update_on_fallback": True}
    defaults.update(parameters)
    steps = (
        RecipeStep("anchor_eligibility", "paper_mechanism", "ONLINE_ANCHOR_STATE", ("P2", "C1") if variant == "pinned_code" else ("P2",)),
        RecipeStep("kv_separate_anchor_weights", "paper_mechanism", "ONLINE_ANCHOR_STATE", ("C1",) if variant == "pinned_code" else ("P2", "P3")),
        RecipeStep("offset_application", "paper_mechanism", None, ("C1",) if variant == "pinned_code" else ("P3",)),
        RecipeStep("fallback_or_anchor_update", "paper_mechanism", "ONLINE_ANCHOR_STATE", ("C2", "C3") if variant == "pinned_code" else ("P2",)),
    )
    return PaperRecipeSpec(
        recipe_id=f"kvcomm/{variant}", paper_id="12", variant_id=variant,
        fidelity_level=FidelityLevel.F1_ALGORITHMIC_REPRODUCTION,
        paper_spec_path="literature_fetch_pack_v3/literature/paper_specs/batch1/12_kvcomm.yaml",
        paper_spec_source_hash="9a578d9e4f47182d0556060a072558515521c8f863d4e9f44309d36397946df5",
        capabilities=RecipeCapabilities(("ONLINE_ANCHOR_STATE",)), required_providers=("ONLINE_ANCHOR_STATE",),
        offline_steps=(), online_steps=steps, selection_granularity="placeholder/sample anchor",
        budget_semantics="all-or-fallback; bounded online anchor pool",
        position_semantics="external RoPE-aware cache assembly required",
        state_update_semantics="add K/V-specific weighted anchor offsets; fallback may add anchor",
        evidence_ids=("P1", "P2", "P3", "P4", "C1", "C2", "C3"),
        unresolved_fields=("paper/code entropy sign and threshold convention",), runtime_dependencies=("CACHE_RUNTIME",), parameters=defaults,
    )


class KVCOMMRecipe(ExecutableRecipe):
    def __init__(self, variant: str = "paper_spec", **parameters: Any):
        super().__init__(kvcomm_spec(variant, **parameters))

    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        self.validate_context(context)
        provider = context.providers["ONLINE_ANCHOR_STATE"]
        if not isinstance(provider, AnchorStateProvider):
            raise TypeError("ONLINE_ANCHOR_STATE provider must implement AnchorStateProvider")
        states = context.copy_states()
        cost = RecipeCostRecord()
        trace = []
        candidates: dict[str, list[dict[str, Any]]] = {}
        for kind in ("K", "V"):
            out = provider.candidates(kind=kind, current_base=states[kind])
            candidates[kind] = list(out.value)
            cost.add(out.cost)
        trace.append(trace_entry(0, self.spec.online_steps[0], input_scope={"kinds": ["K", "V"]}, output_scope={"eligible": {k: len(v) for k, v in candidates.items()}}, cost=cost.provider_costs))

        chosen: dict[str, list[tuple[dict[str, Any], float]]] = {}
        fallback = False
        for kind, rows in candidates.items():
            if not rows:
                fallback = True
                chosen[kind] = []
                continue
            distances = np.asarray([row["distance"] for row in rows], dtype=float)
            logits = -distances / max(float(self.spec.parameters["temperature"]), 1e-12)
            weights = np.exp(logits - logits.max())
            weights /= weights.sum()
            if self.spec.variant_id == "pinned_code":
                order = np.argsort(-weights)
                keep = []
                cumulative = 0.0
                for index in order:
                    keep.append(int(index)); cumulative += float(weights[index])
                    if cumulative >= float(self.spec.parameters["top_p"]): break
                mask = np.zeros_like(weights); mask[keep] = weights[keep]; weights = mask / mask.sum()
            entropy = float(-np.sum(weights * np.log(np.maximum(weights, 1e-12))))
            fallback |= entropy > float(self.spec.parameters["entropy_threshold"])
            chosen[kind] = [(row, float(weight)) for row, weight in zip(rows, weights) if weight > 0]
        trace.append(trace_entry(1, self.spec.online_steps[1], input_scope={"candidate_counts": {k: len(v) for k, v in candidates.items()}}, output_scope={"weights": {k: [w for _, w in v] for k, v in chosen.items()}, "fallback": fallback}))

        if not fallback:
            for kind in ("K", "V"):
                offset = sum((weight * np.asarray(row["offset"]) for row, weight in chosen[kind]), start=np.zeros_like(states[kind]))
                if self.spec.parameters["preserve_layer0"] and offset.ndim >= 3:
                    offset[0] = 0
                states[kind] = states[kind] + offset
                cost.updated_units += int(np.prod(states[kind].shape[:-1]))
        trace.append(trace_entry(2, self.spec.online_steps[2], input_scope={"fallback": fallback}, output_scope={"updated_kinds": [] if fallback else ["K", "V"]}, state_updated=not fallback, selected_units=sum(len(v) for v in chosen.values())))

        update_id = None
        if fallback and self.spec.parameters["update_on_fallback"]:
            fresh = context.metadata.get("fallback_fresh_states")
            if fresh is not None:
                out = provider.update(base=states, fresh=fresh, metadata=context.metadata)
                update_id = out.value; cost.add(out.cost)
        trace.append(trace_entry(3, self.spec.online_steps[3], input_scope={"fallback": fallback}, output_scope={"anchor_update": update_id}, state_updated=False))
        return RecipeResult(self.spec.recipe_id, RecipeExecutionStatus.EXECUTED, states, trace, cost, {"fallback": fallback, "anchor_update": update_id})
