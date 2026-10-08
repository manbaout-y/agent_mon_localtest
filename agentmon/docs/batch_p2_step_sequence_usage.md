# AgentMon Batch P2 Import and Step Analysis Usage

This guide is for running batch mini-SWE tasks on the Ascend server, importing
vLLM P2 service events into each AgentMon run, and producing per-step runtime
analysis.

## 1. Environment

Set these paths first:

```bash
export AGENTMON_REPO=/absolute/path/to/agentmon
export AGENTMON_HOME=/data/node0_disk3/chenyang/agentmon
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python

export MINI_SWE_ROOT=/data/node0_disk3/chenyang/agent-runtime-poc/mini-swe
export MINI_SWE_BIN=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/mini
export TARGET_REPO=/path/to/target/repo

export AGENTMON_CONFIG="$AGENTMON_REPO/examples/config.target.toml"
export PYTHONPATH="$AGENTMON_REPO/src"
```

Check AgentMon:

```bash
"$AGENTMON_PY" -m agentmon --version
"$AGENTMON_PY" -m agentmon import-p2 --help
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" --help
```

## 2. P2 Service Events Path

The vLLM service must already be launched with P2 enabled and writing to one
service-scoped JSONL file:

```bash
export P2_EVENTS=/path/to/vllm-p2/events.jsonl
```

For one serial task on an otherwise idle service, byte offsets can capture a
service-exclusive slice. For concurrent workers, never use per-task byte
offsets: events from all workers interleave. Use the concurrent runner in
section 9; it correlates request events by AgentMon run ID or mini-SWE response
ID and keeps global KV/NPU observations in the service timeline.

## 3. Run One Serial, Service-Exclusive Task

Record the P2 start offset:

```bash
P2_START=$(stat -c %s "$P2_EVENTS" 2>/dev/null || echo 0)
```

Run mini-SWE under AgentMon. Keep the trajectory path unique and include
`{run_id}`:

```bash
"$AGENTMON_PY" -m agentmon run \
  --config "$AGENTMON_CONFIG" \
  --cwd "$TARGET_REPO" \
  --label calculator-batch \
  -- "$MINI_SWE_BIN" \
  -m hosted_vllm/qwen3-coder-30b \
  -c mini.yaml \
  -c "$MINI_SWE_ROOT/configs/local_qwen3_coder.yaml" \
  --agent-class agentmon.adapters.agent_frameworks.mini_swe.agent.InstrumentedInteractiveAgent \
  --model-class agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel \
  --environment-class agentmon.adapters.agent_frameworks.mini_swe.environment.InstrumentedMiniSweLocalEnvironment \
  -y \
  -l 0 \
  --exit-immediately \
  -t "TASK_TEXT_HERE" \
  -o "$MINI_SWE_ROOT/trajectories/task-{run_id}.traj.json" \
  2>&1 | tee /tmp/agentmon-run.log
```

Extract the run id:

```bash
RUN_ID=$(sed -n 's/.*agentmon run_id=\([^ ]*\).*/\1/p' /tmp/agentmon-run.log | tail -1)
echo "$RUN_ID"
```

Record the P2 end offset:

```bash
P2_END=$(stat -c %s "$P2_EVENTS")
```

## 4. Import a Serial, Service-Exclusive P2 Slice

Import only the P2 events produced during this task:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_CONFIG" \
  --run-id "$RUN_ID" \
  --start-offset "$P2_START" \
  --end-offset "$P2_END" \
  --quality service_exclusive \
  "$P2_EVENTS" \
  2>&1 | tee "/tmp/agentmon-import-p2-$RUN_ID.log"
```

Use `--quality service_exclusive` only when the vLLM endpoint had no other
clients during this task. If the service may contain other traffic, use:

```bash
--quality service_stream
```

After import, AgentMon regenerates:

```text
$AGENTMON_HOME/runs/$RUN_ID/report.json
$AGENTMON_HOME/runs/$RUN_ID/summary.json
$AGENTMON_HOME/runs/$RUN_ID/report.txt
$AGENTMON_HOME/runs/$RUN_ID/report.csv
$AGENTMON_HOME/runs/$RUN_ID/metric_definition.json
$AGENTMON_HOME/runs/$RUN_ID/data_quality.json
$AGENTMON_HOME/runs/$RUN_ID/confidence.json
$AGENTMON_HOME/runs/$RUN_ID/limitations.json
$AGENTMON_HOME/runs/$RUN_ID/validation_checks.json
```

Check P2 status:

```bash
"$AGENTMON_PY" -m agentmon summary \
  --config "$AGENTMON_CONFIG" \
  "$RUN_ID" \
  --format json \
  | "$AGENTMON_PY" -m json.tool \
  | grep -E '"p2_status"|"p2_request_count"|"p2_direct_correlation_count"'
```

Expected after a good isolated run:

```text
"p2_status": "complete"
"p2_request_count": <non-zero>
```

## 5. Import mini-SWE Trajectory

If the trajectory was not automatically imported, import it after mini-SWE
finishes:

```bash
TRAJ=$(ls -t "$MINI_SWE_ROOT"/trajectories/*"$RUN_ID"*.traj.json | head -1)

"$AGENTMON_PY" -m agentmon import-trajectory \
  --config "$AGENTMON_CONFIG" \
  --run-id "$RUN_ID" \
  "$TRAJ"
```

This also regenerates reports.

## 6. Per-Step Runtime Analysis

Generate a compact table:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$AGENTMON_HOME/runs/$RUN_ID" \
  --ascend-devices 0,1,2,3 \
  --format table \
  | tee "/tmp/agentmon-step-$RUN_ID.txt"
```

Generate machine-readable JSON:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$AGENTMON_HOME/runs/$RUN_ID" \
  --ascend-devices 0,1,2,3 \
  --format json \
  > "/tmp/agentmon-step-$RUN_ID.json"
```

Generate CSV:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$AGENTMON_HOME/runs/$RUN_ID" \
  --ascend-devices 0,1,2,3 \
  --format csv \
  > "/tmp/agentmon-step-$RUN_ID.csv"
```

The step analysis includes:

- step/model/tool duration
- model P2 E2E, scheduler queue, TTFT, engine TTFT, decode, and first-output time
- prompt, generation, cached, uncached tokens, plus recomputed tokens only when scheduler evidence exists
- KV usage ratio and block alloc/free/evict counts
- prefix-cache status and hit rate semantics
- agent-side CPU time and CPU/wall ratio
- tool subprocess CPU (native rusage plus sampled CPU), PSS, RSS, read/write bytes
- vLLM process CPU/PSS during model windows
- target and host NPU HBM / AI Core utilization windows
- canonical model request identity fields and `p2_identity_audit` for
  matched, unmatched P1, and orphan P2 request diagnostics

Do not pass the full service-level `$P2_EVENTS` file to `--p2-events` during a
multi-task batch. Use `agentmon import-p2` first, then analyze run-local request
events. To overlay the service-level KV/capability timeline without claiming
request ownership, use:

```bash
--service-p2-events "$P2_EVENTS"
```

Those values are labeled `shared_service_observation` and
`not_request_attributed`.

## 7. Minimal Batch Loop Pattern

```bash
for TASK_FILE in /path/to/tasks/*.txt; do
  TASK_TEXT=$(cat "$TASK_FILE")
  P2_START=$(stat -c %s "$P2_EVENTS" 2>/dev/null || echo 0)

  LOG="/tmp/agentmon-run-$(basename "$TASK_FILE").log"
  "$AGENTMON_PY" -m agentmon run \
    --config "$AGENTMON_CONFIG" \
    --cwd "$TARGET_REPO" \
    --label "batch-$(basename "$TASK_FILE")" \
    -- "$MINI_SWE_BIN" \
    -m hosted_vllm/qwen3-coder-30b \
    -c mini.yaml \
    -c "$MINI_SWE_ROOT/configs/local_qwen3_coder.yaml" \
    --agent-class agentmon.adapters.agent_frameworks.mini_swe.agent.InstrumentedInteractiveAgent \
    --model-class agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel \
    --environment-class agentmon.adapters.agent_frameworks.mini_swe.environment.InstrumentedMiniSweLocalEnvironment \
    -y \
    -l 0 \
    --exit-immediately \
    -t "$TASK_TEXT" \
    -o "$MINI_SWE_ROOT/trajectories/batch-{run_id}.traj.json" \
    2>&1 | tee "$LOG"

  RUN_ID=$(sed -n 's/.*agentmon run_id=\([^ ]*\).*/\1/p' "$LOG" | tail -1)
  P2_END=$(stat -c %s "$P2_EVENTS")

  "$AGENTMON_PY" -m agentmon import-p2 \
    --config "$AGENTMON_CONFIG" \
    --run-id "$RUN_ID" \
    --start-offset "$P2_START" \
    --end-offset "$P2_END" \
    --quality service_exclusive \
    "$P2_EVENTS"

  TRAJ=$(ls -t "$MINI_SWE_ROOT"/trajectories/*"$RUN_ID"*.traj.json 2>/dev/null | head -1 || true)
  if [ -n "$TRAJ" ]; then
    "$AGENTMON_PY" -m agentmon import-trajectory \
      --config "$AGENTMON_CONFIG" \
      --run-id "$RUN_ID" \
      "$TRAJ"
  fi

  "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
    "$AGENTMON_HOME/runs/$RUN_ID" \
    --ascend-devices 0,1,2,3 \
    --format json \
    > "$AGENTMON_HOME/runs/$RUN_ID/step_sequence.json"
done
```

## 8. Common Problems

### `p2_status = missing`

Usually this means the vLLM P2 service events were not imported into the run.
Run `agentmon import-p2` with the correct offset range and then regenerate or
read the report again.

### `p2_request_count = 0`

Check:

```bash
sed -n "${P2_START},${P2_END}p" "$P2_EVENTS"
```

That command is line-based, not byte-offset based, so it is only for a quick
manual look. The real import command uses byte offsets.

Also check that the vLLM service was launched with:

```text
AGENTMON_VLLM_P2_ENABLE=1
AGENTMON_EVENTS_PATH=<the same P2_EVENTS path>
PYTHONPATH=$AGENTMON_REPO/src:...
```

### Step analysis has no P2 fields

Confirm run-local `events.jsonl` contains P2 events:

```bash
grep -c '"source":"vllm_p2"' "$AGENTMON_HOME/runs/$RUN_ID/events.jsonl"
```

If the count is zero, import P2 first.

### NPU target metrics are N/A

Pass the physical devices used by the validation vLLM service:

```bash
--ascend-devices 0,1,2,3
```

Without target devices, the script avoids pretending host-wide NPU values belong
to this task.

### Prefix cache disabled

This is expected for the current service if prefix cache is off. Reports should
show:

```text
prefix_cache_status = disabled
prefix_cache_hit_rate = N/A / null
```

It should not be interpreted as `0%`.

## 9. Concurrent Batch Runner

For shared-service concurrent batches, prefer the reusable runner:

```bash
export AGENTMON_REPO=/absolute/path/to/agentmon
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python
export BATCH_ROOT=/data/node0_disk3/chenyang/agentmon/batches/$(date +%Y%m%d-%H%M%S)-dist50-c5
export TASKS=/path/to/tasks.tsv
export P2_EVENTS=/path/to/service-p2/events.jsonl

"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" \
  --batch-root "$BATCH_ROOT" \
  --task-file "$TASKS" \
  --task-id-column instance_id \
  --expected-task-count 50 \
  --concurrency 5 \
  --analysis-workers 8 \
  --p1-mode required \
  --agentmon-python "$AGENTMON_PY" \
  --agentmon-config "$AGENTMON_REPO/examples/config.target.toml" \
  --output-root /data/node0_disk3/chenyang/agentmon/runs \
  --env-file /data/node0_disk3/chenyang/new_version/agentmon-latest-20260708.env \
  --p2-events "$P2_EVENTS" \
  --p2-filter-mode auto \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --ascend-devices 0,1,2,3 \
  --task-package /path/to/task-package-manifest-or-archive \
  --model-path /path/to/model \
  --vllm-launch-command-file /path/to/vllm-command.txt \
  --temperature 0 \
  --seed 1 \
  --metadata benchmark=dist50 \
  --defer-analysis \
  --command-template 'mini-swe-agent run --model qwen3-coder-30b --instance {instance_id} -o {workspace}/mini.traj.json'
```

Use `--concurrency 10` for ten workers. No code change is needed. Increase
concurrency only after checking vLLM queue latency, KV usage/evictions, HBM, and
task failure rate; all workers still share the same service and devices.

For Deep P2 runs, `--defer-analysis` is recommended for the workload phase. It
writes workload results and marks `analysis_deferred=true`, but skips P2 index,
report generation, and step sequence until a later `--analyze-existing` pass.
As an explicit safety guard, the runner can also defer automatically when the
service P2 file crosses a configured size:

```bash
--defer-analysis-if-p2-over-bytes 5000000000
```

Run the offline analysis later with the same `BATCH_ROOT`, task file, P2 source,
and analysis arguments:

```bash
export P2_INDEX_CACHE_ROOT=/data/node0_disk3/chenyang/agentmon/p2-index-cache
mkdir -p "$P2_INDEX_CACHE_ROOT"

"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" \
  --batch-root "$BATCH_ROOT" \
  --task-file "$TASKS" \
  --task-id-column instance_id \
  --analyze-existing \
  --analysis-preset standard \
  --analysis-workers 1 \
  --p1-mode required \
  --agentmon-python "$AGENTMON_PY" \
  --output-root /data/node0_disk3/chenyang/agentmon/runs \
  --env-file /data/node0_disk3/chenyang/new_version/agentmon-latest-20260708.env \
  --p2-events "$P2_EVENTS" \
  --p2-index-cache-root "$P2_INDEX_CACHE_ROOT" \
  --p2-filter-mode auto \
  --ascend-devices 0,1,2,3
```

Use one or two analysis workers first for GB-scale P2 JSONL. More workers can
increase disk contention and do not make request attribution more accurate.

`--analyze-existing` preserves the original execution timestamps, phase events,
pipeline profile, and execution time windows used for throughput. Each analysis
invocation writes its own phase events and profile under
`analysis-resume/<invocation-id>/`; the latest invocation is referenced by
`analysis_resume` in the batch summary and manifest. The original `pipeline_wall_s`
continues to describe the first invocation, including an execution-only run;
analysis time is reported separately. Historical batches without recorded timing
keep unavailable throughput values instead of using the time spent loading results.

Analysis presets:

```text
full      legacy-compatible; writes full p2-service-global.events.jsonl, all-steps.csv, and timeline
standard  recommended offline default; reuses indexed per-window globals and avoids full global fallback when no task window exists
quick     fast triage; skips all-steps.csv and timeline, so it is not a final publication bundle
```

`--p2-index-cache-root` is useful when many batch roots refer to the same P2
source or when analysis is rerun. Cache identity is based on the P2 source
fingerprint, so different service JSONL files still build separate indexes.

The command template is executed under:

```text
agentmon run -- bash -lc '<rendered command>'
```

Each task gets isolated paths:

```text
<batch-root>/workspaces/<task_id>
<batch-root>/homes/<task_id>
<batch-root>/xdg_config/<task_id>
```

The isolated `HOME` and `XDG_CONFIG_HOME` are important. mini-SWE normally
writes `last_mini_run.traj.json` under the user's config directory; sharing
that directory across five workers can overwrite trajectories.

### Workspace and runtime-source safety gate

The batch runner validates every task path before starting the first coding
agent. Formal runs should keep these defaults:

```text
--workspace-policy isolated
--runtime-source-isolation snapshot
--runtime-source-integrity strict
```

After resolving symlinks, the gate requires absolute paths, a workspace below
`<batch-root>/workspaces`, a `cwd` inside that workspace, a unique workspace
per task, and no overlap in either direction with an AgentMon source root.
Resolved contracts are written before execution:

```text
<batch-root>/workspace-contracts.json
<batch-root>/workspace-contracts/<task-id>.json
```

If validation fails, no workload starts and the CLI returns nonzero. Common
messages and actions are:

```text
workspace escapes the isolated workspace root
  Correct --workspace-template. For an intentionally pre-materialized repo,
  select --workspace-policy external explicitly.

workspace/cwd overlaps protected AgentMon source
  Materialize the benchmark into a separate task directory. Do not bypass the gate.

tasks ... resolve to the same workspace
  Add {task_id} or another unique task field to --workspace-template.
```

`external` relaxes only the required location. It still requires `cwd` inside
the workspace and still rejects AgentMon source overlap:

```bash
--workspace-policy external \
--workspace-template '{materialized_repo}' \
--cwd-template '{workspace}'
```

During execution, every `python -m agentmon` worker imports from a batch-local,
read-only copy under `runtime-source-snapshots/`. Source-bearing `PYTHONPATH`,
`AGENTMON_HOME`, `AGENTMON_REPO`, and `AGENTMON_SOURCE_ROOT` entries are removed
from the worker environment when they expose the original source. The runner
first imports the full `agentmon.cli` from that snapshot, so an unsupported or
incomplete `--agentmon-python` fails before any workload starts. It then
compares both the original source and runtime snapshot with their before-workload
manifests.

```text
<batch-root>/runtime-source-isolation.json
<batch-root>/runtime-source-manifest-before.json
<batch-root>/runtime-source-manifest-after.json
<batch-root>/runtime-snapshot-manifest-after.json
<batch-root>/runtime-source-integrity.json
```

If either tree changes, strict mode stops before analysis, returns nonzero,
sets `validity_status=INVALID`, and records exact added, modified, and removed
paths. Preserve this evidence, restore a clean source package, and rerun into a
new batch root. `warn`, integrity `off`, and isolation `off` are diagnostic
escape hatches only; they must not be used for formal A/B runs.

After each task, the runner:

1. imports the task trajectory into the run;
2. extracts `assistant.extra.response.id` values from the trajectory;
3. imports the shared P2 service stream by `payload.agentmon_run_id` when
   available, otherwise falls back to `payload.api_request_id`;
4. imports request-keyed P2 events into the run while leaving global service
   observations in the service stream;
5. regenerates the report;
6. writes `step_sequence.json`, `step_sequence.csv`, and `step_sequence.txt`;
7. validates run/report/P2 quality and builds the batch timeline.

Batch outputs are:

```text
<batch-root>/batch-results.tsv
<batch-root>/batch-manifest.json
<batch-root>/batch-summary.json
<batch-root>/p2-service-global.events.jsonl      # full preset or explicit full global copy
<batch-root>/step-sequence/all-steps.csv         # skipped by quick preset
<batch-root>/timeline/timeline_report.md         # skipped by quick preset
<batch-root>/timeline/concurrency_integral_check.csv # skipped by quick preset
<batch-root>/timeline/service_resource_windows.csv    # skipped by quick preset
```

`batch-results.tsv` separates `execution_success`, `monitoring_success`,
`trajectory_import_success`, `p1_native_status`, `p2_import_success`,
`report_publishability_status`, `analysis_pipeline_success`, `usable_run`,
optional `judge_success`, and `overall_pipeline_success`. The runner exits
nonzero on overall failure. A nonzero tool return code does not by itself make
the coding-agent task fail.

Batch completion is deliberately not one boolean. Read
`batch-summary.json.completion` or the matching manifest fields:

```text
controller_finished       controller reached finalization
result_table_complete     result row count equals the task contract
workload_attempt_complete all expected rows reached a terminal workload state
analysis_complete         deferred analysis has or has not finished
experiment_valid          true / false / null (UNRESOLVED)
validity_status           VALID / INVALID / UNRESOLVED
```

`agentmon_run_id_missing > 0` always makes the experiment invalid, even when
the outer command returned zero or a watcher observed all expected TSV rows.
An invalid or unresolved batch cannot support concurrency, throughput,
latency, bottleneck, A/B, or optimizer-effect conclusions. Inspect, in order,
`workspace-contracts.json`, `runtime-source-integrity.json`,
`batch-summary.json.completion`, then the affected row's `stderr_log` and
`run_dir` in `batch-results.tsv`.

### Shared-Service P2 Attribution

When five tasks share one vLLM service, do not use byte offsets as the primary
P2 attribution mechanism. Offset slices can contain requests from other tasks.

The runner's default `--p2-filter-mode auto` first tries exact run-id filtering:

```text
P2 payload.agentmon_run_id == AgentMon run_id
```

This is the preferred mode with the current P2 hook because it can keep P2
requests that are not present in the mini-SWE trajectory.

If no run-id correlated events are found, the runner falls back to request-id
based filtering:

```text
mini trajectory assistant.extra.response.id
==
P2 payload.api_request_id
```

The same filtering is available directly through `agentmon import-p2`:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_CONFIG" \
  --run-id "$RUN_ID" \
  --quality direct \
  --filter-response-ids-from-trajectory "$AGENTMON_HOME/runs/$RUN_ID/raw/trajectory/mini.traj.json" \
  "$P2_EVENTS"
```

This imports matching request lifecycle events and related request-id block
events. It does not copy global KV snapshots or capability events into every
run. `raw/p2_import.json` records matched/unmatched trajectory IDs, request
counts, event counts, ambiguous API-to-engine mappings, and time-window extras
for diagnostics. A one-to-many API ID is marked `multiple`, excluded from the
exact match count, and retained as separate `model_extra` rows for audit.

This is safe for requests that appear in the trajectory. P2 requests that do
not appear in the trajectory cannot be proven to belong to the task under true
shared-service concurrency unless the vLLM hook captures an AgentMon run id or
model request id. The runner and the direct import command therefore exclude
such extras by default.

If the service is known to be exclusive for a short window, you may opt in to
heuristic window extras:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_CONFIG" \
  --run-id "$RUN_ID" \
  --quality heuristic \
  --filter-response-ids-from-trajectory "$AGENTMON_HOME/runs/$RUN_ID/raw/trajectory/mini.traj.json" \
  --include-p2-window-extras \
  "$P2_EVENTS"
```

Do not enable this for a real five-task shared service unless cross-run
misattribution is acceptable.

### Full KV mode and automatic analysis

Deep collection is now the default once the P2 hook is installed. A
full-performance run may record the setting explicitly:

```text
AGENTMON_VLLM_P2_DEEP=1
```

This mode enables execution-anchored prefill completion, scheduler/continuous-batch
iterations, and KV lookup/allocate/cache/free/evict/reset. The two older,
narrower switches remain compatible:

```text
AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS=1
AGENTMON_VLLM_P2_HOOK_KV_BLOCKS=1
```

The computed-block hook still emits `vllm.request.prompt_cache_accounted` and
does not claim prefill completion. In deep mode, `prefill_completed` is emitted
only after the matching `Scheduler.update_from_output` succeeds, with
`prefill_boundary_quality=engine_execution_observed`. See
`docs/p2_deep_agent_workload_zh.md` for field semantics and overhead guidance.

After generating a combined step CSV, run:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_analyze_steps.py" \
  /path/to/all-steps.csv \
  --out-dir "$BATCH_ROOT/analysis" \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --strict \
  --format report
```

`scripts/agentmon_analyze_steps.py` is a compatibility entrypoint. Its
implementation lives in `agentmon.analysis.step_csv`, so existing batch
commands and server smoke recipes do not change.

Rebuild or validate the automatically generated batch timeline with:

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon timeline "$BATCH_ROOT" --strict
```

Compare batches only after metadata checks pass:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_compare_batches.py" \
  /path/to/c5 /path/to/c10 \
  --expected-task-count 50 \
  --strict
```

The timing contract and complete acceptance checklist are in
`docs/agent_runtime_full_usage_zh.md`.
