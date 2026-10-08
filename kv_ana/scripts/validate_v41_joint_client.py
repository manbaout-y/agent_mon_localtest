#!/usr/bin/env python3
"""Offline-only: real client serialization, synthetic HTTP responses, no sockets.

Not a model/runtime test. Both independent repositories must be available.
"""
from __future__ import annotations

import argparse
import importlib.util
from copy import deepcopy
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agentmon-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    required = {"mini-swe-agent": "2.4.3", "litellm": "1.90.1", "openai": "2.44.0"}
    installed = {name: version(name) for name in required}
    if installed != required:
        raise RuntimeError(f"target client versions required: {required}; found {installed}")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be new/empty")
    args.output.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(args.agentmon_root / "src"), str(Path(__file__).resolve().parents[1] / "src")]
    os.environ.update(LITELLM_LOCAL_MODEL_COST_MAP="true", HF_HUB_OFFLINE="1",
                      MSWEA_SILENT_STARTUP="1", MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT="2",
                      MSWEA_GLOBAL_CONFIG_DIR=str(args.output / "miniswe-config"))
    # Block all actual socket connections, including dependency side requests.
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden in offline validation")):
        import httpx
        import yaml
        import litellm
        from minisweagent.models import get_model
        from minisweagent.models.litellm_model import LitellmModel
        from agentmon.config import load_config
        from agentmon.context_compression import ContextSummaryRequest
        from agentmon.adapters.agent_frameworks.mini_swe.model import build_model_summary_provider
        from agentmon.adapters.agent_frameworks.mini_swe.context import AgentMonContext
        from agentmon.adapters.agent_frameworks.mini_swe.event_sink import AdapterEventSink
        from agentmon.adapters.agent_frameworks.mini_swe.context_compression import MiniSweContextCompressor
        from kvreuse_b5.state_research.request_bridge import request_from_wire, bind_state_captures

        root = args.agentmon_root
        profile = yaml.safe_load((root / "examples/models/local_deepseek_v41.yaml").read_text())["model"]
        profile["litellm_model_registry"] = str(root / "examples/models/deepseek_v41_registry.json")
        cfg = load_config(root / "examples/config.deepseek_v41.toml", env={"AGENTMON_REPO": str(root)})
        sink = AdapterEventSink(AgentMonContext(run_id="offline-v41", run_dir=args.output,
                                               events_path=args.output / "events.jsonl"))
        model = get_model(config=profile)
        model.agentmon_sink = sink
        model.agentmon_context_compressor = MiniSweContextCompressor(cfg)
        records, wires, responses = [], [], []
        phase = "agent"
        fail_next = False

        def intercept(client, request, **kwargs):
            nonlocal fail_next
            if request.url.path != "/v1/chat/completions":
                raise AssertionError(f"unexpected HTTP path {request.url.path}")
            body = json.loads(request.content)
            headers = dict(request.headers)
            wires.append({"phase": phase, "body": body, "headers": headers,
                          "timeout": request.extensions.get("timeout")})
            if phase != "qwen":
                records.append(request_from_wire(
                    body, headers, attempt_id=f"http-{len(wires)}", request_purpose=phase,
                    model_family="DeepSeek-V4.1", context_version=1,
                    source="synthetic"))
            if fail_next:
                fail_next = False
                return httpx.Response(503, json={"error": {"message": "synthetic retry"}}, request=request)
            message = {"role": "assistant", "content": "Synthetic response"}
            if body.get("tool_choice") != "none":
                message["tool_calls"] = [{"id": "call-test", "type": "function", "function": {
                    "name": "bash", "arguments": json.dumps({"command": "printf AGENTMON_V41_SMOKE_OK"})}}]
            response = {"id": f"response-{len(wires)}", "object": "chat.completion", "created": 1,
                        "model": body["model"], "choices": [{"index": 0, "message": message,
                        "finish_reason": "stop" if body.get("tool_choice") == "none" else "tool_calls"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}}
            responses.append(response)
            return httpx.Response(200, json=response, request=request)

        with patch.object(httpx.Client, "send", intercept):
            messages = [{"role": "user", "content": "Synthetic client compatibility task"}]
            answer = model.query(messages)
            # Exercise the real action parser and observation formatter; no model-
            # generated shell command is executed by this offline validator.
            actions = answer["extra"]["actions"]
            observations = model.format_observation_messages(answer, [{"output": "AGENTMON_V41_SMOKE_OK", "returncode": 0, "exception_info": ""}])
            model.query(messages + [answer] + observations)
            phase = "context_summary"
            provider = build_model_summary_provider(
                target=LitellmModel.query.__get__(model),
                base_kwargs={"extra_headers": {}}, sink=sink, run_id="offline-v41",
                step_id="summary", parent_model_call_id="parent-call", model_name="deepseek-v41")
            summary = provider(ContextSummaryRequest(context=messages, instruction="Summarize", target_tokens=64))
            phase = "agent"
            retry_start = len(wires)
            fail_next = True
            model.query(messages)
            retry_wires = wires[retry_start:]
            phase = "qwen"
            qwen = {"model_name": "hosted_vllm/qwen3-coder-30b", "cost_tracking": "ignore_errors",
                    "model_kwargs": {"api_base": "http://127.0.0.1:8000/v1", "api_key": "not-needed", "drop_params": True}}
            get_model(config=qwen).query(messages)
            old_qwen_body = deepcopy(wires[-1]["body"])
            qwen["model_class"] = "agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel"
            get_model(config=qwen).query(messages)
        sink.close()
        v41 = [w for w in wires if w["phase"] != "qwen"]
        agent = [w for w in v41 if w["phase"] == "agent"]
        summaries = [w for w in v41 if w["phase"] == "context_summary"]
        registry = litellm.model_cost["deepseek-v41"]
        # A synthetic binding tests the consumer but never pretends to be P2.
        b = {"service_run_id": "synthetic-service", "engine_request_id": "synthetic-engine",
             "request_attempt_id": records[0]["request_id"], "source": "synthetic"}
        c = {"capture_id": "synthetic-capture", "service_run_id": b["service_run_id"],
             "engine_request_id": b["engine_request_id"], "model_family": "DeepSeek-V4.1",
             "context_version": 1, "runtime_identity": {"kind": "synthetic"}, "source": "synthetic"}
        checks = {
            "FINAL_HTTP_THINKING_FALSE": all(w["body"].get("chat_template_kwargs") == {"thinking": False} for w in v41),
            "EXTRA_BODY_FLATTENED": all("extra_body" not in w["body"] for w in v41),
            "OUTPUT_CAP": all(w["body"].get("max_tokens", w["body"].get("max_completion_tokens")) == 4096 for w in agent),
            "SUMMARY_OUTPUT_CAP": len(summaries) == 1 and summaries[0]["body"].get("max_tokens", summaries[0]["body"].get("max_completion_tokens")) == 64,
            "INFERENCE_TIMEOUT": all(w["timeout"]["read"] == 600 for w in v41),
            "CORRELATION_HEADERS": all(w["headers"].get("x-agentmon-model-call-id") for w in v41),
            "SUMMARY_DISTINCT_CALL": bool(summaries) and summaries[0]["headers"]["x-agentmon-model-call-id"] != agent[0]["headers"]["x-agentmon-model-call-id"],
            "RETRY_PAYLOAD_PRESERVED": len(retry_wires) >= 2 and all(w["body"] == retry_wires[0]["body"] for w in retry_wires),
            "QWEN_UNCHANGED": old_qwen_body == wires[-1]["body"] and "chat_template_kwargs" not in old_qwen_body,
            "MINISWE_ACTION_OBSERVATION": bool(actions) and observations[0]["role"] == "tool",
            "SUMMARY_TEXT_ACCEPTED": bool(summary.summary),
            "REGISTRY_SEPARATE_LIMITS": registry["max_input_tokens"] == 262144 and registry["max_output_tokens"] == 4096,
            "REQUEST_STATE_JOIN": bind_state_captures(records, [b], [c])[0]["status"] == "MATCHED",
            "NO_RUNTIME_FABRICATION": bind_state_captures(records, [], [c])[0]["status"] == "BLOCKED_REQUEST_MAPPING",
        }
        # Execute the exact server-smoke entrypoint with the same intercepted
        # transport. Only its fixed printf tool runs locally, never a real model.
        script_spec = importlib.util.spec_from_file_location(
            "v41_client_smoke", root / "scripts/deepseek_v41_client_smoke.py")
        smoke = importlib.util.module_from_spec(script_spec)
        script_spec.loader.exec_module(smoke)
        phase = "agent"
        with patch.object(httpx.Client, "send", intercept):
            rc = smoke.main(["--live", "--output", str(args.output / "mocked_server_smoke"),
                             "--kvreuse-root", str(Path(__file__).resolve().parents[1])])
        checks["SERVER_SMOKE_ENTRYPOINT_MOCKED"] = rc == 0
    report = {"status": "PASS" if all(checks.values()) else "FAIL", "versions": installed,
              "checks": checks, "http_attempts": len(wires), "real_model_executed": False,
              "runtime_capture": "NOT_IMPLEMENTED", "p2_v41": "NOT_VALIDATED"}
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "request_evidence.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    for name, ok in checks.items(): print(f"{name}={'PASS' if ok else 'FAIL'}")
    print(f"OUTPUT={args.output.resolve()}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
