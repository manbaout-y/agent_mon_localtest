# P2 vLLM Request and KV Observability Requirements

This document freezes the P2 requirements for AgentMon after the P1 server
smoke version passed. It is a requirements and acceptance document, not an
implementation promise that P2 is already available.

P2 extends P0/P1 from run-level and mini-SWE-side spans into vLLM request-level
observability. The target service is vLLM `0.18.0+empty` with vLLM-Ascend
`0.18.0`, serving Qwen3-Coder-30B-A3B on Ascend NPUs.

## Status

- P0/P1 remain the stable default path.
- P2 is opt-in.
- P2.0 discovery tooling and the P2.1 request-mapping event/report contract are
  available.
- P2.2 request lifecycle latency and token summary contracts are available.
- P2.3 KV pool, KV state, and NPU attribution report contracts are available.
- P2.4 prefix-cache A/B planner is available as a non-mutating CLI command.
- The concrete vLLM runtime hook is available as an explicit opt-in loader for
  vLLM `0.18.0`. It is disabled by default and must be enabled only for a
  separately launched validation service.
- Discovery output separates legacy/static probe candidates from the actual
  installed package scan. Runtime hooks must be resolved only from successful
  objects, successful module imports, and scanned modules that exist in the
  target vLLM package.
- P2 must not modify CANN, system drivers, mini-SWE, or the already running
  vLLM environment by default.
- P2 may require a separately launched vLLM service with an AgentMon adapter or
  plugin loaded explicitly by the operator.
- If the adapter cannot prove compatibility with vLLM `0.18.0`, it must refuse
  to load and leave model serving behavior unchanged.

## Success Definition

P2 is successful when a completed AgentMon run can report all of the following,
with explicit quality labels:

- Agent request id correlated to the vLLM-side request id, or a documented
  heuristic mapping when direct propagation is unavailable.
- Request-level queue, prefill, decode, first-token, and end-to-end durations.
- Request-level prompt, cached, computed prefill, recomputed, and generation
  token counts when available.
- KV cache state that distinguishes disabled, enabled with zero hit, cache hit,
  recomputation, block eviction, and scheduler preemption.
- KV pool configuration inputs and formula version.
- Request-window NPU attribution with quality labels, without claiming exact
  per-request hardware energy.
- Prefix-cache A/B planning output that compares runs without changing the
  service automatically.
- Adapter failure isolation: instrumentation errors do not fail successful model
  requests.

## Non-Goals

P2 does not:

- Replace Prometheus, Grafana, or the vLLM metrics endpoint.
- Enable prefix caching automatically.
- Restart or reconfigure vLLM automatically.
- Store prompts, completions, full request bodies, or model outputs by default.
- Emit one event per generated token by default.
- Claim exact per-request NPU energy from card-level samples.
- Support arbitrary vLLM versions through guessing.
- Implement Ascend profiler control; that is P3.

## Deployment Model

P2 code should live under:

```text
src/agentmon/adapters/inference/vllm_018/
```

`agentmon.adapters.vllm_018` is a compatibility namespace only; new inference
adapter behavior belongs in the domain namespace above.

The adapter must be loaded only by an explicit vLLM launch/configuration step.
It must not be injected into an already running production process by AgentMon
without operator action.

The adapter must:

- Check the vLLM version at startup.
- Check the vLLM-Ascend version when available.
- Record the exact source file paths and signatures it binds to.
- Refuse to bind to failed discovery module/object references, even if those
  references appear in generic candidate lists from older vLLM layouts.
- Fail closed on incompatible versions.
- Fail open at runtime: adapter observation errors are emitted as
  `vllm.adapter.error` when possible and must not change vLLM return values or
  exception behavior.
- Write fixed-size JSONL events to an AgentMon sink or spool.
- Use a low-lock or background queue so request handling is not blocked by
  metrics I/O.

## Runtime Hook Loader

The P2 hook lives in `agentmon.adapters.inference.vllm_018.hook` and is bootstrapped by
`sitecustomize.py` only when this environment variable is truthy:

```text
AGENTMON_VLLM_P2_ENABLE=1
```

The loader does not modify vLLM source files or site-packages. It monkeypatches
only runtime objects that exist in the process and only when the detected vLLM
base version is `0.18.0`.

Current vLLM `0.18.0` hook targets:

```text
vllm.v1.engine.async_llm.AsyncLLM.add_request/generate/abort
vllm.v1.engine.llm_engine.LLMEngine.add_request/step
vllm.v1.engine.core.EngineCore.add_request/step/step_with_batch_queue
vllm.v1.core.sched.scheduler.Scheduler.__init__/add_request/schedule/make_stats
vllm.v1.core.kv_cache_manager.KVCacheManager.__init__/make_prefix_cache_stats
```

Deep collection is enabled by default once the P2 hook is installed. In that
mode, the resolver additionally binds the real
vLLM 0.18 methods `Scheduler.update_from_output` and the available
`KVCacheManager.get_computed_blocks/allocate_slots/cache_blocks/free/evict_blocks/reset_prefix_cache`
methods. Set `AGENTMON_VLLM_P2_DEEP=false` before service startup for an
explicit low-overhead or compatibility run; these methods include scheduler and
KV hot paths.

`EngineCore.step` and `EngineCore.step_with_batch_queue` always share one
output observer. The latter is required when vLLM asynchronous scheduling uses
the batch queue and may return `(None, model_executed)`. Reentrant wrappers are
guarded per thread so the same output cannot be counted twice. EngineCore
`new_token_ids` are accumulated across decode/speculative-decode iterations;
the resulting `engine_finished.generation_tokens` takes precedence over API
output counts when reports merge events from separate processes.

The compatibility export `vllm.engine.async_llm_engine.AsyncLLMEngine` may
appear in discovery output as an alias, but the runtime hook binds the real V1
implementation path `vllm.v1.engine.async_llm.AsyncLLM`.

`KVCacheManager.get_computed_blocks` is a hot-path method and is not patched by
default. It is patched only when this explicit debug flag is truthy:

```text
AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS=1
```

Request-level KV allocation/free/eviction hooks are also opt-in because they
wrap hot-path `KVCacheManager` methods when those methods exist in the target
vLLM build:

```text
AGENTMON_VLLM_P2_HOOK_KV_BLOCKS=1
```

When enabled, P2 may emit `vllm.kv.block_event` with
`operation`, `cache_hit_block_count`, `allocated_block_count`,
`cached_block_count`, `freed_block_count`, `evicted_block_count`,
`kv_usage_before`, and `kv_usage_after`.

Deep-mode prefill completion is not emitted at cache lookup or schedule time.
The hook associates the exact `SchedulerOutput` object with its scheduling
iteration and emits `vllm.request.prefill_completed` only after the matching
`Scheduler.update_from_output()` call succeeds. The event is marked
`prefill_boundary_quality=engine_execution_observed`.

P1 model adapters inject `X-AgentMon-Run-Id` and
`X-AgentMon-Request-Id`. The P2 hook preserves those values in request and KV
payloads as `agentmon_run_id`, `agentmon_model_request_id`, and
`agent_request_id`. That is the preferred correlation path for shared-service
batch runs.

Because vLLM V1 may run separate API and EngineCore processes, the same
`PYTHONPATH`, `AGENTMON_VLLM_P2_ENABLE`, `AGENTMON_RUN_ID`, and
`AGENTMON_EVENTS_PATH` environment must be inherited by the vLLM service and its
children. Loading only the API process is insufficient for scheduler/KV events.
The validation service must be launched from the same CANN/ACL/vLLM source
environment as the working service; a sanitized terminal without `acl`,
`vllm_ascend`, or `/data/node0_disk1/vllm` on the import path is not a valid
P2 launch environment.

Preflight:

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

Start P2 only when `HOOK_TARGET_CHECK_RC=0`. If it fails, update the runtime
hook from the successful objects in the actual service environment; do not
bind to failed legacy references from generic discovery lists.

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

The printed file must be `$AGENTMON_REPO/src/sitecustomize.py` from the newly
unpacked release.
If a different `sitecustomize.py` wins import resolution, fix `PYTHONPATH`
before enabling P2.

Validation service isolation:

```bash
npu-smi info
ps -eo pid,ppid,cmd | grep -E '[v]llm|[a]pi_server'
ss -ltnp | grep -E ':8000|:8001|:8002'
```

The validation service must use a different listening port and a free,
non-overlapping NPU device set. Do not launch another TP=4 service on devices
already used by the existing service. If no isolated devices are available, do
not perform the runtime smoke test concurrently. The `...` in the launch shape
is not executable by itself: copy the existing service's full launch arguments
and change only the listening port, device selection, log path, optional served
model name, and AgentMon P2 environment variables.

Minimal validation launch shape:

```bash
umask 077

export AGENTMON_REPO=/absolute/path/to/agentmon
export AGENTMON_HOME=/data/node0_disk3/chenyang/agentmon
export AGENTMON_RUN_ID=vllm-p2-smoke-$(date +%Y%m%d-%H%M%S)
export AGENTMON_RUN_DIR="$AGENTMON_HOME/runs/$AGENTMON_RUN_ID"
export AGENTMON_EVENTS_PATH="$AGENTMON_RUN_DIR/events.jsonl"

mkdir -p -m 700 "$AGENTMON_RUN_DIR"
chmod 700 "$AGENTMON_RUN_DIR"

export PYTHONPATH="$AGENTMON_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
export AGENTMON_VLLM_P2_ENABLE=1

"$VLLM_PY" -m vllm.entrypoints.openai.api_server ...
```

Do not set `AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS=1` or
`AGENTMON_VLLM_P2_HOOK_KV_BLOCKS=1` for the first smoke test. First validate
lower-overhead request, scheduler, and prefix-stat events.

The validation hook output is a service-scoped event stream. The hook creates
or appends `events.jsonl`; it does not create `manifest.json`,
`config.resolved.toml`, `metrics.sqlite3`, child logs, or a final AgentMon run
status. Do not treat such a directory as a complete run for `summary` or
`aggregate` until a manifest/finalization step is added separately.

For batch AgentMon runs, import the service stream into the completed run before
generating final reports. If the service emits `payload.agentmon_run_id`, prefer
exact run-id filtering:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --run-id "<run-id>" \
  --filter-run-id "<run-id>" \
  --quality direct \
  "$P2_EVENTS"
```

If the service was launched without header correlation, filter by mini-SWE
trajectory response ids:

```bash
"$AGENTMON_PY" -m agentmon import-p2 \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  --run-id "<run-id>" \
  --filter-response-ids-from-trajectory "$RUN_DIR/raw/trajectory/mini.traj.json" \
  --quality direct \
  "$P2_EVENTS"
```

Byte offsets are still available for isolated serial smoke tests, but they are
not the primary attribution mechanism for shared-service concurrency.

The API process and EngineCore child processes may write the same
`AGENTMON_EVENTS_PATH`. The writer must remain append-only and single-write per
event:

```bash
grep -RIn \
  -E 'AGENTMON_EVENTS_PATH|O_APPEND|O_CREAT|os\.open|os\.write|0o600|event_id' \
  "$AGENTMON_REPO/src"
```

The expected implementation uses `os.open(..., O_APPEND|O_CREAT|O_WRONLY,
0o600)`, one `os.write()` per JSONL event, UUID event ids, and fail-open event
sink error handling.

## Discovery Snapshot Semantics

`scripts/agentmon_p2_discovery.py` is a read-only inspector. It may contain
static candidate module names for broad coverage, including module paths that
exist in older or different vLLM layouts. Those failed references are diagnostic
data only.

Hook implementation must use this priority order:

1. `hook_resolution_inputs.successful_objects`
2. `hook_resolution_inputs.successful_modules`
3. `hook_resolution_inputs.package_scan_candidate_modules`

Do not implement hooks against entries listed in:

```text
hook_resolution_inputs.ignored_failed_modules
hook_resolution_inputs.ignored_failed_objects
```

The `vllm_ascend.worker.worker` module may import `acl` and other CANN runtime
libraries. If discovery is run from a sanitized shell without the same
`LD_LIBRARY_PATH`, `ASCEND_HOME_PATH`, and related variables used by the real
vLLM service, that import may fail even though the service can load it. Re-run
discovery in the actual service launch environment before treating such a
failure as incompatible.

## Request ID Propagation

Preferred path:

```text
P1 model_request_id
  -> OpenAI-compatible HTTP header or body metadata
  -> vLLM API server request context
  -> vLLM internal request id
  -> scheduler/request metrics
```

P1 already generates `agentmon_model_request_id` and tries to send
`X-AgentMon-Request-Id` when the LiteLLM call path accepts custom headers. P2
must preserve that id if the API server receives it.

At minimum, P2 must persist this mapping:

```json
{
  "agent_request_id": "modelreq-...",
  "vllm_request_id": "vllm-...",
  "run_id": "20260702-...",
  "received_ns": 123,
  "queued_ns": 234,
  "first_scheduled_ns": 456,
  "first_token_ns": 789,
  "first_output_ns": 950,
  "engine_finished_ns": 990,
  "finished_ns": 1000,
  "correlation_quality": "direct"
}
```

Allowed `correlation_quality` values:

- `direct`: AgentMon id was received by vLLM and attached to the internal
  request.
- `server_generated`: no AgentMon id was present, but vLLM request id and timing
  are exact.
- `heuristic`: matched by request order and time window.
- `unavailable`: no safe mapping exists.

Heuristic correlation must never be presented as exact.

## Event Contract

P2 events use the same JSONL event envelope as P0/P1 and `source="vllm_p2"`.
Event payloads must avoid prompt text, completion text, and raw request bodies.

Required event types:

- `vllm.request.received`
- `vllm.request.queued`
- `vllm.request.first_scheduled`
- `vllm.request.first_token`
- `vllm.request.first_output`
- `vllm.request.engine_finished`
- `vllm.request.finished`
- `vllm.request.cancelled`
- `vllm.request.preempted`
- `vllm.kv.snapshot`
- `vllm.adapter.capability`
- `vllm.adapter.error`

Capability-dependent event types:

- `vllm.request.prompt_cache_accounted` (requires the computed-block hot-path hook)
- `vllm.kv.block_event` request fields (requires the KV block hot-path hook)

Optional event type:

- `vllm.request.prefill_completed` (only when a true post-prefill boundary is available)
- `vllm.request.decode_iteration`

`decode_iteration` is disabled by default. When enabled for debugging, it must
be sampled or aggregated so high token rates cannot overwhelm the event writer.

## MoE Logical Routing Capture

### Capability and boundary

vLLM `0.18.x` can return the router-selected top-k logical expert IDs through
`CompletionOutput.routed_experts` when `enable_return_routed_experts` is enabled.
vLLM Ascend `0.18.0` calls the same routed-expert capturer from its fused-MoE
operator. This is an opt-in inference-adapter capability; Runtime core does not
import vLLM or know MoE event names.

Primary references:

- [vLLM model configuration](https://github.com/vllm-project/vllm/blob/v0.18.0/vllm/config/model.py)
- [vLLM completion output contract](https://github.com/vllm-project/vllm/blob/v0.18.0/vllm/outputs.py)
- [vLLM routed-expert capturer](https://github.com/vllm-project/vllm/blob/v0.18.0/vllm/model_executor/layers/fused_moe/routed_experts_capturer.py)
- [vLLM routed-expert example](https://docs.vllm.ai/en/v0.18.2/examples/offline_inference/routed_experts_e2e/)
- [vLLM Ascend fused-MoE integration](https://github.com/vllm-project/vllm-ascend/blob/v0.18.0/vllm_ascend/ops/fused_moe/fused_moe.py)

The observed IDs are logical router IDs. They are not physical device/rank IDs,
do not include per-expert kernel time, and cannot by themselves prove that one
physical expert caused a latency spike. A physical placement conclusion requires
a contemporaneous EPLB expert map from the same service configuration and time
window.

### Enabling collection

The default is `off`. The service must be restarted with both the AgentMon mode
and vLLM's return option enabled:

```bash
export AGENTMON_VLLM_P2_ENABLE=1
export AGENTMON_VLLM_P2_MOE=summary

# Add this to the existing, otherwise unchanged vLLM serve command:
--enable-return-routed-experts
```

Modes:

| Mode | Stored evidence | Intended use |
|---|---|---|
| `off` | Capability reports disabled; no request routing events. | Normal runs that do not study MoE routing. |
| `summary` | Per-request/per-completion shape, alignment and per-layer balance metrics. | Long performance experiments with bounded event size. |
| `trace` | The summary plus lossless compressed logical top-k IDs. | Short diagnosis and route-pattern analysis. |

Trace chunking is configured rather than tied to an experiment:

```bash
export AGENTMON_VLLM_P2_MOE_CHUNK_TOKENS=256
```

The configured value is a storage chunk size, not an eligibility or performance
threshold. AgentMon clamps malformed or unsafe values to implementation bounds
and records the effective value in the capability event.

### Event contract

The adapter emits:

- `vllm.moe.routing.capability`: service-global runtime discovery state. It
  records mode, engine flag, discovered expert count, source and limitations.
- `vllm.moe.routing.summary`: request-attributed, one event per completion.
- `vllm.moe.routing.chunk`: request-attributed, trace mode only.

The summary never contains prompt text, completion text, request bodies or token
IDs. Its core fields are:

```text
status = COMPLETE | PARTIAL | UNRESOLVED
mode
completion_index
shape = [routed_token_count, layer_count, top_k]
expected_routed_token_count
token_alignment = MATCH | MISMATCH | UNKNOWN
expert_count and expert_count_quality
assignment_count
max_expert_share
min_normalized_entropy
max_load_coefficient_of_variation
layers[]
quality
evidence_source
limitations[]
prohibited_conclusions[]
```

For vLLM's routed output, the expected first dimension is normally
`prompt_tokens + generated_tokens - 1`. AgentMon compares rather than forces
this identity. Missing usage remains `UNKNOWN`; disagreement remains
`MISMATCH/PARTIAL` and the observed shape is retained.

For each layer, let `c_e` be the number of top-k assignments to logical expert
`e`, `N = sum(c_e)`, `p_e = c_e/N`, and `E` the discovered expert domain:

```text
max_expert_share = max(c_e) / N
normalized_entropy = -sum(p_e * log(p_e)) / log(E)
load_coefficient_of_variation = std(c_1..c_E) / mean(c_1..c_E)
```

High max share, low normalized entropy, or high coefficient of variation means
the logical routing distribution is concentrated for that observed request. It
does not establish a performance cause without aligned latency, scheduler,
resource and placement evidence.

Trace chunks use a canonical little-endian `int32` C-order array, split only on
the token axis, compressed with zlib, and base64 encoded for JSONL. Every chunk
records shape, token half-open range, raw byte count and SHA256. `events.jsonl`
remains authoritative; the report only summarizes the raw evidence and does not
replace it.

### Report and batch semantics

MoE request events participate in the same request-ID index as
`vllm.request.*`, so concurrent batch selection uses run/request identities and
the existing correctness-preserving time fallback. The capability is
service-global state; task-window slicing retains the most recent capability
before the task window.

Run output adds `vllm_p2.moe_routing`; request rows and canonical model-request
rows add `moe_*` fields. Batch summaries aggregate counts and balance metrics.
When routes, token alignment, expert count or mapping evidence are missing, the
output remains `missing`, `partial` or `unresolved`; it is never filled from a
model name or an assumed platform topology.

Check a live service with:

```bash
grep -m1 '"event_type":"vllm.moe.routing.capability"' "$AGENTMON_EVENTS_PATH"
grep -m1 '"event_type":"vllm.moe.routing.summary"' "$AGENTMON_EVENTS_PATH"
```

`summary` and `trace` can add model-output retention and serialization overhead.
Performance comparisons must keep the mode and chunk setting identical across
conditions. Exact overhead and support for the target model, quantization,
EP/TP, ACLGraph and speculative/MTP combination require server validation.

## Request Metrics

For each vLLM request, P2 should report:

```text
agent_request_id
model_call_id
client_request_id
server_request_id
engine_request_id
vllm_request_id
run_id
model_name
arrival_time
queue_duration_s
prefill_duration_s
decode_duration_s
exact_tpot_s
time_to_first_token_s
e2e_duration_s
prompt_tokens
cached_prompt_tokens
computed_prefill_tokens
recomputed_prompt_tokens
generation_tokens
finish_reason
preemption_count
correlation_quality
metrics_quality
```

Timing boundaries are:

```text
queue        queued -> first_scheduled
engine TTFT  queued -> first_token
TTFT         received -> first_token
prefill proxy first_scheduled -> first_token
prefill exact first_scheduled -> prefill_completed (deep mode only)
decode       first_token -> engine_finished
token decode first_token -> last_token
exact TPOT   (last_token - first_token) / (generation_tokens - 1)
E2E          received -> finished
first output received -> first_output
```

`KVCacheManager.get_computed_blocks()` runs before model prefill. Its hook emits
`prompt_cache_accounted` for cached/uncached/computed-prefill token accounting and
must not emit `prefill_completed`. The lower-overhead vLLM `0.18.0` hook
therefore leaves exact prefill latency null and reports the separately named
`prefill_to_first_token` proxy. With the default Deep mode, the hook emits
`prefill_completed` only after the matching `Scheduler.update_from_output()`
succeeds. Neither mode may present the proxy as exact prefill compute time.

`first_token` is emitted by EngineCore when `new_token_ids` first becomes
non-empty. The hook remembers the latest EngineCore token observation and writes
it as `last_token_ns` on `engine_finished`; this state is bounded and is cleared
on finish, abort, cancellation, and error. `first_output` is an API-side observation. In particular, a
non-streaming `FINAL_ONLY` response near request completion must not be used as
the first-token timestamp. Every event includes source PID, host, and
`CLOCK_MONOTONIC` domain; reports mark legacy or cross-domain pairs instead of
silently treating them as exact.

Missing values are `null` in JSON and `N/A` in table/CSV output. They are not
converted to zero.

## KV Cache Requirements

P2 must distinguish these cases:

- `disabled`: prefix caching or relevant KV feature is explicitly disabled.
- `enabled_zero_hit`: enabled and queried, with a valid denominator but zero hit.
- `hit`: cached tokens or block reuse observed.
- `recomputed`: recomputed prompt tokens observed.
- `evicted`: KV block eviction observed.
- `preempted`: scheduler preemption affected the request.
- `unknown`: feature support or denominator is unavailable.

Per-request KV payload target:

```json
{
  "request_id": "vllm-...",
  "num_cached_tokens": 0,
  "num_computed_tokens": 512,
  "num_recomputed_tokens": 0,
  "allocated_block_count": 32,
  "freed_block_count": 32,
  "evicted_block_count": 0,
  "preemption_count": 0,
  "kv_residency_s": 12.3,
  "kv_residency_quality": "exact_request_lifecycle",
  "kv_usage_before": 0.21,
  "kv_usage_after": 0.24,
  "kv_quality": "exact"
}
```

Prefix-cache rates:

```text
prefix_hit_rate = delta(prefix_cache_hits) / delta(prefix_cache_queries)
cached_prompt_ratio = cached_prompt_tokens / prompt_tokens
recomputed_prompt_ratio = recomputed_prompt_tokens / prompt_tokens
```

When final `RequestOutput` exposes both prompt and cached token counts, AgentMon
derives `new_prompt_tokens`, `uncached_prompt_tokens`, and
`computed_prefill_tokens` as `max(prompt_tokens - cached_prompt_tokens, 0)` and
records `prompt_token_accounting_quality=derived_prompt_minus_cached`.
`recomputed_prompt_tokens` is a different metric: it is derived only when
scheduler prefill accounting proves tokens were scheduled again. Otherwise it
remains null with `recomputed_token_quality=unavailable_not_observed`.

KV residency is derived only when request-keyed `allocate` and `free` block
events both exist. A partial lifecycle remains null and carries a quality label.

If the denominator is zero or unavailable, the value is `null` / `N/A`. If
prefix caching is disabled, the value is `disabled` / `N/A`, not `0%`.

## KV Pool Configuration

P2 must read or infer the KV pool inputs from vLLM cache configuration or
initialization logs:

```text
num_gpu_blocks
block_size
cache_dtype
num_layers_per_rank
num_kv_heads_per_rank
head_dim
tensor_parallel_size
```

The report must preserve:

```text
formula_version
source
source_quality
bytes_per_block_per_rank
allocated_pool_bytes_per_rank
calculation_inputs
```

Formula:

```text
bytes_per_block_per_rank =
  block_size * layers_per_rank * kv_heads_per_rank * head_dim * 2 * dtype_bytes

allocated_pool_bytes_per_rank =
  num_gpu_blocks * bytes_per_block_per_rank
```

Do not interpret `npu-smi` process HBM as exact KV cache bytes.

## NPU Request Attribution

P2 may attribute card-level NPU samples to request windows, but must preserve
quality labels:

- `window`: integrate card-level samples over the request interval.
- `exclusive`: the request window overlaps a period where vLLM running requests
  equals one.
- `estimated_share`: concurrent requests were active and samples were divided by
  token count or active time.
- `unavailable`: insufficient samples or missing request timing.

Reports must state that NPU attribution is sampled/estimated unless an exact
source is available.

## Report and Summary Additions

`report.json` should add a `vllm_p2` section:

```json
{
  "status": "complete",
  "adapter_version": "0.1",
  "target_vllm_version": "0.18.0",
  "version_match": true,
  "request_count": 3,
  "correlation": {
    "direct": 3,
    "heuristic": 0,
    "unavailable": 0
  },
  "latency": {
    "queue": {"p50": 0.01, "p95": 0.02, "sample_count": 3},
    "prefill": {"p50": 0.4, "p95": 0.7, "sample_count": 3},
    "decode": {"p50": 1.5, "p95": 2.0, "sample_count": 3}
  },
  "kv_cache": {
    "prefix_cache_status": "disabled",
    "prefix_cache_status_conflict": false,
    "prefix_cache_hit_rate": null,
    "prefix_cache_hit_rate_quality": "disabled"
  }
}
```

`prefix_cache_status` is resolved from explicit expected launch/config metadata
and P2 runtime snapshots. Unknown snapshots must not imply enabled prefix
caching. If an explicit disabled/enabled expectation conflicts with observed P2
snapshots or positive cached-token accounting, the status is `CONFLICT` and
`prefix_cache_status_conflict_reasons` lists the evidence.

Each `report.json.vllm_p2.requests[]` row also exposes stable identity fields
for downstream analysis: `canonical_request_id`, `canonical_request_id_kind`,
`request_id`, `request_id_kind`, `request_id_aliases`, `response_id`, and
`trajectory_request_id`. `request_id` is a canonical analysis identifier, not a
promise that the upstream API, vLLM API process, and EngineCore used the same
native ID. `request_id_aliases` preserves all observed aliases so consumers can
audit ID propagation without guessing.

`report.json.client_server.request_identity_audit` records request correlation
quality across P1 and P2. It lists matched P1/P2 requests, P1 model spans that
did not find a P2 row, and orphan P2 rows that did not match a P1 model span.
These diagnostics are evidence only; an orphan P2 request in a shared service
must not be attributed to the nearest task without an explicit request ID match.

`summary.json` should expose compact P2 fields:

```text
p2_status
p2_version_match
p2_request_count
p2_direct_correlation_count
p2_heuristic_correlation_count
p2_unavailable_correlation_count
request_queue_latency
request_prefill_latency
request_decode_latency
request_ttft_latency
request_e2e_latency
p2_cached_prompt_tokens
p2_computed_prefill_tokens
p2_recomputed_prompt_tokens
p2_preemption_count
p2_kv_pool_bytes_per_rank
p2_kv_pool_quality
p2_npu_attribution_quality_counts
```

Aggregate reports should include sample counts and coverage for every P2 metric.

## Prefix Cache A/B Planning

P2 should add planning support:

```bash
agentmon experiment plan prefix-cache \
  --baseline-run <run-id> \
  --variant-config <path>
```

The planner must not restart vLLM. It should produce a checklist and comparable
run matrix:

- same task set
- same model
- same vLLM version
- same tensor/expert parallelism
- same max model len, max seqs, max batched tokens
- same warmup strategy
- cold-cache and warm-cache runs separated
- repeated runs per condition
- compared metrics: success, TTFT, queue, prefill, decode, E2E, KV usage,
  cached tokens, recomputed tokens, preemption count

## Capability Discovery

`agentmon doctor` should include P2 capability checks when configured:

- vLLM version is exactly supported.
- vLLM-Ascend version is detected.
- Adapter import works in the target vLLM Python environment.
- Required hook points exist with expected signatures.
- Event sink path is writable.
- Adapter can run in dry-run mode without serving a request.

Doctor must not block indefinitely. Each P2 probe needs its own timeout.

## Failure Isolation

Instrumentation failures must be reported as `vllm.adapter.error` and counted
as P2 data-quality failures. They must not:

- change generated model text
- change HTTP response status for a successful model request
- crash the vLLM worker
- block the request path on event writer backpressure

If the adapter queue is full, P2 may drop low-priority debug events, but must
preserve request lifecycle terminal events when possible and count drops.

## Test Requirements

Unit tests:

- version check accepts exactly supported vLLM `0.18.0` / `0.18.0+empty`
- version check rejects incompatible versions
- request id mapping stores direct and heuristic quality separately
- request latency calculations handle missing timestamps
- prefix cache disabled returns `null` / `N/A`, not zero
- denominator zero returns `null`
- cached/computed/recomputed token fields remain null when missing
- KV pool formula uses the documented inputs
- adapter exceptions are isolated and emitted as errors
- queue overflow drops only low-priority events
- report and summary expose P2 fields with sample counts and coverage

Integration tests on the target server:

- P2 dry-run adapter import in the vLLM Python environment
- one successful request with direct request-id correlation
- one concurrent-request run with attribution quality not marked exact
- prefix-cache-disabled run reports disabled / N/A
- missing P2 adapter degrades to `p2_status=missing`

## Acceptance Checklist

- [x] Strict vLLM `0.18.0` compatibility gate.
- [x] Request id end-to-end or stable quality-labeled fallback event contract.
- [x] Request-level queue/prefill/decode/TTFT/E2E report contract.
- [x] Cached/computed/recomputed token report contract.
- [x] KV pool parameters and formula inputs.
- [x] KV disabled/zero-hit/hit/recomputed/evicted/preempted state reporting.
- [x] Adapter failure isolation in unit-tested runtime wrappers.
- [x] Concurrent request attribution quality label fields.
- [x] Prefix Cache A/B planning report.
- [x] `doctor`, `report`, `summary`, and `aggregate` understand P2 data.
- [ ] Target-server vLLM P2 smoke validates live hook loading in API and
  EngineCore processes.

## Suggested Implementation Order

### P2.0 Discovery Snapshot

On the server, export vLLM `0.18.0` source context and runtime signatures for
the API server, request objects, scheduler, cache config, and metrics hooks.
No code changes yet.

Use the read-only discovery script from the vLLM Python environment:

```bash
export AGENTMON_REPO=/absolute/path/to/agentmon
export VLLM_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
export OUT=/data/node0_disk3/chenyang/agentmon-p2-discovery-$(date +%Y%m%d-%H%M%S).json

PYTHONNOUSERSITE=1 \
  "$VLLM_PY" "$AGENTMON_REPO/scripts/agentmon_p2_discovery.py" \
  --output "$OUT" \
  --strict-version

python3 -m json.tool "$OUT" >/dev/null
ls -lh "$OUT"
```

The script imports candidate vLLM modules, records package versions, module
paths, object signatures, source hashes, and bounded source excerpts. It does
not start vLLM, send requests, hook functions, or modify site-packages.
With `--strict-version`, the gate uses the actually imported `vllm.__version__`
first. `importlib.metadata` is used only as fallback evidence and a
source-consistency diagnostic. Missing or failed imports and selected-version
compatibility mismatch return non-zero; disagreement between distribution
metadata and the imported module is recorded in
`readiness.vllm_version_evidence.version_source_consistency` and
`version_identity_status`; it does not override
`readiness.compatibility_status` or the legacy compatibility alias
`readiness.vllm_version_status`. Local build suffixes such as `0.18.0+empty`
are retained as evidence while release tuple compatibility remains `0.18.0`.
The CLI treats stdout as a machine-readable channel. When `--output` is not
used, stdout contains exactly one JSON document. When `--output <file>` is
used, stdout stays empty. Discovery suppresses stdout written by vLLM,
vLLM-Ascend, Torch, or platform plugin imports while still recording import
errors in the payload; diagnostics must go to stderr/logging, never into the
JSON stream.

If the server's vLLM `0.18.0` uses different internal paths, rerun with extra
targets instead of editing the script:

```bash
PYTHONNOUSERSITE=1 \
  "$VLLM_PY" "$AGENTMON_REPO/scripts/agentmon_p2_discovery.py" \
  --output "$OUT" \
  --module vllm.some.module \
  --object vllm.some.module:SomeClass
```

### P2.1 Request ID and Mapping

Implement the smallest adapter that records request lifecycle and maps
AgentMon request ids to vLLM request ids. Update reports and tests.

Current foundation:

- `agentmon.adapters.inference.vllm_018.version` gates vLLM `0.18.0` / `0.18.0+...`.
- `agentmon.adapters.inference.vllm_018.events.VllmP2EventSink` emits
  `vllm.request.received`, `vllm.request.finished`,
  `vllm.adapter.capability`, and `vllm.adapter.error` events.
- `report.json` includes a `vllm_p2` section when those events are present.
- `summary.json` includes compact P2 request/correlation/error counts.
- P2 JSONL events are excluded from P0 SQLite consistency checks, the same way
  P1 adapter events are excluded.

Runtime hook status for P2.1:

- The opt-in runtime loader binds request entry/completion events to
  `AsyncLLM`, `LLMEngine`, and `EngineCore` when those classes exist.
- Verify direct `X-AgentMon-Request-Id` propagation with a live request on the
  target service.
- Add server integration tests for direct and heuristic mapping quality.

### P2.2 Request Latency and Token Metrics

Add queue/prefill/decode/TTFT/E2E and prompt/cached/computed/recomputed/generation
token metrics. Keep per-token events disabled by default.

Current foundation:

- `VllmP2EventSink` can emit `vllm.request.queued`,
  `vllm.request.first_scheduled`, `vllm.request.prompt_cache_accounted`,
  optional `vllm.request.prefill_completed`,
  `vllm.request.first_token`, `vllm.request.first_output`,
  `vllm.request.engine_finished` (including `last_token_ns` when observed),
  `vllm.request.preempted`, and
  `vllm.request.finished`.
- `report.json.vllm_p2.latency` derives queue, exact prefill,
  prefill-to-first-token proxy, decode, exact TPOT, TTFT, and E2E summaries only when the
  required timestamp pairs are available.
- `report.json.vllm_p2.tokens` summarizes prompt, cached prompt, computed
  prefill, recomputed prompt, generation tokens, and preemption count with
  sample counts and coverage.
- `summary.json` and `agentmon aggregate` expose compact P2 latency/token
  fields. Missing values remain `null` / `N/A`; they are not converted to zero.

Runtime hook status for P2.2:

- The opt-in runtime loader emits request received, queued, first scheduled,
  EngineCore first token/finish, API first output/finish, cancelled, and
  preempted events from the vLLM V1 API, EngineCore, Scheduler, and output
  boundaries when the fields are available.
- Validate token field values against a target-server request smoke.
- Add server integration tests for successful, cancelled, and preempted
  requests.

### P2.3 KV Pool and NPU Attribution

Add KV pool configuration extraction, KV block/preemption events when available,
and request-window NPU attribution quality labels.

Current foundation:

- `agentmon.adapters.inference.vllm_018.kv.calculate_kv_pool()` implements the documented
  bytes-per-block and allocated-pool formula with preserved inputs and formula
  version.
- `VllmP2EventSink` can emit `vllm.kv.snapshot`,
  `vllm.kv.block_event`, and `vllm.request.npu_attribution`.
- `report.json.vllm_p2.kv_cache` reports prefix-cache status/hit rate,
  cached/recomputed prompt ratios, KV state counts, KV block event totals,
  KV usage ratio samples, and KV pool inputs/bytes.
- `report.json.vllm_p2.npu_attribution` reports request attribution quality
  counts and sampled utilization/power totals.
- `summary.json` and `agentmon aggregate` expose compact KV pool/state and NPU
  attribution fields.

Performance attribution must separate host-wide collection from task target
devices. `npu-smi` samples may include devices not used by the validation vLLM
service, so compact performance summaries should pass explicit physical device
ids when known:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_perf_summary.py" \
  "$RUN_DIR" \
  --p2-events "$RUN_DIR/p2-events-for-run.jsonl" \
  --ascend-devices 0,1,2,3 \
  --format table
```

For per-step runtime load analysis, use:

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$RUN_DIR" \
  --ascend-devices 0,1,2,3 \
  --format json
```

The sequence output preserves run-level reports and adds step/model/tool window
attribution: agent-side CPU time and CPU/wall ratio, tool subprocess CPU/PSS/I/O
deltas, vLLM process CPU/PSS during model windows, target/host NPU HBM and AI
Core utilization windows, P2 request latency, token counts, KV usage ratios,
block allocation/free/eviction counts, and prefix-cache status/hit-rate
semantics. Model items include the same canonical request identity fields as
report rows, and `step_sequence.json.p2_identity_audit` summarizes matched P1
model spans, unmatched P1 spans, and orphan P2 requests. After
`agentmon import-p2`, run-local imported P2 events are used automatically. Pass
`--p2-events` only for a pre-sliced per-run P2 JSONL. Pass the full stream as
`--service-p2-events` to overlay global KV/capability state; the result is
explicitly labeled shared and not request-attributed.

The runtime hook emits `visible_devices` metadata from
`ASCEND_RT_VISIBLE_DEVICES` / `ASCEND_VISIBLE_DEVICES` when available, but
explicit CLI devices have priority. Without reliable target-device attribution,
target NPU metrics remain `null` / `N/A`; host-wide maxima are reported under
`ascend_host_*` fields. P2 service-stream token totals must not backfill Agent
tokens unless `--p2-service-exclusive` is set for an isolated request window.

Runtime hook status for P2.3:

- The opt-in runtime loader emits KV snapshots from `Scheduler` and
  `KVCacheManager.make_prefix_cache_stats`. `get_computed_blocks` and KV block
  allocation/free/eviction hooks remain opt-in; enable them before service
  startup for a full-metric experiment and record the added overhead.
- Validate target-server cached/uncached/computed-prefill values and independently
  validate scheduler-evidenced recomputed tokens under real request traffic.
- Validate request-window NPU attribution against real sampled vLLM concurrency.

### Prefix Cache Configuration Gate

Do not hard-code a negative CLI spelling. The target vLLM 0.18 installation is
the authority: `agentmon_server_preflight.py` imports its `EngineArgs`, inspects
the argparse action for `enable_prefix_caching`, and reports every supported
option in `prefix_cache_cli_options`.

For an experiment that requires caching disabled:

1. Put the exact service launch command in a mode-0600 text file, using the
   negative option reported by the target environment.
2. Start the service with
   `AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE=disabled`.
3. Send the same sufficiently long prompt twice.
4. Re-run preflight with `--expect-prefix-cache disabled`,
   `--vllm-launch-command-file`, `--events-path`, and `--require-runtime-p2`.

The second check requires a runtime Scheduler capability, a successfully
installed async token-boundary hook when async scheduling is active, at least
two requests with cache accounting, and `cached_prompt_tokens == 0` for every
observed request. A positive cache hit makes the check fail. Capability events
record `prefix_cache_enabled`, its config source, the expectation, and the
validation result.

Streaming `RequestOutputKind.DELTA` chunks are accumulated per request;
CUMULATIVE and FINAL_ONLY outputs use their cumulative size. EngineCore token
counts remain authoritative and API/Engine disagreement is retained as
`generation_tokens_consistency=mismatch` instead of silently overwriting the
engine value.

### P2.4 Prefix Cache Experiment Planner

Add the non-mutating `agentmon experiment plan prefix-cache` helper and report
comparisons. Do not restart or reconfigure vLLM automatically.

Current foundation:

```bash
agentmon experiment plan prefix-cache \
  --baseline-run <run-id> \
  --variant-config <path> \
  --format json
```

The planner reads existing AgentMon run reports plus a candidate variant config
and emits a checklist/matrix. It does not restart, reconfigure, or send requests
to vLLM.
