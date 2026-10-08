from __future__ import annotations

from contextlib import contextmanager
import os
import sys
import threading
import time
from typing import Any, Iterator

from agentmon.events import Event
from agentmon.clock import host_boot_id, monotonic_clock_domain, source_host
from agentmon.sampling.procfs import read_proc_stat
from agentmon.security import exception_fingerprint
from agentmon.storage.jsonl import JsonlWriter

from .context import AgentMonContext


class AdapterEventSink:
    """Append-only sink used inside the mini-SWE child process."""

    def __init__(self, context: AgentMonContext | None = None) -> None:
        self.context = context or AgentMonContext.from_env()
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
        self._writer: JsonlWriter | None = None
        if self.context.events_path is not None:
            try:
                self._writer = JsonlWriter(self.context.events_path)
            except Exception as exc:
                self.write_errors_total += 1
                print(f"agentmon adapter event sink unavailable: {exc}", file=sys.stderr)

    @classmethod
    def from_env(cls) -> "AdapterEventSink":
        return cls(AgentMonContext.from_env())

    def emit(
        self,
        event_type: str,
        *,
        source: str = "mini_swe",
        quality: str = "exact",
        labels: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        enriched_payload = dict(payload or {})
        enriched_payload.setdefault("trace_id", self.context.run_id)
        if self.context.agent_id is not None:
            enriched_payload.setdefault("agent_id", self.context.agent_id)
        enriched_payload.setdefault("source_pid", self.source_pid)
        enriched_payload.setdefault("source_host", self.source_host)
        enriched_payload.setdefault("clock_domain", self.clock_domain)
        if self.host_boot_id is not None:
            enriched_payload.setdefault("host_boot_id", self.host_boot_id)
        if self.source_starttime_ticks is not None:
            enriched_payload.setdefault("source_starttime_ticks", self.source_starttime_ticks)
        enriched_payload.setdefault("sequence", self.next_sequence())
        event = Event(
            run_id=self.context.run_id,
            source=source,
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
                print(f"agentmon adapter event write failed: {exc}", file=sys.stderr)
        return event

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    @contextmanager
    def span(
        self,
        *,
        span_id: str,
        span_type: str,
        name: str,
        labels: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        source: str = "mini_swe",
    ) -> Iterator[dict[str, Any]]:
        start_wall = time.time_ns()
        start_mono = time.monotonic_ns()
        parent_span_id = infer_parent_span_id(
            span_type=span_type,
            context=self.context,
            attributes=attributes or {},
        )
        span_attributes = dict(attributes or {})
        step_id = None if span_type == "run" else span_attributes.get("step_id", self.context.step_id)
        base_payload = {
            **span_attributes,
            "trace_id": self.context.run_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "span_type": span_type,
            "name": name,
            "step_id": step_id,
            "attributes": span_attributes,
        }
        self.emit(
            span_start_event_type(span_type),
            source=source,
            labels=labels,
            payload={**base_payload, "start_wall_ns": start_wall, "start_mono_ns": start_mono},
        )
        status = "success"
        error_payload: dict[str, Any] = {}
        state: dict[str, Any] = {"start_wall_ns": start_wall, "start_mono_ns": start_mono, "end_payload": {}}
        previous_span_id = self.context.current_span_id
        self.context = self.context.child(current_span_id=span_id)
        try:
            yield state
        except BaseException as exc:
            control_flow_payload = mini_swe_submitted_control_flow_payload(exc)
            if control_flow_payload is not None:
                status = "submitted"
                error_payload = control_flow_payload
            else:
                status = "error"
                error_payload = exception_fingerprint(exc)
            raise
        finally:
            self.context = self.context.child(current_span_id=previous_span_id)
            end_wall = time.time_ns()
            end_mono = time.monotonic_ns()
            end_payload = state.get("end_payload", {})
            if not isinstance(end_payload, dict):
                end_payload = {}
            reported_status = status if status != "success" else end_payload.get("status", status)
            self.emit(
                span_finish_event_type(span_type, status),
                source=source,
                labels=labels,
                payload={
                    **base_payload,
                    "end_wall_ns": end_wall,
                    "end_mono_ns": end_mono,
                    "duration_ns": end_mono - start_mono,
                    **end_payload,
                    "status": reported_status,
                    **error_payload,
                },
            )

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()


_SINK: AdapterEventSink | None = None


def get_sink() -> AdapterEventSink:
    global _SINK
    if _SINK is None:
        _SINK = AdapterEventSink.from_env()
    return _SINK


def set_sink_for_tests(sink: AdapterEventSink | None) -> None:
    global _SINK
    _SINK = sink


def infer_parent_span_id(
    *,
    span_type: str,
    context: AgentMonContext,
    attributes: dict[str, Any],
) -> str | None:
    if attributes.get("parent_span_id"):
        return str(attributes["parent_span_id"])
    if context.current_span_id:
        return context.current_span_id
    step_id = attributes.get("step_id") or context.step_id
    if span_type in {"model_request", "tool_call"} and step_id:
        return str(step_id)
    if span_type == "agent_step":
        return context.root_span_id
    return None


def span_start_event_type(span_type: str) -> str:
    return f"{span_type}.start"


def span_finish_event_type(span_type: str, status: str) -> str:
    if status == "error" and span_type in {"agent_step", "model_request", "tool_call"}:
        return f"{span_type}.error"
    return f"{span_type}.end"


def mini_swe_submitted_control_flow_payload(exc: BaseException) -> dict[str, Any] | None:
    """Identify mini-SWE's Submitted control-flow exception without importing mini-SWE."""
    name = type(exc).__name__
    exit_status = submitted_exit_status(exc)
    if name != "Submitted" and str(exit_status).lower() != "submitted":
        return None
    return {
        "status": "submitted",
        "terminal_reason": "submitted",
        "exit_status": exit_status or "Submitted",
        "submission_control_flow": True,
        "control_flow_exception": name,
    }


def submitted_exit_status(exc: BaseException) -> Any:
    direct = getattr(exc, "exit_status", None)
    if direct is not None:
        return direct
    for message in getattr(exc, "messages", ()) or ():
        if not isinstance(message, dict):
            continue
        extra = message.get("extra")
        if isinstance(extra, dict) and extra.get("exit_status") is not None:
            return extra.get("exit_status")
    return None
