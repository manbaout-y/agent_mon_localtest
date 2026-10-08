# Algorithm specifications — v2.0.1

## State and evaluation contract

For every pair, `A` is the reused/old state, `B` is the exact target, and `Delta = B - A`. Algorithms receive only `[T,F]` states. Input validation rejects shape mismatch and NaN/Inf before a method runs.

Identity has three non-interchangeable levels:

- `state_identity = (model_family, condition, state_kind, pair_id, layer_id)` uniquely identifies one result/checkpoint state.
- `sample_identity = (model_family, condition, state_kind, pair_id)` identifies an aligned cross-layer sample.
- `base_pair_identity = (model_family, pair_id)` defines the split-assignment boundary across conditions and state kinds.
- `dataset_group_identity` is the explicit runner grouping boundary and never crosses model/condition/state implicitly.

Before fitting, every `sample_identity` must have one split across all layers, and every `base_pair_identity` must have one split across all conditions and state kinds. A repeated `pair_id` in another model family remains an independent namespace. Violations fail before any train artifact is created and report the conflicting layers/states.

The implementation accepts NumPy arrays and CPU torch tensors through the same `StatePair` boundary. Internally it uses NumPy so the complete synthetic suite remains runnable in a minimal CPU Python environment. Future adapters own all reshaping such as `[1,H,T,D] -> [T,H*D]` and packed-row mapping.

`MethodCapabilities` distinguishes oracle/reference, offline-trainable, deployment-like estimators, and methods that are not executable with current offline data. It separately audits full-target, probe-target, and selected-repair-target access. A full-target method cannot be categorized as deployable.

## Implemented corrections

| Registry name | Definition | Class | Target visibility |
|---|---|---|---|
| `no_correction` | `A_hat=A` | `NoCorrection` | none |
| `shared_residual_oracle` | add row-mean of current `Delta` | `SharedResidualOracle` | full target; oracle |
| `pair_lowrank_oracle` | best rank-r SVD of current `Delta` | `PairLowRankOracle` | full target; oracle |
| `frozen_lowrank_oracle` | train-frozen feature basis, exact test coefficients | `FrozenSubspaceOracleCoeff` | full target; oracle upper bound |
| `probe_mean` | mean residual from midpoint/uniform probes | `ProbeMean` | probe rows only |
| `probe_frozen_basis` | probe coefficients plus independent `linear_depth` interpolation | `ProbeFrozenBasis` | probe rows only |
| `anchor_residual` | kNN over explicit train anchor descriptors | `AnchorResidualCorrection` | no test target for neighbor search |
| `random_expected` | exact theoretical expected SSE under random row repair | `RandomExpected` | none; metric-only and forbidden in composites |
| `boundary_first` | smallest relative positions first | `BoundaryFirst` | selected repair rows |
| `joint_unit_oracle` | descending current row residual energy | `JointUnitOracle` | full target; oracle |
| `static_layer` | train-frozen mean energy by layer | `StaticLayerSelector` | selected repair rows |
| `static_position` | train-frozen position-bin score | `StaticPositionSelector` | selected repair rows |
| `static_joint` | train-frozen layer × position-bin score | `StaticLayerPositionSelector` | selected repair rows |
| `detector_layer_selector` | aggregate residual energy from train-fixed detector layers and project position ranking | `DetectorLayerCorrection` | all rows of detector layers plus selected repair rows |
| `rope_position` | explicit RoPE source-to-target rotation | `RoPEPositionCorrection` | no target state; explicit RoPE metadata |
| `composite` | sequential stage application with residual and cost propagation | `CompositeCorrection` | union of stage capabilities |

Ranks, probe counts, anchor counts, budgets, bin mode/count, detector selection/count, weighting, and composite stages are configuration parameters.

## Fit and grouping rules

Train artifacts are keyed by `model_family × condition × state_kind × layer_id`. A run-scoped `ArtifactStore` trains one `FrozenSubspaceArtifact` at the maximum requested rank and shares its prefixes across frozen-oracle, probe, estimator, and composite stages. `PairSVDArtifact` similarly caches one decomposition per full `state_identity`. Artifacts record feature dimension, singular values, train state identities, fit config hash, and artifact hash. `PrecomputedArtifactProvider` is interface-only for future external artifacts.

Every fit entry point checks both `split == train` and membership in `FitContext.train_ids`. Artifact audit asserts that stored fit identities do not intersect test identities.

Selective budgets apply to all `layer × row` units in one logical group. `neutral_partial` uses an exact row budget and SHA-256 tie-breaking from config seed, state identity, and logical position; it never uses stable row order, layer order, residual energy, or target B to resolve equal scores. `group_atomic` repairs whole layers or bins and records overshoot. Every result reports requested budget, unit budget, actual budget, and overshoot.

Position scoring supports fixed edges relative to explicit `x_start_position`, normalized-depth bins, and unbounded uniform absolute-width bins. Test data never changes bin definitions.

Detector groups require aligned positions. In `exact_recompute` mode, every detector-layer state is replaced by exact B, then detector residual energy predicts additional positions in other layers. In `external_score` mode, a real `DetectorScoreProvider` is mandatory; without it the method returns `CAPABILITY_MISSING`. Detector IDs are fixed or train-selected. Per-layer costs allocate detector recompute only to detector layers, and `sample_costs.json` records the group total once.

## Position correction

`apply_rope_shift` validates `F == num_kv_heads × head_dim`, reshapes `[T,H*D]` to `[T,H,D]`, rotates `rotary_dim` independently inside every head, preserves each head's non-rotary tail, and flattens back. Metadata must provide head count/dimension, source/target positions, inverse frequencies, rotary dimension, `T_HD` flatten layout, and rotary layout. No model defaults are guessed.

The `interleaved` layout rotates adjacent feature pairs. The `split_half` layout splits each head's final rotary feature axis as `x1=x[..., :rotary_dim/2]` and `x2=x[..., rotary_dim/2:]`; it never splits or mixes the head axis. Both layouts require an even `rotary_dim`.

## Metrics and invariants

The evaluator always computes both baseline and corrected SSE. If both are within tolerance, status is `ZERO_RESIDUAL` and energy is `NaN`. If baseline is zero but correction introduces nonzero SSE, status is `ZERO_BASELINE_DEGRADED`, true corrected SSE is preserved, and energy remains `NaN`. Formal runs default to policy `error`; diagnostic runs may use `record`. Neither status enters mean, median, or pooled energy, and summaries report both counts separately. Pooled energy is always computed from summed valid SSE values.

Aggregation uses the canonical dimensions `(model_family, condition, state_kind, method, variant, method_config_hash, rank, requested_budget, probe_count, anchor_count, detector_count)`. `method_config_hash` is a SHA-256 hash of canonical, key-sorted JSON containing the complete method configuration. Composite configuration is recursive and includes ordered stage configurations. Output contains global, budget, per-layer, and method-ranking summaries; different binners, weighting strategies, composite stages, ranks, or budgets cannot merge.

Automatic checks cover state-identity uniqueness, checkpoint identity/method/dataset-fingerprint round-trip, canonical aggregation dimensions, artifact train/test audit, basis rank/feature dimension, multi-head RoPE consistency, selector budgets, composite materialization, detector cost de-duplication, no-correction zero explained energy, low-rank upper bounds, tensor finiteness, and zero residual.

## Definition decisions and open ambiguities

- A discrete positive budget uses `ceil(total_units * budget)` so a nonzero budget observes/repairs at least one row.
- Fixed/absolute bins require explicit `x_start_position`; long sequences are never clipped into a catch-all configured bin except where a fixed edge intentionally defines `1024+`.
- Train basis fitting concatenates residual rows within a fit key and performs uncentered SVD. No residual mean is subtracted.
- Anchor residual alignment is an explicit `ResidualAlignmentStrategy`; v2 provides `normalized_linear`. The method is an experimental generic mechanism baseline inspired by KVCOMM, not an exact reproduction.
- `RandomExpected` reports theoretical expected SSE rather than sampling a mask. Its accounting distributes the group repair count deterministically only to make total algorithmic units auditable.
- Static layer ranking requires multi-layer groups to be meaningful. Direct single-pair calls are the single-layer special case.
- Composite evaluation applies every stage to the currently corrected state. Because v2.0.1 has no stage-scoped artifact store, a frozen-subspace-dependent method is allowed only as a standalone method or composite stage 0; placing it later fails fast. A composition containing any oracle stage is itself reference/oracle, not deployable.
