from __future__ import annotations

from agentmon.contracts import AdapterCapability


class AdapterCapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, AdapterCapability] = {}

    def register(self, capability: AdapterCapability, *, replace: bool = False) -> None:
        if capability.name in self._items and not replace:
            raise ValueError(f"adapter capability already registered: {capability.name}")
        self._items[capability.name] = capability

    def list(self, *, domain: str = "all") -> list[AdapterCapability]:
        items = sorted(self._items.values(), key=lambda item: (item.domain, item.name))
        if domain == "all":
            return items
        return [item for item in items if item.domain == domain]

    def catalog(self, *, domain: str = "all") -> list[dict[str, object]]:
        return [item.to_dict() for item in self.list(domain=domain)]


ADAPTER_CAPABILITIES = AdapterCapabilityRegistry()
ADAPTER_CAPABILITIES.register(
    AdapterCapability(
        name="mini_swe",
        domain="agent_framework",
        namespace="agentmon.adapters.agent_frameworks.mini_swe",
        legacy_namespace="agentmon.adapters.mini_swe",
        level="L2",
        status="available",
        capabilities=(
            "trajectory_import",
            "agent_step_events",
            "model_request_events",
            "tool_call_events",
            "tool_pid_attribution",
            "runtime_model_gate_opt_in",
            "runtime_tool_pool_opt_in",
            "context_compression_strategy_opt_in",
        ),
    )
)
ADAPTER_CAPABILITIES.register(
    AdapterCapability(
        name="vllm_018",
        domain="inference",
        namespace="agentmon.adapters.inference.vllm_018",
        legacy_namespace="agentmon.adapters.vllm_018",
        level="P2",
        status="available",
        capabilities=(
            "engine_request_events",
            "first_token_timing",
            "prefill_decode_token_accounting",
            "kv_cache_accounting",
            "scheduler_step_observation",
            "moe_logical_routing_opt_in",
        ),
        optional_dependencies=("vllm==0.18.*",),
    )
)
ADAPTER_CAPABILITIES.register(
    AdapterCapability(
        name="npu_process_mapping",
        domain="resource",
        namespace="agentmon.sampling.npu_mapping",
        legacy_namespace=None,
        level="R1",
        status="available",
        capabilities=(
            "process_to_npu_mapping",
            "npu_smi_process_evidence",
            "visible_device_scope",
            "rank_device_hinting",
        ),
        optional_dependencies=("npu-smi",),
    )
)


def adapter_capability_catalog(*, domain: str = "all") -> list[dict[str, object]]:
    return ADAPTER_CAPABILITIES.catalog(domain=domain)
