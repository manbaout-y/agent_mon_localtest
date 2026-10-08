# Experiment output schema — v2.0.1

Each successful `results.jsonl` row contains:

| Field | Meaning |
|---|---|
| `pair_id`, `state_identity`, `sample_identity`, `dataset_group_identity`, `split` | collision-free state and grouping identities |
| `model_family`, `condition`, `state_kind`, `layer_id` | StatePair partition fields |
| `method`, `variant`, `method_config_hash` | algorithm identity and deterministic hash of its complete canonical configuration |
| `requested_budget`, `unit_budget`, `actual_budget`, `budget_overshoot`, `rank`, `probe_count`, `anchor_count`, `detector_count` | configured and realized variant dimensions |
| `baseline_sse`, `corrected_sse`, `energy_explained` | residual metrics |
| `observed_probe_units`, `detector_recomputed_units`, `exact_repaired_units` | disjoint algorithmic compute categories |
| `exact_compute_units`, `observed_units`, `total_units`, `logical_repair_fraction` | exact compute is detector+repair; observed is probe+detector+repair |
| `basis_rank`, `detector_layers`, `method_specific_cost` | additional algorithmic costs |
| `oracle`, `deployable_like`, `category`, `capabilities` | leakage/capability declaration |
| `artifact_hash` | shared fitted/decomposition artifact identity, or null |
| `status` | `OK`, `ZERO_RESIDUAL`, `ZERO_BASELINE_DEGRADED`, `CAPABILITY_MISSING`, or `ERROR` |
| `config_hash` | SHA-256 of canonical full configuration |
| `checkpoint_key` | run-scoped checkpoint identity derived from method config, full config, dataset, and framework version |
| `version` | framework output version; `2.0.1` for this release |
| `diagnostics` | method-specific auditable details |

Example:

```json
{
  "pair_id": "shared-test",
  "state_identity": ["q8_like", "PREFIX_ONLY", "K", "shared-test", 0],
  "sample_identity": ["q8_like", "PREFIX_ONLY", "K", "shared-test"],
  "split": "test",
  "model_family": "q8_like",
  "condition": "PREFIX_ONLY",
  "state_kind": "K",
  "layer_id": 0,
  "method": "probe_frozen_basis",
  "variant": "default",
  "method_config_hash": "...",
  "requested_budget": null,
  "rank": 4,
  "probe_count": 4,
  "anchor_count": null,
  "detector_count": null,
  "baseline_sse": 19.42,
  "corrected_sse": 0.0,
  "energy_explained": 1.0,
  "observed_units": 4,
  "detector_recomputed_units": 0,
  "exact_repaired_units": 0,
  "total_units": 24,
  "logical_repair_fraction": 0.0,
  "oracle": false,
  "status": "OK",
  "config_hash": "...",
  "checkpoint_key": "...",
  "version": "2.0.1"
}
```

`ZERO_RESIDUAL` rows have zero baseline and zero corrected SSE within tolerance. `ZERO_BASELINE_DEGRADED` rows preserve the actual nonzero corrected SSE when a correction damages an originally exact state. Both serialize energy as JSON `NaN` for audit fidelity and are excluded from mean, median, and pooled energy. Summaries expose `zero_residual_count` and `zero_baseline_degraded_count`; formal runs reject degraded rows by default, while record mode retains them for diagnostics. `CAPABILITY_MISSING` and `ERROR` rows retain pair, method, capability, and config identity plus an `error` message.

Checkpoint files are atomic JSON records under `checkpoints/pairs/`; their key includes full state identity, method configuration hash, full config hash, dataset fingerprint, and framework version. Load/save verifies identity and checkpoint-key round-trip, while the manifest rejects reuse after dataset membership or tensor content changes. Aggregation keys use the pure `method_config_hash`, not the run-scoped checkpoint key. `sample_costs.json` contains one cost record per logical group × canonical method configuration, preventing detector cost duplication.
