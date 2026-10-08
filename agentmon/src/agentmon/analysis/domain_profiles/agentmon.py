from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentmon.analysis.profiles import SchemaProfile


def builtin_agentmon_profiles() -> dict[str, "SchemaProfile"]:
    # Import lazily to keep the generic profile detector independent from
    # AgentMon/vLLM event names and avoid an import cycle.
    from agentmon.analysis.profiles import SchemaProfile

    return {
        "agentmon": SchemaProfile(
            name="agentmon",
            source_signals=("mini_swe", "trajectory"),
            event_type_signals=("model_request.start", "model_request.end", "model_request.error"),
            event_prefix_signals=("model_request.", "tool_call.", "agent_step."),
            model_request_event_types=("model_request.start", "model_request.end", "model_request.error", "model_request.attempt.error"),
            p1_event_types=("model_request.start", "model_request.end", "model_request.error", "model_request.attempt.error"),
            p2_event_prefixes=(),
            p2_event_types=(),
            request_id_keys=("model_call_id", "client_request_id", "server_request_id", "response_id"),
            run_id_keys=("agentmon_run_id", "run_id"),
            task_id_keys=("task_id",),
            batch_id_keys=("agentmon_batch_id", "batch_id"),
            p2_eligibility_fields=(
                "api_request_id",
                "server_request_id",
                "engine_request_id",
                "vllm_request_id",
                "server_received_mono_ns",
                "server_finished_mono_ns",
                "client_response_received_mono_ns",
            ),
        ),
        "agentmon_vllm": SchemaProfile(
            name="agentmon_vllm",
            source_signals=("mini_swe", "trajectory", "vllm_p2"),
            event_type_signals=("model_request.start", "vllm.adapter.capability", "vllm.request.received"),
            event_prefix_signals=("model_request.", "vllm.request.", "vllm.moe.routing."),
            model_request_event_types=("model_request.start", "model_request.end", "model_request.error", "model_request.attempt.error"),
            p1_event_types=("model_request.start", "model_request.end", "model_request.error", "model_request.attempt.error"),
            p2_event_prefixes=("vllm.request.", "vllm.moe.routing."),
            p2_event_types=("vllm.kv.block_event",),
            request_id_keys=(
                "model_call_id",
                "client_request_id",
                "server_request_id",
                "response_id",
                "api_request_id",
                "engine_request_id",
                "vllm_request_id",
                "request_id",
            ),
            run_id_keys=("agentmon_run_id", "run_id"),
            task_id_keys=("task_id",),
            batch_id_keys=("agentmon_batch_id", "batch_id"),
            p2_eligibility_fields=(
                "api_request_id",
                "server_request_id",
                "engine_request_id",
                "vllm_request_id",
                "server_received_mono_ns",
                "server_finished_mono_ns",
                "client_response_received_mono_ns",
            ),
        ),
    }
