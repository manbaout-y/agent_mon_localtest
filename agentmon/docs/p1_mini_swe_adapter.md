# P1 mini-SWE Adapter

P1 adds exact mini-SWE-side spans without modifying `site-packages`.
The adapter interfaces are aligned with mini-SWE-agent `v2.4.3`:

P1 model/agent spans also include content-free workload structure: context
growth and prompt-token deltas, tool-result character/token estimates,
truncation/rewrite/summarization signals, LiteLLM transport attempts, semantic
phase, and action fan-out. Token estimates always carry an `estimated_*`
quality; provider usage remains the exact prompt-token source.

The model adapter can also apply an opt-in context-compression strategy before
each provider request. Compression changes only the request copy. The Agent's
in-memory history and serialized trajectory remain complete.

- `minisweagent.run.mini`
- `minisweagent.agents.default.DefaultAgent`
- `minisweagent.agents.interactive.InteractiveAgent`
- `minisweagent.models.litellm_model.LitellmModel`
- `minisweagent.environments.local.LocalEnvironment`

Use these class paths with mini-SWE:

```text
agentmon.adapters.agent_frameworks.mini_swe.agent.InstrumentedInteractiveAgent
agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel
agentmon.adapters.agent_frameworks.mini_swe.environment.InstrumentedMiniSweLocalEnvironment
```

The mini-SWE child process must be able to import `agentmon`. On the target
server, `examples/config.target.toml` first removes inherited `PYTHONPATH` and
`PYTHONHOME`, then sets a clean AgentMon-only `PYTHONPATH`:

```text
PYTHONPATH=/data/node0_disk3/chenyang/agentmon-ascend/src
```

The target config may use `${AGENTMON_REPO}/src`; AgentMon expands `${...}`
from the environment when loading TOML.

## Context Compression

The target example uses this policy:

```toml
[context_compression]
enabled = true
trigger_strategy = "token_ratio"
strategy = "model_summary"
plugin_modules = []
trigger_ratio = 0.80
target_ratio = 0.20
window_tokens = 0
estimator_chars_per_token = 4.0
trigger_options_json = '{}'
strategy_options_json = '{"min_recent_groups":1}'
```

`window_tokens=0` resolves from `model.max_model_len`. The built-in mini-SWE
mapping pins the leading system/developer messages and initial task, and groups
an assistant tool call with its following tool/observation results. The generic
strategy sees only ordered groups and costs; it contains no mini-SWE role names.

`model_summary` is the default algorithm. It sends older complete groups to a
Summary Provider, retains required instructions, the initial task, and the
newest complete interaction group, then inserts the returned continuation
summary before the retained recent group. The mini-SWE provider reuses the
current model by default. The summary request has its own request ID,
`request_purpose=context_summary`, P1 span, and P2 correlation headers.

`recent_groups` remains available only as an explicitly selected deterministic
pruning strategy for comparison runs. Additional compression algorithms register through
`register_context_compression_strategy()`. A configured module is imported
from `plugin_modules`, `strategy` selects its registered name, and
`strategy_options_json` passes private options. This supports semantic summary,
tool-output deduplication, retrieval memory, or model-specific tokenization
without editing this adapter or Runtime Core.

Trigger policies are independently pluggable through
`register_context_compression_trigger()`. `trigger_strategy` selects when to
compress and `trigger_options_json` carries policy-specific options. The built-in
`token_ratio` policy implements the configured 80% threshold; a plugin may use
turn count, low-progress evidence, tool-output growth, or an explicit marker.

The adapter emits `context_compression.decision` and copies content-free fields
onto `model_request.start`: status, trigger strategy, compression strategy,
threshold/target, estimated tokens
before and after, removed counts, compression start/end/duration, summary input,
output, cached and uncached tokens, summary model/request ID, measurement
quality, limitations, and prohibited conclusions. It never writes prompt text,
generated summary text, or
removed tool output into AgentMon events. A plugin error is `ERROR_BYPASS` and
forwards the original context; it does not silently fall back to truncation.

## Preflight

Check that mini-SWE exposes class-path options:

```bash
"$MINI_SWE_BIN" --help | grep -E 'agent-class|model-class|environment-class'
```

Check imports from the mini-SWE Python environment:

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

Check MRO/signatures if imports fail later at runtime:

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
    print("class:", cls)
    print("MRO:", cls.__mro__)
    print("__init__:", inspect.signature(cls.__init__))
PY
```

Events are appended to the same `events.jsonl` as the P0 monitor via:

```text
AGENTMON_RUN_ID
AGENTMON_RUN_DIR
AGENTMON_EVENTS_PATH
AGENTMON_ROOT_SPAN_ID
```

`agentmon run` injects these variables into the child environment. The adapters
read them with `AgentMonContext.from_env()` and append JSONL events directly to
`AGENTMON_EVENTS_PATH`. They do not use stdout, HTTP, or a Unix socket. The
trajectory `info.agentmon` block is extra metadata written during
`serialize()`; it is not the primary event transport.

The JSONL writer opens the file with `O_APPEND` and emits each event with one
`os.write()` call. No Python buffered stream is used, and `fsync()` is not
performed for each event by default. This is what allows the P0 monitor process
and the mini-SWE P1 process to append to the same `events.jsonl` on normal local
Linux filesystems. Revalidate the append semantics before using NFS or another
network filesystem for the output root.

Adapter write failures are caught and printed to child stderr. They should not
change mini-SWE behavior.

Span payloads include:

```text
trace_id
span_id
parent_span_id
step_id
start_wall_ns / end_wall_ns
start_mono_ns / end_mono_ns
duration_ns
source_pid
source_starttime_ticks
sequence
status
error_type
error_category
error_message_hash
error_message_length
```

Parent relationship:

```text
run span: span_id = run:<run-id>, trace_id = <run-id>
run
└─ agent_step
   ├─ model_request
   └─ tool_call
```

`parent_span_id` always points to another span id. It is `null` only on the
synthetic root run span. `sequence` is local to the adapter process; use
`start_mono_ns`, `source_pid`, and `sequence` for ordering, and use `event_id`
for de-duplication.

Model request ids are generated by AgentMon before request execution as
`agentmon_model_request_id`. If the model query signature explicitly supports
custom headers, the id is also sent as `X-AgentMon-Request-Id`. If headers are
not supported, P1 silently records the id in events and leaves the LiteLLM call
unchanged.

P1 event types include:

- `run.start` / `run.end`
- `agent_step.start` / `agent_step.end` / `agent_step.error`
- `model_request.start` / `model_request.end` / `model_request.error`
- `tool_call.start` / `tool_call.process_start` / `tool_call.end` / `tool_call.error`

Native local-environment tool end events include PID, PGID, process starttime,
command hash, return code, output byte counts, timeout limit/signal/type, and a
`RUSAGE_CHILDREN` user/system/total CPU delta. The rusage delta catches short
commands that periodic procfs sampling can miss. RSS/PSS and read/write byte
deltas still come from PID/PGID procfs samples and therefore carry independent
sample coverage/quality. `returncode != 0` is `tool_status=nonzero_return`;
instrumentation failure and timeout are separate statuses.

The P1 tool process-group observer defaults to a 0.1 second interval. Override
it with `AGENTMON_TOOL_SAMPLE_INTERVAL_SECONDS`; set it to `0` to keep only the
initial sample. Lower intervals improve short-tool memory coverage but add
procfs/smaps overhead, so record the value with experiment metadata.

mini-SWE's `Submitted` exception is control flow, not an instrumentation error.
When it closes a step or tool span, AgentMon emits `*.end` with
`status=submitted`, `terminal_reason=submitted`, and
`submission_control_flow=true`, then re-raises so mini-SWE can keep its native
control-flow semantics.

Privacy defaults:

- Prompt and response text are not written to AgentMon events.
- Full shell command text is not written to AgentMon events.
- Tool stdout/stderr are not written to AgentMon events.
- Events contain hashes, counts, return codes, byte counts, PID/PGID, and
  timing metadata.
- Raw exception messages are not written to P1 events by default. P1 stores
  error type/category plus message hash and byte length.

Command hashes are SHA-256 over argv, NUL-separated. They are unsalted by
default. Set `privacy.command_hash_salt_file` to enable a local salt, which is
passed to P1 through `AGENTMON_COMMAND_HASH_SALT`.

mini-SWE's own trajectory may still contain full commands and model messages.
AgentMon event privacy does not rewrite raw trajectory content.

## Success Checks

After a P1 run:

```bash
export RUN_DIR="$AGENTMON_HOME/runs/<run-id>"

grep -o '"event_type":"[^"]*"' "$RUN_DIR/events.jsonl" | sort | uniq -c
grep -o '"span_type":"[^"]*"' "$RUN_DIR/events.jsonl" | sort | uniq -c
```

Expected P1 signals include:

- `run.start` / `run.end`
- `agent_step.start` / `agent_step.end`
- `model_request.start` / `model_request.end`
- `tool_call.start` / `tool_call.process_start` / `tool_call.end`
- `span_type` values: `run`, `agent_step`, `model_request`, `tool_call`

Then regenerate/report:

```bash
"$AGENTMON_PY" -m agentmon report \
  --config "$AGENTMON_REPO/examples/config.target.toml" \
  <run-id>
```

The report should contain `P1 Spans` and `Tool Attribution`.
For smoke automation, prefer `summary.json` / `agentmon summary --format json`
and its `task_success`, `data_integrity_pass`,
`instrumentation_error_count`, `command_nonzero_count`, and
`submission_control_flow_count` fields. Do not fail a smoke run merely because a
command returned nonzero; nonzero commands are counted separately from true
instrumentation errors.

P1 does not provide request-level vLLM queue/prefill/decode or exact KV cache
bytes. Those are P2 scope.

## Storage and Abandoned Spans

`events.jsonl` is the source of truth. SQLite is a rebuildable index/cache and
does not receive P1 adapter events in real time.

Reports compare JSONL P0 events with SQLite P0 events. They do not compare
JSONL total events with SQLite total events, because P1 adapter events are
JSONL-only in real time.

If a process exits after writing a `*.start` event but before a matching
terminal event, reports preserve the span as `abandoned` and estimate duration
to the run end time when available. Terminal events include `*.end`, `*.error`,
`*.cancelled`, and `*.timeout`; error termination closes the span and should not
be counted as open or orphaned.
