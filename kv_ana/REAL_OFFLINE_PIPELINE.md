# REAL-OFFLINE-PIPELINE v1

This extension runs CPU-only representation-level regression and offline experiments over the frozen `kvreuse_b5` v2.0.1 framework. It does not evaluate logits, next-token distributions, generation quality, runtime latency, or functional reuse safety.

## What runs

The single pipeline command performs:

1. real-path and fixed-anchor preflight;
2. B1 all-561 shared-residual regression for nine model/condition/state tracks;
3. B2 rank 1/2/4/8/16 pair-oracle curves on the canonical 48 pairs;
4. B2.5 train32/test16 frozen-basis transfer and pair-oracle retention;
5. B3 probe 1/2/4/8 mean, raw-linear, and frozen-basis-linear heldout tests;
6. B4 1/5/10/20% historical selective references for all48 and heldout16;
7. 20-paper capability audit;
8. strictly scoped offline paper components (AgentKVShift `full_exact` interpretation and CacheSlide WCA ambiguity variants), with saved target-B usage labelled explicitly;
9. normalized B1--B4 mechanism rows, provider requirements, and a bounded text capsule.

B2 pair low rank is oracle compressibility. B2.5 uses full test residual coefficients and remains an oracle-coefficient test. B4 residual-energy selectors are upper bounds. Negative B3 explained energy is valid. The historical B4 module deliberately preserves the old `int(round(budget*n))` convention without changing the frozen evaluator's v2.0.1 budget convention.

## Server installation

```sh
cd /data/node0_disk3/chenyang/kv_ana
tar -xzf kvreuse_b5_real_offline_pipeline_v1.tar.gz
cd kvreuse_b5_real_offline_pipeline_v1
PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
```

No model path is consumed and no accelerator is requested.

## Smoke command

```sh
PYTHONPATH=src "$PY" scripts/run_real_offline_smoke.py \
  --q8-root /data/node0_disk3/chenyang/kv_ana/qwen8_rawkv_full/raw_kv \
  --q35-root /data/node0_disk3/chenyang/kv_ana/qwen35_27b_rawkv_full/raw_kv \
  --deepseek-root /data/node0_disk3/chenyang/kv_ana/deepseek_v4_0221/lifemem_batch_real_prefix/packed \
  --deepseek-mapping /data/node0_disk3/chenyang/kv_ana/correction_b1_shared_residual_v1/deepseek_prefix_c4_strict_mapping.csv \
  --output /data/node0_disk3/chenyang/kv_ana/real_offline_pipeline_v1_smoke
```

`--max-pairs 3` is added by the smoke wrapper. Historical anchors are reported as `MISSING_REFERENCE` for this deliberately incomplete run.

## Full command

```sh
PYTHONPATH=src "$PY" scripts/run_real_offline_pipeline.py \
  --q8-root /data/node0_disk3/chenyang/kv_ana/qwen8_rawkv_full/raw_kv \
  --q35-root /data/node0_disk3/chenyang/kv_ana/qwen35_27b_rawkv_full/raw_kv \
  --deepseek-root /data/node0_disk3/chenyang/kv_ana/deepseek_v4_0221/lifemem_batch_real_prefix/packed \
  --deepseek-mapping /data/node0_disk3/chenyang/kv_ana/correction_b1_shared_residual_v1/deepseek_prefix_c4_strict_mapping.csv \
  --output /data/node0_disk3/chenyang/kv_ana/real_offline_pipeline_v1 \
  --resume
```

Stages checkpoint independently in `pipeline_state.json`. `--force-stage REGRESSION` reruns that stage and all downstream stages. Paper-component failures are recorded in `paper_method_failures.csv` and do not stop other methods.

## Interpreting status

- `PASS`: within strict historical tolerance.
- `NUMERIC_WARN`: within the wider tolerance for rounded/randomized references; execution continues.
- `SEMANTIC_SCOPE_DIFFERENCE`: an expected all48/heldout16 scope distinction.
- `METHOD_BLOCKED`: one recipe/component lacks a strict source/provider interpretation; other work continues.
- `FATAL`: adapter/schema anchor failure, Q35 pair146 nonzero regression, split leakage, severe historical divergence, or target-leakage contract violation. Downstream stages stop.

The pasteable result is:

```text
/data/node0_disk3/chenyang/kv_ana/real_offline_pipeline_v1/real_offline_pipeline_capsule.txt
```

The writer enforces the configured UTF-8 byte limit (default 45,000 bytes). Full CSV/JSON/NPZ artifacts remain on the server and are listed in the capsule.

## Leakage boundary

Deployment-visible rows must report `oracle_target_usage=none`. B is allowed only for named oracle references or explicit exact-fresh paper-component providers. The latter declare `oracle=True`, `uses_full_target=True`, expose only requested scopes, and are labelled `oracle_probe/full_exact_*`. Query, attention, semantic, anchor, partial-context, and runtime signals never fall back to B residuals or generic surrogates.

