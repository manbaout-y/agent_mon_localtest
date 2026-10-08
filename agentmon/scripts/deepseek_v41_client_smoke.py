#!/usr/bin/env python3
"""Explicit, sequential client smoke against an already-running local endpoint.

Does not launch vLLM, collect tensors, install hooks or enable P2. Tool execution
is restricted to one literal harmless command, never arbitrary model output.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from unittest.mock import patch


SAFE_COMMAND = "printf AGENTMON_V41_SMOKE_OK"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true", help="explicitly authorize requests to local port 8800")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--kvreuse-root", type=Path, help="optional independent kv_ana tree for content-free request sidecar")
    args = p.parse_args(argv)
    if not args.live:
        p.error("no network by default; pass --live on the server")
    if args.output.exists() and any(args.output.iterdir()):
        p.error("output must be new/empty")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    if args.kvreuse_root:
        sys.path.insert(0, str(args.kvreuse_root / "src"))
    expected = {"mini-swe-agent": "2.4.3", "litellm": "1.90.1", "openai": "2.44.0"}
    actual = {name: version(name) for name in expected}
    if actual != expected:
        raise RuntimeError(f"validated client versions required: {expected}; found {actual}")
    os.environ.update(LITELLM_LOCAL_MODEL_COST_MAP="true", MSWEA_SILENT_STARTUP="1",
                      MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT="1", AGENTMON_REPO=str(root),
                      MSWEA_GLOBAL_CONFIG_DIR=str(args.output / "miniswe-config"))
    os.environ["AGENTMON_CONFIG"] = str(root / "examples/config.deepseek_v41.toml")
    import yaml
    import httpx
    from minisweagent.models import get_model
    from agentmon.config import load_config
    from agentmon.adapters.agent_frameworks.mini_swe.context import AgentMonContext
    from agentmon.adapters.agent_frameworks.mini_swe.event_sink import AdapterEventSink
    from agentmon.adapters.agent_frameworks.mini_swe.context_compression import MiniSweContextCompressor
    from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment

    profile = yaml.safe_load((root / "examples/models/local_deepseek_v41.yaml").read_text())["model"]
    profile["litellm_model_registry"] = str(root / "examples/models/deepseek_v41_registry.json")
    # Small explicit smoke cap; normal profile output policy remains 4096.
    profile["model_kwargs"]["max_tokens"] = 256
    profile["model_kwargs"]["max_retries"] = 0
    cfg = load_config(root / "examples/config.deepseek_v41.toml")
    sink = AdapterEventSink(AgentMonContext(run_id=f"v41-smoke-{uuid4().hex}",
                                          run_dir=args.output, events_path=args.output / "events.jsonl"))
    checks = {}
    wire_records = []
    original_send = httpx.Client.send

    def observed_send(client, request, **kwargs):
        # Only this sequential smoke process is instrumented; no global package
        # patch is installed. Do not send data anywhere except the selected API.
        if str(request.url) != "http://127.0.0.1:8800/v1/chat/completions":
            raise ValueError("unexpected smoke HTTP destination")
        from kvreuse_b5.state_research.request_bridge import request_from_wire
        payload = json.loads(request.content)
        if payload.get("chat_template_kwargs") != {"thinking": False}:
            raise ValueError("thinking=false missing on actual HTTP request")
        record = request_from_wire(payload, dict(request.headers), attempt_id=uuid4().hex,
                                   request_purpose="agent", model_family="DeepSeek-V4.1",
                                   context_version=sum(m["role"] == "tool" for m in payload["messages"]))
        wire_records.append(record)
        response = original_send(client, request, **kwargs)
        record["http_status"] = response.status_code
        if response.status_code == 200:
            record["agentmon"]["api_request_id"] = response.json().get("id")
        return response

    transport = patch.object(httpx.Client, "send", observed_send) if args.kvreuse_root else nullcontext()
    try:
        model = get_model(config=profile)
        model.agentmon_sink = sink
        model.agentmon_context_compressor = MiniSweContextCompressor(cfg)
        environment = InstrumentedMiniSweLocalEnvironment(cwd=str(args.output.resolve()), timeout=10)
        environment.agentmon_sink = sink
        messages = [{"role": "user", "content": (
            "Client compatibility check. Call the bash tool exactly once with command "
            f"{SAFE_COMMAND!r}. Do not add quotes or other shell commands. "
            "After seeing its observation, call exactly the same command once more.")}]
        with transport:
            first = model.query(messages)
        actions = first["extra"]["actions"]
        checks["FIRST_ACTION_VALID"] = len(actions) == 1 and actions[0]["command"] == SAFE_COMMAND
        if not checks["FIRST_ACTION_VALID"]:
            raise ValueError("model did not return the single allowed smoke action; nothing executed")
        observation = environment.execute(actions[0])
        checks["TOOL_EXECUTION"] = observation["returncode"] == 0 and observation["output"] == "AGENTMON_V41_SMOKE_OK"
        messages += [first] + model.format_observation_messages(first, [observation])
        transport = patch.object(httpx.Client, "send", observed_send) if args.kvreuse_root else nullcontext()
        with transport:
            second = model.query(messages)
        second_actions = second["extra"]["actions"]
        checks["SECOND_ACTION_VALID"] = len(second_actions) == 1 and second_actions[0]["command"] == SAFE_COMMAND
        if args.kvreuse_root:
            checks["WIRE_THINKING_AND_REQUEST_IDS"] = len(wire_records) >= 2
        # No second tool execution; two sequential model calls are sufficient.
        report = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
                  "concurrency": 1, "model_requests": 2,
                  "state_capture": "NOT_IMPLEMENTED", "p2": "NOT_ENABLED",
                  "full_agent_task_completion": "NOT_TESTED"}
    except Exception as exc:
        report = {"status": "FAIL", "checks": checks, "error_type": type(exc).__name__,
                  "state_capture": "NOT_IMPLEMENTED", "p2": "NOT_ENABLED"}
    finally:
        sink.close()
        if args.kvreuse_root:
            (args.output / "request_evidence.jsonl").write_text("".join(json.dumps(r) + "\n" for r in wire_records))
    (args.output / "client_smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    for key, value in checks.items(): print(f"{key}={'PASS' if value else 'FAIL'}")
    print(f"CLIENT_SMOKE={report['status']}")
    print(f"OUTPUT={args.output.resolve()}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
