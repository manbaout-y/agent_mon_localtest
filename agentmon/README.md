# AgentMon Ascend

AgentMon Ascend is a staged monitor for mini-SWE-agent, LiteLLM, vLLM-Ascend,
and Ascend NPU runs. It includes the P0 no-intrusion monitor, the P1 mini-SWE
adapter layer, and an opt-in, version-gated vLLM `0.18.0` P2 hook.

It provides:

- `agentmon doctor`
- `agentmon run -- <command>`
- `agentmon live <run-id>`
- `agentmon report <run-id>`
- `agentmon compare <run-id-a> <run-id-b>`
- `agentmon import-trajectory <path.traj.json>`
- `agentmon import-p2 <service-events.jsonl> --run-id <run-id>`
- `agentmon export <run-id> --format json|csv`
- `agentmon summary <run-id> --format table|json|csv`
- `agentmon aggregate ... --format table|json|csv`
- `agentmon timeline <batch-dir> --strict`
- `agentmon experiment plan prefix-cache ...`
- `agentmon optimizer experiment --batch-root <dir> --strategy static_threshold --output-dir <dir> [--preflight-only]`
- `scripts/agentmon_experiment.py prepare|smoke|pilot|formal|analyze ...`
- `scripts/agentmon_experiment_init.py --request <experiment.toml> --output-dir <dir>`
- `scripts/agentmon_server_quick_smoke.py --request <request.json> --server-profile <profile.json> --output-root <dir>`
- `scripts/agentmon_batch_runtime.py --batch-root <dir> --task-file <tasks.tsv> --concurrency 5`
- `scripts/agentmon_experiment_orchestrator.py --contract <experiment.json> --output-dir <dir>`
- `scripts/agentmon_optimizer_experiment.py --batch-root <dir> --strategy workload_aware --output-dir <dir>`
- `scripts/agentmon_analyze_steps.py <step-sequence.csv> --out-dir <dir>`
- `scripts/agentmon_compare_batches.py <batch-a> <batch-b> --strict`
- authoritative JSONL event storage with an optional SQLite query cache
- host/procfs, vLLM `/metrics`, Ascend `npu-smi`, and mini-SWE trajectory ingestion
- optional P1 mini-SWE model/agent/environment class-path adapters
- opt-in, plugin-based coding-agent context compression before model requests
- deterministic load-analysis data foundation with experiment contracts,
  metric semantics, data-quality gates, and basic handoff registers
- JSON/text/CSV run reports

The deterministic analysis runtime and non-CLI server workflow are documented
in `docs/analysis_sop.md` and `docs/server_test_from_package_no_cli_zh.md`.
For a complete Chinese project introduction, CLI server workflow, runtime/control overview, troubleshooting checklist, and model handoff prompt, see `docs/agentmon_project_handoff_and_server_usage_zh.md`.
For runtime optimizations that stay entirely inside AgentMon/AGENTRUN without
changing vLLM, CANN, LMCache, or OS/system software, including the master
enable/disable switch and algorithmic rationale for queueing, TTFT, tail
latency, and end-to-end workload time, see
`docs/runtime_speed_optimization_algorithms_zh.md`.
For the offline Optimization Experiment Harness research line, including M1
evidence outputs, M2 multi-strategy advice replay outputs, and M3 advice-proxy
mechanism evaluation, see
`docs/optimizer_experiment_harness_research_line_handoff_zh.md`.
For one-click experiment readiness gates that connect simple experiment
requests, contract generation, preflight, activation evidence, pilot checks,
analysis routing, and formal-matrix blocking, see
`docs/experiment_orchestration_zh.md`.
For turning natural-language server experiment intent into a self-contained
runtime smoke instance without asking users to guess vLLM/mini-SWE protocol
details, including generated `resource-gate.sh`, `service-start-gate.sh`,
`tool-call-gate.sh`, and structured `STEP/STATUS/REASON/NEXT` outputs, see
`docs/experiment_intent_to_contract_zh.md`.

The legacy script paths remain stable entrypoints, but implementation now lives
under responsibility-specific packages:

- `src/agentmon/batch/`: batch CLI parser, orchestration, workload execution,
  per-task analysis runner, analysis policy, manifest/artifact writing,
  resource profiling, and batch summary metrics;
- `src/agentmon/step_sequence/`: per-step sequence core, P1 span assembly, P2
  projection, resource-window attribution, JSONL IO, formatting, constants,
  and numeric helpers;
- `src/agentmon/analysis/existing_data.py`: read-only analysis for completed
  batch directories, used by `scripts/agentmon_existing_data_analysis.py`;
- `src/agentmon/analysis/step_csv.py`: automatic bottleneck analysis for step
  sequence CSV exports, used by `scripts/agentmon_analyze_steps.py`;
- `src/agentmon/server/preflight.py`: server runtime, vLLM, CANN/ACL, P2 hook,
  and active probe checks, used by `scripts/agentmon_server_preflight.py`;
- `src/agentmon/reporting/`: public reporting facade plus domain entry modules
  for event logs, P1, P2/client-server, quality gates, builders, and writers;
- `src/agentmon/analysis/stages.py`: Analysis Runtime stage catalog and plugin
  protocol used by `analysis.execution`.
- `src/agentmon/experiments/` and `src/agentmon/optimizer/harness/`: offline
  optimizer experiment evidence, snapshot construction, advice replay, risk
  audit, mechanism evaluation, Analysis SOP packaging, and report generation.
  Strategy plugins remain separate from schema profiles; use
  `agentmon optimizer experiment --schema-profile <json>` when step CSV field
  names or P2 scheduler/KV event names differ from the AgentMon defaults.
  Use `--preflight-only` before replay, `--run-search-root` for explicitly scoped
  historical run discovery, and `--status-file` for machine-readable progress.
  Inputs without observed timeline evidence remain `UNRESOLVED` and do not emit
  synthetic snapshots or optimizer decisions.
  `--resume` reuses a completed replay only when input files, source tree,
  strategy order, resolved AgentMon configuration, schema profile, memory
  budget, checkpoint artifact hashes, and snapshot-part hashes still match.
  M3 evaluation completes before report/provenance validation. A failed stage
  makes the top-level result `FAILED` and the CLI exits nonzero even when input
  preflight itself passed.

The server quick-smoke generator never assumes a fixed benchmark task id. An
explicit `--smoke-task` or request value wins; otherwise it discovers task ids
from the supplied benchmark zip, including nested runtime tarballs, and selects
the first lexical id as a deterministic smoke candidate. If discovery fails,
prepare returns `smoke_task_unresolved` and does not advance to Resource Gate.

New code should target these package modules. The `scripts/agentmon_*.py`
entrypoints listed here are compatibility wrappers for existing server commands
and tests.

Context compression is disabled by default in the library and enabled explicitly
in `examples/config.target.toml`. The supplied trigger policy activates at 80%
of the configured model window. The default `model_summary` algorithm uses a
separate, correlated model request to replace older complete context groups with
a continuation summary and targets 20%, while preserving the Agent trajectory.
It retains required instructions, the task contract, and the newest complete
interaction group. Trigger policies and compression algorithms are separate
plugin registries. Token count is an attributed estimate unless model usage or
a custom exact tokenizer is available. See `docs/agent_runtime_full_usage_zh.md` and
`docs/p1_mini_swe_adapter.md` for configuration and evidence fields.

It intentionally does not load the P2 vLLM runtime hook or P3 profiler control
by default. P2 is installed only when `AGENTMON_VLLM_P2_ENABLE=1` is present in
the vLLM service environment. Once the P2 hook is installed, Deep P2 collection
is enabled by default. Set `AGENTMON_VLLM_P2_DEEP=false` before service startup
only for a deliberate low-overhead compatibility run. The older hot-path flags
remain available as partial, legacy switches.
MoE logical routing capture is separately opt-in. It requires the vLLM service
option `--enable-return-routed-experts` plus
`AGENTMON_VLLM_P2_MOE=summary|trace`. Event, quality, storage, and
interpretation rules are in `docs/p2_vllm_adapter.md`.
The P2 requirements are tracked separately in `docs/p2_vllm_adapter.md`.
Deep P2 and Agent workload setup is documented in
`docs/p2_deep_agent_workload_zh.md`.
For a clean server deployment, target-runtime preflight, and one-task release
gate, follow `docs/server_release_smoke_zh.md`.
For the batch-run workflow that imports service-scoped P2 events and produces
per-step runtime analysis, see `docs/batch_p2_step_sequence_usage.md`.
Shared-service batch imports should use `agentmon import-p2
--filter-response-ids-from-trajectory <mini.traj.json>` so P2 requests are
selected by mini-SWE response ids instead of by byte offsets.

P2 discovery must be run with the same environment used to launch the target
vLLM service. In particular, do not remove CANN/ACL library variables such as
`LD_LIBRARY_PATH` or `ASCEND_HOME_PATH` when checking `vllm_ascend` imports.
The discovery JSON includes `hook_resolution_inputs`; only successful modules,
successful objects, and actual package-scan candidates should be used for hook
binding. Failed legacy probe paths are diagnostics, not hook targets.
When `scripts/agentmon_p2_discovery.py --strict-version` is used, the gate
checks the actually imported `vllm.__version__` first. Package metadata is only
fallback evidence and source-consistency diagnostics; unavailable imports or a
selected-version compatibility mismatch must stop the static P2 preflight.
Discovery exposes the stable machine fields `readiness.compatibility_status`,
`readiness.version_source_consistency`, and
`readiness.vllm_version_evidence.version_identity_status`; the legacy
`readiness.vllm_version_status` remains a compatibility alias.
The discovery CLI keeps stdout machine-readable: stdout is exactly one JSON
document unless `--output` is used, in which case stdout stays empty. Import-time
stdout from vLLM, Ascend plugins, or Torch is suppressed during probes.

The P2 runtime hook is disabled unless `AGENTMON_VLLM_P2_ENABLE=1` is present
in the vLLM service environment. It version-gates on vLLM `0.18.0`, preserves
vLLM return values/exceptions, and emits adapter errors instead of failing
requests when observation code fails.

## Server Paths

Use one fixed AgentMon Python environment. Do not install AgentMon into the
mini-SWE or vLLM environments.

```bash
export AGENTMON_REPO=/absolute/path/to/agentmon
export AGENTMON_HOME=/data/node0_disk3/chenyang/agentmon
export AGENTMON_VENV=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon
export AGENTMON_PY=$AGENTMON_VENV/bin/python
export VLLM_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python

export MINI_SWE_ROOT=/data/node0_disk3/chenyang/agent-runtime-poc/mini-swe
export MINI_SWE_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export MINI_SWE_BIN=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/mini
export TARGET_REPO=/path/to/target/git/repository
```

If the AgentMon checkout is elsewhere, change `AGENTMON_REPO` explicitly before
running any command. Do not assume the P2 patch has already been applied to both
`/data/node0_disk3/chenyang/agentmon-ascend` and
`/absolute/path/to/agentmon`. The target config
uses `${AGENTMON_REPO}/src` for the child `PYTHONPATH`, and AgentMon expands
`${...}` from the environment while loading TOML.

## Offline Setup

Use the existing AgentMon environment first. The current validation path is
no-install mode with `PYTHONPATH`; it does not recreate or mutate the existing
venv:

```bash
test -x "$AGENTMON_PY" || { echo "AgentMon Python does not exist"; exit 1; }
"$AGENTMON_PY" -c 'import sys; print(sys.executable)'
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" -m agentmon --version
```

Do not rerun this on an already validated server environment unless you
intentionally want to rebuild the AgentMon venv:

```bash
"$VLLM_PY" -m venv "$AGENTMON_VENV"
"$AGENTMON_PY" -m pip install --no-build-isolation -e "$AGENTMON_REPO"
"$AGENTMON_PY" -m agentmon --version
```

This borrows the existing Python 3.11 interpreter only to create an independent
AgentMon venv. It does not install AgentMon into the vLLM environment. If you
already created and validated an older env such as `agentmon-p0`, you can keep
using it; the name is not semantically important.

Do not allow the server to fall back to public PyPI. If editable install needs
build tooling that is not already present, use your internal wheelhouse or
internal pip index, or use the no-install `PYTHONPATH` mode.

## Required mini-SWE Config

The local model configuration must ignore LiteLLM cost lookup failures:

```yaml
model:
  model_name: "hosted_vllm/qwen3-coder-30b"
  cost_tracking: "ignore_errors"
```

`examples/config.target.toml` passes these child-process environment settings
for mini-SWE:

```text
MSWEA_CONFIGURED=1
MSWEA_COST_TRACKING=ignore_errors
OPENAI_API_KEY=not-needed
LITELLM_MODEL_REGISTRY_PATH=/data/node0_disk3/chenyang/agent-runtime-poc/mini-swe/configs/model_registry.json
NO_PROXY=127.0.0.1,localhost,::1
no_proxy=127.0.0.1,localhost,::1
```

It also removes `PYTHONPATH` and `PYTHONHOME` before launching the child
process, then sets a clean `PYTHONPATH` containing only
`${AGENTMON_REPO}/src` plus
`PYTHONNOUSERSITE=1`. This avoids leaking CANN packages from the shell into the
mini-SWE virtual environment while still allowing mini-SWE to import the P1
AgentMon class-path adapters.

## Doctor

Run doctor first:

```bash
"$AGENTMON_PY" -m agentmon doctor \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --output "$AGENTMON_HOME/doctor/doctor.json"
```

The output directory parent is created automatically. Doctor records capability
status for host, vLLM `/metrics`, vLLM `/v1/models`, `npu-smi`, and mini-SWE.
It also includes P1 static compatibility checks for mini-SWE class-path CLI options,
adapter import, adapter compatibility, event path writability, and child
`PYTHONPATH` cleanliness.
Single probe failures produce `partial`, `unsupported`, `timeout`, or
`not_found`; they do not abort the whole doctor run.

## Run mini-SWE

`agentmon run` must execute from the target Git repository root, because
mini-SWE operates on the current working directory. Either `cd` there first or
use `--cwd`.

Recommended form:

```bash
"$AGENTMON_PY" -m agentmon run \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --cwd "$TARGET_REPO" \
  --label calculator-smoke \
  -- "$MINI_SWE_BIN" \
  -m hosted_vllm/qwen3-coder-30b \
  -c mini.yaml \
  -c "$MINI_SWE_ROOT/configs/local_qwen3_coder.yaml" \
  -y \
  -l 0 \
  --exit-immediately \
  -t "task text" \
  -o "$MINI_SWE_ROOT/trajectories/calculator-smoke-{run_id}.traj.json"
```

Config merge order is intentional:

```text
-c mini.yaml
-c local_qwen3_coder.yaml
```

Do not use a fixed trajectory name such as `run.traj.json`; it will be
overwritten. AgentMon replaces `{run_id}` and `{AGENTMON_RUN_ID}` in child
argv before launch, so the trajectory path can share the AgentMon run id.

The command returns the mini-SWE child exit code. AgentMon monitor failures are
reported in the run data quality fields instead of changing a successful child
exit code.

## Enable P1 mini-SWE Spans

P0 wrapping works without changing the mini-SWE command. To enable P1 exact
model/tool/step spans, add the AgentMon class paths to the mini-SWE command:

```bash
"$AGENTMON_PY" -m agentmon run \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --cwd "$TARGET_REPO" \
  --label calculator-smoke-p1 \
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
  -t "task text" \
  -o "$MINI_SWE_ROOT/trajectories/calculator-smoke-p1-{run_id}.traj.json"
```

P1 records:

- `agent_step` spans
- `model_request` spans with request id and token/cost summaries when available
- `tool_call` spans
- tool root PID/PGID and PID starttime ticks
- tool return code, timeout flag, and output byte counts
- `info.agentmon` metadata in serialized trajectory data when mini-SWE calls
  `serialize()`

P1 still does not store prompt text, full shell commands, source code, or tool
stdout/stderr in AgentMon events by default. Command attribution uses hashes,
executable name, PID/PGID, and sampled proc counters.

## Enable P2 vLLM Hooks

P2 request/KV hooks are for a separately launched validation vLLM service. Do
not inject them into an already-running production service, and do not restart
the service currently serving `127.0.0.1:8000` just to test P2. The hook is
loaded through `sitecustomize.py`, so the AgentMon `src` directory must be on
the vLLM service `PYTHONPATH`, and the service plus EngineCore child processes
must inherit the same environment.

Launch the validation service from the same shell/script environment used by
the working vLLM-Ascend service. A clean terminal that lacks CANN/ACL variables
such as `LD_LIBRARY_PATH`, `ASCEND_HOME_PATH`, or the vLLM source checkout in
`PYTHONPATH` is not sufficient.

Preflight before enabling P2:

```bash
"$VLLM_PY" - <<'PY'
import sys
import vllm

print("python:", sys.executable)
print("vllm_version:", vllm.__version__)
print("vllm_file:", vllm.__file__)

import acl
print("acl_file:", acl.__file__)

import vllm_ascend
print("vllm_ascend_file:", vllm_ascend.__file__)
PY
```

Expected preflight facts:

```text
vllm_version: 0.18.0
vllm_file: /data/node0_disk1/vllm/vllm/__init__.py
acl import succeeds
vllm_ascend import succeeds
```

If this preflight fails, do not start P2. Fix the validation service
environment first.

Hook target preflight:

```bash
"$VLLM_PY" - <<'PY'
import importlib
import inspect

targets = [
    ("vllm.v1.engine.async_llm", "AsyncLLM", ["add_request", "generate", "abort"]),
    ("vllm.v1.engine.llm_engine", "LLMEngine", ["add_request", "step"]),
    ("vllm.v1.engine.core", "EngineCore", ["add_request", "step", "step_with_batch_queue"]),
    ("vllm.v1.core.sched.scheduler", "Scheduler", ["__init__", "add_request", "schedule", "make_stats"]),
    ("vllm.v1.core.kv_cache_manager", "KVCacheManager", ["__init__", "make_prefix_cache_stats"]),
]

failed = False
for module_name, class_name, methods in targets:
    print(f"\n{module_name}.{class_name}")
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        print("CLASS_FILE:", inspect.getsourcefile(cls))
    except Exception as exc:
        failed = True
        print("CLASS_ERROR:", type(exc).__name__, exc)
        continue
    for method_name in methods:
        method = getattr(cls, method_name, None)
        if method is None:
            failed = True
            print("MISSING_METHOD:", method_name)
        else:
            print("METHOD:", method_name, inspect.signature(method))

raise SystemExit(1 if failed else 0)
PY
echo "HOOK_TARGET_CHECK_RC=$?"
```

Only start P2 when the target check exits `0`. If it fails, update the hook
resolver and this target list from the actual service environment instead of
falling back to legacy module paths.

`sitecustomize.py` loader preflight:

```bash
find "$AGENTMON_REPO/src" -maxdepth 3 -type f \
  \( -name 'sitecustomize.py' -o -iname '*vllm*' -o -iname '*p2*' \) \
  -print

env \
  -u AGENTMON_VLLM_P2_ENABLE \
  PYTHONPATH="$AGENTMON_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  PYTHONNOUSERSITE=1 \
  "$VLLM_PY" -c \
  'import sitecustomize; print("SITECUSTOMIZE_FILE=", sitecustomize.__file__)'
```

Expected result:

```text
SITECUSTOMIZE_FILE=/absolute/path/to/agentmon/src/sitecustomize.py
```

If the path points to another project, P2 will not load according to this
README.

Validation service isolation:

```bash
npu-smi info
ps -eo pid,ppid,cmd | grep -E '[v]llm|[a]pi_server'
ss -ltnp | grep -E ':8000|:8001|:8002'
```

The validation service must use a different listening port and a free,
non-overlapping NPU device set. Do not launch another TP=4 service on devices
already used by the existing service. If no isolated devices are available, do
not perform the runtime smoke test concurrently. Do not treat the `...` below
as an executable command: copy the existing service's full launch arguments and
change only the listening port, device selection, log path, optional served
model name, and AgentMon P2 environment variables.

Service-scoped P2 event stream setup:

```bash
umask 077

export AGENTMON_RUN_ID=vllm-p2-smoke-$(date +%Y%m%d-%H%M%S)
export AGENTMON_RUN_DIR="$AGENTMON_HOME/runs/$AGENTMON_RUN_ID"
export AGENTMON_EVENTS_PATH="$AGENTMON_RUN_DIR/events.jsonl"

mkdir -p -m 700 "$AGENTMON_RUN_DIR"
chmod 700 "$AGENTMON_RUN_DIR"

export PYTHONPATH="$AGENTMON_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
export AGENTMON_VLLM_P2_ENABLE=1

# Use enabled/disabled only when the experiment requires a fixed cache state.
export AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE=disabled

# Scheduler iteration, execution-anchored prefill, and full KV lifecycle.
# Deep is already the default; keep this explicit for experiment provenance.
export AGENTMON_VLLM_P2_DEEP=1

"$VLLM_PY" -m vllm.entrypoints.openai.api_server ...
```

This P2 smoke directory is a service-scoped event stream. It is not a complete
`agentmon run` by itself: the hook writes `events.jsonl`, but it does not create
`manifest.json`, `config.resolved.toml`, `metrics.sqlite3`, child logs, or final
run status. Use it to validate hook events first. Create a manifest separately
before treating the directory as a full run for `summary` or `aggregate`.

For batch mini-SWE runs, keep the P2 service stream separate and import only the
events that belong to one AgentMon run. When the vLLM service was launched with
the current P2 hook, prefer exact run-id filtering:

```bash
P2_EVENTS="$AGENTMON_HOME/vllm-p2/events.jsonl"

"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --run-id "<run-id>" \
  --filter-run-id "<run-id>" \
  --quality direct \
  "$P2_EVENTS"
```

If the service does not yet emit `payload.agentmon_run_id`, use the mini-SWE
trajectory response ids:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --run-id "<run-id>" \
  --filter-response-ids-from-trajectory "$RUN_DIR/raw/trajectory/mini.traj.json" \
  --quality direct \
  "$P2_EVENTS"
```

Byte offsets are still useful for isolated serial smoke tests, but they are not
safe as the primary attribution mechanism when several tasks share one vLLM
service.

For single `agentmon run` executions, final report generation now happens after
a deterministic finalization barrier. The controller waits for collectors to
stop, drains/fsyncs the event writer, waits for a detected trajectory path to
exist and become stable, optionally imports a configured P2 service window, and
only then snapshots `events.jsonl` for `report.json` / `summary.json`. Set
`[finalization].p2_events_path` or `AGENTMON_FINALIZATION_P2_EVENTS_PATH` to the
service-scoped P2 JSONL when you want automatic run-local P2 import. For server
handoffs, the controller also recognizes `AGENTMON_P2_EVENTS_PATH`,
`AGENTMON_VLLM_P2_EVENTS_PATH`, `AGENTMON_SERVICE_P2_EVENTS_PATH`, and a parent
shell `AGENTMON_EVENTS_PATH` as compatible service-stream hints. The
controller records the service byte window at run start/end and, when a
trajectory is available, filters request events by trajectory response ids
before falling back to low-quality service-window observation.

If a mini-SWE trajectory is written outside the run directory and is not
detectable from `-o/--output` or `agent.trajectory_glob`, run
`agentmon import-trajectory --run-id <run-id> <path.traj.json>` and regenerate
the report after the trajectory exists.

Because the API process and EngineCore child processes may inherit the same
`AGENTMON_EVENTS_PATH`, the shared JSONL writer must remain append-only and
single-write per event:

```bash
grep -RIn \
  -E 'AGENTMON_EVENTS_PATH|O_APPEND|O_CREAT|os\.open|os\.write|0o600|event_id' \
  "$AGENTMON_REPO/src"
```

The expected implementation uses `os.open(..., O_APPEND|O_CREAT|O_WRONLY,
0o600)`, one `os.write()` per JSONL event, UUID event ids, and fail-open event
sink error handling.

The default hook targets fully qualified vLLM `0.18.0` V1 implementation paths:

```text
vllm.v1.engine.async_llm.AsyncLLM.add_request/generate/abort
vllm.v1.engine.llm_engine.LLMEngine.add_request/step
vllm.v1.engine.core.EngineCore.add_request/step/step_with_batch_queue
vllm.v1.core.sched.scheduler.Scheduler.__init__/add_request/schedule/make_stats
vllm.v1.core.kv_cache_manager.KVCacheManager.__init__/make_prefix_cache_stats
```

Deep mode additionally requires `Scheduler.update_from_output` and attempts the
real vLLM 0.18 KV lifecycle methods. Run this check in the service environment
before relying on the default Deep mode:

```bash
"$VLLM_PY" - <<'PY'
from vllm.v1.core.sched.scheduler import Scheduler
from vllm.v1.core.kv_cache_manager import KVCacheManager

required = {
    Scheduler: ["update_from_output"],
}
optional = {
    KVCacheManager: [
        "get_computed_blocks", "allocate_slots", "cache_blocks",
        "free", "evict_blocks", "reset_prefix_cache",
    ],
}
missing_required = [
    f"{cls.__name__}.{name}"
    for cls, names in required.items()
    for name in names
    if not hasattr(cls, name)
]
missing_optional = [
    f"{cls.__name__}.{name}"
    for cls, names in optional.items()
    for name in names
    if not hasattr(cls, name)
]
print("DEEP_HOOK_REQUIRED_MISSING=", missing_required)
print("DEEP_HOOK_OPTIONAL_MISSING=", missing_optional)
raise SystemExit(1 if missing_required else 0)
PY
```

`Scheduler.update_from_output` is required for the execution-anchored prefill
boundary. Missing KV methods do not block request lifecycle collection, but the
capability status is `partial` and the corresponding KV operations remain
unavailable. A complete KV-lifecycle experiment requires the optional-missing
list to be empty.

The compatibility export `vllm.engine.async_llm_engine.AsyncLLMEngine` may
appear in discovery output as an alias, but the default hook binds the real V1
implementation path `vllm.v1.engine.async_llm.AsyncLLM`.

`KVCacheManager.get_computed_blocks` is not patched by default because it is a
scheduler hot path. Enable it only for a short debug run:

```bash
export AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS=1
```

For the fuller request-level KV/block view, explicitly enable:

```bash
export AGENTMON_VLLM_P2_HOOK_KV_BLOCKS=1
```

Deep mode is enabled by default after P2 hook installation. Experiments may
still record the effective setting explicitly:

```bash
export AGENTMON_VLLM_P2_DEEP=1
```

It includes both legacy hot-path switches and adds synchronous and asynchronous
EngineCore token boundaries (`step` and `step_with_batch_queue`), scheduler iteration,
continuous-batching composition, execution-anchored prefill completion, KV
cache commit/reset, and client/server stage projection. Set
`AGENTMON_VLLM_P2_DEEP=false` only for a deliberate compatibility or
low-overhead run.

When supported by the target vLLM build, this records per-request
allocated/freed/evicted block counts and KV usage before/after. Leave it unset
for the lowest-risk lifecycle smoke.

First smoke checks:

```bash
grep -o '"event_type":"[^"]*"' "$AGENTMON_EVENTS_PATH" | sort | uniq -c
```

Expected P2 signals include:

```text
vllm.adapter.capability
vllm.request.received
vllm.request.queued
vllm.request.first_scheduled
vllm.request.finished
vllm.kv.snapshot
```

For the service-scoped smoke stream, emitted capability/KV events must record
prefix caching as disabled with a null hit rate. When these events are later
integrated into a complete AgentMon run, reports must display `disabled` /
`N/A`, not a zero-percent hit rate.

### P1 Preflight

Before running a real task, confirm mini-SWE exposes the class-path options:

```bash
"$MINI_SWE_BIN" --help | grep -E 'agent-class|model-class|environment-class'
```

Confirm the mini-SWE Python can import the adapters from the cleaned child
environment:

```bash
env \
  -u PYTHONPATH \
  -u PYTHONHOME \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$AGENTMON_REPO/src" \
  "$MINI_SWE_PY" - <<'PY'
from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedInteractiveAgent
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedLitellmModel
from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment

print("P1_IMPORT_OK")
print(InstrumentedInteractiveAgent)
print(InstrumentedLitellmModel)
print(InstrumentedMiniSweLocalEnvironment)
PY
```

Optional deeper compatibility check:

```bash
env \
  -u PYTHONPATH \
  -u PYTHONHOME \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$AGENTMON_REPO/src" \
  "$MINI_SWE_PY" - <<'PY'
import inspect
from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedInteractiveAgent
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedLitellmModel
from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment

for cls in (InstrumentedInteractiveAgent, InstrumentedLitellmModel, InstrumentedMiniSweLocalEnvironment):
    print("=" * 80)
    print(cls)
    print("MRO:", cls.__mro__)
    print("__init__:", inspect.signature(cls.__init__))
PY
```

### P1 Event Sink

`agentmon run` injects these variables into the mini-SWE child process:

```text
AGENTMON_RUN_ID=<current run id>
AGENTMON_RUN_DIR=/data/node0_disk3/chenyang/agentmon/runs/<run-id>
AGENTMON_EVENTS_PATH=/data/node0_disk3/chenyang/agentmon/runs/<run-id>/events.jsonl
AGENTMON_ROOT_SPAN_ID=run:<run-id>
```

The P1 adapters read those variables and append events directly to the same
`events.jsonl` as the P0 monitor. They do not use stdout, HTTP, or a Unix
socket. `events.jsonl` is opened with `O_APPEND`; each event is serialized as
one complete JSON line and written with a single `os.write()` call. No
user-space buffered stream is used, and `fsync()` is not performed for each
event by default. The `O_APPEND` plus single-write contract should be
revalidated before placing the output root on NFS or another network
filesystem. Adapter write failures are caught and reported on child stderr;
they are not intended to change mini-SWE semantics.

Trajectory metadata is separate: when mini-SWE calls `serialize()`, P1 also
adds `info.agentmon` with the run id and event path. That metadata is useful
after the run, but the span stream itself is written live to `events.jsonl`.

P1 uses these event types:

```text
run.start
run.end
agent_step.start
agent_step.end
agent_step.error
model_request.start
model_request.end
model_request.error
tool_call.start
tool_call.process_start
tool_call.end
tool_call.error
```

`span_type` is one of `run`, `agent_step`, `model_request`, or `tool_call`.
The parent relationship is:

```text
run span: span_id = run:<run-id>, trace_id = <run-id>
run
└─ agent_step
   ├─ model_request
   └─ tool_call
```

P1 span payloads include:

```text
trace_id              = AgentMon run id
span_id               = stable span id
parent_span_id        = parent span id, null only for the root run span
step_id               = current mini-SWE step id where applicable
start_wall_ns/end_wall_ns
start_mono_ns/end_mono_ns
duration_ns
source_pid
source_starttime_ticks
sequence             = monotonic sequence within the adapter process, not global
status
error_type
error_category
error_message_hash
error_message_length
```

For global ordering, prefer `start_mono_ns`, then `source_pid`, then
`sequence`. `event_id` is globally unique and is intended for de-duplication.
Do not treat `sequence` as a global order across P0 and P1 processes.

Model request ids are AgentMon-generated ids created before the request starts:

```text
agentmon_model_request_id
```

The same id is used in start/end/error events. When the model query signature
explicitly supports custom headers, AgentMon also tries to send it as
`X-AgentMon-Request-Id` for future P2 correlation. If headers are not supported,
P1 silently records the id in events and does not change the LiteLLM call.

### P1 Hashing and Privacy

AgentMon events do not store full shell command text. They store:

- SHA-256 command hash over command argv, NUL-separated
- executable basename, not the full executable path
- argument count
- PID/PGID and timing/resource metadata

By default the hash is unsalted, so the same command produces the same hash
across runs. To prevent cross-run or cross-project matching, set
`privacy.command_hash_salt_file` in the config. The salt is read by
`agentmon run` and passed to P1 adapters through `AGENTMON_COMMAND_HASH_SALT`.
The salt content is not written to `config.resolved.toml`, `manifest.json`,
reports, or environment summaries.

AgentMon does not redact before hashing; if a secret appears in a command, the
hash still reflects it. Also note that mini-SWE's own trajectory file may still
contain full tool-call arguments. AgentMon's privacy guarantees apply to
AgentMon events and reports, not to raw mini-SWE trajectory content.
P1 error payloads store error type/category plus message hash and byte length;
they do not store the raw exception message by default.

### P1 Success Checks

After a P1 run:

```bash
export RUN_ID=<actual-run-id>
export RUN_DIR="$AGENTMON_HOME/runs/$RUN_ID"

grep -o '"event_type":"[^"]*"' "$RUN_DIR/events.jsonl" | sort | uniq -c
grep -o '"span_type":"[^"]*"' "$RUN_DIR/events.jsonl" | sort | uniq -c
```

Expected P1 signals include:

```text
run.start / run.end
agent_step.start / agent_step.end
model_request.start / model_request.end
tool_call.start / tool_call.process_start / tool_call.end
span_type: run, agent_step, model_request, tool_call
```

`report.txt` should contain `P1 Spans`, `Tool Attribution`, and `Event Log`.
If mini-SWE is killed before a span end event is written, the finished report
marks that span as `abandoned` with `duration_estimated=true`. Span terminal
events include `.end`, `.error`, `.cancelled`, and `.timeout`; error
termination closes a span instead of creating a false open span.

Each run writes under the absolute output root configured in
`examples/config.target.toml`:

```text
/data/node0_disk3/chenyang/agentmon/runs/<run-id>/
  manifest.json
  config.resolved.toml
  events.jsonl
  metrics.sqlite3             # only when storage.sqlite=true
  raw/
  report.json
  summary.json
  report.txt
  report.csv
  logs/
```

Default permissions are private to the running user:

```text
runs/<run-id>/              0700
events.jsonl                0600
metrics.sqlite3             0600 when enabled
manifest/report/config      0600
raw trajectory copy         0600
logs/child.*.log            0600
```

## Inspect Runs

`--config` is a subcommand argument in the current CLI, so place it after the
subcommand:

```bash
"$AGENTMON_PY" -m agentmon live --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --once
"$AGENTMON_PY" -m agentmon report --config "$AGENTMON_REPO/examples/config.target.toml" <run-id>
"$AGENTMON_PY" -m agentmon summary --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --format table
"$AGENTMON_PY" -m agentmon export --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --format json
"$AGENTMON_PY" -m agentmon aggregate --config "$AGENTMON_REPO/examples/config.target.toml" --runs-root "$AGENTMON_HOME/runs" --format table
"$AGENTMON_PY" -m agentmon compare --config "$AGENTMON_REPO/examples/config.target.toml" <run-id-a> <run-id-b>
```

For P1 runs, `live` reads `events.jsonl` on refresh and shows a compact P1
summary: completed step/model/tool span counts, open span count, and the most
recent span status. It is still a simple text view, not a full trace UI.

`summary` prints a compact single-run summary without dumping long vLLM
`counter_deltas` into the default table:

```bash
"$AGENTMON_PY" -m agentmon summary --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --format table
"$AGENTMON_PY" -m agentmon summary --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --format json
"$AGENTMON_PY" -m agentmon summary --config "$AGENTMON_REPO/examples/config.target.toml" <run-id> --format csv
```

`aggregate` summarizes many runs from explicit ids or a run root:

```bash
"$AGENTMON_PY" -m agentmon aggregate --config "$AGENTMON_REPO/examples/config.target.toml" <run-id-a> <run-id-b> --format json
"$AGENTMON_PY" -m agentmon aggregate --config "$AGENTMON_REPO/examples/config.target.toml" --runs-root "$AGENTMON_HOME/runs" --label calculator-smoke --format csv
"$AGENTMON_PY" -m agentmon aggregate --config "$AGENTMON_REPO/examples/config.target.toml" --runs-root "$AGENTMON_HOME/runs" --status success --started-after 2026-07-02T00:00:00Z
```

Both commands support `--strict`. In strict mode the CLI exits non-zero if an
included run failed or has invalid JSON, duplicate event ids, open spans,
negative span durations, or a P0 JSONL/SQLite mismatch. Non-strict mode only
prints the result.

Smoke checks should use the compact summary fields instead of rejecting any
event whose type ends with `.error`. A successful P1 smoke run should require
the wrapper/test return codes to be zero, `run_status=success`,
`agent_exit_status=Submitted`/`submitted`, and `data_integrity_pass=true`.
Nonzero shell commands are reported separately as `command_nonzero_count`; they
do not automatically mean the whole coding task failed. Submitted mini-SWE
control flow is reported separately as `submission_control_flow_count`.

```bash
SMOKE_RC=0 FINAL_TEST_RC=0 \
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_p1_smoke_check.py" \
  "$AGENTMON_HOME/runs/<run-id>"
```

Each report generation also writes `summary.json` next to `report.json`:

```json
{
  "schema_version": "1.0",
  "summary_type": "agentmon.run_summary",
  "run_id": "20260702-...",
  "task_success": true,
  "data_integrity_pass": true,
  "run_status": "success",
  "agent_exit_status": "submitted",
  "duration_seconds": 12.3,
  "event_count": 1234,
  "finalization_completed": true,
  "agent_steps": 4,
  "model_requests": 5,
  "tool_calls": 3,
  "instrumentation_error_count": 0,
  "command_nonzero_count": 2,
  "expected_test_failure_count": null,
  "submission_control_flow_count": 1,
  "model_latency": {"total": 3.2, "mean": 0.64, "p50": 0.5, "p95": 1.1, "max": 1.2, "sample_count": 5},
  "tool_latency": {"total": 1.0, "mean": 0.33, "p50": 0.2, "p95": 0.7, "max": 0.8, "sample_count": 3},
  "prefix_cache_status": "disabled",
  "prefix_cache_hit_rate": null,
  "prefix_cache_hit_rate_quality": "disabled",
  "strict_failures": []
}
```

When prefix caching is disabled, hit rate is reported as `null` / `N/A`; it is
not printed as `0%` and is not included in aggregate hit-rate averages. Missing
metrics remain `null` in JSON and `N/A` in table/CSV output; aggregate sections
include sample counts and coverage where applicable.

For ad-hoc performance inspection, use the compact performance summary script.
Ascend collection is host-wide, so task-level NPU metrics require explicit
target devices or trustworthy P2 service metadata. The compatibility fields
`ascend_peak_hbm_used_bytes` and `ascend_max_aicore_util_pct` are target-device
aliases; when target devices are unknown they remain `N/A` instead of falling
back to host-wide maxima.

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_perf_summary.py" \
  "$AGENTMON_HOME/runs/<run-id>" \
  --p2-events "$AGENTMON_HOME/runs/<run-id>/p2-events-for-run.jsonl" \
  --ascend-devices 0,1,2,3 \
  --format table
```

Use `--p2-service-exclusive` only when the captured P2 stream is known to
contain requests from this run only, for example a dedicated validation service
or a captured offset window with no other clients. Without that explicit flag,
P2 token totals are shown as service-stream metrics but are not used to backfill
Agent token fields. Host-wide NPU maxima remain available as
`ascend_host_peak_hbm_used_bytes` and `ascend_host_max_aicore_util_pct`.

For per-step runtime analysis, use the step sequence script. It reconstructs
P1 `agent_step`, `model_request`, and `tool_call` spans, then overlays procfs,
Ascend, and P2 samples within each span window:

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$AGENTMON_HOME/runs/<run-id>" \
  --ascend-devices 0,1,2,3 \
  --format table
```

JSON/CSV output includes step/model/tool duration, model queue/TTFT/decode/e2e,
prompt/generation/cached/recomputed token counts, KV usage and prefix-cache
state, step-level agent CPU time and CPU/wall ratio, tool subprocess CPU/PSS/I/O
deltas, vLLM process CPU/PSS during model windows, and target/host NPU HBM and
AI Core utilization windows. Native P1 tool spans also record a per-call
`RUSAGE_CHILDREN` CPU delta so short commands are not silently missed by the
periodic procfs sampler. `--ascend-devices` is recommended for batch runs;
without target devices, NPU fields remain host-wide or `N/A` instead of being
misattributed to the task. After `agentmon import-p2`, the script reads the
run-local imported request P2 events automatically. Use
`--service-p2-events <service-events.jsonl>` to overlay KV/NPU service state;
those values are labeled `shared_service_observation` and
`not_request_attributed`. They are not copied into each run as request-owned
events.

For concurrent batches, use `scripts/agentmon_batch_runtime.py` with
`--p1-mode required`. It isolates each worker's HOME/workspace, correlates P2
requests by trajectory response ID or AgentMon run ID, writes separate
execution/monitoring/import/judge statuses, and produces `batch-manifest.json`,
`batch-summary.json`, `batch-results.tsv`, a service-level global-event stream,
and validated run/model/tool timelines. Use `--analysis-workers` to parallelize
postprocessing and keep `--min-p2-match-rate` / `--min-p2-lifecycle-rate` as
explicit publication gates. Byte-offset slicing is valid only
for a serial, service-exclusive run; it is not a concurrent attribution method.
For Deep P2 runs with large service JSONL files, prefer `--defer-analysis`
during workload execution and rerun the same batch root later with
`--analyze-existing --analysis-preset standard`; use one or two analysis
workers first to avoid disk I/O contention. Pass `--p2-index-cache-root` to
reuse the same P2 source index across batch roots. Use
`--analysis-preset quick` only for triage because it skips the combined step CSV
and batch timeline.
Batch postprocessing uses a compact shared P2 cache, indexed request-window
queries, task-scoped service-global slices, and one shared run-event read for
report plus step analysis. `p2-index-metrics.json` and the per-task P2 scan fields
show whether the indexed path or the correctness-preserving fallback was used.
If one API response ID resolves to multiple engine request IDs, the runner
records `multiple_p2_api_request_id_count` and the affected IDs, excludes them
from the exact match count, and leaves the request rows available for audit.

Batch execution applies workspace and runtime-source gates before any coding
agent starts. The default `--workspace-policy isolated` requires each resolved
workspace under `<batch-root>/workspaces`, requires `cwd` inside that workspace,
rejects duplicate task workspaces and symlink escapes, and rejects any
workspace that contains or is contained by the AgentMon source tree. Worker
AgentMon processes import from a batch-local read-only source snapshot; the
original source and snapshot are hashed before/after the workload. Keep
`--runtime-source-isolation snapshot` and `--runtime-source-integrity strict`
for formal runs.

`batch-manifest.json` and `batch-summary.json` separately record
`controller_finished`, `result_table_complete`, `workload_attempt_complete`,
`analysis_complete`, and `experiment_valid`. A complete TSV is not sufficient:
missing AgentMon run ids, a workspace-policy failure, or changed runtime source
makes the batch `INVALID` and forbids A/B or performance conclusions.
The complete server workflow and field semantics are in
`docs/batch_p2_step_sequence_usage.md` and the Chinese tutorial
`docs/agent_runtime_full_usage_zh.md`.

## P0 Limits

P0 can show process CPU/RSS/PSS/I/O, host load and PSI, NPU utilization and
HBM, vLLM aggregate metrics, child status, and imported trajectory summaries.
P1 adds exact mini-SWE step/model/tool spans when the class-path adapters above
are enabled.

P0 alone cannot reliably show these without P1/P2:

- exact real-time Agent step boundaries
- exact tool-call intervals for short tools
- a specific vLLM request mapped to a specific Agent step
- request-level TTFT/prefill/decode/KV cache
- exact KV cache bytes from `npu-smi` memory

Even with P1, request-level vLLM queue/prefill/decode/KV cache still requires
P2 because P1 does not instrument vLLM internals.

In the current vLLM `0.18.0` hook, `get_computed_blocks` emits
`vllm.request.prompt_cache_accounted`; it occurs before actual prefill and must
not be interpreted as `prefill_completed`. Exact prefill remains null unless a
real completion boundary exists. The separately named
`prefill_to_first_token` interval is the default proxy. Engine decode is always
`first_token -> engine_finished`; API `finished` is never substituted as the
decode end. The EngineCore hook also records `last_token_ns`; exact TPOT uses
`(last_token - first_token) / (generation_tokens - 1)` only when the token
boundaries share a verified clock domain.

`agentmon report` writes stable canonical `runs`, `requests`, `tools`, `events`,
and `system_samples` tables under each run. Use `agentmon canonical` to combine
runs, `agentmon compare --baseline ... --target ... --pair-by task,step` for
paired batch analysis, and `agentmon matrix --repeat N -- ...` for isolated
repeated experiments. JSONL/CSV are always available; Parquet requires the
optional `pyarrow` dependency.

Each run report and batch analysis writes `metric_definition.json`,
`data_quality.json`, `confidence.json`, `limitations.json`, and
`validation_checks.json`. A blocking integrity or correlation failure marks the
analysis `UNPUBLISHABLE` while retaining diagnostic artifacts.

## Existing-Data Offline Analysis

Completed concurrency batches can be compared without rerunning workloads or
restarting vLLM:

```bash
PYTHONPATH=src python3 scripts/agentmon_existing_data_analysis.py \
  --batch C1=/path/to/c1-batch \
  --batch C5=/path/to/c5-batch \
  --batch C10=/path/to/c10-batch \
  --baseline C1 \
  --strict \
  --output-dir /path/to/offline-analysis
```

The analyzer independently audits P1-to-P2 candidates in each run's event
stream, derives null-safe request metrics, validates absolute monotonic
timelines, pairs tasks and model-call sequences across concurrency levels,
separates workload amplification from unit-cost slowdown, classifies tail
runs, and summarizes per-device imbalance and host pressure. Shared NPU/host
windows remain explicitly non-request-attributed. See
`docs/existing_data_offline_analysis_zh.md` for formulas, output files, quality
gates, and interpretation boundaries.

Trajectory files are usually complete only after mini-SWE finishes. P1 spans,
however, are emitted live to `events.jsonl`. The current `agentmon live` command
shows only a compact P1 span summary from that stream; detailed per-span timing
is available in `report.json` / `report.txt`.

## Storage Contract

`events.jsonl` is the source of truth. `metrics.sqlite3` is an optional
index/cache used by the P0 writer and can be rebuilt from JSONL if needed. The
cache is disabled by default for concurrent experiments. P1 adapters write
JSONL directly; they do not write SQLite in real time.

Use either of these opt-in controls when the cache is required:

```toml
[storage]
sqlite = true
```

```bash
export AGENTMON_STORAGE_SQLITE=true
```

Set `AGENTMON_STORAGE_SQLITE=false` to override an older config that still
enables it. Environment overrides take precedence over the TOML file.

Reports include an `Event Log` section with:

- JSONL total lines
- JSONL valid, invalid, P0, and P1 event counts
- duplicate event id count
- SQLite P0 event count when available
- `p0_jsonl_sqlite_difference`

The consistency check is `jsonl_p0_count == sqlite_p0_count`. P1 adapters write
JSONL directly and do not write SQLite in real time, so `jsonl_total_count` and
`sqlite_p0_count` are expected to differ whenever P1 spans are enabled.

`npu-smi` process memory includes model weights, KV cache, runtime buffers,
communication buffers, activations, allocator reserves, and fragmentation. P0
reports NPU memory and vLLM KV cache usage ratio when available; exact KV cache
bytes require knowing vLLM's allocated KV pool.

Prefix caching is enabled by default for some vLLM 0.18 model/config
combinations. Do not assume that a fixed negative flag exists. Run
`scripts/agentmon_server_preflight.py` in the actual vLLM environment to read
the `EngineArgs` argparse action and its `prefix_cache_cli_options`, then place
the exact supported disable option in the recorded launch command. Set
`AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE=disabled` and run the post-start runtime
check after two identical prompts. A disabled experiment with any positive
`cached_prompt_tokens` is a required failure. Reports continue to render a
genuinely disabled cache as `disabled` / `N/A`, never `0%`.

For the post-start P2 release gate, the same preflight command can actively send
all required traffic instead of relying on hand-written curl commands:

```bash
python scripts/agentmon_server_preflight.py \
  --agentmon-repo "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --events-path "$AGENTMON_EVENTS_PATH" \
  --vllm-launch-command-file "$P2_ROOT/vllm-command.txt" \
  --expect-prefix-cache disabled --require-runtime-p2 \
  --runtime-base-url http://127.0.0.1:8001/v1 \
  --runtime-model qwen3-coder-30b \
  --runtime-stream-probe --runtime-stream-max-tokens 128 \
  --runtime-concurrency 4 --runtime-concurrent-max-tokens 192 \
  --timeout 180 --deep --strict
```

This performs two identical prefix-cache probes, one streaming long-output
probe, and four barrier-synchronized non-streaming requests. The machine-readable
result audits exact API-to-engine ID cardinality, all eight lifecycle boundaries,
API/EngineCore token agreement, timestamp ordering, observed continuous batching,
invalid JSON, duplicate event IDs, and per-process sequence regressions. Active
HTTP traffic remains opt-in: omitting `--runtime-base-url` and `--runtime-model`
preserves the original static preflight behavior.

Default sampling is 1 second for proc/host, vLLM metrics, and NPU. This is good
for run-level trends but not enough to capture every tool call lasting only
tens or hundreds of milliseconds.

## Development

Use Python 3.10 or newer:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m agentmon --help
```
