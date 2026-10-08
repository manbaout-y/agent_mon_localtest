# V4.1 client / KV research joint integration v1

## What changes

- Independent `examples/models/local_deepseek_v41.yaml` and registry; existing
  Qwen profile and generic InstrumentedLitellmModel are unchanged.
- Opt-in `ProfiledLitellmModel` promotes profile headers into call kwargs, then
  delegates to the existing instrumentation and mini-SWE model. It adds no
  thinking parameter, no model-name branch and no alternate inference client.
- Independent target TOML for the already-running localhost:8800 service.
- A sequential, explicit `--live` client smoke; no service/NPU launcher.
- kv_ana consumes content-free wire sidecars and explicit runtime bindings.
  Production AgentMon core does not import kv_ana. Only the joint smoke script
  optionally imports its request-sidecar function via `--kvreuse-root`.

## Exact source semantics (client versions pinned for acceptance)

mini-SWE 2.4.3 `models/__init__.py:get_model` selects the configured class.
`models/litellm_model.py:LitellmModel._query` calls `litellm.completion` with
`self.config.model_kwargs | kwargs`; query kwargs take precedence. It also
passes a bash tool schema. `query` prepares messages, retries, calculates cost
and validates tool calls. Plain answer text is NOT sufficient for this model
class; summary requests use the existing request-local text-summary adapter.

LiteLLM 1.90.1 `utils.py:get_optional_params` preserves extra_body for
OpenAI-compatible providers. The profile uses:

```yaml
extra_body:
  chat_template_kwargs:
    thinking: false
```

Acceptance intercepts the HTTP request after real serialization, not the
completion function. It requires top-level `chat_template_kwargs` on wire,
including summary and retry calls, and absence of an `extra_body` wrapper.

`LitellmModelConfig.litellm_model_registry` is model/cost metadata, loaded by
`litellm.utils.register_model`. LiteLLM 1.90.1 `utils.py:get_max_tokens`
(lines 5434–5486 in the wheel) documents output tokens and prefers
`max_output_tokens`, falling back to legacy `max_tokens`. Do not use legacy
max_tokens alone as a context-window setting. The new registry separates:

- `max_input_tokens=262144`: supplied serving window ceiling, not guaranteed
  usable prompt length with generation enabled;
- `max_output_tokens=4096`, legacy `max_tokens=4096`: conservative **client policy**,
  not a claim about the model's maximum generation capability;
- explicit HTTP completion limit from profile `model_kwargs.max_tokens=4096`.

Input + generation + actual chat-template/control tokens must fit the serving
window. Registry metadata does not itself resize server capacity or enforce
that combined constraint. Compression/window settings are independent.

## Timeouts and capability boundaries

The model profile sets `timeout=600`; mini-SWE retries can multiply elapsed time.
The small live smoke disables SDK retries and limits mini-SWE attempts to one.
The target's `vllm.request_timeout_seconds=2` stays a short probe timeout. The
existing gated HTTP provider also uses that field; this profile does NOT use
that provider and must not be treated as fixing its timeout policy.

P2 remains version-gated for vLLM 0.18.0. Do not enable it for V4.1 until the
actual runtime has a separately validated adapter. Do not apply dense K/V byte
formulas to unknown V4.1 state types. `prefix_caching=unknown` intentionally
does not assert a serving feature. bf16 compute dtype is not W4 weight storage
or a claim about cache dtype. No model path is required on the Mac.

## Run from the joint bundle

Extract into a new directory; do not overwrite your existing installation.

```bash
tar -xzf agentmon_kvreuse_v41_joint_v1.tar.gz
cd agentmon_kvreuse_v41_joint_v1
export AGENTMON_REPO="$PWD/agentmon"
export KVREUSE_REPO="$PWD/kv_ana"
# Set PY to your existing mini-SWE Python 3.11 environment.
# It must contain mini-swe-agent 2.4.3, litellm 1.90.1, openai 2.44.0,
# PyYAML, and numpy. No package installation is performed by these scripts.

# Offline real-client wire validation, all network connections blocked:
"$PY" "$KVREUSE_REPO/scripts/validate_v41_joint_client.py" \
  --agentmon-root "$AGENTMON_REPO" --output ./offline-client-check

# Only on the server, with the existing service already running; C=1:
"$PY" "$AGENTMON_REPO/scripts/deepseek_v41_client_smoke.py" \
  --live --kvreuse-root "$KVREUSE_REPO" --output ./v41-client-smoke
```

The live smoke asks for exactly `printf AGENTMON_V41_SMOKE_OK`, validates the
returned tool call before executing that single literal command in the output
directory, submits its observation, and verifies a second valid model response.
No arbitrary model-generated shell commands are accepted. A different command
is a smoke failure, not evidence the model is generally incompatible.

Outputs: `client_smoke.json`, `events.jsonl` and optional
`request_evidence.jsonl`. No full prompt or K/V tensor is stored. With the joint
sidecar enabled, thinking=false is checked at the real HTTP boundary. Service
and engine IDs remain unavailable until actual runtime binding is supplied.

This is a bounded model/tool client closure, not an entire mini-SWE benchmark,
not P0/P2/full AgentMon performance acceptance, and not state capture. It does
not run metrics/NPU samplers. Subsequent AgentMon batch runs may reuse this
profile and current batch runtime after the client smoke passes; do not reuse
the old Qwen service-start script or the V4 state-intervention patch.
