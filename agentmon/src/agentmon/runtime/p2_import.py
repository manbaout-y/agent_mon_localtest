from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from agentmon.adapters.inference.vllm_018.importer import (
    is_p2_global_event,
    is_p2_run_capability_event,
    normalize_imported_p2_event,
    summarize_p2_imported_events,
)
from agentmon.adapters.inference.vllm_018.p2_index import (
    is_p2_request_event_type,
    is_p2_request_or_keyed_event_type,
)
from agentmon.storage.event_queue import EventSink


@dataclass
class P2RunWindow:
    source_path: Path | None
    start_offset: int | None
    source_exists_at_start: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "start_offset": self.start_offset,
            "source_exists_at_start": self.source_exists_at_start,
        }


def open_p2_run_window(path: Path | None) -> P2RunWindow:
    if path is None:
        return P2RunWindow(source_path=None, start_offset=None, source_exists_at_start=False)
    source = path.expanduser()
    if not source.is_file():
        return P2RunWindow(source_path=source, start_offset=0, source_exists_at_start=False)
    return P2RunWindow(
        source_path=source,
        start_offset=source.stat().st_size,
        source_exists_at_start=True,
    )


def import_p2_run_window(
    *,
    run_id: str,
    window: P2RunWindow,
    sink: EventSink,
    trajectory_paths: Iterable[Path] = (),
    end_offset: int | None = None,
    quality: str = "service_exclusive",
    global_event_policy: str = "auto",
    include_window_extras: bool = False,
    window_margin_seconds: float = 1.0,
) -> dict[str, Any]:
    source = window.source_path
    if source is None:
        return {
            "status": "NOT_CONFIGURED",
            "p2_source_path": None,
            "p2_start_offset": None,
            "p2_end_offset": None,
            "p2_import_status": "NOT_CONFIGURED",
            "p2_event_count": 0,
            "p2_request_count": 0,
        }
    if not source.is_file():
        return {
            "status": "SOURCE_MISSING",
            "p2_source_path": str(source),
            "p2_start_offset": window.start_offset,
            "p2_end_offset": None,
            "p2_import_status": "SOURCE_MISSING",
            "p2_event_count": 0,
            "p2_request_count": 0,
        }

    resolved_end_offset = source.stat().st_size if end_offset is None else end_offset
    read_result = read_p2_event_slice(
        source,
        start_offset=window.start_offset,
        end_offset=resolved_end_offset,
    )
    response_ids = extract_response_ids_from_trajectories(trajectory_paths)
    p2_events = [event for event in read_result["events"] if isinstance(event, dict) and event.get("source") == "vllm_p2"]
    run_filtered_events = [event for event in p2_events if p2_event_matches_run(event, run_id)]
    selection_mode = "run_id" if run_filtered_events else "trajectory" if response_ids else "byte_window"
    effective_quality = "direct" if selection_mode == "run_id" else quality if response_ids else "service_stream"
    filtered_import = selection_mode in {"run_id", "trajectory"}
    include_global_events = global_event_policy == "copy" or (
        global_event_policy == "auto" and not filtered_import
    )

    if selection_mode == "run_id":
        selected_events = run_filtered_events
        selection_diagnostics = {
            "trajectory_response_id_count": len(response_ids),
            "matched_p2_request_count": count_distinct_api_ids(selected_events),
            "matched_p2_request_ids": sorted(distinct_api_ids(selected_events)),
            "unmatched_trajectory_id_count": 0,
            "unmatched_trajectory_ids": [],
            "p2_requests_in_task_window_not_in_trajectory_count": 0,
            "p2_requests_in_task_window_not_in_trajectory": [],
            "task_window_start_mono_ns": None,
            "task_window_end_mono_ns": None,
            "extras_attribution_quality": "not_applicable_run_id_filter",
        }
    elif selection_mode == "trajectory":
        expanded_ids = expand_request_ids_by_suffix(response_ids, p2_events)
        selection_diagnostics = p2_selection_diagnostics(
            p2_events,
            expanded_ids,
            window_margin_seconds=window_margin_seconds,
        )
        selection_diagnostics["trajectory_response_ids_requested"] = sorted(response_ids)
        selection_diagnostics["trajectory_response_ids_expanded"] = sorted(expanded_ids)
        selected_events = select_p2_events_for_api_request_ids(
            p2_events,
            expanded_ids,
            include_window_extras=include_window_extras,
            window_margin_seconds=window_margin_seconds,
            include_global_events=include_global_events,
        )
    else:
        selected_events = p2_events
        selection_diagnostics = {
            "trajectory_response_id_count": 0,
            "matched_p2_request_count": 0,
            "matched_p2_request_ids": [],
            "unmatched_trajectory_id_count": 0,
            "unmatched_trajectory_ids": [],
            "p2_requests_in_task_window_not_in_trajectory_count": 0,
            "p2_requests_in_task_window_not_in_trajectory": [],
            "task_window_start_mono_ns": None,
            "task_window_end_mono_ns": None,
            "extras_attribution_quality": "not_applicable_byte_window",
        }

    imported_events: list[dict[str, object]] = []
    for event in selected_events:
        if event.get("source") != "vllm_p2":
            continue
        if filtered_import and not include_global_events and is_p2_global_event(event):
            continue
        imported = normalize_imported_p2_event(
            event,
            run_id=run_id,
            source_path=source,
            start_offset=window.start_offset,
            end_offset=resolved_end_offset,
            quality=effective_quality,
            attribution_scope=(
                "shared_service_observation"
                if is_p2_global_event(event) or selection_mode == "byte_window"
                else "request_attributed"
            ),
        )
        if sink.emit_event(imported, critical=True):
            imported_events.append(imported)

    event_counts = summarize_p2_imported_events(imported_events)
    status = "IMPORTED" if imported_events else "NO_MATCHING_EVENTS"
    metadata = {
        "schema_version": "1.0",
        "status": status,
        "p2_source_path": str(source),
        "p2_start_offset": window.start_offset,
        "p2_end_offset": resolved_end_offset,
        "p2_source_exists_at_start": window.source_exists_at_start,
        "p2_import_status": status,
        "p2_event_count": len(imported_events),
        "selection_mode": selection_mode,
        "quality": effective_quality,
        "global_event_policy": global_event_policy,
        "global_events_copied": include_global_events,
        "scanned_lines": read_result["scanned_lines"],
        "invalid_lines": read_result["invalid_lines"],
        "non_p2_event_count": read_result["non_p2_event_count"],
        **selection_diagnostics,
        **event_counts,
    }
    return metadata


def read_p2_event_slice(path: Path, *, start_offset: int | None, end_offset: int | None) -> dict[str, Any]:
    if start_offset is not None and start_offset < 0:
        raise ValueError("start-offset must be >= 0")
    if end_offset is not None and end_offset < 0:
        raise ValueError("end-offset must be >= 0")
    if start_offset is not None and end_offset is not None and end_offset < start_offset:
        raise ValueError("end-offset must be >= start-offset")

    start = start_offset or 0
    file_size = path.stat().st_size
    length = None if end_offset is None else max(0, end_offset - start)
    with path.open("rb") as handle:
        drop_first = False
        if start > 0:
            handle.seek(start - 1)
            drop_first = handle.read(1) != b"\n"
        handle.seek(start)
        raw = handle.read() if length is None else handle.read(length)
    lines = raw.splitlines()
    if drop_first and lines:
        lines = lines[1:]
    if end_offset is not None and end_offset < file_size and raw and not raw.endswith(b"\n") and lines:
        lines = lines[:-1]

    events: list[dict[str, object]] = []
    invalid_lines = 0
    non_p2_event_count = 0
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(item, dict):
            invalid_lines += 1
            continue
        if item.get("source") != "vllm_p2":
            non_p2_event_count += 1
        events.append(item)
    return {
        "events": events,
        "scanned_lines": len(lines),
        "invalid_lines": invalid_lines,
        "non_p2_event_count": non_p2_event_count,
    }


def extract_response_ids_from_trajectory(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    ids: set[str] = set()
    for message in trajectory_messages(data):
        if message.get("role") != "assistant" or not isinstance(message.get("tool_calls"), list):
            continue
        response = as_dict(as_dict(message.get("extra")).get("response"))
        response_id = string_or_none(response.get("id"))
        if response_id:
            ids.add(response_id)
    return ids


def extract_response_ids_from_trajectories(paths: Iterable[Path]) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            ids.update(extract_response_ids_from_trajectory(path))
        except (OSError, json.JSONDecodeError):
            continue
    return ids


def trajectory_messages(data: object) -> list[dict[str, object]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    messages = data.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    nested = data.get("trajectory")
    if isinstance(nested, dict):
        nested_messages = nested.get("messages")
        if isinstance(nested_messages, list):
            return [item for item in nested_messages if isinstance(item, dict)]
    return []


def select_p2_events_for_api_request_ids(
    events: object,
    api_request_ids: set[str],
    *,
    include_window_extras: bool,
    window_margin_seconds: float,
    include_global_events: bool = False,
) -> list[dict[str, object]]:
    rows = [event for event in events if isinstance(event, dict)]
    matched_api_ids: set[str] = set()
    matched_vllm_ids: set[str] = set()
    matched_times: list[int] = []
    for event in rows:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        if not is_p2_request_event_type(event_type):
            continue
        payload = as_dict(event.get("payload"))
        api_request_id = string_or_none(payload.get("api_request_id"))
        if api_request_id not in api_request_ids:
            continue
        matched_api_ids.add(api_request_id)
        for key in ("vllm_request_id", "request_id"):
            value = string_or_none(payload.get(key))
            if value:
                matched_vllm_ids.add(value)
        event_ns = p2_event_time_ns(event, payload)
        if event_ns is not None:
            matched_times.append(event_ns)

    if not matched_api_ids:
        return []
    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    selected: list[dict[str, object]] = []
    for event in rows:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        include = False
        if is_p2_request_or_keyed_event_type(event_type):
            include = p2_payload_matches_ids(payload, matched_api_ids, matched_vllm_ids)
            if not include and include_window_extras:
                include = p2_event_in_window(event, payload, window_start, window_end)
        elif is_p2_global_event(event) and include_global_events:
            include = (
                True
                if is_p2_run_capability_event(event)
                else p2_event_in_window(event, payload, window_start, window_end)
            )
        if include:
            selected.append(event)
    return selected


def p2_selection_diagnostics(
    events: object,
    api_request_ids: set[str],
    *,
    window_margin_seconds: float,
) -> dict[str, object]:
    rows = [event for event in events if isinstance(event, dict) and event.get("source") == "vllm_p2"]
    available_api_ids: set[str] = set()
    request_times: dict[str, list[int]] = {}
    matched_times: list[int] = []
    for event in rows:
        if not is_p2_request_event_type(str(event.get("event_type") or "")):
            continue
        payload = as_dict(event.get("payload"))
        api_request_id = string_or_none(payload.get("api_request_id"))
        if api_request_id is None:
            continue
        available_api_ids.add(api_request_id)
        event_ns = p2_event_time_ns(event, payload)
        if event_ns is not None:
            request_times.setdefault(api_request_id, []).append(event_ns)
            if api_request_id in api_request_ids:
                matched_times.append(event_ns)

    matched_ids = api_request_ids & available_api_ids
    unmatched_ids = api_request_ids - available_api_ids
    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    extras: set[str] = set()
    if window_start is not None and window_end is not None:
        for request_id, timestamps in request_times.items():
            if request_id in matched_ids:
                continue
            if any(window_start <= timestamp <= window_end for timestamp in timestamps):
                extras.add(request_id)
    return {
        "trajectory_response_id_count": len(api_request_ids),
        "matched_p2_request_count": len(matched_ids),
        "matched_p2_request_ids": sorted(matched_ids),
        "unmatched_trajectory_id_count": len(unmatched_ids),
        "unmatched_trajectory_ids": sorted(unmatched_ids),
        "p2_requests_in_task_window_not_in_trajectory_count": len(extras),
        "p2_requests_in_task_window_not_in_trajectory": sorted(extras),
        "task_window_start_mono_ns": window_start,
        "task_window_end_mono_ns": window_end,
        "extras_attribution_quality": "diagnostic_shared_window_not_request_attributed",
    }


def expand_request_ids_by_suffix(ids: set[str], events: Iterable[dict[str, object]]) -> set[str]:
    if not ids:
        return set()
    available = distinct_api_ids(events)
    expanded = set(ids)
    for wanted in ids:
        for candidate in available:
            if request_ids_equivalent(wanted, candidate):
                expanded.add(candidate)
    return expanded


def request_ids_equivalent(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_parts = left.split("-")
    right_parts = right.split("-")
    return len(left_parts) > 1 and len(right_parts) > 1 and (
        left.endswith(right) or right.endswith(left)
    )


def p2_payload_matches_ids(payload: dict[str, object], api_ids: set[str], vllm_ids: set[str]) -> bool:
    api_request_id = string_or_none(payload.get("api_request_id"))
    if api_request_id in api_ids:
        return True
    for key in ("vllm_request_id", "request_id"):
        value = string_or_none(payload.get(key))
        if value in vllm_ids:
            return True
    return False


def p2_event_in_window(
    event: dict[str, object],
    payload: dict[str, object],
    start_ns: int | None,
    end_ns: int | None,
) -> bool:
    if start_ns is None or end_ns is None:
        return False
    event_ns = p2_event_time_ns(event, payload)
    return event_ns is not None and start_ns <= event_ns <= end_ns


def p2_event_time_ns(event: dict[str, object], payload: dict[str, object]) -> int | None:
    for key in (
        "received_ns",
        "queued_ns",
        "first_scheduled_ns",
        "prefill_completed_ns",
        "first_token_ns",
        "first_output_ns",
        "engine_finished_ns",
        "finished_ns",
    ):
        value = integer_or_none(payload.get(key))
        if value is not None:
            return value
    return integer_or_none(event.get("monotonic_time_ns"))


def p2_event_matches_run(event: dict[str, object], run_id: str) -> bool:
    payload = as_dict(event.get("payload"))
    return (
        event.get("run_id") == run_id
        or payload.get("run_id") == run_id
        or payload.get("agentmon_run_id") == run_id
    )


def count_distinct_api_ids(events: Iterable[dict[str, object]]) -> int:
    return len(distinct_api_ids(events))


def distinct_api_ids(events: Iterable[dict[str, object]]) -> set[str]:
    ids: set[str] = set()
    for event in events:
        if not is_p2_request_event_type(str(event.get("event_type") or "")):
            continue
        payload = as_dict(event.get("payload"))
        for key in ("api_request_id", "server_request_id", "vllm_request_id", "request_id"):
            value = string_or_none(payload.get(key))
            if value:
                ids.add(value)
                break
    return ids


def as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def integer_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
