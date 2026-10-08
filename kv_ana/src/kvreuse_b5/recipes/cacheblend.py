from __future__ import annotations

import math
from typing import Any

import numpy as np

from kvreuse_b5.providers import PartialForwardProvider

from .contract import *


def cacheblend_spec(variant: str = "paper_spec", **parameters: Any) -> PaperRecipeSpec:
    if variant not in {"paper_spec", "pinned_code"}:
        raise ValueError("CacheBlend variant must be paper_spec or pinned_code")
    params = {"recompute_ratio": 0.2, "candidate_ratio": 0.5, "variant_controller": "gradual" if variant == "paper_spec" else "check_layers"}
    params.update(parameters)
    evidence = ("P4",) if variant == "paper_spec" else ("C1",)
    return PaperRecipeSpec(
        recipe_id=f"cacheblend/{variant}", paper_id="01", variant_id=variant,
        fidelity_level=FidelityLevel.F1_ALGORITHMIC_REPRODUCTION,
        paper_spec_path="literature_fetch_pack_v3/literature/paper_specs/batch1/01_cacheblend.yaml",
        paper_spec_source_hash="608426f1a39512adee9de723fd5e3fee406ca3b9c9a87852af4c3cfa476593a2",
        capabilities=RecipeCapabilities(("PARTIAL_FORWARD",)), required_providers=("PARTIAL_FORWARD",), offline_steps=(),
        online_steps=(
            RecipeStep("layer1_candidate_discovery", "paper_mechanism", "PARTIAL_FORWARD", evidence),
            RecipeStep("layerwise_selective_recompute", "paper_mechanism", "PARTIAL_FORWARD", ("P3", "P4") if variant == "paper_spec" else ("C1",)),
            RecipeStep("cache_scatter_and_hidden_propagation", "paper_mechanism", None, ("P3", "C1")),
            RecipeStep("load_recompute_overlap", "system_optimization", "CACHE_RUNTIME", ("P5",), {"required_for_algorithm": False}),
        ),
        selection_granularity="token x layer", budget_semantics="full layer 1; staged reduction from r1 toward r",
        position_semantics="cached keys positioned by runtime before contextual repair",
        state_update_semantics="fresh selected K/V overwrite cache; selected hidden flow propagates layerwise",
        evidence_ids=("P1", "P2", "P3", "P4", "P5", "P6", "C1"), runtime_dependencies=("CACHE_RUNTIME",), parameters=params,
    )


class CacheBlendRecipe(ExecutableRecipe):
    def __init__(self, variant: str = "paper_spec", **parameters: Any):
        super().__init__(cacheblend_spec(variant, **parameters))

    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        self.validate_context(context)
        provider = context.providers["PARTIAL_FORWARD"]
        if not isinstance(provider, PartialForwardProvider): raise TypeError("CacheBlend requires PartialForwardProvider")
        states = context.copy_states(); cost = RecipeCostRecord(); trace = []
        layers, tokens = states["K"].shape[:2]
        score = provider.score(layer=0, kinds=("K", "V"), current=states); cost.add(score.cost)
        candidate_count = max(1, math.ceil(tokens * float(self.spec.parameters["candidate_ratio"])))
        candidates = np.argsort(-np.asarray(score.value), kind="stable")[:candidate_count]
        full = np.arange(tokens)
        fresh = provider.recompute(layer=0, positions=full, kinds=("K", "V"), current=states); cost.add(fresh.cost)
        for kind in ("K", "V"): states[kind][0, full] = fresh.value[kind]
        cost.updated_units += tokens * 2
        trace.append(trace_entry(0, self.spec.online_steps[0], input_scope={"layer": 0}, output_scope={"candidates": candidates.tolist()}, selected_units=tokens, state_updated=True, cost={**score.cost, **fresh.cost}))
        final_count = max(1, math.ceil(tokens * float(self.spec.parameters["recompute_ratio"])))
        for layer in range(1, layers):
            score = provider.score(layer=layer, kinds=("K", "V"), current=states, positions=candidates); cost.add(score.cost)
            if self.spec.variant_id == "paper_spec":
                progress = layer / max(1, layers - 1)
                count = max(final_count, math.ceil(candidate_count + progress * (final_count - candidate_count)))
            else:
                count = final_count
            selected = candidates[np.argsort(-np.asarray(score.value)[candidates], kind="stable")[:count]]
            fresh = provider.recompute(layer=layer, positions=selected, kinds=("K", "V"), current=states); cost.add(fresh.cost)
            trace.append(trace_entry(len(trace), self.spec.online_steps[1], input_scope={"layer": layer, "candidate_count": len(candidates)}, output_scope={"selected": selected.tolist()}, selected_units=len(selected), cost={**score.cost, **fresh.cost}))
            for kind in ("K", "V"): states[kind][layer, selected] = fresh.value[kind]
            cost.selected_units += len(selected); cost.updated_units += len(selected) * 2
            trace.append(trace_entry(len(trace), self.spec.online_steps[2], input_scope={"layer": layer, "selected": selected.tolist()}, output_scope={"cache_written": ["K", "V"]}, selected_units=len(selected), state_updated=True))
        return RecipeResult(self.spec.recipe_id, RecipeExecutionStatus.EXECUTED, states, trace, cost, {"system_optimization": "load/recompute overlap not executed"})
