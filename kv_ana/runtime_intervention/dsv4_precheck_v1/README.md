# DeepSeek-V4 Runtime State Intervention Precheck v1

This bundle adds a narrow, fail-closed intervention hook to the already
instrumented server `dsa_v1.py`. It supports only C4, transformer layer 2,
explicit rows, and same-input replay. It does not implement cross-context KV
reuse.

## Exact hook

The apply script requires the installed `dsa_v1.py` SHA256 to be exactly:

`99903d816a1967634f9684dd098088fdeb4711c64324739a3a0b8a7b2a4df9f2`

It verifies the unique capture anchor:

`# [DSV4_KV_DUMP] LifeMem compressed-KV experiment.`

and inserts the hook immediately before:

`if compressed_kv.numel() == 0:`

The existing capture instrumentation is not moved. The downstream path remains:

`compressed_kv -> DeviceOperator.dsa_kv_compress_scatter -> compress_kv_cache`

With `DSV4_STATE_INTERVENTION=off` (the default), the patch reads that one
environment variable and performs no helper import, tensor clone, spec read,
trace write, or other I/O.

## Install and restore

```bash
PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
PRECHECK=/data/node0_disk3/chenyang/kv_ana/dsv4_state_intervention_precheck_v1
PYTHON_BIN="$PY" "$PRECHECK/apply_intervention_patch.sh"
```

Restore the exact captured baseline:

```bash
PYTHON_BIN="$PY" "$PRECHECK/restore_intervention_patch.sh"
```

Both operations verify SHA256. Apply saves
`dsa_v1.py.dsv4_precheck_v1.backup`; restore refuses an unrecognized backup or
patched target.

## Server start contract

`start.INTERVENTION.sh` delegates to the unchanged historical launcher. Point it
at that existing file:

```bash
export DSV4_BASELINE_START=/absolute/path/to/existing/start.BASELINE.sh
```

The intervention mode is process environment, so the server must be restarted
between E0, E1, E2, and E3. Use one fixed prompt file for every case.

```bash
OUT=/data/node0_disk3/chenyang/kv_ana/dsv4_precheck_pair0
PROMPT="$OUT/pair0_prefix_a_prompt.txt"
MODEL=/data/node0_disk1/Public/DeepSeek-V4-Flash-w8a8-mtp
RUNNER="$PRECHECK/server_precheck_runner.py"
mkdir -p "$OUT/dumps"
```

The commands below assume the OpenAI-compatible chat endpoint is
`http://127.0.0.1:8000/v1/chat/completions`. Adjust `--endpoint` only if the
existing baseline launcher exposes a different local endpoint.

### E0: unpatched baseline and capture

Run this before applying the patch. Start the unchanged baseline with the
existing dump instrumentation, pointing `DSV4_KV_TAG_FILE` at
`$OUT/active_tag.txt` and `DSV4_KV_DUMP_DIR` at `$OUT/dumps`. Then run:

```bash
"$PY" "$RUNNER" run --case E0 --output-dir "$OUT" \
  --prompt-file "$PROMPT" --model "$MODEL"
```

Use the resulting layer-2 C4 artifact as `ARTIFACT` below. Its exact path follows
the existing capture naming scheme, for example:

```bash
ARTIFACT="$OUT/dumps/DSV4_PRECHECK_E0__L2__C4__P0.pt"
```

Stop the baseline server, apply the patch, then prepare and run each remaining
case. Preparation writes the spec and request tag before the server starts.

### E1: identity

```bash
"$PY" "$RUNNER" prepare --case E1 --output-dir "$OUT" --artifact "$ARTIFACT" --rows 1,2,3,4
export DSV4_STATE_INTERVENTION=identity
export DSV4_STATE_INTERVENTION_SPEC="$OUT/active_spec.json"
export DSV4_STATE_INTERVENTION_TAG_FILE="$OUT/active_tag.txt"
export DSV4_STATE_INTERVENTION_TRACE="$OUT/intervention_trace.jsonl"
"$PRECHECK/start.INTERVENTION.sh"
```

After the endpoint is ready, in another shell:

```bash
"$PY" "$RUNNER" run --case E1 --output-dir "$OUT" \
  --prompt-file "$PROMPT" --model "$MODEL"
```

Stop the server before the next case.

### E2: zero selected rows

```bash
"$PY" "$RUNNER" prepare --case E2 --output-dir "$OUT" --artifact "$ARTIFACT" --rows 1,2,3,4
export DSV4_STATE_INTERVENTION=zero_selected
export DSV4_STATE_INTERVENTION_SPEC="$OUT/active_spec.json"
export DSV4_STATE_INTERVENTION_TAG_FILE="$OUT/active_tag.txt"
export DSV4_STATE_INTERVENTION_TRACE="$OUT/intervention_trace.jsonl"
"$PRECHECK/start.INTERVENTION.sh"
```

Then:

```bash
"$PY" "$RUNNER" run --case E2 --output-dir "$OUT" \
  --prompt-file "$PROMPT" --model "$MODEL"
```

Stop the server before E3.

### E3: external same-input replay

```bash
"$PY" "$RUNNER" prepare --case E3 --output-dir "$OUT" --artifact "$ARTIFACT" --rows 1,2,3,4
export DSV4_STATE_INTERVENTION=external
export DSV4_STATE_INTERVENTION_SPEC="$OUT/active_spec.json"
export DSV4_STATE_INTERVENTION_TAG_FILE="$OUT/active_tag.txt"
export DSV4_STATE_INTERVENTION_TRACE="$OUT/intervention_trace.jsonl"
"$PRECHECK/start.INTERVENTION.sh"
```

Then:

```bash
"$PY" "$RUNNER" run --case E3 --output-dir "$OUT" \
  --prompt-file "$PROMPT" --model "$MODEL"
"$PY" "$RUNNER" summarize --output-dir "$OUT"
```

The summary reports logprob equality only when both responses contain logprobs;
it never fabricates an unavailable metric. Token IDs are similarly recorded only
if returned by the endpoint.

## Fail-closed matching and trace

For the in-scope C4/layer-2 call, request tag, mode, tensor shape, row bounds,
source shape/layer/ratio, and optional full slot mapping must all match. A
mismatch writes an error record and raises. Other transformer layers naturally
pass through the same hook and are recorded as `out_of_scope_layer_or_ratio`;
they are not claimed as successful interventions.

The JSONL trace contains norms and row indices but no full tensors. `identity`
reassigns cloned selected rows and requires zero delta. `zero_selected` and
`external` are tagged `CAUSAL_PRECHECK` and are not reuse-quality methods.

## Known risks and limits

- This code has not been executed against the server or Ascend runtime locally.
- NPU support for `index_select`, `index_copy_`, `index_fill_`, and norm at this
  exact hook must be established by E1/E2.
- The request tag is a shared file contract inherited from the capture setup;
  run the precheck with one request in flight.
- Output equality depends on deterministic serving configuration. E0 checks this
  before intervention conclusions are accepted.
- v1 intentionally rejects C128, layers other than 2, shape conversion,
  interpolation, row inference, cross-context replay, and automatic mapping.

