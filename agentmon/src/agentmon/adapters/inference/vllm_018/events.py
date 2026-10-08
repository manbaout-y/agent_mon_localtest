from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

from agentmon.events import Event
from agentmon.clock import host_boot_id, monotonic_clock_domain, source_host
from agentmon.sampling.procfs import read_proc_stat
from agentmon.security import exception_fingerprint
from agentmon.storage.jsonl import JsonlWriter

from .kv import calculate_kv_pool


SOURCE = "vllm_p2"


@dataclass
class RequestMapping:
    agent_request_id: str | None
    vllm_request_id: str | None
    run_id: str
    correlation_quality: str
    agentmon_run_id: str | None = None
    agentmon_model_request_id: str | None = None
    api_request_id: str | None = None
    received_ns: int | None = None
    queued_ns: int | None = None
    first_scheduled_ns: int | None = None
    prefill_completed_ns: int | None = None
    first_token_ns: int | None = None
    first_output_ns: int | None = None
    engine_finished_ns: int | None = None
    finished_ns: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_request_id": self.agent_request_id,
            "vllm_request_id": self.vllm_request_id,
            "run_id": self.run_id,
            "correlation_quality": self.correlation_quality,
            "agentmon_run_id": self.agentmon_run_id,
            "agentmon_model_request_id": self.agentmon_model_request_id,
            "api_request_id": self.api_request_id,
            "received_ns": self.received_ns,
            "queued_ns": self.queued_ns,
            "first_scheduled_ns": self.first_scheduled_ns,
            "prefill_completed_ns": self.prefill_completed_ns,
            "first_token_ns": self.first_token_ns,
            "first_output_ns": self.first_output_ns,
            "engine_finished_ns": self.engine_finished_ns,
            "finished_ns": self.finished_ns,
        }


class VllmP2EventSink:
    """Append-only P2 event sink for vLLM adapter code.

    The sink is safe to instantiate in dry-run tests. If no events path is
    configured, events are returned to the caller but not persisted.
    """

    def __init__(
        self,
        *,
        run_id: str | None = None,
        events_path: str | Path | None = None,
        writer: JsonlWriter | None = None,
    ) -> None:
        self.run_id = run_id or os.environ.get("AGENTMON_RUN_ID", "unattached")
        events_raw = events_path or os.environ.get("AGENTMON_EVENTS_PATH")
        self.events_path = Path(events_raw) if events_raw else None
        self._lock = threading.Lock()
        self._sequence = 0
        self.source_pid = os.getpid()
        self.source_host = source_host()
        self.host_boot_id = host_boot_id()
        self.clock_domain = monotonic_clock_domain(host=self.source_host, boot_id=self.host_boot_id)
        proc_stat = read_proc_stat(self.source_pid)
        self.source_starttime_ticks = (
            int(proc_stat["starttime_ticks"])
            if proc_stat is not None and isinstance(proc_stat.get("starttime_ticks"), int)
            else None
        )
        self.write_errors_total = 0
        self._writer = writer
        if self._writer is None and self.events_path is not None:
            try:
                self._writer = JsonlWriter(self.events_path)
            except Exception as exc:
                self.write_errors_total += 1
                print(f"agentmon vllm p2 event sink unavailable: {exc}", file=sys.stderr)

    @classmethod
    def from_env(cls) -> "VllmP2EventSink":
        return cls()

    def emit(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        quality: str = "exact",
        labels: dict[str, Any] | None = None,
    ) -> Event:
        enriched_payload = dict(payload or {})
        enriched_payload.setdefault("trace_id", self.run_id)
        enriched_payload.setdefault("source_pid", self.source_pid)
        enriched_payload.setdefault("source_host", self.source_host)
        enriched_payload.setdefault("clock_domain", self.clock_domain)
        if self.host_boot_id is not None:
            enriched_payload.setdefault("host_boot_id", self.host_boot_id)
        if self.source_starttime_ticks is not None:
            enriched_payload.setdefault("source_starttime_ticks", self.source_starttime_ticks)
        enriched_payload.setdefault("sequence", self.next_sequence())
        event = Event(
            run_id=self.run_id,
            source=SOURCE,
            event_type=event_type,
            quality=quality,
            labels=labels or {},
            payload=enriched_payload,
        )
        if self._writer is not None:
            try:
                with self._lock:
                    self._writer.write_event(event)
            except Exception as exc:
                self.write_errors_total += 1
                print(f"agentmon vllm p2 event write failed: {exc}", file=sys.stderr)
        return event

    def request_received(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        received_ns: int | None = None,
        model_name: str | None = None,
        agentmon_run_id: str | None = None,
        agentmon_model_request_id: str | None = None,
        api_request_id: str | None = None,
    ) -> Event:
        received_ns = received_ns if received_ns is not None else time.monotonic_ns()
        mapping = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            agentmon_run_id=agentmon_run_id,
            agentmon_model_request_id=agentmon_model_request_id,
            api_request_id=api_request_id,
            received_ns=received_ns,
        )
        payload = mapping.to_payload()
        if model_name is not None:
            payload["model_name"] = model_name
        return self._request_event("vllm.request.received", payload, correlation_quality)

    def request_queued(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        queued_ns: int | None = None,
    ) -> Event:
        queued_ns = queued_ns if queued_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            queued_ns=queued_ns,
        ).to_payload()
        return self._request_event("vllm.request.queued", payload, correlation_quality)

    def request_first_scheduled(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        first_scheduled_ns: int | None = None,
    ) -> Event:
        first_scheduled_ns = first_scheduled_ns if first_scheduled_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            first_scheduled_ns=first_scheduled_ns,
        ).to_payload()
        return self._request_event("vllm.request.first_scheduled", payload, correlation_quality)

    def request_prefill_completed(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        prefill_completed_ns: int | None = None,
        prompt_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        computed_prefill_tokens: int | None = None,
        recomputed_prompt_tokens: int | None = None,
    ) -> Event:
        prefill_completed_ns = prefill_completed_ns if prefill_completed_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            prefill_completed_ns=prefill_completed_ns,
        ).to_payload()
        payload.update(
            {
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "computed_prefill_tokens": computed_prefill_tokens,
                "recomputed_prompt_tokens": recomputed_prompt_tokens,
            }
        )
        return self._request_event("vllm.request.prefill_completed", payload, correlation_quality)

    def request_prompt_cache_accounted(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        prompt_cache_accounted_ns: int | None = None,
        prompt_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        computed_prefill_tokens: int | None = None,
        recomputed_prompt_tokens: int | None = None,
    ) -> Event:
        """Record prompt-cache accounting without claiming prefill completed."""
        accounted_ns = prompt_cache_accounted_ns if prompt_cache_accounted_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
        ).to_payload()
        payload.update(
            {
                "prompt_cache_accounted_ns": accounted_ns,
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "computed_prefill_tokens": computed_prefill_tokens,
                "recomputed_prompt_tokens": recomputed_prompt_tokens,
            }
        )
        return self._request_event("vllm.request.prompt_cache_accounted", payload, correlation_quality)

    def request_first_token(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        first_token_ns: int | None = None,
    ) -> Event:
        first_token_ns = first_token_ns if first_token_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            first_token_ns=first_token_ns,
        ).to_payload()
        return self._request_event("vllm.request.first_token", payload, correlation_quality)

    def request_preempted(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        preemption_count: int | None = None,
    ) -> Event:
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
        ).to_payload()
        payload["preemption_count"] = preemption_count
        return self._request_event("vllm.request.preempted", payload, correlation_quality)

    def request_finished(
        self,
        *,
        agent_request_id: str | None,
        vllm_request_id: str | None,
        correlation_quality: str,
        received_ns: int | None = None,
        first_scheduled_ns: int | None = None,
        prefill_completed_ns: int | None = None,
        first_token_ns: int | None = None,
        first_output_ns: int | None = None,
        engine_finished_ns: int | None = None,
        finished_ns: int | None = None,
        finish_reason: str | None = None,
        agentmon_run_id: str | None = None,
        agentmon_model_request_id: str | None = None,
        api_request_id: str | None = None,
        prompt_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        computed_prefill_tokens: int | None = None,
        recomputed_prompt_tokens: int | None = None,
        generation_tokens: int | None = None,
        preemption_count: int | None = None,
    ) -> Event:
        finished_ns = finished_ns if finished_ns is not None else time.monotonic_ns()
        payload = RequestMapping(
            agent_request_id=agent_request_id,
            vllm_request_id=vllm_request_id,
            run_id=self.run_id,
            correlation_quality=correlation_quality,
            agentmon_run_id=agentmon_run_id,
            agentmon_model_request_id=agentmon_model_request_id,
            api_request_id=api_request_id,
            received_ns=received_ns,
            first_scheduled_ns=first_scheduled_ns,
            prefill_completed_ns=prefill_completed_ns,
            first_token_ns=first_token_ns,
            first_output_ns=first_output_ns,
            engine_finished_ns=engine_finished_ns,
            finished_ns=finished_ns,
        ).to_payload()
        payload.update(
            {
                "finish_reason": finish_reason,
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "computed_prefill_tokens": computed_prefill_tokens,
                "recomputed_prompt_tokens": recomputed_prompt_tokens,
                "generation_tokens": generation_tokens,
                "preemption_count": preemption_count,
            }
        )
        return self._request_event("vllm.request.finished", payload, correlation_quality)

    def adapter_capability(self, *, status: str, details: dict[str, Any] | None = None) -> Event:
        details = details or {}
        payload = {"status": status, "details": details}
        for key in ("visible_devices", "visible_devices_source"):
            if details.get(key) is not None:
                payload[key] = details[key]
        return self.emit("vllm.adapter.capability", payload=payload)

    def adapter_error(self, exc: BaseException, *, where: str | None = None) -> Event:
        payload = exception_fingerprint(exc)
        if where is not None:
            payload["where"] = where
        return self.emit("vllm.adapter.error", payload=payload, quality="partial")

    def kv_snapshot(
        self,
        *,
        prefix_cache_status: str | None = None,
        prefix_cache_queries: int | None = None,
        prefix_cache_hits: int | None = None,
        kv_usage_ratio: float | None = None,
        pool_inputs: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> Event:
        payload: dict[str, Any] = {
            "prefix_cache_status": prefix_cache_status,
            "prefix_cache_queries": prefix_cache_queries,
            "prefix_cache_hits": prefix_cache_hits,
            "kv_usage_ratio": kv_usage_ratio,
        }
        if pool_inputs is not None:
            payload["kv_pool"] = calculate_kv_pool(pool_inputs, source=source)
        return self.emit("vllm.kv.snapshot", payload=payload, quality="exact")

    def scheduler_snapshot(
        self,
        *,
        running_requests: int | None = None,
        waiting_requests: int | None = None,
        scheduled_requests: int | None = None,
        scheduled_tokens: int | None = None,
        source: str = "scheduler_stats",
    ) -> Event:
        return self.emit(
            "vllm.scheduler.snapshot",
            payload={
                "running_requests": running_requests,
                "waiting_requests": waiting_requests,
                "scheduled_requests": scheduled_requests,
                "scheduled_tokens": scheduled_tokens,
                "source": source,
                "observation_scope": "shared_service_observation",
                "request_attribution": "not_request_attributed",
            },
            quality="exact",
        )

    def scheduler_iteration(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        quality: str = "exact",
    ) -> Event:
        enriched = {
            **payload,
            "observation_scope": "shared_service_observation",
            "request_attribution": "request_ids_listed_not_resource_exclusive",
        }
        return self.emit(event_type, payload=enriched, quality=quality)

    def kv_block_event(
        self,
        *,
        request_id: str | None = None,
        operation: str | None = None,
        allocated_block_count: int | None = None,
        cached_block_count: int | None = None,
        cache_hit_block_count: int | None = None,
        freed_block_count: int | None = None,
        evicted_block_count: int | None = None,
        kv_usage_before: float | None = None,
        kv_usage_after: float | None = None,
        kv_quality: str = "exact",
        agent_request_id: str | None = None,
        agentmon_run_id: str | None = None,
        agentmon_model_request_id: str | None = None,
        api_request_id: str | None = None,
    ) -> Event:
        return self.emit(
            "vllm.kv.block_event",
            payload={
                "request_id": request_id,
                "vllm_request_id": request_id,
                "agent_request_id": agent_request_id,
                "agentmon_run_id": agentmon_run_id,
                "agentmon_model_request_id": agentmon_model_request_id,
                "api_request_id": api_request_id,
                "operation": operation,
                "allocated_block_count": allocated_block_count,
                "cached_block_count": cached_block_count,
                "cache_hit_block_count": cache_hit_block_count,
                "freed_block_count": freed_block_count,
                "evicted_block_count": evicted_block_count,
                "kv_usage_before": kv_usage_before,
                "kv_usage_after": kv_usage_after,
                "kv_quality": kv_quality,
            },
            quality=kv_quality if kv_quality in {"exact", "estimated"} else "partial",
        )

    def kv_reset(
        self,
        *,
        succeeded: bool | None,
        kv_usage_before: float | None,
        kv_usage_after: float | None,
    ) -> Event:
        return self.emit(
            "vllm.kv.reset",
            payload={
                "operation": "reset",
                "succeeded": succeeded,
                "kv_usage_before": kv_usage_before,
                "kv_usage_after": kv_usage_after,
                "observation_scope": "shared_service_observation",
                "request_attribution": "not_request_attributed",
            },
            quality="exact" if succeeded is not None else "partial",
        )

    def request_npu_attribution(
        self,
        *,
        vllm_request_id: str | None,
        agent_request_id: str | None = None,
        attribution_quality: str,
        npu_utilization_integral: float | None = None,
        npu_power_integral: float | None = None,
        sample_count: int | None = None,
    ) -> Event:
        return self.emit(
            "vllm.request.npu_attribution",
            payload={
                "vllm_request_id": vllm_request_id,
                "agent_request_id": agent_request_id,
                "attribution_quality": attribution_quality,
                "npu_utilization_integral": npu_utilization_integral,
                "npu_power_integral": npu_power_integral,
                "sample_count": sample_count,
            },
            quality="exact" if attribution_quality == "exclusive" else "estimated",
        )

    def _request_event(self, event_type: str, payload: dict[str, Any], correlation_quality: str) -> Event:
        model_call_id = payload.get("model_call_id") or payload.get("agentmon_model_request_id") or payload.get("agent_request_id")
        if model_call_id is not None:
            payload.setdefault("model_call_id", model_call_id)
            payload.setdefault("client_request_id", model_call_id)
        if payload.get("api_request_id") is not None:
            payload.setdefault("server_request_id", payload["api_request_id"])
        engine_request_id = payload.get("vllm_request_id") or payload.get("request_id")
        if engine_request_id is not None:
            payload.setdefault("engine_request_id", engine_request_id)
        return self.emit(
            event_type,
            payload=payload,
            quality=quality_from_correlation(correlation_quality),
        )

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()


def quality_from_correlation(correlation_quality: str) -> str:
    return "exact" if correlation_quality in {"direct", "server_generated"} else "estimated"
