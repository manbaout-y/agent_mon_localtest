from __future__ import annotations

from pathlib import Path
import json
import re
import shlex
from typing import Any

from agentmon.contracts import AdapterDescriptor, AgentFrameworkAdapter, TelemetryCapability


MINI_SWE_P1_CLASSES = {
    "--agent-class": "agentmon.adapters.agent_frameworks.mini_swe.agent.InstrumentedInteractiveAgent",
    "--model-class": "agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel",
    "--environment-class": "agentmon.adapters.agent_frameworks.mini_swe.environment.InstrumentedMiniSweLocalEnvironment",
}


class MiniSweAgentFrameworkAdapter:
    name = "mini_swe"

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            name=self.name,
            domain="agent_framework",
            namespace="agentmon.adapters.agent_frameworks.mini_swe",
            capabilities=(
                TelemetryCapability(
                    name="agent_step_events",
                    quality="exact",
                    required_fields=("step_id", "start_mono_ns", "end_mono_ns"),
                ),
                TelemetryCapability(
                    name="model_request_events",
                    quality="exact",
                    required_fields=("span_id", "request_start", "request_finish"),
                ),
                TelemetryCapability(
                    name="tool_call_events",
                    quality="exact",
                    required_fields=("span_id", "command", "exit_code"),
                ),
                TelemetryCapability(
                    name="context_compression_decisions",
                    quality="estimated_or_plugin_defined",
                    required_fields=(
                        "context_compression_status",
                        "context_compression_strategy",
                        "context_compression_measurement_quality",
                    ),
                ),
            ),
        )

    def prepare_command(self, command: str, *, mode: str = "auto") -> tuple[str, str]:
        return configure_p1_command(command, mode)

    def extract_request_ids(self, trajectory_path: str) -> list[str]:
        return extract_trajectory_response_ids(Path(trajectory_path))


def configure_p1_command(command: str, mode: str) -> tuple[str, str]:
    if mode == "off":
        return command, "disabled"
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        if mode == "required":
            raise ValueError(f"cannot parse mini-SWE command for P1 injection: {exc}") from exc
        return command, "parse_unavailable"
    recognizable_names = {"mini", "mini-swe-agent", "mini_swe_agent", "minisweagent"}
    recognizable = re.search(r"(?:mini[-_]?swe|minisweagent)", command, flags=re.IGNORECASE) or any(
        Path(token).name.lower() in recognizable_names for token in tokens[:8]
    )
    if not recognizable:
        if mode == "required":
            command_head = " ".join(shlex.quote(token) for token in tokens[:4]) or "<empty>"
            raise ValueError(
                "--p1-mode required needs a directly recognizable mini-SWE executable "
                f"(mini, mini-swe-agent, mini_swe_agent, or minisweagent); detected command head: {command_head}. "
                "If the agent is hidden behind a wrapper, expose the mini executable in the command template "
                "or pass the AgentMon --agent-class/--model-class/--environment-class options inside the wrapper."
            )
        return command, "not_mini_swe"
    shell_operators = {"|", "||", "&&", ";", "&", ">", ">>", "<", "<<", "2>", "2>>"}
    if any(token in shell_operators or re.match(r"^\d*(?:>>?|<<?).+", token) for token in tokens):
        if mode == "required":
            raise ValueError(
                "P1 auto-injection does not support compound, piped, or redirected shell commands in "
                "--p1-mode required. Use a directly recognizable mini-SWE command template, or add the "
                "AgentMon adapter class options manually before shell composition."
            )
        return command, "compound_command_not_injected"
    appended: list[str] = []
    for flag, class_path in MINI_SWE_P1_CLASSES.items():
        if flag not in tokens:
            appended.extend([flag, class_path])
    if not appended:
        return command, "already_configured"
    return f"{command} {shlex.join(appended)}", "injected"


def adapter() -> AgentFrameworkAdapter:
    return MiniSweAgentFrameworkAdapter()


def extract_trajectory_response_ids(path: Path) -> list[str]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    messages = data.get("messages") or data.get("trajectory") or []
    if not isinstance(messages, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        response_id = response_id_from_message(message)
        if response_id and response_id not in seen:
            seen.add(response_id)
            ids.append(response_id)
    return ids


def response_id_from_message(message: dict[str, Any]) -> str | None:
    extra = message.get("extra")
    if isinstance(extra, dict):
        response = extra.get("response")
        if isinstance(response, dict) and response.get("id") not in {None, ""}:
            return str(response["id"])
        if extra.get("response_id") not in {None, ""}:
            return str(extra["response_id"])
    for key in ("response_id", "api_request_id", "server_request_id"):
        if message.get(key) not in {None, ""}:
            return str(message[key])
    return None
