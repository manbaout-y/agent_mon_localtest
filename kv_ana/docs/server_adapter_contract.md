# Future server adapter contract — v2.0.1

The server-side reader must implement `StatePairAdapter.iter_pairs()` and yield normalized states:

```python
yield StatePair(
    pair_id=sample_id,
    split="train",                 # or "test"
    model_family=model_family,
    condition=condition,
    state_kind=state_kind,
    layer_id=layer_id,
    a=reused_state_TF,
    b=exact_state_TF,
    positions=positions_T,
    metadata=metadata,
)
```

Required guarantees:

- `a` and `b` are finite CPU tensor-like arrays with the same `[T,F]` shape.
- `positions` is finite `[T]` and row-aligned with both states.
- `(pair_id, layer_id)` belongs to exactly one predefined split.
- The full `(model_family, condition, state_kind, pair_id, layer_id)` state identity is unique across the dataset.
- The adapter performs all native layout conversion. Core code must never see Qwen head layout or DeepSeek packed-row indexing.
- Logical layers from the same sample use the same `pair_id` or explicit `metadata.sample_id`; detector evaluation additionally requires aligned positions.

Optional metadata is explicit and capability-gated:

| Key | Contract |
|---|---|
| `anchor_descriptor` | finite `[D]` tensor representing a precomputed, declared descriptor; never derived from test B by the framework |
| `rope_metadata` | mapping with `source_positions`, `target_positions`, `num_kv_heads`, `head_dim`, `inv_freq`, `rotary_dim`, `flatten_layout=T_HD`, `rotary_layout` |
| `query_info` | adapter-defined query tokens/features for a future `QueryImportanceProvider` |
| `query_score` | genuine query-conditioned importance, with provenance documented by adapter |
| `attention_info` | genuine attention contribution/weights, with layout documented by adapter |
| `dataset_group_id` | explicit group namespace when the default state-scoped `pair_id` grouping is insufficient |
| `x_start_position` | explicit X/context start used by fixed-edge or uniform-absolute position bins |
| `unit_ids` | optional stable logical row IDs when positions are not unique and neutral tie-breaking is required |

Not implemented here: `QwenRawKVAdapter`, `DeepSeekStrictC4Adapter`, any checkpoint discovery, native file paths, device execution, model loading, or runtime attention hooks. Those adapters may depend on torch; the offline package itself does not require torch for synthetic operation.
