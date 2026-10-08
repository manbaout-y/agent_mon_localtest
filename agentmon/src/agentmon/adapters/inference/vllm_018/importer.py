from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from .p2_index import (
    P2_GLOBAL_EVENT_TYPES,
    is_p2_request_event_type,
    is_p2_run_capability_event_type,
)


def is_p2_global_event(event: dict[str, object]) -> bool:
    event_type = str(event.get("event_type") or "")
    if event_type in P2_GLOBAL_EVENT_TYPES:
        return True
    if event_type == "vllm.kv.block_event":
        payload = as_dict(event.get("payload"))
        return not any(
            string_or_none(payload.get(key))
            for key in (
                "api_request_id",
                "vllm_request_id",
                "request_id",
                "agentmon_model_request_id",
            )
        )
    return False


def is_p2_run_capability_event(event: dict[str, object]) -> bool:
    return is_p2_run_capability_event_type(str(event.get("event_type") or ""))


def summarize_p2_imported_events(events: list[dict[str, object]]) -> dict[str, object]:
    request_ids: set[str] = set()
    vllm_to_api: dict[str, str] = {}
    for event in events:
        payload = as_dict(event.get("payload"))
        api_id = string_or_none(payload.get("api_request_id"))
        vllm_id = string_or_none(payload.get("vllm_request_id") or payload.get("request_id"))
        if api_id and vllm_id:
            vllm_to_api[vllm_id] = api_id
    request_event_count = 0
    keyed_event_count = 0
    sample_event_count = 0
    global_event_count = 0
    event_type_counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        payload = as_dict(event.get("payload"))
        if is_p2_request_event_type(event_type):
            request_event_count += 1
            vllm_id = string_or_none(payload.get("vllm_request_id") or payload.get("request_id"))
            request_id = string_or_none(
                payload.get("api_request_id")
                or (vllm_to_api.get(vllm_id) if vllm_id else None)
                or vllm_id
                or payload.get("agentmon_model_request_id")
            )
            if request_id:
                request_ids.add(request_id)
        elif event_type == "vllm.kv.snapshot":
            sample_event_count += 1
        elif event_type == "vllm.kv.block_event" and not is_p2_global_event(event):
            keyed_event_count += 1
        else:
            global_event_count += 1
    return {
        "p2_request_count": len(request_ids),
        "p2_request_event_count": request_event_count,
        "p2_request_keyed_event_count": keyed_event_count,
        "p2_global_event_count": global_event_count,
        "p2_sample_event_count": sample_event_count,
        "p2_total_event_count": len(events),
        "p2_event_type_counts": event_type_counts,
    }


def normalize_imported_p2_event(
    event: dict[str, object],
    *,
    run_id: str,
    source_path: Path,
    start_offset: int | None,
    end_offset: int | None,
    quality: str,
    attribution_scope: str,
) -> dict[str, object]:
    copied = dict(event)
    labels = dict(as_dict(copied.get("labels")))
    payload = dict(as_dict(copied.get("payload")))
    original_event_id = copied.get("event_id")
    original_run_id = copied.get("run_id")
    original_payload_run_id = payload.get("run_id")
    if original_payload_run_id not in {None, "", run_id}:
        payload.setdefault("service_run_id", original_payload_run_id)
    payload["run_id"] = run_id
    payload["_agentmon_p2_import"] = {
        "source": str(source_path),
        "source_event_id": original_event_id,
        "source_run_id": original_run_id,
        "source_payload_run_id": original_payload_run_id,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "quality": quality,
        "attribution_scope": attribution_scope,
    }
    payload["observation_scope"] = attribution_scope
    if attribution_scope == "shared_service_observation":
        payload["request_attribution"] = "not_request_attributed"
    labels["p2_import_quality"] = quality
    labels["p2_observation_scope"] = attribution_scope
    copied["run_id"] = run_id
    copied["event_id"] = f"p2-import-{uuid4()}"
    copied["labels"] = labels
    copied["payload"] = payload
    return copied


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)
