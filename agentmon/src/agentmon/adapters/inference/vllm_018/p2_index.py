from __future__ import annotations

from bisect import bisect_left, bisect_right
import gzip
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from agentmon.paths import chmod_private_file, make_private_dir, write_private_text


P2_REQUEST_EVENT_PREFIX = "vllm.request."
P2_REQUEST_EVENT_TYPES = {
    "vllm.moe.routing.summary",
    "vllm.moe.routing.chunk",
}
P2_GLOBAL_EVENT_TYPES = {
    "vllm.kv.snapshot",
    "vllm.scheduler.snapshot",
    "vllm.scheduler.iteration.scheduled",
    "vllm.scheduler.iteration.completed",
    "vllm.kv.reset",
    "vllm.adapter.capability",
    "vllm.adapter.error",
    "vllm.moe.routing.capability",
}
P2_RUN_CAPABILITY_EVENT_TYPES = {
    "vllm.adapter.capability",
    "vllm.moe.routing.capability",
}
P2_KEYED_EVENT_TYPES = {"vllm.kv.block_event"}
P2_STATE_EVENT_TYPES = {
    "vllm.kv.snapshot",
    "vllm.scheduler.snapshot",
    "vllm.adapter.capability",
    "vllm.moe.routing.capability",
}
P2_INDEX_CACHE_SCHEMA_VERSION = 3
P2_FINGERPRINT_SAMPLE_BYTES = 64 * 1024


def is_p2_run_capability_event_type(event_type: str) -> bool:
    """Return whether a capability event applies to the complete service run."""

    return event_type in P2_RUN_CAPABILITY_EVENT_TYPES


def build_or_load_p2_batch_index(
    path: Path,
    batch_root: Path,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    source = path.expanduser().resolve()
    fingerprint = source_fingerprint(source)
    cache_dir = (cache_root.expanduser().resolve() if cache_root is not None else batch_root / "_p2_index")
    make_private_dir(cache_dir)
    cache_key = sha256(
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    cache_path = cache_dir / f"p2-index-v{P2_INDEX_CACHE_SCHEMA_VERSION}-{cache_key}.json"
    if fingerprint.get("exists") and cache_path.is_file():
        load_started = time.monotonic()
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("cache_schema_version") == P2_INDEX_CACHE_SCHEMA_VERSION
                and cached.get("source_fingerprint") == fingerprint
            ):
                index = hydrate_compact_index(cached)
                metrics = as_dict(index.get("metrics"))
                metrics["p2_index_cache_hit"] = True
                metrics["p2_cache_path"] = str(cache_path)
                metrics["p2_cache_load_wall_s"] = time.monotonic() - load_started
                metrics["p2_cache_bytes"] = cache_path.stat().st_size
                metrics["p2_source_hash_scans"] = 0
                metrics["p2_source_parse_scans"] = 0
                index["metrics"] = metrics
                return index
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
            pass
    index = build_p2_batch_index(
        source,
        source_fingerprint_value=fingerprint,
    )
    metrics = as_dict(index.get("metrics"))
    metrics["p2_index_cache_hit"] = False
    metrics["p2_cache_path"] = str(cache_path)
    index["metrics"] = metrics
    cache_started = time.monotonic()
    serialized = serialize_compact_index(index)
    write_private_text(cache_path, serialized)
    chmod_private_file(cache_path)
    metrics["p2_cache_serialize_wall_s"] = time.monotonic() - cache_started
    metrics["p2_cache_bytes"] = cache_path.stat().st_size
    return index


def build_p2_batch_index(
    path: Path,
    *,
    source_sha256: str | None = None,
    source_fingerprint_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parse_started = time.monotonic()
    parsed = read_jsonl_events_with_offsets(path)
    parse_wall_s = time.monotonic() - parse_started
    source_hash_scans = 0
    if source_sha256 is None:
        source_sha256 = string_or_none(parsed.get("source_sha256"))
    if source_sha256 is None:
        source_sha256 = file_hash_if_exists(path)
        source_hash_scans = 1 if source_sha256 is not None else 0
    build_started = time.monotonic()
    events = [event for event in parsed["events"] if is_p2_event(event)]
    by_api_id: dict[str, list[dict[str, Any]]] = {}
    by_vllm_id: dict[str, list[dict[str, Any]]] = {}
    by_run_id: dict[str, list[dict[str, Any]]] = {}
    by_alias_id: dict[str, list[dict[str, Any]]] = {}
    request_events: list[dict[str, Any]] = []
    global_events: list[dict[str, Any]] = []
    for sequence, event in enumerate(events, start=1):
        event["_p2_index_sequence"] = sequence
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        if is_p2_request_or_keyed_event_type(event_type):
            request_events.append(event)
        if event_type in P2_GLOBAL_EVENT_TYPES or (
            event_type == "vllm.kv.block_event"
            and not any(
                not is_blank_value(payload.get(key))
                for key in ("api_request_id", "vllm_request_id", "request_id")
            )
        ):
            global_events.append(event)
        api_id = string_or_none(payload.get("api_request_id"))
        if api_id:
            by_api_id.setdefault(api_id, []).append(event)
        for key in ("vllm_request_id", "request_id"):
            vllm_id = string_or_none(payload.get(key))
            if vllm_id:
                by_vllm_id.setdefault(vllm_id, []).append(event)
        run_id = string_or_none(payload.get("agentmon_run_id"))
        if run_id:
            by_run_id.setdefault(run_id, []).append(event)
        primary_ids = {
            value
            for key in ("api_request_id", "vllm_request_id", "request_id")
            if (value := string_or_none(payload.get(key))) is not None
        }
        for alias_id in p2_alias_ids(payload):
            if alias_id not in primary_ids:
                by_alias_id.setdefault(alias_id, []).append(event)
    input_bytes = path.stat().st_size if path.exists() else 0
    service_bounds = p2_service_bounds_from_events(events)
    build_wall_s = time.monotonic() - build_started
    index = {
        "source": str(path),
        "source_sha256": source_sha256,
        "source_fingerprint": source_fingerprint_value or source_fingerprint(path),
        "events": events,
        "request_events": request_events,
        "global_events": global_events,
        "by_api_id": by_api_id,
        "by_vllm_id": by_vllm_id,
        "by_run_id": by_run_id,
        "by_alias_id": by_alias_id,
        "event_count": len(events),
        "service_bounds": service_bounds,
        "metrics": {
            "p2_input_bytes": input_bytes,
            "p2_total_jsonl_lines": parsed["total_lines"],
            "p2_invalid_jsonl_lines": parsed["invalid_lines"],
            "p2_event_count": len(events),
            "p2_request_event_count": len(request_events),
            "p2_global_event_count": len(global_events),
            "p2_parse_wall_s": parse_wall_s,
            "p2_index_build_wall_s": build_wall_s,
            "p2_index_source": str(path),
            "p2_source_hash_scans": source_hash_scans,
            "p2_source_parse_scans": 1 if path.is_file() else 0,
        },
    }
    add_time_indexes(index)
    index["metrics"]["p2_request_time_index_count"] = len(index["_request_time_values"])
    index["metrics"]["p2_global_time_index_count"] = len(index["_global_time_values"])
    return index


def compact_index(index: dict[str, Any]) -> dict[str, Any]:
    events = list(index.get("events") or [])
    positions = {id(event): position for position, event in enumerate(events)}

    def event_positions(items: Any) -> list[int]:
        if not isinstance(items, list):
            return []
        return [positions[id(event)] for event in items if isinstance(event, dict) and id(event) in positions]

    def mapping_positions(value: Any) -> dict[str, list[int]]:
        if not isinstance(value, dict):
            return {}
        return {str(key): event_positions(items) for key, items in value.items() if isinstance(items, list)}

    return {
        "cache_schema_version": P2_INDEX_CACHE_SCHEMA_VERSION,
        "source": index.get("source"),
        "source_sha256": index.get("source_sha256"),
        "source_fingerprint": index.get("source_fingerprint"),
        "events": events,
        "request_event_indices": event_positions(index.get("request_events")),
        "global_event_indices": event_positions(index.get("global_events")),
        "by_api_id_indices": mapping_positions(index.get("by_api_id")),
        "by_vllm_id_indices": mapping_positions(index.get("by_vllm_id")),
        "by_run_id_indices": mapping_positions(index.get("by_run_id")),
        "by_alias_id_indices": mapping_positions(index.get("by_alias_id")),
        "event_count": index.get("event_count", len(events)),
        "service_bounds": index.get("service_bounds", {}),
        "metrics": dict(as_dict(index.get("metrics"))),
    }


def serialize_compact_index(index: dict[str, Any]) -> str:
    removed_metadata: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in index.get("events") or []:
        if not isinstance(event, dict):
            continue
        removed = {
            key: event.pop(key)
            for key in ("_source_path", "_p2_index_sequence")
            if key in event
        }
        if removed:
            removed_metadata.append((event, removed))
    try:
        compact = compact_index(index)
        return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    finally:
        for event, metadata in removed_metadata:
            event.update(metadata)


def hydrate_compact_index(cached: dict[str, Any]) -> dict[str, Any]:
    events = cached.get("events")
    if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
        raise ValueError("invalid P2 cache event table")
    source = string_or_none(cached.get("source"))
    for sequence, event in enumerate(events, start=1):
        event["_p2_index_sequence"] = sequence
        if source is not None:
            event.setdefault("_source_path", source)

    def event_refs(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        refs: list[dict[str, Any]] = []
        for position in value:
            if not isinstance(position, int) or position < 0 or position >= len(events):
                raise ValueError("invalid P2 cache event reference")
            refs.append(events[position])
        return refs

    def mapping_refs(value: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(value, dict):
            return {}
        return {str(key): event_refs(positions) for key, positions in value.items()}

    index = {
        "source": cached.get("source"),
        "source_sha256": cached.get("source_sha256"),
        "source_fingerprint": cached.get("source_fingerprint"),
        "events": events,
        "request_events": event_refs(cached.get("request_event_indices")),
        "global_events": event_refs(cached.get("global_event_indices")),
        "by_api_id": mapping_refs(cached.get("by_api_id_indices")),
        "by_vllm_id": mapping_refs(cached.get("by_vllm_id_indices")),
        "by_run_id": mapping_refs(cached.get("by_run_id_indices")),
        "by_alias_id": mapping_refs(cached.get("by_alias_id_indices")),
        "event_count": cached.get("event_count", len(events)),
        "service_bounds": as_dict(cached.get("service_bounds")),
        "metrics": dict(as_dict(cached.get("metrics"))),
    }
    add_time_indexes(index)
    return index


def add_time_indexes(index: dict[str, Any]) -> None:
    request_rows = sorted_time_rows(index.get("request_events"))
    global_rows = sorted_time_rows(index.get("global_events"))
    index["_request_time_values"] = [row[0] for row in request_rows]
    index["_request_time_events"] = [row[2] for row in request_rows]
    index["_global_time_values"] = [row[0] for row in global_rows]
    index["_global_time_events"] = [row[2] for row in global_rows]
    state_rows: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for row in global_rows:
        event_type = str(row[2].get("event_type") or "")
        if event_type in P2_STATE_EVENT_TYPES:
            state_rows.setdefault(event_type, []).append(row)
    index["_state_time_values"] = {
        event_type: [row[0] for row in rows]
        for event_type, rows in state_rows.items()
    }
    index["_state_time_events"] = {
        event_type: [row[2] for row in rows]
        for event_type, rows in state_rows.items()
    }


def sorted_time_rows(value: Any) -> list[tuple[int, int, dict[str, Any]]]:
    if not isinstance(value, list):
        return []
    rows: list[tuple[int, int, dict[str, Any]]] = []
    for fallback_sequence, event in enumerate(value):
        if not isinstance(event, dict):
            continue
        timestamp = event_time_ns(event, as_dict(event.get("payload")))
        if timestamp is None:
            continue
        sequence = integer_or_none(event.get("_p2_index_sequence"))
        rows.append((timestamp, sequence if sequence is not None else fallback_sequence, event))
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def indexed_events_in_window(
    index: dict[str, Any],
    *,
    prefix: str,
    fallback_events: Any,
    start_ns: int | None,
    end_ns: int | None,
) -> tuple[list[dict[str, Any]], int, bool]:
    if start_ns is None or end_ns is None:
        return [], 0, True
    values = index.get(f"_{prefix}_time_values")
    events = index.get(f"_{prefix}_time_events")
    if (
        isinstance(values, list)
        and isinstance(events, list)
        and len(values) == len(events)
        and (
            not values
            or (isinstance(values[0], int) and isinstance(values[-1], int))
        )
    ):
        left = bisect_left(values, start_ns)
        right = bisect_right(values, end_ns)
        return [event for event in events[left:right] if isinstance(event, dict)], right - left, True
    candidates = [event for event in fallback_events if isinstance(event, dict)] if isinstance(fallback_events, list) else []
    return (
        [
            event
            for event in candidates
            if event_in_window(event, as_dict(event.get("payload")), start_ns, end_ns)
        ],
        len(candidates),
        False,
    )


def select_indexed_p2_events(
    index: dict[str, Any],
    *,
    run_id: str,
    response_ids: set[str],
    filter_mode: str,
    include_window_extras: bool,
    window_margin_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    query_started = time.monotonic()
    events_scanned = 0
    run_seed = list(as_list_dict(index.get("by_run_id")).get(run_id, []))
    events_scanned += len(run_seed)
    mode = "run-id"
    seed = run_seed if filter_mode in {"auto", "run-id"} else []
    if not request_identity_count(seed) and filter_mode in {"auto", "trajectory"}:
        mode = "trajectory"
        seed = [
            event
            for response_id in response_ids
            for event in (
                as_list_dict(index.get("by_api_id")).get(response_id, [])
                + as_list_dict(index.get("by_vllm_id")).get(response_id, [])
                + as_list_dict(index.get("by_alias_id")).get(response_id, [])
            )
        ]
        seed = unique_events(seed)
        events_scanned += len(seed)

    api_ids, vllm_ids = request_ids_from_events(seed)
    selected = []
    for api_id in api_ids:
        selected.extend(as_list_dict(index.get("by_api_id")).get(api_id, []))
    for vllm_id in vllm_ids:
        selected.extend(as_list_dict(index.get("by_vllm_id")).get(vllm_id, []))
    selected = [
        event
        for event in unique_events(selected)
        if is_p2_request_or_keyed_event_type(str(event.get("event_type") or ""))
    ]
    matched_times = [
        timestamp
        for event in selected
        if (timestamp := event_time_ns(event, as_dict(event.get("payload")))) is not None
    ]
    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    window_events, window_events_scanned, used_time_index = indexed_events_in_window(
        index,
        prefix="request",
        fallback_events=index.get("request_events", []),
        start_ns=window_start,
        end_ns=window_end,
    )
    events_scanned += window_events_scanned
    extras = request_ids_in_window(window_events, window_start, window_end) - api_ids
    if include_window_extras and window_start is not None and window_end is not None:
        selected.extend(window_events)
    selected = unique_events(selected)
    matched_response_ids = response_ids & api_ids
    ambiguous_api_ids = ambiguous_api_request_ids(selected)
    exact_response_ids = matched_response_ids - ambiguous_api_ids
    diagnostics = {
        "trajectory_response_id_count": len(response_ids),
        "matched_p2_request_count": (
            len(exact_response_ids)
            if response_ids
            else max(request_identity_count(selected) - len(ambiguous_api_ids), 0)
        ),
        "matched_p2_request_ids": sorted(exact_response_ids if response_ids else api_ids - ambiguous_api_ids),
        "multiple_p2_api_request_id_count": len(ambiguous_api_ids),
        "multiple_p2_api_request_ids": sorted(ambiguous_api_ids),
        "unmatched_trajectory_id_count": len(response_ids - api_ids),
        "unmatched_trajectory_ids": sorted(response_ids - api_ids),
        "p2_requests_in_task_window_not_in_trajectory_count": len(extras),
        "p2_requests_in_task_window_not_in_trajectory": sorted(extras),
        "task_window_start_mono_ns": window_start,
        "task_window_end_mono_ns": window_end,
        "extras_attribution_quality": "diagnostic_shared_window_not_request_attributed",
        "p2_source_event_count": index.get("event_count", 0),
        "p2_query_wall_s": time.monotonic() - query_started,
        "p2_bytes_scanned_per_run": 0,
        "p2_events_scanned_per_run": events_scanned,
        "p2_window_candidate_event_count": len(window_events),
        "p2_window_time_index_used": used_time_index,
        "index_cache_hit": as_dict(index.get("metrics")).get("p2_index_cache_hit"),
        "p2_index_source": as_dict(index.get("metrics")).get("p2_index_source") or index.get("source"),
    }
    return selected, diagnostics, mode


def select_indexed_p2_global_events(
    index: dict[str, Any],
    *,
    start_ns: int | None,
    end_ns: int | None,
    missing_window_policy: str = "full",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    global_value = index.get("global_events")
    global_events = global_value if isinstance(global_value, list) else []
    if start_ns is None or end_ns is None:
        if missing_window_policy == "skip":
            return [], {
                "p2_service_global_filter_mode": "skipped_missing_task_window",
                "p2_service_global_source_event_count": len(global_events),
                "p2_service_global_written_event_count": 0,
                "p2_service_global_events_scanned": 0,
                "p2_service_global_time_index_used": True,
            }
        return global_events, {
            "p2_service_global_filter_mode": "full_fallback_missing_task_window",
            "p2_service_global_source_event_count": len(global_events),
            "p2_service_global_written_event_count": len(global_events),
            "p2_service_global_events_scanned": len(global_events),
            "p2_service_global_time_index_used": False,
        }

    selected, scanned, used_time_index = indexed_events_in_window(
        index,
        prefix="global",
        fallback_events=global_events,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    latest_state: dict[str, dict[str, Any]] = {}
    state_values = index.get("_state_time_values")
    state_events = index.get("_state_time_events")
    if isinstance(state_values, dict) and isinstance(state_events, dict):
        for event_type in P2_STATE_EVENT_TYPES:
            values = state_values.get(event_type)
            events = state_events.get(event_type)
            if not isinstance(values, list) or not isinstance(events, list) or len(values) != len(events):
                continue
            position = bisect_left(values, start_ns)
            scanned += 1
            if position > 0 and isinstance(events[position - 1], dict):
                latest_state[event_type] = events[position - 1]
    else:
        for event in global_events:
            timestamp = event_time_ns(event, as_dict(event.get("payload")))
            event_type = str(event.get("event_type") or "")
            if timestamp is not None and timestamp < start_ns and event_type in P2_STATE_EVENT_TYPES:
                latest_state[event_type] = event

    filtered = unique_events(list(latest_state.values()) + selected)
    filtered.sort(
        key=lambda event: (
            event_time_ns(event, as_dict(event.get("payload"))) or -1,
            integer_or_none(event.get("_p2_index_sequence")) or -1,
        )
    )
    return filtered, {
        "p2_service_global_filter_mode": "task_window_with_prior_state",
        "p2_service_global_source_event_count": len(global_events),
        "p2_service_global_written_event_count": len(filtered),
        "p2_service_global_events_scanned": scanned,
        "p2_service_global_time_index_used": used_time_index,
    }


def filter_p2_events_for_response_ids(
    source: Path,
    output: Path,
    response_ids: set[str],
    *,
    include_window_extras: bool = False,
    include_global_events: bool = False,
    window_margin_seconds: float = 1.0,
) -> dict[str, Any]:
    events = read_jsonl_events(source)
    matched_vllm_ids: set[str] = set()
    matched_api_ids: set[str] = set()
    matched_times: list[int] = []
    for event in events:
        if not is_p2_event(event):
            continue
        event_type = str(event.get("event_type") or "")
        if not is_p2_request_event_type(event_type):
            continue
        payload = as_dict(event.get("payload"))
        api_request_id = string_or_none(payload.get("api_request_id"))
        if api_request_id not in response_ids:
            continue
        matched_api_ids.add(api_request_id)
        for key in ("vllm_request_id", "request_id"):
            value = string_or_none(payload.get(key))
            if value:
                matched_vllm_ids.add(value)
        time_ns = event_time_ns(event, payload)
        if time_ns is not None:
            matched_times.append(time_ns)

    margin_ns = int(max(window_margin_seconds, 0.0) * 1_000_000_000)
    window_start = min(matched_times) - margin_ns if matched_times else None
    window_end = max(matched_times) + margin_ns if matched_times else None
    selected: list[dict[str, Any]] = []
    for event in events:
        if not is_p2_event(event):
            continue
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        include = False
        if is_p2_request_or_keyed_event_type(event_type):
            include = p2_payload_matches(payload, matched_api_ids, matched_vllm_ids)
            if not include and include_window_extras:
                include = event_in_window(event, payload, window_start, window_end)
        elif include_global_events and event_type in P2_GLOBAL_EVENT_TYPES:
            include = event_in_window(event, payload, window_start, window_end)
        if include:
            selected.append(event)

    write_jsonl_events(output, selected)
    return {
        "source_events": len(events),
        "response_id_count": len(response_ids),
        "matched_api_request_count": len(matched_api_ids),
        "matched_vllm_request_count": len(matched_vllm_ids),
        "written_events": len(selected),
        "output": str(output),
    }


def p2_service_bounds(path: Path) -> dict[str, Any]:
    return p2_service_bounds_from_events(read_jsonl_events(path))


def p2_service_bounds_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    first_wall: int | None = None
    last_wall: int | None = None
    first_mono: int | None = None
    last_mono: int | None = None
    event_count = 0
    capability: dict[str, Any] = {}
    for event in events:
        if not is_p2_event(event):
            continue
        event_count += 1
        wall = integer_or_none(event.get("wall_time_ns"))
        mono = integer_or_none(event.get("monotonic_time_ns"))
        if wall is not None:
            first_wall = wall if first_wall is None else min(first_wall, wall)
            last_wall = wall if last_wall is None else max(last_wall, wall)
        if mono is not None:
            first_mono = mono if first_mono is None else min(first_mono, mono)
            last_mono = mono if last_mono is None else max(last_mono, mono)
        if event.get("event_type") == "vllm.adapter.capability":
            payload = as_dict(event.get("payload"))
            details = as_dict(payload.get("details"))
            if details:
                capability.update(details)
    return {
        "event_count": event_count,
        "start_wall_ns": first_wall,
        "end_wall_ns": last_wall,
        "start_mono_ns": first_mono,
        "end_mono_ns": last_mono,
        "vllm_version": capability.get("detected_vllm_version"),
        "vllm_ascend_version": capability.get("detected_vllm_ascend_version"),
        "visible_devices": capability.get("visible_devices"),
        "hook_get_computed_blocks": capability.get("hot_path_get_computed_blocks"),
        "hook_kv_blocks": capability.get("hot_path_kv_blocks"),
        "patched_targets": capability.get("patched"),
        "async_scheduling_enabled": capability.get("async_scheduling_enabled"),
        "async_scheduling_config_source": capability.get("async_scheduling_config_source"),
        "engine_core_step_hooked": capability.get("engine_core_step_hooked"),
        "engine_core_step_with_batch_queue_hooked": capability.get("engine_core_step_with_batch_queue_hooked"),
        "engine_token_boundary_collection": capability.get("engine_token_boundary_collection"),
        "prefix_cache_enabled": capability.get("prefix_cache_enabled"),
        "prefix_cache_config_source": capability.get("prefix_cache_config_source"),
        "prefix_cache_launch_source": capability.get("prefix_cache_launch_source"),
        "prefix_cache_cli_arguments": capability.get("prefix_cache_cli_arguments"),
        "prefix_cache_expectation": capability.get("prefix_cache_expectation"),
        "prefix_cache_expectation_status": capability.get("prefix_cache_expectation_status"),
    }


def ambiguous_api_request_ids(events: list[dict[str, Any]]) -> set[str]:
    engine_ids_by_api_id: dict[str, set[str]] = {}
    for event in events:
        payload = as_dict(event.get("payload"))
        api_id = string_or_none(payload.get("api_request_id"))
        if api_id is None:
            continue
        engine_ids = {
            value
            for key in ("vllm_request_id", "request_id")
            if (value := string_or_none(payload.get(key))) is not None
        }
        engine_ids_by_api_id.setdefault(api_id, set()).update(engine_ids)
    return {api_id for api_id, engine_ids in engine_ids_by_api_id.items() if len(engine_ids) > 1}


def request_ids_from_events(events: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    api_ids: set[str] = set()
    vllm_ids: set[str] = set()
    for event in events:
        payload = as_dict(event.get("payload"))
        api_id = string_or_none(payload.get("api_request_id"))
        if api_id:
            api_ids.add(api_id)
        for key in ("vllm_request_id", "request_id"):
            value = string_or_none(payload.get(key))
            if value:
                vllm_ids.add(value)
    return api_ids, vllm_ids


def request_identity_count(events: list[dict[str, Any]]) -> int:
    api_ids, vllm_ids = request_ids_from_events(events)
    return len(api_ids or vllm_ids)


def request_ids_in_window(
    events: list[dict[str, Any]],
    start_ns: int | None,
    end_ns: int | None,
) -> set[str]:
    if start_ns is None or end_ns is None:
        return set()
    ids: set[str] = set()
    for event in events:
        payload = as_dict(event.get("payload"))
        if not event_in_window(event, payload, start_ns, end_ns):
            continue
        api_id = string_or_none(payload.get("api_request_id"))
        if api_id:
            ids.add(api_id)
    return ids


def unique_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        key = str(event.get("event_id") or id(event))
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def read_jsonl_events(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl_events_with_offsets(path)["events"])


def read_jsonl_events_with_offsets(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return {"events": rows, "total_lines": 0, "invalid_lines": 0, "source_sha256": None}
    invalid_lines = 0
    total_lines = 0
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for total_lines, line in enumerate(handle, start=1):
                item = parse_jsonl_event(line)
                if item is None:
                    invalid_lines += 1 if line.strip() else 0
                    continue
                item["_source_path"] = str(path)
                item["_source_line"] = total_lines
                item["_source_offset"] = None
                rows.append(item)
        return {
            "events": rows,
            "total_lines": total_lines,
            "invalid_lines": invalid_lines,
            "source_sha256": None,
        }
    if path.suffix == ".zst":
        try:
            import zstandard as zstd  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError("zstd P2 streams require the optional zstandard package") from exc
        with path.open("rb") as raw:
            reader = zstd.ZstdDecompressor().stream_reader(raw)
            import io

            with io.TextIOWrapper(reader, encoding="utf-8", errors="replace") as handle:
                for total_lines, line in enumerate(handle, start=1):
                    item = parse_jsonl_event(line)
                    if item is None:
                        invalid_lines += 1 if line.strip() else 0
                        continue
                    item["_source_path"] = str(path)
                    item["_source_line"] = total_lines
                    item["_source_offset"] = None
                    rows.append(item)
        return {
            "events": rows,
            "total_lines": total_lines,
            "invalid_lines": invalid_lines,
            "source_sha256": None,
        }
    source_digest = sha256()
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            source_digest.update(raw_line)
            total_lines += 1
            line = raw_line.decode("utf-8", errors="replace")
            item = parse_jsonl_event(line)
            if item is None:
                invalid_lines += 1 if line.strip() else 0
                continue
            item["_source_path"] = str(path)
            item["_source_line"] = total_lines
            item["_source_offset"] = offset
            rows.append(item)
    return {
        "events": rows,
        "total_lines": total_lines,
        "invalid_lines": invalid_lines,
        "source_sha256": source_digest.hexdigest(),
    }


def parse_jsonl_event(line: str) -> dict[str, Any] | None:
    if not line.strip():
        return None
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None


def write_jsonl_events(path: Path, events: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events)
    write_private_text(path, text)


def is_p2_event(event: dict[str, Any]) -> bool:
    return event.get("source") == "vllm_p2"


def is_p2_request_event_type(event_type: str) -> bool:
    return event_type.startswith(P2_REQUEST_EVENT_PREFIX) or event_type in P2_REQUEST_EVENT_TYPES


def is_p2_request_or_keyed_event_type(event_type: str) -> bool:
    return is_p2_request_event_type(event_type) or event_type in P2_KEYED_EVENT_TYPES


def p2_payload_matches(payload: dict[str, Any], api_ids: set[str], vllm_ids: set[str]) -> bool:
    api_request_id = string_or_none(payload.get("api_request_id"))
    if api_request_id in api_ids:
        return True
    for key in ("vllm_request_id", "request_id"):
        value = string_or_none(payload.get(key))
        if value in vllm_ids:
            return True
    return False


def p2_alias_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in (
        "api_request_id",
        "vllm_request_id",
        "request_id",
        "agent_request_id",
        "agentmon_model_request_id",
        "model_call_id",
        "client_request_id",
        "server_request_id",
        "response_id",
    ):
        value = string_or_none(payload.get(key))
        if value and value not in ids:
            ids.append(value)
    return ids


def event_in_window(
    event: dict[str, Any],
    payload: dict[str, Any],
    start_ns: int | None,
    end_ns: int | None,
) -> bool:
    if start_ns is None or end_ns is None:
        return False
    time_ns = event_time_ns(event, payload)
    return time_ns is not None and start_ns <= time_ns <= end_ns


def event_time_ns(event: dict[str, Any], payload: dict[str, Any]) -> int | None:
    for key in (
        "received_ns",
        "queued_ns",
        "first_scheduled_ns",
        "first_token_ns",
        "first_output_ns",
        "engine_finished_ns",
        "finished_ns",
    ):
        value = integer_or_none(payload.get(key))
        if value is not None:
            return value
    return integer_or_none(event.get("monotonic_time_ns"))


def as_list_dict(value: Any) -> dict[str, list[dict[str, Any]]]:
    return value if isinstance(value, dict) else {}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: Any) -> str | None:
    if is_blank_value(value):
        return None
    return str(value)


def is_blank_value(value: Any) -> bool:
    return value is None or value == ""


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def source_fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    size = stat.st_size
    offsets = sorted(
        {
            0,
            max((size - P2_FINGERPRINT_SAMPLE_BYTES) // 2, 0),
            max(size - P2_FINGERPRINT_SAMPLE_BYTES, 0),
        }
    )
    digest = sha256()
    with resolved.open("rb") as handle:
        for offset in offsets:
            handle.seek(offset)
            digest.update(offset.to_bytes(8, "big", signed=False))
            digest.update(handle.read(P2_FINGERPRINT_SAMPLE_BYTES))
    return {
        "path": str(resolved),
        "exists": True,
        "size": size,
        "mtime_ns": stat.st_mtime_ns,
        "sample_sha256": digest.hexdigest(),
    }


def file_hash_if_exists(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_private(path: Path, payload: dict[str, Any]) -> None:
    write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    chmod_private_file(path)
