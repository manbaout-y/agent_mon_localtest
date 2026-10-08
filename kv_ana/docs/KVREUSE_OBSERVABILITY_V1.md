# KVReuse Observability v1

KVReuse Observability is a torch-free, optional metadata layer around the frozen correction and paper-recipe algorithms. It records decisions and state work; it never stores K/V tensors and never participates in selection, correction, or cache updates.

Schema version: `kvreuse-observability-v1`.

Observability release: `1.0.1`. The stable schema identifier remains unchanged;
v1.0.1 tightens field semantics without changing any correction algorithm.

## Trace levels

Set `KVREUSE_TRACE=off|summary|detail`. The default is `summary`.

- `off`: no observability directory or files are written.
- `summary`: one aggregated event per `pair × method × layer × state_kind × action`. Events are accumulated in memory and written once; there is no per-token flush.
- `detail`: writes the same summary plus position-bearing decision/action events to compressed `trace_detail.jsonl.gz`.

`KVREUSE_TRACE_DIR` overrides the default `<experiment-output>/observability` directory.

Outputs are additive and do not replace existing experiment files:

- `trace_summary.jsonl`
- `trace_detail.jsonl.gz` in detail mode
- `cost_accounting.csv`
- `forensic_summary.csv`
- `run_manifest.json`
- `trace_schema.json`

## Actions

| Action | Meaning |
|---|---|
| `select` | A selector admits a position/state unit for a later action. Selection itself is not recomputation. |
| `probe` | A state unit is observed through the declared probe provider. Exact probes count as observed and recomputed work. |
| `recompute` | Fresh state is computed for the listed layer-position units. |
| `reuse` | Cached state is retained without correction. |
| `correct` | A materialized approximate transformation is applied. |
| `replace` | A state unit is overwritten by an exact/fresh target supplied by the declared provider. |
| `retire` | A position leaves the active selected set. |
| `skip` | A method or layer performs no materialized state action. |

## Sources

- `cached`: the action uses already cached/current state or static metadata.
- `fresh`: a non-oracle external fresh-state source.
- `oracle`: full target information is used as an oracle/reference coefficient or decision source.
- `full_exact`: an exact fresh state/probe is supplied. Provider metadata separately records whether that exact source is backed by saved target B.
- `approximate_context`: a fresh probe is evaluated under an explicitly approximate surrounding-state interpretation.

Oracle/full-target provenance is never silently relabelled as cached or deployable. Provider fields include `provider_oracle`, `provider_uses_full_target`, and the explicit probe semantics where applicable.

## Cost accounting

The unit is one complete `layer × position/state-row × state_kind` vector. The accounting CSV separates:

- `selected_units`
- `observed_units` and `probe_units`
- `recomputed_units_additive`
- `recomputed_units_unique_layer_position`
- `unique_positions`
- `corrected_units`
- `replaced_units`
- `total_available_units`

v1.0.1 makes the two denominators explicit:

- `total_positions` is the number of unique X-relative positions/state rows.
- `total_layer_position_units` is the number of available `layer × position`
  units for one state kind.
- `selected_position_fraction` divides `selected_unique_positions` by
  `total_positions`.
- observed, recomputed, and corrected layer-position fractions divide by
  `total_layer_position_units`.

The older `selected_fraction`, `probe_fraction`, `recomputed_fraction_*`, and
`corrected_fraction` columns remain for file compatibility and carry an
explicit deprecation note where their numerator granularity is ambiguous.

The v1.0.1 canonical fractions use the explicit matching denominators above.
Additive work sums every execution and can exceed 100%. Unique recompute
deduplicates `(layer, position)` within a pair/method/state. `unique_positions`
additionally removes the layer dimension.

`additive work != unique recompute != latency`.

Offline runs do not estimate latency. `duration_ns` remains null unless a future runtime supplies a real measurement. Work fractions must not be converted into latency or throughput claims.

## Instrumented algorithms

AgentKVShift emits:

- checkpoint-layer selection, full checkpoint recompute, and exact replace;
- K/V-separated later-layer probes;
- non-probe correction counts and correction norms;
- exact probe replacements;
- divergence scores in detail metadata;
- `full_exact` versus `approximate_context` probe provenance.

CacheSlide WCA emits:

- initial K-distance selection;
- layer-1 full recompute and replace;
- later-layer selected recompute and weighted correction;
- CKSim selected-set before/after state, retired positions, and newly selected positions.

CCPE training and SLIDE runtime remain outside this implementation. WCA trace is not a RoPE-shift trace or a full CacheSlide system reproduction. The real-offline pipeline includes `Qwen3.5-27B / PREFIX_ONLY / V`, enabling direct forensic comparison with AgentKVShift on that track.

The generic `ExperimentRunner` also adapts existing method diagnostics (`probe_indices`, `selected_indices`, repair costs, and residual metrics) into the same schema without changing method APIs or hashes.

## Tensor boundary

Observability files contain identifiers, positions, counts, scalar SSE/norm diagnostics, provider provenance, and small one-dimensional score/weight lists in detail mode. They never contain complete K/V, hidden-state, target-state, or reconstructed-state tensors. Existing checkpoint/artifact files remain the state-bearing boundary.

## Future AgentMon correlation

Every event reserves nullable fields:

- `request_id`
- `agent_run_id`
- `agent_step_id`
- `model_request_id`
- `reuse_request_id`
- `chunk_id`

They can later be populated by an AgentMon integration without changing the event schema. This version contains no AgentMon adapter and does not modify AgentMon.

## CPU server smoke

After unpacking the project on the server:

```sh
PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
PYTHONPATH=src "$PY" scripts/real_observability_smoke.py \
  --q8-root /data/node0_disk3/chenyang/kv_ana/qwen8_rawkv_full/raw_kv \
  --pair-id 221 \
  --condition PREFIX_ONLY \
  --output /data/node0_disk3/chenyang/kv_ana/kvreuse_observability_v1_smoke
```

This loads one saved Q8 heldout pair on CPU. It does not load a model or request an NPU. The command prints only validation flags, event counts, and observability file sizes.
`HISTORICAL_REGRESSION=PASS` in this small smoke means the pair's B1 residual inputs/formula are unchanged before and after traced recipe execution; the complete B1--B4 anchor gate remains the responsibility of `run_real_offline_pipeline.py`.

## v1.0.1 forensic semantics

### Strict summary/detail boundary

Summary records contain scalar counts only. Position-level arrays such as
`selected_before`, `selected_after`, `retire_positions`,
`newly_selected_positions`, per-position selector scores, fusion weights, and
CKSim values appear only in compressed detail records. Summary metadata exposes
the corresponding `*_count` fields and `unique_position_count`.

### Selector degeneration

AgentKVShift checkpoint selection reports score and weight min/max/mean,
zero/nonzero counts, unique score values, ties, and `selector_degenerate`.
An all-equal score vector is diagnostic information, not an error: stable top-k
selection remains exactly as defined by the frozen recipe. In particular, an
all-zero score vector may select the earliest positions under the existing
stable tie behavior; v1.0.1 records this without changing it.

### Correction attribution

`correction_norm` and `applied_correction_norm` mean the norm of the correction
actually materialized in the current state. `estimated_residual_mean_norm` is
the norm of the estimated shared residual and may be nonzero even when clipping
or zero weights cause the applied norm to be zero.

AgentKVShift records three per-layer checkpoints:

1. `sse_before_layer_repair`
2. `sse_after_nonprobe_correction`
3. `sse_after_probe_replacement`

They derive correction-only, incremental exact-replacement, and total recovery.
These are forensic metrics only and do not alter update order or output state.

### Forensic ablations

The unregistered `FORENSIC_ABLATION` variants never enter historical anchors or
the paper tournament:

- `probe_only`: retains the exact checkpoint layer and exact later-layer probe
  replacements; it disables non-probe mean-shift correction.
- `shift_only`: retains the exact checkpoint layer; later exact probes estimate
  the normal residual mean, the frozen recipe's weighted pre-replacement shift
  is applied to all positions, and later exact probe replacement is disabled.

Neither variant replaces or changes the official AgentKVShift recipe or its
`method_config_hash`.

### CacheSlide forensic variants

`retire_if_similarity_lte` is the canonical forensic WCA rule used by the
project's formal result configuration and is marked
`canonical_forensic_variant=true`. The GTE rule may be emitted as a separately
hashed sensitivity variant with `sensitivity_variant=true`. CCPE and SLIDE are
still outside this offline WCA component, and neither variant is a RoPE shift.

### Compact forensic summary

`forensic_summary.csv` provides one row per method/config/model/condition/state/
pair with selector degeneration, correct-denominator work fractions, correction
and replacement recovery, unique positions, unique layer-position units, and
additive recompute units. Empty recovery cells mean the trace did not contain a
strictly attributable SSE checkpoint; they are not estimated latency or safety
claims.
