from __future__ import annotations

from typing import Any

from agentmon.contracts import AdapterDescriptor, InferenceTelemetryProvider, TelemetryCapability
from .p2_index import build_or_load_p2_batch_index, build_p2_batch_index, select_indexed_p2_events


class Vllm018InferenceTelemetryProvider:
    name = "vllm_018"

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            name=self.name,
            domain="inference",
            namespace="agentmon.adapters.inference.vllm_018",
            capabilities=(
                TelemetryCapability(
                    name="engine_request_events",
                    quality="exact",
                    required_fields=("api_request_id", "vllm_request_id"),
                ),
                TelemetryCapability(
                    name="first_token_timing",
                    quality="exact",
                    required_fields=("first_token_ns",),
                ),
                TelemetryCapability(
                    name="scheduler_step_observation",
                    quality="sampled",
                    required_fields=("running_requests", "waiting_requests"),
                ),
                TelemetryCapability(
                    name="kv_cache_accounting",
                    quality="sampled",
                    required_fields=("kv_usage_ratio",),
                ),
                TelemetryCapability(
                    name="moe_logical_routing",
                    quality="opt_in_exact",
                    required_fields=("routed_experts",),
                ),
            ),
        )

    def build_index(self, source_path: str, cache_dir: str | None = None) -> dict[str, Any]:
        from pathlib import Path

        source = Path(source_path)
        if cache_dir is None:
            return build_p2_batch_index(source)
        return build_or_load_p2_batch_index(source, Path(cache_dir))

    def select_events(
        self,
        index: dict[str, Any],
        *,
        run_id: str,
        request_ids: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected, diagnostics, mode = select_indexed_p2_events(
            index,
            run_id=run_id,
            response_ids=request_ids,
            filter_mode="auto",
            include_window_extras=False,
            window_margin_seconds=1.0,
        )
        diagnostics["selection_mode"] = mode
        return selected, diagnostics


def provider() -> InferenceTelemetryProvider:
    return Vllm018InferenceTelemetryProvider()
