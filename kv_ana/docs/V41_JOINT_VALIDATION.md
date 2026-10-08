# V4.1 joint-client validation — 2026-09-23

## Results

| Check | Actual result |
|---|---|
| kv_ana full pytest, Python 3.11 | 333 passed |
| kv_ana full pytest, Mac Python 3.9 | 333 passed |
| AgentMon full pytest, target client environment | 466 passed, 1 skipped, 17 subtests passed |
| AgentMon unittest discovery, Python 3.12 | 466 passed |
| Frozen framework synthetic smoke | PASS; 448 records, 224 summary groups; version 2.0.1 |
| State research offline synthetic smoke | All 10 acceptance checks PASS |
| Both source trees + scripts compileall | PASS |
| Real client HTTP-boundary validation | All 15 checks PASS |

Client acceptance used Python 3.11, mini-swe-agent 2.4.3, LiteLLM 1.90.1 and
OpenAI 2.44.0. Dependencies were obtained in a temporary environment, not added
to either repository or bundled. Only dependencies required by the model/client
path were installed: this was NOT a test of mini-SWE's interactive TUI or dataset
runner. The pre-existing AgentMon integration-test skip is not a server PASS.

Initial Mac regression failures were sandbox restrictions on the Python bytecode
cache and a local HTTP fixture socket. Reruns used a writable temporary cache
and approved loopback fixture access. No production code was changed for these.

## Wire acceptance checks

- FINAL_HTTP_THINKING_FALSE
- EXTRA_BODY_FLATTENED
- OUTPUT_CAP (4096) / SUMMARY_OUTPUT_CAP (64)
- INFERENCE_TIMEOUT (HTTP read timeout 600)
- CORRELATION_HEADERS / SUMMARY_DISTINCT_CALL
- RETRY_PAYLOAD_PRESERVED (synthetic 503 then success through actual retry path)
- QWEN_UNCHANGED (plain vs existing instrumented model, same request body)
- MINISWE_ACTION_OBSERVATION / SUMMARY_TEXT_ACCEPTED
- REGISTRY_SEPARATE_LIMITS
- REQUEST_STATE_JOIN / NO_RUNTIME_FABRICATION
- SERVER_SMOKE_ENTRYPOINT_MOCKED

All real socket connections were blocked during wire validation. Responses were
synthetic HTTP responses, not mock replacements for LiteLLM serialization. The
server-smoke entrypoint was also exercised with intercepted transport and its
single fixed printf tool; actual model behavior remains untested. The local
result is `outputs/v41_joint_client_v1/check3/validation.json`.

## Change boundary

Only new files were added. Existing Qwen configuration, AgentMon generic model
wrapper, P2/version gates, resource samplers and gated provider are unchanged.
kv_ana data/methods/metrics/execution/recipes/providers and historical numerical
definitions are unchanged. The new profile-specific subclass opts into existing
header injection; request controls still flow through mini-SWE/LiteLLM.

## Remaining acceptance

1. Real V4.1 tool-call support and C=1 model/tool closure on the running service.
2. Actual service/request-to-engine mapping and P2-compatible instrumentation.
3. V4.1 runtime source identity, state kinds/layouts and capture coverage.
4. Actual chat-template token IDs and chunk/state coordinate mapping.
5. Functional reuse, capture overhead and performance experiments.

No model, GPU/NPU, state intervention, runtime patch or remote server was started
or accessed in this implementation. A request-sidecar match is not state capture,
safe reuse, physical-memory attribution or evidence of latency improvement.
