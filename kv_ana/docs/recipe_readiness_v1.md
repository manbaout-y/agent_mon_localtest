# Paper Recipe readiness v1

Phase 2.2A adds an extension layer above the frozen `kvreuse_b5` v2.0.1 evaluator. It does not add a server adapter or claim an end-to-end paper-system reproduction.

## Contract boundary

`PaperRecipeSpec` is the source-traceable algorithm contract. It records paper/variant identity, source-spec hash, fidelity, capability/provider requirements, ordered offline/online steps, budget/position/state-update semantics, evidence IDs, unresolved fields, and runtime dependencies. `ExecutableRecipe` may run only when its required providers are present and are neither oracle nor full-target providers unless the spec explicitly permits that use.

The deterministic `recipe_spec_hash` covers the frozen paper-spec hash and complete recipe contract. `recipe_variant_hash` additionally makes variant, parameter, step-order, and provider requirements checkpoint-visible.

Provider calls return values, algorithmic cost, exact/oracle/full-target declarations, and source metadata. Every execution returns ordered trace entries containing the step, provider, scopes, selected units, state mutation, and cost.

## Representative components

| Paper | Recipe component | Recipe-spec fidelity | Current executable fidelity | Deliberately not executed |
|---|---|---|---|---|
| KVCOMM | paper and pinned-code anchor-offset orchestration | F1 for the separately sourced variants | F1 orchestration with synthetic online anchor state | real RoPE/cache assembly and production fallback prefill |
| AgentKVShift | checkpoint selection, per-layer fresh probes, K/V-specific weighted mean shift | F1 with explicit probe-semantics variant | F1 orchestration under the selected synthetic provider semantics | unresolved choice between full-exact and approximate-context probe generation |
| CacheBlend | first-layer discovery and layerwise partial recompute/scatter | F1 algorithmic component | F1 orchestration with synthetic exact partial-forward provider | cache I/O overlap and production sparse forward kernel |
| CacheSlide | WCA detection, fusion, and periodic gating | F1 WCA component | F1 WCA orchestration | CCPE training/model change and SLIDE page runtime; pinned code WCA remains non-executable |

`RoPEPositionCorrection` is not used as a CacheSlide alias: CCPE is a trained position model, WCA is a K-distance-driven weighted cache update, and SLIDE is a runtime page manager.

## Declarative 20-paper status

All 20 Phase 2.1 paper specs have a JSON-compatible YAML skeleton under `src/kvreuse_b5/recipes/specs/`. Four papers expose executable synthetic components. The remaining papers stay declarative and blocked by their actual query, attention, partial-forward, semantic, training, model, cache, or system requirements. DAF additionally remains method-spec blocked.

## Provider work before server integration

Production work still needs model-bound implementations of `PartialForwardProvider`, `FreshProbeProvider`, `AnchorStateProvider`, `QuerySignalProvider`, `AttentionSignalProvider`, `PositionTransformProvider`, and `CacheRuntimeProvider`. Papers involving semantic matching additionally need `SemanticEmbeddingProvider`; detector-style recipes need `DetectorScoreProvider`. None is silently substituted by residual energy, random scores, or target `B`.

## Core audit

All files that existed under `src/kvreuse_b5` before Phase 2.2A match their frozen SHA-256 baseline. Phase 2.2A only adds `recipes/`, `providers/`, `cli/recipes.py`, tests, this document, and validation artifacts. No core extension was required.
