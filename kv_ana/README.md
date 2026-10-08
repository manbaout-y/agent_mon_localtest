# kvreuse_b5 v2.0.1

`kvreuse_b5` is a correctness-first, CPU-only offline research harness for non-prefix KV/state reuse. Version 2.0.1 retains the v2.0 identity, artifact, selector, and detector design while fixing configuration-isolated aggregation, split-half multi-head RoPE, base-pair split leakage, unsafe non-first frozen-subspace composite stages, and zero-baseline degradation auditing.

The built-in synthetic suite requires only Python and NumPy. CPU torch tensors are accepted as `StatePair` inputs when torch is present, but torch is not required for smoke tests.

```bash
PYTHONPATH=src python3 -m kvreuse_b5.cli.smoke \
  --config configs/synthetic_smoke.yaml \
  --output outputs/synthetic_smoke_v201

python3 -m pytest
python3 -m compileall -q src tests
```

Configuration files in `configs/` use JSON syntax, which is valid YAML and can be loaded without PyYAML. Conventional YAML is supported when the optional PyYAML dependency is installed.

See `docs/algorithm_specs.md`, `docs/server_adapter_contract.md`, and `docs/experiment_output_schema.md` for the research and integration contracts.

## Heterogeneous-state research extension

`state_research/` is a separate CPU-only extension; it does not change the frozen
2.0.1 evaluator or paper recipe mathematics. It prepares LifeMem inputs, checks
state lifecycles, compares fixed-trace cache baselines, and evaluates externally
evidenced recovery plans and supplied states. These are research tools, **not** a
validated dynamic resource optimizer, online joint-reuse executor, or repair implementation.
DeepSeek-V4.1 runtime capture and real functional/performance acceptance remain pending.
The v1.0.1 audit includes one isolated core metadata fix: sorting training identities
before the existing artifact hash, without changing fitted bases or method configuration hashes.

```bash
python3 scripts/state_research_offline_smoke.py --output outputs/state_research_acceptance
python3 scripts/run_state_research_offline.py \
  --config outputs/state_research_acceptance/inputs/config.json \
  --output outputs/state_research_rerun
```

See [offline scope and acceptance](docs/STATE_RESEARCH_OFFLINE_PHASES_2_3.md) and
[correctness audit](docs/STATE_RESEARCH_AUDIT_V1_0_1.md) before interpreting results.
