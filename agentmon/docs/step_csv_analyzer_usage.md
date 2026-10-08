# AgentMon Step CSV Analyzer

`scripts/agentmon_analyze_steps.py` reads the CSV produced by
`scripts/agentmon_step_sequence.py` and produces an automatic runtime-load
diagnosis.

It does not require pandas or access to the original run directories. It only
needs the step CSV.

The script path is a compatibility entrypoint. The implementation lives in
`src/agentmon/analysis/step_csv.py`, so existing commands do not change.

## Basic Usage

```bash
python3 scripts/agentmon_analyze_steps.py \
  /path/to/all-tasks-step-sequence.csv
```

Write machine-readable and derived CSV outputs:

```bash
python3 scripts/agentmon_analyze_steps.py \
  /path/to/all-tasks-step-sequence.csv \
  --out-dir /path/to/analysis-output \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --strict
```

JSON output:

```bash
python3 scripts/agentmon_analyze_steps.py \
  /path/to/all-tasks-step-sequence.csv \
  --format json
```

Run-level CSV summary:

```bash
python3 scripts/agentmon_analyze_steps.py \
  /path/to/all-tasks-step-sequence.csv \
  --format csv > run-summary.csv
```

## Output Files

When `--out-dir` is used, the script writes:

- `analysis.json`: complete analysis payload.
- `run_summary.csv`: one row per run with bottleneck labels.
- `model_hotspots.csv`: slowest model requests.
- `tool_hotspots.csv`: slowest tool calls.
- `metric_definition.json`: field formulas, units, and aggregation scope.
- `data_quality.json`: coverage and matching evidence.
- `confidence.json`: attribution and lifecycle confidence.
- `limitations.json`: explicit interpretation limits.
- `validation_checks.json`: blocking checks and PUBLISHABLE/UNPUBLISHABLE.

## What It Diagnoses

Run-level load (explicit aggregation names are used in new output):

- `run_wall_s`, `run_model_e2e_sum_s`, `run_tool_duration_sum_s`;
- per-call model/tool p50, p95, and max;
- scheduler queue, TTFT, engine TTFT, exact prefill,
  prefill-to-first-token proxy, decode, and E2E;
- shared-service scheduler running/waiting/scheduled-token windows;
- prompt/generation/cached tokens;
- cache reuse and recomputed prompt rates;
- Agent-side CPU time and wall ratio;
- tool CPU time and wall ratio;
- target NPU HBM and AI Core utilization when available;
- KV usage and eviction signals.
- explicit tool timeout count/rate grouped by executable and timeout limit.

Bottleneck labels:

- `model_wait_bound`: most wall time is model waiting.
- `tool_wait_bound`: tool execution dominates.
- `vllm_queue_pressure`: vLLM queue time is high.
- `ttft_heavy`: API receive to EngineCore first-token time is high.
- `prefill_heavy`: a real prefill-completed boundary shows high prefill time.
- `shared_scheduler_waiting_pressure`: waiting requests were observed in the
  shared service window.
- `decode_heavy`: generation/decode dominates model time.
- `agent_cpu_bound`: Agent-side CPU ratio is high.
- `tool_cpu_bound`: tool CPU ratio is high.
- `kv_eviction_pressure`: KV block eviction was observed.
- `low_cache_reuse`: prompt cache reuse is low.
- `high_prompt_recompute`: prompt recomputation is high.
- `model_wait_with_low_aicore`: model wait is high but AI Core utilization is low.

Data quality:

- alignment quality distribution;
- alignment status distribution;
- P2 correlation quality distribution;
- `model_extra` requests;
- per-column coverage for P2, KV, NPU and tool CPU fields. Each field reports
  the eligible denominator (`model_request`, `agent_step`, or `tool_call`).

## Interpretation Notes

`N/A` and empty values are treated as missing data, not zero.

The analyzer applies a minimum coverage gate before emitting strong bottleneck
labels. If fewer than 30% of the relevant rows contain a field, the script keeps
the metric as diagnostic data and adds an `insufficient ... coverage` warning
instead of labeling the run as queue-bound, cache-bound, CPU-bound, or
NPU-bound.

P2 latency also has a clock-quality gate. Exact modern events carry source PID,
host, and `CLOCK_MONOTONIC` domain. `legacy_unverified`,
`clock_domain_mismatch`, and API `first_output` timestamps are not accepted as
an exact EngineCore decode boundary.

`get_computed_blocks` occurs before model prefill. It supplies cache-accounting
tokens through `vllm.request.prompt_cache_accounted`; it is not a prefill end
timestamp. Consequently, `p2_prefill_s` remains null without a true
`prefill_completed` event. `p2_prefill_to_first_token_s` is reported as a
proxy and is never relabeled as exact prefill.

Strict mode exits 1 when request matching, P2 lifecycle completeness, clock
integrity, or input integrity fails the configured threshold. It still writes
all outputs so the failure can be audited.

`model_extra` requests are counted in model totals, but are not assigned to a
nearby trajectory step.

`service_stream` attribution is treated as lower confidence than
`service_exclusive` or direct response-id alignment.

Prefix-cache disabled or missing hit-rate values are not averaged as `0%`.

NPU/HBM and service KV snapshots are shared-service observations under a
concurrent vLLM endpoint. They describe service state during the step window;
they are not request-owned resource attribution.
