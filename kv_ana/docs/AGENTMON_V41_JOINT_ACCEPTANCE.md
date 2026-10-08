# AgentMon × kv_ana: V4.1 joint acceptance v1

## Scope

AgentMon integration is based on the current `load_monitor_optimizer_harness`
working tree, including its later audit fixes, not the July batch-runtime tar.
This extension does not port the vLLM 0.18 P2 hook, modify reuse algorithms,
apply the historical V4 intervention patch, or start a model/runtime.

The two projects remain independent. AgentMon owns execution/request/resource
events; kv_ana owns state evidence and offline analysis. Exchange JSON records,
not package imports in either production core. Only joint validation needs both
projects on PYTHONPATH.

## Acceptance boundaries

1. **Client contract:** real mini-SWE 2.4.3 + LiteLLM 1.90.1 + OpenAI 2.44.0
   serialization, intercepted HTTP transport. Validate thinking=false, output
   budget, timeout, headers, normal/summary calls, and unchanged Qwen behavior.
   A canned response validates orchestration, never model quality or runtime.
2. **Agent closure:** a real server must produce parseable mini-SWE actions,
   tool results and a subsequent model call. C=1 is the initial envelope, not
   proof of concurrent scheduler correctness.
3. **State linkage:** explicit request-attempt -> service-run/engine-request
   binding. Synthetic bindings cannot authorize runtime-labelled captures.
4. **Runtime coverage:** V4.1 source/layout and actual producer/consumer evidence
   still required. HTTP success does not imply a state capture exists.

## Request evidence

`state_research.request_bridge.request_from_wire` consumes the **final serialized
HTTP JSON**, after context preparation, plus headers and an explicit attempt ID.
It records a control whitelist and message count. It does not retain prompts,
tool definitions, authentication, or full responses. Each retry has a distinct
attempt ID; logical model-call ID is shared across retries. Summary calls have
their own call/attempt identity and `request_purpose=context_summary`.

The caller supplies the context version of the effective request, not the
original trajectory. The record explicitly says server input token IDs and
state tensors are not captured. Text offsets/tokenization of original LifeMem
content cannot stand in for server chat-template tokenization.

`bind_state_captures` consumes request records, explicit runtime bindings and
capture descriptors. Bindings are scoped by service run and engine request;
no timestamp-nearest matching is permitted. Model and context must match.
Matched requests do not prove allocation ownership, completeness, or safe reuse.
Absent runtime evidence stays BLOCKED. The V4.1 runtime field names/ratios are
not inferred from V4.

## Data needed next (not collected by this extension)

- Actual V4.1 runtime/model/tokenizer/chat-template revisions and enabled paths.
- Server input IDs and explicit chunk/token/state-row mapping, including control
  tokens; research text storage remains explicit opt-in, separate from AgentMon.
- Explicit request/attempt/API/engine bridges and service lifetime identity.
- Typed objects, physical allocation identity, logical views, valid rows,
  layout/scales, producer/consumer graph and state content/context versions.
- Rank/device and clock-domain/sequence metadata before merging worker traces.
- Engram/offload objects classified from evidence, not automatically labelled KV.

## Accounting and measurement

Physical allocation bytes, logical view bytes, serialized artifact bytes,
algorithmic work and observed runtime durations are separate measurements.
Do not apply the traditional dense K/V pool formula to unknown V4.1 structures.
No latency is inferred from work or bytes. Full tensor capture is opt-in and
measured separately; metadata coverage is not a claim that all tensors were saved.

## Server sequence

Use the independent AgentMon V4.1 profile and its client contract validator first.
Do not use the old service launcher or intervention apply script for V4.1.
Keep runtime optimization/context compression off for the first simple smoke;
test summary compatibility separately before enabling compression experiments.
Keep P2 disabled until its runtime-specific compatibility is established.
No remote execution is performed by local validation.
