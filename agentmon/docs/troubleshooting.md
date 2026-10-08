# Analysis Troubleshooting

## Bundle Is PARTIAL

Open `analysis_ready.json` and inspect:

- `module_status`
- `forbidden_conclusions`
- `unresolved_inputs`
- `required_followup_experiments`

PARTIAL can still contain publishable observed facts.

## Parquet Missing

Install the analysis extra (`python -m pip install -e '.[analysis]'`) from an approved package source, or run with:

```bash
agentmon analysis run-all --output-format csv ...
```

CSV output is compressed as `.csv.gz`.

For `analysis run-all --strict`, preflight is automatic. A REQUIRED dependency failure returns before input event sampling and writes `analysis_preflight.json` plus a failed `run_summary.json`.

## Analysis Is Slow Or Appears Single-Core

Shared P2 indexing is intentionally one sequential stream so a large compressed source is opened and decompressed once. Run-local normalization uses `--jobs`; the effective worker count is capped by CPU count, task count, process-launch safety and `--memory-limit-bytes`.

Run normalization uses the multiprocessing `spawn` context so the status heartbeat thread is never inherited through `fork`. Requested/effective jobs and any serial fallback reason are written to `01_normalized/normalization_execution.json` and printed in the final status lines.

Check these files instead of relying on process-list CPU alone:

- `analysis_status.json` for heartbeat and current run;
- `stage_runtime_summary.csv` for wall/CPU/RSS/disk growth;
- `00_inventory/p2_index_metrics.json` for full versus incremental scans;
- `01_normalized/p2_index_query_audit.csv` or the configured table format for per-run indexed queries.

The optional `event_inventory` table is disabled by default. Enabling it can greatly increase output size and should not be part of the first server smoke run.

The status file reports P2 batch progress, byte/event/run rates, derivable ETA, RSS and temporary-filesystem space. `--timeout-seconds` is cooperative and preserves completed source/stage checkpoints.

## Resume Was Rejected

Read `resume_status.json` and `resume_rejected_attempt.json`. Input boundary fingerprint, Profile, semantic configuration, analysis format, AgentMon source, dependency versions and cached artifact metadata must be compatible. The prior compatible `analysis_manifest.json` is intentionally preserved when a new resume attempt is rejected.

## P2 Audit Fails

Check:

- `03_analysis/p2_match_audit.csv.gz`
- `03_analysis/publishability_gap_mapping.csv.gz`
- `03_analysis/publishability_gate_audit.json`

P2 eligibility is evidence-based. Error requests enter the denominator when they have request identity or service-side evidence; failed requests without P2 identity remain outside the denominator.

## P1 Capture Is PARTIAL Or FAIL

Check:

- `03_analysis/p1_capture_summary.csv.gz`
- `03_analysis/p1_capture_audit.csv.gz`
- `01_normalized/trajectories.csv.gz`
- `01_normalized/requests.csv.gz`

Most failures are trajectory response IDs that do not appear in P1 request aliases, or missing trajectory model records.

## Token Timeseries Is UNRESOLVED

Scheduler rows exist, but `actual_generated_tokens_delta` is missing. The analyzer will not use `scheduled_token_count` as generated-token evidence. Add actual generated-token deltas in P2/runtime telemetry before reporting steady-state generated-token throughput.

## NPU Mapping Is UNRESOLVED

Physical mapping requires LEVEL_1 or LEVEL_2 evidence. Utilization/HBM pattern matching alone is not enough.

## Pipeline Reconciliation Is PARTIAL

Prefer `batch-phase-events.jsonl` and `batch-summary.json.pipeline_phase_validation`. If those are missing, the analyzer falls back to worker intervals, which may be partial. Add runner phase events or batch result timestamps.

## Report Validation Fails

Inspect `forbidden_claims.csv.gz` first, then `unsupported_claims.csv.gz` and `numeric_mismatches.csv.gz`.
