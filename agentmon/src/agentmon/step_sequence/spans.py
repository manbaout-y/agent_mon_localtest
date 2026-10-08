from __future__ import annotations

from typing import Any

from agentmon.step_sequence.constants import SPAN_TYPES
from agentmon.step_sequence.utils import as_dict, first_int, first_value, ns_delta, ns_to_s, sort_time

def build_spans(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    ends: list[dict[str, Any]] = []
    for event in events:
        lifecycle = span_lifecycle(event)
        if lifecycle is None:
            continue
        payload = as_dict(event.get("payload"))
        span_id = span_identifier(payload)
        if span_id is None:
            continue
        if lifecycle == "start":
            starts[span_id] = event
        else:
            ends.append(event)

    spans: list[dict[str, Any]] = []
    ended: set[str] = set()
    for end_event in ends:
        end_payload = as_dict(end_event.get("payload"))
        span_id = span_identifier(end_payload)
        if span_id is None:
            continue
        ended.add(span_id)
        spans.append(merge_span(span_id, starts.get(span_id), end_event))

    for span_id, start_event in starts.items():
        if span_id not in ended:
            spans.append(merge_span(span_id, start_event, None))

    spans.sort(key=lambda span: sort_time(span))
    return spans


def span_lifecycle(event: dict[str, Any]) -> str | None:
    payload = as_dict(event.get("payload"))
    span_type = payload.get("span_type")
    if span_type not in SPAN_TYPES:
        return None
    event_type = str(event.get("event_type") or "")
    if event_type == "span_start" or event_type.endswith(".start"):
        return "start"
    if event_type == "span_end" or event_type.endswith(".end") or event_type.endswith(".error"):
        return "finish"
    return None


def span_identifier(payload: dict[str, Any]) -> str | None:
    value = payload.get("span_id") or payload.get("tool_call_id") or payload.get("request_id")
    return str(value) if value not in {None, ""} else None


def merge_span(span_id: str, start_event: dict[str, Any] | None, end_event: dict[str, Any] | None) -> dict[str, Any]:
    start_payload = as_dict(start_event.get("payload")) if start_event else {}
    end_payload = as_dict(end_event.get("payload")) if end_event else {}
    attributes = as_dict(start_payload.get("attributes"))
    span_type = end_payload.get("span_type") or start_payload.get("span_type")
    start_mono_ns = first_int(
        start_payload.get("start_mono_ns"),
        start_event.get("monotonic_time_ns") if start_event else None,
        end_payload.get("start_mono_ns"),
    )
    end_mono_ns = first_int(
        end_payload.get("end_mono_ns"),
        end_event.get("monotonic_time_ns") if end_event else None,
    )
    duration_ns = first_int(end_payload.get("duration_ns"), ns_delta(start_mono_ns, end_mono_ns))
    return {
        "span_id": span_id,
        "span_type": span_type,
        "step_id": first_value(end_payload.get("step_id"), start_payload.get("step_id"), attributes.get("step_id")),
        "parent_span_id": first_value(end_payload.get("parent_span_id"), start_payload.get("parent_span_id")),
        "status": first_value(end_payload.get("status"), "open" if end_event is None else None),
        "start_mono_ns": start_mono_ns,
        "end_mono_ns": end_mono_ns,
        "duration_ns": duration_ns,
        "duration_s": ns_to_s(duration_ns),
        "start_event_type": start_event.get("event_type") if start_event else None,
        "end_event_type": end_event.get("event_type") if end_event else None,
        "start_payload": start_payload,
        "end_payload": end_payload,
        "attributes": attributes,
    }
