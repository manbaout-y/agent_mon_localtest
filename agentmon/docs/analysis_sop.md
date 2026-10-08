# AgentMon Analysis SOP

This SOP turns raw AgentMon experiment outputs into deterministic analysis bundles and a compact `model_input_pack`.

Install the analysis capability set in the selected Python environment with `python -m pip install -e '.[analysis]'`, or provide equivalent packages from the server's approved wheelhouse. AgentMon never installs dependencies during a run.

## Run After An Experiment

```bash
agentmon analysis run-all \
  --input /path/to/experiment-or-export-root \
  --output /path/to/analysis_bundle \
  --profile auto \
  --experiment-contract /path/to/experiment-contract.json \
  --preflight
```

Use `--output-format csv` when `pyarrow` is unavailable. With preflight or strict mode, a missing Parquet engine is a required failure for Parquet output and stops before event scanning.

`--strict` automatically runs preflight and returns a stable nonzero exit code for dependency, baseline, resume, validation, or required-artifact failures.

The experiment contract is optional for basic parsing, but recommended for
standard load analysis. When omitted, AgentMon writes a derived
`00_inventory/experiment_contract.json` with `contract_status=PARTIAL` and keeps
unknown design fields as `UNKNOWN` instead of inventing them. Generate a starter
contract, fill in the experiment-specific fields, then validate it with:

```bash
agentmon analysis build-contract-template --output /path/to/experiment-contract.json
agentmon analysis validate-contract --contract /path/to/experiment-contract.json
```

## Fast Smoke For Large Exports

For a large server-side export, first run a fast smoke pass:

```bash
agentmon analysis run-all \
  --input /path/to/experiment-or-export-root \
  --output /path/to/fast_smoke_bundle \
  --output-format csv \
  --jobs 8 \
  --fast-smoke \
  --no-figures
```

Fast smoke keeps deterministic normalized request/run/P2/resource summaries but
skips heavyweight artifacts that are not required to prove the input is
parseable:

- `event_inventory` is omitted from `01_normalized/`;
- `04_evidence/` contains `skip_reason.json` instead of the full evidence pack;
- `04_evidence/optimizer_experiment/` contains `skip_reason.json` instead of the
  optimizer experiment harness side bundle when evidence output is skipped;
- `05_handoff/` contains `skip_reason.json` instead of handoff registers;
- `model_input_pack/` contains `skip_reason.json` instead of report inputs.

After the smoke pass reports expected run and request counts, rerun without
`--fast-smoke` for the final full evidence/model bundle.

`event_inventory` is also omitted by default in a full run because it duplicates raw events and is not needed for deterministic metrics. Use `--include-event-inventory` only when a per-event materialized table is explicitly required.

## Preflight, Resume, And Stage Control

```bash
agentmon analysis preflight --input /path/to/root --output /path/to/bundle --output-format csv
agentmon analysis run-all --input /path/to/root --output /path/to/bundle --resume --strict
```

Long runs update `analysis_status.json` and per-stage checkpoint files. `--dry-run` writes the execution plan without reading input. `--from-stage`, `--to-stage`, `--force-stage`, and `--invalidate-stage` operate on names in `stage_dag.json`. Input, code, Profile, dependency, configuration, schema, and cached artifact compatibility are checked before reuse.

The stage list is defined in `agentmon.analysis.stages` as a catalog of
`AnalysisStageSpec` entries. `agentmon.analysis.execution` consumes that catalog
when writing `stage_dag.json` and deciding whether a stage is cacheable. New
analysis stages should be added to the catalog first, then wired into the
pipeline implementation, instead of hard-coding names in unrelated modules.

Use `--timeout-seconds N` for a cooperative wall-clock limit. On timeout, the active stage is failed audibly and completed P2 source checkpoints remain reusable. `analysis_status.json` includes processing rates, ETA when derivable, RSS, and temporary-filesystem free space.

The shared P2 stream is indexed once. Identical inputs reuse the SQLite cache; verified append-only updates to uncompressed JSONL read only appended bytes. Changed compressed streams are rebuilt conservatively.

Advanced output controls:

```bash
agentmon analysis run-all \
  --input /path/to/root \
  --output /path/to/bundle \
  --omit-normalized-table event_inventory \
  --skip-evidence \
  --skip-handoff \
  --skip-model-pack
```

## Input Directory

The input root may contain one experiment, many batch directories, repeated trials, nested run directories, shared P2 streams, exported reports, or partial data. Discovery is recursive and does not rely on fixed task counts, fixed concurrency values, model names, vLLM versions, device IDs, or task ID formats.

Recognized inputs include:

- run directories with `manifest.json` and `events.jsonl`;
- batch directories with `batch-manifest.json`, `batch-summary.json`, or `batch-results.tsv`;
- batch runtime artifacts such as `batch-phase-events.jsonl`, `p2-index-metrics.json`, `npu-process-mapping.json`, and `npu-mapping-evidence.csv`;
- `report.json`, `summary.json`, trajectory artifacts, step CSV files;
- service or P2 JSONL streams, including `.jsonl.gz`.

## Incomplete Experiments

Single-run failures do not stop analysis. Missing or invalid data is recorded under `00_inventory/` and restricts `analysis_ready.json`. The bundle may be `PARTIAL` while still containing publishable observed facts.

## Data Foundation And Basic Analysis

The standard pipeline now materializes a fixed data foundation before report
generation:

- `00_inventory/experiment_contract.json`: explicit or derived experiment
  contract with field-level validation and limitations;
- `00_inventory/research_questions.*`: normalized research-question rows from
  the contract;
- `00_inventory/expected_artifacts.json`: expected source roles and observed
  file-role counts;
- `02_semantics/metric_dictionary.*`: schema-profile metric registry as both
  table and Markdown;
- `02_semantics/entity_set_definitions.*`: canonical entity sets such as
  attempted requests, P2-eligible requests, scheduler iterations, resource
  samples, and task results;
- `02_semantics/clock_domain_audit.*`: metric clock-domain inventory;
- `02_validation/data_quality_gate.json`: module status counts, blocking
  modules, unresolved inputs, follow-up requirements, and forbidden
  conclusions;
- `02_validation/conservation_checks.*`: time, lifecycle, Little Law, pipeline,
  and request-candidate accounting checks in one table;
- `05_handoff/*.parquet|csv.gz`: basic observation, anomaly, contradiction,
  unresolved-question, and backlog registers.

`05_handoff` is a lightweight handoff package, not an adaptive investigation
engine. It organizes already-computed basic evidence and guardrails. It does not
generate Planner/Executor/Reviewer tasks, choose custom diagnostics, or promote
mechanism candidates to conclusions.

## Repeated Experiments

Trial identity is discovered from manifests or path names such as `trial-*` and `repeat-*`. Repeated trial outputs are represented separately in `trials`, `batches`, and trial-scoped analysis tables.

## Compare Experiments

```bash
agentmon analysis compare \
  --bundle /path/to/bundle-a \
  --bundle /path/to/bundle-b \
  --output /path/to/comparison
```

The comparison checks task set hash, model, serving framework, AgentMon version, hardware/device visibility, and schema compatibility before emitting deltas.

## Fine-Grained Runtime Evidence

A full SOP run now preserves the fine-grained evidence that is available in the
input instead of reducing every module to run-level aggregates. The principal
tables under `01_normalized/` are:

- `requests`: Agent/Task/Step identity, request purpose, cached/uncached and
  recomputed tokens, Prefill/TTFT/TPOT/Decode/Engine Residence, compression and
  summary-model usage, scheduler totals, and per-request KV totals;
- `request_lifecycle`: request-stage durations and lifecycle residuals;
- `request_batch_membership`: one row per request per scheduler iteration,
  including phase, batch position, scheduled tokens, Batch enter/exit and
  execution-observed residence;
- `scheduler_iterations`: running/waiting requests, per-iteration Prefill and
  Decode requests/tokens, schedule time, and execution completion time;
- `kv_block_events`: request-attributed allocate/cache/hit/free/evict/reset
  observations and KV usage before/after;
- `tool_calls`: content-free result hashes, return/timeout state, parsed test
  counts, and explicit `git diff` summaries;
- `error_events`: normalized tool, model, adapter, and collector error evidence.

Every field remains nullable. Deep P2 absence, an unsupported vLLM target,
missing API usage, failed identity correlation, or privacy redaction must result
in `UNKNOWN`, `PARTIAL`, or an empty field. The SOP must not substitute total
Prompt Tokens for actual Prefill Tokens, API first output for EngineCore first
token, or request membership for resource exclusivity.

## Optimizer Experiment Harness

For offline optimizer research on an existing batch, run the M1/M2 harness
after the batch artifacts are present:

```bash
PYTHONPATH=src python -m agentmon optimizer experiment \
  --batch-root /path/to/batch \
  --strategy static_threshold \
  --strategy workload_aware \
  --schema-profile /path/to/schema-profile.json \
  --output-dir /path/to/optimizer-experiment
```

`--schema-profile` is optional. It can override step CSV field mappings and P2
service event source/type/payload fields without changing optimizer strategies or
generic replay logic. Keep framework-specific event names in a schema profile or
adapter-owned mapping, not inside new strategy code.

Minimal shape:

```json
{
  "p2_service_event_profile": {
    "event_sources": ["custom_p2"],
    "scheduler_event_types": ["custom.scheduler.sample"],
    "kv_event_types": ["custom.kv.sample"],
    "running_request_fields": ["active"],
    "waiting_request_fields": ["queued"],
    "kv_usage_ratio_fields": ["cache_pressure"]
  },
  "step_csv_profile": {
    "request_ttft_fields": ["engine_ttft_s"]
  }
}
```

The harness writes an SOP-compatible evidence/replay side bundle:

- `experiment_manifest.json`
- `evidence_graph.json`
- `critical_path.csv`
- `bottleneck_attribution.json`
- `workload_normalized_metrics.csv`
- `data_quality.json`
- `optimizer_decision_timeline.csv`
- `optimizer_decisions.jsonl`
- `optimizer_strategy_summary.json`
- `optimizer_risk_audit.json`
- `prediction_variable_validation.json`
- `prediction_variable_validation.csv`
- `optimizer_replay_report.md`
- `mechanism_comparison.json`
- `ablation_table.csv`
- `contribution_decomposition.csv`
- `conflict_audit.json`
- `evaluation_report.md`

`analysis run-all` now also has an `optimizer_harness_build` packaging stage.
When batch artifacts are discovered and evidence output is enabled, it writes a
side bundle under:

```text
04_evidence/optimizer_experiment/
```

Each discovered batch gets its own subdirectory, and
`04_evidence/optimizer_experiment/optimizer_harness_manifest.json` summarizes
the generated harness outputs. Fast-smoke runs, disabled evidence output, or
inputs without batch artifacts write `skip_reason.json` instead.

The harness manifest labels the bundle as evidence/replay output, and the
Markdown reports carry data quality, evidence sources, limitations, and
forbidden conclusions. Advice replay and M3 advice-proxy evaluation do not
enforce runtime actions and must not be cited as causal optimizer benefit
without a controlled baseline, ablation, and end-to-end evaluation.

The standard scheduler analysis consumes Deep P2 rows when present and emits
Prefill/Decode token totals plus observed KV lifecycle totals. The request
lifecycle analysis emits separate `all`, `agent_primary`, and
`context_summary` buckets so model-summary overhead remains visible.

### Planned Deeper Optimization

The current pass establishes a field-preserving normalized contract for the
listed metrics and makes the
detail tables available to report generation. The next analysis iteration should
build on that contract in this order:

1. Add a per-run metric availability matrix with observed-row coverage, clock
   quality, identity quality, and explicit missing reason for every required
   metric.
2. Add Prefix Cache effectiveness decomposition using cached, uncached,
   scheduled Prefill, and recomputed tokens; publish conclusions only when the
   relevant quality gates pass.
3. Add request-level queue, Batch, KV, Prefill, Decode, summary, tool, and idle
   critical-path attribution with conservation checks against client and task
   wall time.
4. Add privacy-controlled repository change and test-case artifacts. Hashes and
   counts remain the default; source snippets, full diffs, and test output must
   require explicit retention policy.
5. Add multi-Agent identity coverage and cross-Agent contention analysis once
   upstream adapters provide stable `agent_id` values.

These are analysis-layer extensions. They must consume canonical fields and
Schema Profiles rather than introducing model names, task IDs, concurrency
levels, event names, or empirical thresholds into generic analyzers.

## Model Report Workflow

```bash
agentmon analysis build-model-pack \
  --bundle /path/to/analysis_bundle \
  --output /path/to/model_input_pack
```

Give only `model_input_pack/` to the local model. In addition to aggregate
analysis outputs, the pack includes normalized request, lifecycle, Batch, KV,
tool, error, and task-result detail tables when they are present. The model must
not recalculate raw metrics from JSONL. It should cite `evidence_index`, use the
detail tables to explain aggregates, and preserve conclusion classes.

## Verify A Model Report

```bash
agentmon analysis verify-report \
  --model-pack /path/to/model_input_pack \
  --report /path/to/report.md
```

The validator checks forbidden conclusions, unsupported causal language, scheduled/generated token confusion, NPU mapping overclaiming, and numbers not found in the model pack.

## When Not To Write Strong Conclusions

Do not write strong mechanism conclusions when:

- `analysis_ready.module_status.p1_capture != PASS`;
- `analysis_ready.module_status.p2_audit != PASS`;
- `analysis_ready.module_status.token_timeseries != PASS` and the conclusion needs steady-state generated-token throughput;
- `npu_mapping != PASS`;
- lifecycle conservation is not `PASS`;
- pipeline reconciliation is not `PASS`;
- Little Law validation is not `PASS`;
- the comparison reports non-comparable dimensions.

## New Runtime-Aware Outputs

The batch runtime emits deterministic phase events and index/mapping evidence when those inputs are available:

- `batch-phase-events.jsonl`: start/finish records for pipeline, preprocessing, workload, P2 index, analysis, and finalization.
- `batch-summary.json.phase_timings` and `pipeline_phase_validation`: component-sum reconciliation against full pipeline wall time.
- `p2-index-metrics.json`: one shared P2 parse/index pass, cache hit status, source bytes, invalid lines, and per-run query metrics.
- `npu-process-mapping.json` plus `npu-mapping-evidence.csv`: LEVEL_1/2/3/NONE evidence for PID/rank/device mapping.

Analysis consumes these artifacts into normalized `batches`, `pipeline_summary`, `p1_capture_*`, `token_timeseries`, `p2_match_audit`, and `npu_mapping_evidence`.
