from __future__ import annotations

from typing import Any

from agentmon.adapters.inference.vllm_018.p2_index import is_p2_request_or_keyed_event_type
from agentmon.step_sequence.constants import (
    P2_CANONICAL_REQUEST_ID_PRIORITY,
    P2_IDENTITY_KEYS,
    P2_REQUIRED_LIFECYCLE_EVENTS,
    P2_VALUE_KEYS,
    REQUEST_TIME_KEYS,
)
from agentmon.step_sequence.utils import (
    as_dict,
    count_values,
    first_int,
    first_value,
    integer_or_none,
    max_present,
    mean_present,
    min_present,
    nonzero,
    ns_delta,
    ns_to_s,
    number_or_none,
    ratio_or_none,
    sort_time,
    sum_numeric,
    sum_present,
    text_or_none,
)
from agentmon.step_sequence.utils import *  # noqa: F403 - shared internal P2 helpers

def build_p2_index(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_key: dict[str, list[dict[str, Any]]] = {}
    snapshots: list[dict[str, Any]] = []
    for event in events:
        if event.get("source") != "vllm_p2":
            continue
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        if event_type == "vllm.kv.snapshot":
            snapshots.append(kv_snapshot_row(payload, event))
            continue
        if not is_p2_request_or_keyed_event_type(event_type):
            continue
        keys = p2_row_keys(payload)
        if not keys:
            continue
        matched_rows = unique_p2_rows(
            row
            for key in keys
            for row in rows_by_key.get(key, [])
            if p2_row_identity_compatible(row, payload)
        )
        row = matched_rows[0] if matched_rows else {}
        for duplicate in matched_rows[1:]:
            merge_p2_rows(row, duplicate)
            for existing_key, existing_rows in list(rows_by_key.items()):
                rows_by_key[existing_key] = unique_p2_rows(
                    row if existing_row is duplicate else existing_row
                    for existing_row in existing_rows
                )
        for key in keys:
            bucket = rows_by_key.setdefault(key, [])
            if all(existing is not row for existing in bucket):
                bucket.append(row)
        merge_p2_payload(row, payload, event_type=event_type, event=event)

    rows = unique_p2_rows(row for bucket in rows_by_key.values() for row in bucket)
    by_id: dict[str, dict[str, Any]] = {}
    rows_by_identity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        finalize_p2_lifecycle(row)
        for key in P2_IDENTITY_KEYS:
            value = row.get(key)
            if value not in {None, ""}:
                rows_by_identity.setdefault(str(value), []).append(row)
    ambiguous_ids: dict[str, int] = {}
    for identity, identity_rows in rows_by_identity.items():
        unique_rows = unique_p2_rows(identity_rows)
        if len(unique_rows) == 1:
            by_id[identity] = unique_rows[0]
        else:
            ambiguous_ids[identity] = len(unique_rows)
    snapshots.sort(key=lambda row: row.get("monotonic_time_ns") if isinstance(row.get("monotonic_time_ns"), int) else -1)
    return {"rows": rows, "by_id": by_id, "ambiguous_ids": ambiguous_ids, "snapshots": snapshots}


def p2_row_identity_compatible(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    existing_vllm_id = first_value(row.get("vllm_request_id"), row.get("request_id"))
    incoming_vllm_id = first_value(payload.get("vllm_request_id"), payload.get("request_id"))
    if existing_vllm_id not in {None, ""} and incoming_vllm_id not in {None, ""}:
        if str(existing_vllm_id) != str(incoming_vllm_id):
            return False
    for key in ("agentmon_model_request_id", "agent_request_id"):
        existing = row.get(key)
        incoming = payload.get(key)
        if existing not in {None, ""} and incoming not in {None, ""} and str(existing) != str(incoming):
            return False
    return True


def p2_row_key(payload: dict[str, Any]) -> str | None:
    for key in P2_IDENTITY_KEYS:
        value = payload.get(key)
        if value not in {None, ""}:
            return f"{key}:{value}"
    return None


def p2_row_keys(payload: dict[str, Any]) -> list[str]:
    keys = []
    for key in P2_IDENTITY_KEYS:
        value = payload.get(key)
        if value not in {None, ""}:
            keys.append(f"{key}:{value}")
    return keys


def merge_p2_rows(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key in {
            "_event_types",
            "_scheduler_iterations",
            "_kv_lifecycle",
            "_moe_routing_summaries",
        }:
            if key != "_event_types":
                target.setdefault(key, []).extend(value if isinstance(value, list) else [])
                continue
            current = target.setdefault("_event_types", [])
            for event_type in value if isinstance(value, list) else []:
                if event_type not in current:
                    current.append(event_type)
        elif key == "_moe_routing_chunk_count":
            target[key] = int(target.get(key) or 0) + int(value or 0)
        elif target.get(key) is None and value is not None:
            target[key] = value


def finalize_p2_lifecycle(row: dict[str, Any]) -> None:
    event_types = set(row.get("_event_types") or [])
    scheduler_iterations = list(row.get("_scheduler_iterations") or [])
    kv_lifecycle = list(row.get("_kv_lifecycle") or [])
    moe_summaries = list(row.get("_moe_routing_summaries") or [])
    required = list(P2_REQUIRED_LIFECYCLE_EVENTS)
    generation_tokens = integer_or_none(row.get("generation_tokens"))
    if generation_tokens is not None and generation_tokens > 0:
        required.extend(
            [
                "vllm.request.first_token",
                "vllm.request.first_output",
                "vllm.request.engine_finished",
            ]
        )
    if any(item.get("prefill_completion_candidate") is True for item in scheduler_iterations):
        required.append("vllm.request.prefill_completed")
    missing = [event_type for event_type in required if event_type not in event_types]
    row["lifecycle_event_types"] = sorted(event_types)
    row["lifecycle_missing_events"] = missing
    row["lifecycle_status"] = "complete" if not missing else "partial"
    row["scheduler_iteration_count"] = len(scheduler_iterations)
    row["prefill_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("scheduler_phase") == "prefill"
    )
    row["decode_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("scheduler_phase") == "decode"
    )
    row["chunked_prefill_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("chunked_prefill") is True
    )
    row["mixed_batch_iteration_count"] = sum(
        1 for item in scheduler_iterations if item.get("mixed_prefill_decode_batch") is True
    )
    row["max_batch_request_count"] = max_present(
        item.get("batch_request_count") for item in scheduler_iterations
    )
    row["total_scheduled_tokens"] = sum_present(
        item.get("scheduled_token_count") for item in scheduler_iterations
    )
    row["scheduled_prompt_tokens"] = sum_present(
        item.get("scheduled_token_count")
        for item in scheduler_iterations
        if item.get("scheduler_phase") == "prefill"
    )
    row["scheduled_decode_tokens"] = sum_present(
        item.get("scheduled_token_count")
        for item in scheduler_iterations
        if item.get("scheduler_phase") == "decode"
    )
    uncached = number_or_none(row.get("uncached_prompt_tokens"))
    if uncached is None:
        uncached = number_or_none(row.get("computed_prefill_tokens"))
    scheduled_prompt = number_or_none(row.get("scheduled_prompt_tokens"))
    if uncached is not None and scheduled_prompt is not None:
        row["recomputed_prompt_tokens"] = max(0, int(scheduled_prompt - uncached))
        row["recomputed_token_quality"] = "derived_scheduler_prefill_minus_uncached"
    else:
        row.setdefault("recomputed_prompt_tokens", None)
        row.setdefault("recomputed_token_quality", "unavailable_not_observed")
    row["kv_lifecycle_event_count"] = len(kv_lifecycle)
    row["kv_lifecycle_operation_counts"] = count_values(
        item.get("operation") or "unknown" for item in kv_lifecycle
    )
    for key in (
        "allocated_block_count",
        "cached_block_count",
        "cache_hit_block_count",
        "freed_block_count",
        "evicted_block_count",
    ):
        total = sum_present(item.get(key) for item in kv_lifecycle)
        if total is not None:
            row[key] = total
    if kv_lifecycle:
        before_values = [number_or_none(item.get("kv_usage_before")) for item in kv_lifecycle]
        after_values = [number_or_none(item.get("kv_usage_after")) for item in kv_lifecycle]
        row["kv_usage_before"] = next(
            (value for value in before_values if value is not None),
            row.get("kv_usage_before"),
        )
        row["kv_usage_after"] = next(
            (value for value in reversed(after_values) if value is not None),
            row.get("kv_usage_after"),
        )
    add_kv_residency(row, kv_lifecycle)
    row["moe_routing_summary_count"] = len(moe_summaries)
    row["moe_routing_chunk_count"] = int(row.get("_moe_routing_chunk_count") or 0)
    if moe_summaries:
        status_counts = count_values(item.get("status") for item in moe_summaries)
        row["moe_routing_status"] = (
            "partial"
            if status_counts.get("PARTIAL") or status_counts.get("UNRESOLVED")
            else "complete"
        )
        row["moe_routing_mode"] = next(
            (item.get("mode") for item in moe_summaries if item.get("mode") is not None),
            None,
        )
        row["moe_routed_token_count"] = sum_present(
            item.get("routed_token_count") for item in moe_summaries
        )
        row["moe_assignment_count"] = sum_present(
            item.get("assignment_count") for item in moe_summaries
        )
        row["moe_max_expert_share"] = max_present(
            item.get("max_expert_share") for item in moe_summaries
        )
        row["moe_min_normalized_entropy"] = min_present(
            item.get("min_normalized_entropy") for item in moe_summaries
        )
        row["moe_max_load_coefficient_of_variation"] = max_present(
            item.get("max_load_coefficient_of_variation") for item in moe_summaries
        )
        row["moe_token_alignment_counts"] = count_values(
            item.get("token_alignment") for item in moe_summaries
        )
    else:
        row["moe_routing_status"] = "missing"


def add_kv_residency(row: dict[str, Any], lifecycle: list[dict[str, Any]]) -> None:
    allocation_times = [
        integer_or_none(item.get("monotonic_time_ns"))
        for item in lifecycle
        if item.get("operation") == "allocate"
    ]
    release_times = [
        integer_or_none(item.get("monotonic_time_ns"))
        for item in lifecycle
        if item.get("operation") == "free"
    ]
    allocation_times = [value for value in allocation_times if value is not None]
    release_times = [value for value in release_times if value is not None]
    row["kv_allocated_ns"] = min(allocation_times) if allocation_times else None
    row["kv_released_ns"] = max(release_times) if release_times else None
    row["kv_residency_s"] = ns_to_s(ns_delta(row["kv_allocated_ns"], row["kv_released_ns"]))
    row["kv_residency_quality"] = (
        "exact_request_lifecycle"
        if row["kv_residency_s"] is not None
        else "partial_lifecycle"
        if allocation_times or release_times
        else "unavailable_not_observed"
    )


def unique_p2_rows(rows: Any) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        row_id = id(row)
        if row_id in seen:
            continue
        seen.add(row_id)
        unique.append(row)
    return unique


def merge_p2_payload(row: dict[str, Any], payload: dict[str, Any], *, event_type: str, event: dict[str, Any]) -> None:
    if event_type == "vllm.moe.routing.summary":
        row.setdefault("_moe_routing_summaries", []).append(dict(payload))
    elif event_type == "vllm.moe.routing.chunk":
        row["_moe_routing_chunk_count"] = int(row.get("_moe_routing_chunk_count") or 0) + 1
    if event_type == "vllm.request.scheduler_iteration":
        row.setdefault("_scheduler_iterations", []).append(
            {
                key: payload.get(key)
                for key in (
                    "scheduler_iteration_id",
                    "scheduler_phase",
                    "scheduled_token_count",
                    "chunked_prefill",
                    "prefill_completion_candidate",
                    "batch_request_count",
                    "mixed_prefill_decode_batch",
                    "running_requests_after_schedule",
                    "waiting_requests_after_schedule",
                )
            }
        )
    if event_type == "vllm.kv.block_event":
        row.setdefault("_kv_lifecycle", []).append(
            {
                key: payload.get(key)
                for key in (
                    "operation",
                    "allocated_block_count",
                    "cached_block_count",
                    "cache_hit_block_count",
                    "freed_block_count",
                    "evicted_block_count",
                    "kv_usage_before",
                    "kv_usage_after",
                    "kv_quality",
                )
            }
        )
        row["_kv_lifecycle"][-1]["monotonic_time_ns"] = event.get("monotonic_time_ns")
    for key in P2_VALUE_KEYS:
        value = payload.get(key)
        if value is not None:
            target = "vllm_request_id" if key == "request_id" else key
            if event_type == "vllm.kv.block_event" and key in {
                "allocated_block_count",
                "cached_block_count",
                "cache_hit_block_count",
                "freed_block_count",
                "evicted_block_count",
            }:
                row[target] = (number_or_none(row.get(target)) or 0) + value
            elif event_type == "vllm.kv.block_event" and key == "kv_usage_before":
                row.setdefault(target, value)
            else:
                row[target] = value
    for key in REQUEST_TIME_KEYS:
        value = integer_or_none(payload.get(key))
        if value is not None:
            row[key] = value
    fallback_time = integer_or_none(event.get("monotonic_time_ns"))
    fallback_wall_time = integer_or_none(event.get("wall_time_ns"))
    event_time_key = {
        "vllm.request.received": "received_ns",
        "vllm.request.queued": "queued_ns",
        "vllm.request.first_scheduled": "first_scheduled_ns",
        "vllm.request.prompt_cache_accounted": "prompt_cache_accounted_ns",
        "vllm.request.prefill_completed": "prefill_completed_ns",
        "vllm.request.first_token": "first_token_ns",
        "vllm.request.first_output": "first_output_ns",
        "vllm.request.engine_finished": "engine_finished_ns",
        "vllm.request.finished": "finished_ns",
    }.get(event_type)
    if event_time_key and row.get(event_time_key) is None and fallback_time is not None:
        row[event_time_key] = fallback_time
    event_wall_time_key = {
        "vllm.request.received": "received_wall_time_ns",
        "vllm.request.queued": "queued_wall_time_ns",
        "vllm.request.first_scheduled": "first_scheduled_wall_time_ns",
        "vllm.request.prompt_cache_accounted": "prompt_cache_accounted_wall_time_ns",
        "vllm.request.prefill_completed": "prefill_completed_wall_time_ns",
        "vllm.request.first_token": "first_token_wall_time_ns",
        "vllm.request.first_output": "first_output_wall_time_ns",
        "vllm.request.engine_finished": "engine_finished_wall_time_ns",
        "vllm.request.finished": "finished_wall_time_ns",
    }.get(event_type)
    if event_wall_time_key and row.get(event_wall_time_key) is None and fallback_wall_time is not None:
        row[event_wall_time_key] = fallback_wall_time
    timestamp_prefix = {
        "vllm.request.received": "received",
        "vllm.request.queued": "queued",
        "vllm.request.first_scheduled": "first_scheduled",
        "vllm.request.prompt_cache_accounted": "prompt_cache_accounted",
        "vllm.request.prefill_completed": "prefill_completed",
        "vllm.request.first_token": "first_token",
        "vllm.request.first_output": "first_output",
        "vllm.request.engine_finished": "engine_finished",
        "vllm.request.finished": "finished",
    }.get(event_type)
    if timestamp_prefix:
        for key in ("source_pid", "source_host", "clock_domain", "host_boot_id"):
            if payload.get(key) is not None:
                row[f"{timestamp_prefix}_{key}"] = payload[key]
                if event_type == "vllm.request.engine_finished" and payload.get("last_token_ns") is not None:
                    row[f"last_token_{key}"] = payload[key]
    event_types = row.setdefault("_event_types", [])
    if event_type not in event_types:
        event_types.append(event_type)
    row["last_event_type"] = event_type


def find_p2_match(span: dict[str, Any], p2_index: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    start_payload = span["start_payload"]
    end_payload = span["end_payload"]
    attributes = span["attributes"]
    candidates = [
        ("api_response_id", end_payload.get("api_request_id")),
        ("api_response_id", end_payload.get("response_id")),
        ("agentmon_model_request_id", start_payload.get("agentmon_model_request_id")),
        ("agentmon_model_request_id", end_payload.get("agentmon_model_request_id")),
        ("agentmon_model_request_id", attributes.get("agentmon_model_request_id")),
        ("request_id", start_payload.get("request_id")),
        ("request_id", end_payload.get("request_id")),
        ("request_id", attributes.get("request_id")),
        ("span_id", span.get("span_id")),
    ]
    by_id = p2_index["by_id"]
    ambiguous_ids = as_dict(p2_index.get("ambiguous_ids"))
    available = []
    ambiguous = []
    for method, candidate in candidates:
        if candidate not in {None, ""}:
            available.append(str(candidate))
            if str(candidate) in ambiguous_ids:
                ambiguous.append(str(candidate))
        if candidate not in {None, ""} and str(candidate) in by_id:
            return by_id[str(candidate)], {
                "p2_match_status": "matched",
                "p2_match_method": method,
                "p2_match_confidence": "exact",
                "p2_missing_reason": None,
            }
    if ambiguous:
        return {}, {
            "p2_match_status": "multiple",
            "p2_match_method": "ambiguous_identity",
            "p2_match_confidence": "none",
            "p2_missing_reason": "multiple_p2_requests_share_identifier",
        }
    if not p2_index.get("rows"):
        reason = "p2_request_events_missing"
    elif not available:
        reason = "p1_model_span_has_no_correlation_identifier"
    else:
        reason = "p1_identifiers_not_found_in_p2"
    return {}, {
        "p2_match_status": "unmatched",
        "p2_match_method": None,
        "p2_match_confidence": "none",
        "p2_missing_reason": reason,
    }


def find_p2_row(span: dict[str, Any], p2_index: dict[str, Any]) -> dict[str, Any]:
    row, _ = find_p2_match(span, p2_index)
    return row


def p2_projection(
    row: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    *,
    client_span: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not row:
        return None
    prompt_tokens = integer_or_none(row.get("prompt_tokens"))
    cached_prompt_tokens = integer_or_none(row.get("cached_prompt_tokens"))
    recomputed_prompt_tokens = integer_or_none(row.get("recomputed_prompt_tokens"))
    usage_before = number_or_none(row.get("kv_usage_before"))
    usage_after = number_or_none(row.get("kv_usage_after"))
    queue_start_ns = row.get("queued_ns") or row.get("received_ns")
    queue_start_prefix = "queued" if row.get("queued_ns") is not None else "received"
    decode_end_ns = row.get("engine_finished_ns")
    decode_end_prefix = "engine_finished"
    queue_quality = timestamp_pair_quality(
        row,
        queue_start_prefix,
        "first_scheduled",
        same_process_label="engine_process_monotonic",
    )
    ttft_quality = timestamp_pair_quality(row, "received", "first_token")
    engine_ttft_quality = timestamp_pair_quality(
        row,
        "queued",
        "first_token",
        same_process_label="engine_process_monotonic",
    )
    prefill_quality = timestamp_pair_quality(
        row,
        "first_scheduled",
        "prefill_completed",
        same_process_label="engine_process_monotonic",
    )
    decode_quality = timestamp_pair_quality(
        row,
        "first_token",
        decode_end_prefix,
        same_process_label="engine_process_monotonic",
    )
    e2e_quality = timestamp_pair_quality(
        row,
        "received",
        "finished",
        same_process_label="api_process_monotonic",
    )
    first_output_quality = timestamp_pair_quality(row, "received", "first_output")
    proxy_decode_quality = timestamp_pair_quality(row, "first_output", "finished")
    engine_first_token = row.get("first_token_timestamp_source") in {
        None,
        "engine_core",
        "engine_core_output",
        "engine_output",
        "engine_observed",
    }
    exact_prefill = (
        ns_to_s(ns_delta(row.get("first_scheduled_ns"), row.get("prefill_completed_ns")))
        if verified_timestamp_quality(prefill_quality)
        and row.get("prefill_boundary_quality") in {
            "exact",
            "execution_observed",
            "derived_execution_observed",
            "engine_execution_observed",
        }
        else None
    )
    exact_ttft = (
        ns_to_s(ns_delta(row.get("received_ns"), row.get("first_token_ns")))
        if engine_first_token and verified_timestamp_quality(ttft_quality)
        else None
    )
    exact_decode = (
        ns_to_s(ns_delta(row.get("first_token_ns"), decode_end_ns))
        if engine_first_token and verified_timestamp_quality(decode_quality)
        else None
    )
    exact_tpot = token_period_seconds(
        row.get("first_token_ns"),
        row.get("last_token_ns"),
        row.get("generation_tokens"),
        timestamp_pair_quality(
            row,
            "first_token",
            "last_token",
            same_process_label="engine_process_monotonic",
        ),
    )
    client_stages = client_server_stage_projection(client_span, row)
    canonical_identity = canonical_p2_request_identity(row)
    return {
        "canonical_request_id": canonical_identity.get("value"),
        "canonical_request_id_kind": canonical_identity.get("kind"),
        "request_id": canonical_identity.get("value"),
        "request_id_kind": canonical_identity.get("kind"),
        "request_id_aliases": p2_request_identity_aliases(row),
        "response_id": row.get("api_request_id") or row.get("server_request_id"),
        "trajectory_request_id": row.get("api_request_id") or row.get("server_request_id"),
        "trajectory_request_id_quality": "api_response_id_candidate"
        if row.get("api_request_id") or row.get("server_request_id")
        else None,
        "model_call_id": row.get("model_call_id") or row.get("agentmon_model_request_id"),
        "client_request_id": row.get("client_request_id") or row.get("agentmon_model_request_id"),
        "server_request_id": row.get("server_request_id") or row.get("api_request_id"),
        "engine_request_id": row.get("engine_request_id") or row.get("vllm_request_id"),
        "agent_request_id": row.get("agent_request_id"),
        "agentmon_run_id": row.get("agentmon_run_id"),
        "agentmon_model_request_id": row.get("agentmon_model_request_id"),
        "vllm_request_id": row.get("vllm_request_id"),
        "api_request_id": row.get("api_request_id"),
        "correlation_quality": row.get("correlation_quality"),
        "lifecycle_status": row.get("lifecycle_status"),
        "lifecycle_missing_events": row.get("lifecycle_missing_events"),
        "lifecycle_event_types": row.get("lifecycle_event_types"),
        "received_wall_time_s": ns_to_s(row.get("received_wall_time_ns")),
        "finished_wall_time_s": ns_to_s(row.get("finished_wall_time_ns")),
        "e2e_s": ns_to_s(ns_delta(row.get("received_ns"), row.get("finished_ns"))),
        "queue_s": ns_to_s(ns_delta(queue_start_ns, row.get("first_scheduled_ns"))),
        "ttft_s": ns_to_s(ns_delta(row.get("received_ns"), row.get("first_token_ns"))),
        "engine_ttft_s": ns_to_s(ns_delta(row.get("queued_ns"), row.get("first_token_ns"))),
        "prefill_s": ns_to_s(ns_delta(row.get("first_scheduled_ns"), row.get("prefill_completed_ns"))),
        "prefill_to_first_token_s": ns_to_s(ns_delta(row.get("first_scheduled_ns"), row.get("first_token_ns"))),
        "decode_s": ns_to_s(ns_delta(row.get("first_token_ns"), decode_end_ns)),
        "first_output_s": ns_to_s(ns_delta(row.get("received_ns"), row.get("first_output_ns"))),
        "exact_ttft_s": exact_ttft,
        "exact_prefill_s": exact_prefill,
        "exact_decode_s": exact_decode,
        "exact_tpot_s": exact_tpot,
        "engine_residence_s": (
            ns_to_s(ns_delta(row.get("first_scheduled_ns"), decode_end_ns))
            if verified_timestamp_quality(
                timestamp_pair_quality(
                    row,
                    "first_scheduled",
                    "engine_finished",
                    same_process_label="engine_process_monotonic",
                )
            )
            else None
        ),
        "proxy_ttft_s": (
            ns_to_s(ns_delta(row.get("received_ns"), row.get("first_output_ns")))
            if verified_timestamp_quality(first_output_quality)
            else None
        ),
        "proxy_decode_s": (
            ns_to_s(ns_delta(row.get("first_output_ns"), row.get("finished_ns")))
            if verified_timestamp_quality(proxy_decode_quality)
            else None
        ),
        "queue_quality": queue_quality,
        "ttft_quality": ttft_quality,
        "engine_ttft_quality": engine_ttft_quality,
        "prefill_quality": prefill_quality,
        "prefill_boundary_source": row.get("prefill_boundary_source"),
        "prefill_boundary_quality": row.get("prefill_boundary_quality"),
        "decode_quality": decode_quality if row.get("first_token_ns") is not None and decode_end_ns is not None else None,
        "e2e_quality": e2e_quality,
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "uncached_prompt_tokens": integer_or_none(
            row.get("uncached_prompt_tokens")
        ),
        "computed_prefill_tokens": integer_or_none(row.get("computed_prefill_tokens")),
        "recomputed_prompt_tokens": recomputed_prompt_tokens,
        "recomputed_token_quality": row.get("recomputed_token_quality") or "unavailable_not_observed",
        "prompt_token_accounting_quality": row.get("prompt_token_accounting_quality"),
        "generation_tokens": integer_or_none(row.get("generation_tokens")),
        "preemption_count": integer_or_none(row.get("preemption_count")),
        "scheduler_iteration_count": integer_or_none(row.get("scheduler_iteration_count")),
        "prefill_iteration_count": integer_or_none(row.get("prefill_iteration_count")),
        "decode_iteration_count": integer_or_none(row.get("decode_iteration_count")),
        "chunked_prefill_iteration_count": integer_or_none(row.get("chunked_prefill_iteration_count")),
        "mixed_batch_iteration_count": integer_or_none(row.get("mixed_batch_iteration_count")),
        "max_batch_request_count": integer_or_none(row.get("max_batch_request_count")),
        "total_scheduled_tokens": integer_or_none(row.get("total_scheduled_tokens")),
        "first_scheduler_iteration_id": integer_or_none(
            row.get("first_scheduler_iteration_id")
        ),
        "last_scheduler_iteration_id": integer_or_none(
            row.get("last_scheduler_iteration_id")
        ),
        "first_batch_enter_ns": integer_or_none(row.get("first_batch_enter_ns")),
        "last_batch_exit_ns": integer_or_none(row.get("last_batch_exit_ns")),
        "batch_residence_s_total": number_or_none(
            row.get("batch_residence_s_total")
        ),
        "finish_reason": row.get("finish_reason"),
        "cache_reuse_rate": ratio_or_none(cached_prompt_tokens, prompt_tokens),
        "recomputed_prompt_rate": (
            ratio_or_none(recomputed_prompt_tokens, prompt_tokens)
            if row.get("recomputed_token_quality") not in {None, "unavailable", "unavailable_not_observed"}
            else None
        ),
        "queue_depth_on_arrival": integer_or_none(row.get("queue_depth_on_arrival")),
        "running_requests_on_arrival": integer_or_none(row.get("running_requests_on_arrival")),
        "running_requests_after_schedule": integer_or_none(row.get("running_requests_after_schedule")),
        "waiting_requests_after_schedule": integer_or_none(row.get("waiting_requests_after_schedule")),
        "scheduled_prompt_tokens": integer_or_none(row.get("scheduled_prompt_tokens")),
        "scheduled_decode_tokens": integer_or_none(row.get("scheduled_decode_tokens")),
        "kv_usage_before": usage_before,
        "kv_usage_after": usage_after,
        "kv_usage_delta": usage_after - usage_before if usage_before is not None and usage_after is not None else None,
        "allocated_block_count": integer_or_none(row.get("allocated_block_count")),
        "cached_block_count": integer_or_none(row.get("cached_block_count")),
        "cache_hit_block_count": integer_or_none(row.get("cache_hit_block_count")),
        "freed_block_count": integer_or_none(row.get("freed_block_count")),
        "evicted_block_count": integer_or_none(row.get("evicted_block_count")),
        "kv_quality": row.get("kv_quality"),
        "kv_lifecycle_event_count": integer_or_none(row.get("kv_lifecycle_event_count")),
        "kv_lifecycle_operation_counts": row.get("kv_lifecycle_operation_counts"),
        "kv_allocated_ns": integer_or_none(row.get("kv_allocated_ns")),
        "kv_released_ns": integer_or_none(row.get("kv_released_ns")),
        "kv_residency_s": number_or_none(row.get("kv_residency_s")),
        "kv_residency_quality": row.get("kv_residency_quality"),
        "moe_routing_status": row.get("moe_routing_status"),
        "moe_routing_mode": row.get("moe_routing_mode"),
        "moe_routing_summary_count": integer_or_none(row.get("moe_routing_summary_count")),
        "moe_routing_chunk_count": integer_or_none(row.get("moe_routing_chunk_count")),
        "moe_routed_token_count": integer_or_none(row.get("moe_routed_token_count")),
        "moe_assignment_count": integer_or_none(row.get("moe_assignment_count")),
        "moe_max_expert_share": number_or_none(row.get("moe_max_expert_share")),
        "moe_min_normalized_entropy": number_or_none(row.get("moe_min_normalized_entropy")),
        "moe_max_load_coefficient_of_variation": number_or_none(
            row.get("moe_max_load_coefficient_of_variation")
        ),
        "moe_token_alignment_counts": row.get("moe_token_alignment_counts"),
        "kv_snapshot": kv_snapshot_projection(snapshot),
        **client_stages,
    }


def verified_timestamp_quality(value: Any) -> bool:
    return value not in {None, "unavailable", "legacy_unverified", "clock_domain_mismatch"}


def token_period_seconds(
    first_token_ns: Any,
    last_token_ns: Any,
    generation_tokens: Any,
    quality: Any,
) -> float | None:
    token_count = integer_or_none(generation_tokens)
    if token_count is None or token_count <= 1 or not verified_timestamp_quality(quality):
        return None
    duration = ns_to_s(ns_delta(first_token_ns, last_token_ns))
    return duration / (token_count - 1) if duration is not None else None


def client_server_stage_projection(
    span: dict[str, Any] | None,
    p2_row: dict[str, Any],
) -> dict[str, Any]:
    if not span:
        return {
            "client_model_call_to_server_received_s": None,
            "server_finished_to_client_return_s": None,
            "client_observed_e2e_s": None,
            "client_non_server_overhead_s": None,
            "client_to_server_quality": None,
            "server_to_client_quality": None,
        }
    start_payload = as_dict(span.get("start_payload"))
    end_payload = as_dict(span.get("end_payload"))
    outbound_quality = cross_process_timestamp_quality(start_payload, p2_row, "received")
    inbound_quality = cross_process_timestamp_quality(end_payload, p2_row, "finished")
    client_e2e = number_or_none(span.get("duration_s"))
    server_e2e = ns_to_s(ns_delta(p2_row.get("received_ns"), p2_row.get("finished_ns")))
    return {
        "client_model_call_to_server_received_s": verified_delta_s(
            span.get("start_mono_ns"),
            p2_row.get("received_ns"),
            outbound_quality,
        ),
        "server_finished_to_client_return_s": verified_delta_s(
            p2_row.get("finished_ns"),
            span.get("end_mono_ns"),
            inbound_quality,
        ),
        "client_observed_e2e_s": client_e2e,
        "client_non_server_overhead_s": (
            client_e2e - server_e2e
            if client_e2e is not None and server_e2e is not None
            else None
        ),
        "client_to_server_quality": outbound_quality,
        "server_to_client_quality": inbound_quality,
    }


def cross_process_timestamp_quality(
    client_payload: dict[str, Any],
    p2_row: dict[str, Any],
    p2_prefix: str,
) -> str:
    client_domain = client_payload.get("clock_domain")
    p2_domain = p2_row.get(f"{p2_prefix}_clock_domain")
    client_host = client_payload.get("source_host")
    p2_host = p2_row.get(f"{p2_prefix}_source_host")
    if client_domain is not None and p2_domain is not None and client_domain != p2_domain:
        return "clock_domain_mismatch"
    if client_host is not None and p2_host is not None and client_host != p2_host:
        return "clock_domain_mismatch"
    if client_domain is not None and client_domain == p2_domain:
        return "same_host_cross_process_monotonic"
    return "legacy_unverified"


def verified_delta_s(start_ns: Any, end_ns: Any, quality: str) -> float | None:
    if quality == "clock_domain_mismatch":
        return None
    return ns_to_s(ns_delta(start_ns, end_ns))


def timestamp_pair_quality(
    row: dict[str, Any],
    start_prefix: str,
    end_prefix: str,
    *,
    same_process_label: str = "same_process_monotonic",
) -> str | None:
    if row.get(f"{start_prefix}_ns") is None or row.get(f"{end_prefix}_ns") is None:
        return None
    start_domain = row.get(f"{start_prefix}_clock_domain")
    end_domain = row.get(f"{end_prefix}_clock_domain")
    start_host = row.get(f"{start_prefix}_source_host")
    end_host = row.get(f"{end_prefix}_source_host")
    start_pid = integer_or_none(row.get(f"{start_prefix}_source_pid"))
    end_pid = integer_or_none(row.get(f"{end_prefix}_source_pid"))
    if start_domain is not None and end_domain is not None and start_domain != end_domain:
        return "clock_domain_mismatch"
    if start_host is not None and end_host is not None and start_host != end_host:
        return "clock_domain_mismatch"
    if start_pid is not None and end_pid is not None and start_pid == end_pid:
        return same_process_label
    if start_domain is not None and start_domain == end_domain:
        return "same_host_cross_process_monotonic"
    return "legacy_unverified"


def kv_snapshot_row(payload: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    row = {
        "monotonic_time_ns": integer_or_none(event.get("monotonic_time_ns")),
        "prefix_cache_status": payload.get("prefix_cache_status"),
        "prefix_cache_queries": integer_or_none(payload.get("prefix_cache_queries")),
        "prefix_cache_hits": integer_or_none(payload.get("prefix_cache_hits")),
        "kv_usage_ratio": number_or_none(payload.get("kv_usage_ratio")),
    }
    kv_pool = as_dict(payload.get("kv_pool"))
    if kv_pool:
        row["kv_pool"] = kv_pool
    return row


def nearest_kv_snapshot(snapshots: list[dict[str, Any]], mono_ns: int | None) -> dict[str, Any] | None:
    if not snapshots:
        return None
    if mono_ns is None:
        return snapshots[-1]
    previous = [row for row in snapshots if isinstance(row.get("monotonic_time_ns"), int) and row["monotonic_time_ns"] <= mono_ns]
    if previous:
        return previous[-1]
    return snapshots[0]


def kv_snapshot_projection(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    queries = integer_or_none(snapshot.get("prefix_cache_queries"))
    hits = integer_or_none(snapshot.get("prefix_cache_hits"))
    status = snapshot.get("prefix_cache_status")
    kv_pool = as_dict(snapshot.get("kv_pool"))
    return {
        "prefix_cache_status": status,
        "prefix_cache_queries": queries,
        "prefix_cache_hits": hits,
        "prefix_cache_hit_rate": None if status == "disabled" else ratio_or_none(hits, queries),
        "kv_usage_ratio": number_or_none(snapshot.get("kv_usage_ratio")),
        "kv_pool_allocated_bytes_per_rank": integer_or_none(kv_pool.get("allocated_pool_bytes_per_rank")),
        "kv_pool_bytes_per_block_per_rank": integer_or_none(kv_pool.get("bytes_per_block_per_rank")),
        "kv_pool_source_quality": kv_pool.get("source_quality"),
        "observation_scope": "shared_service_observation",
        "request_attribution": "not_request_attributed",
    }


def step_kv_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    model_items = [item for item in items if item.get("item_type") == "model"]
    p2_rows = [as_dict(item.get("p2")) for item in model_items if isinstance(item.get("p2"), dict)]
    prompt_total = sum_present(row.get("prompt_tokens") for row in p2_rows)
    cached_total = sum_present(row.get("cached_prompt_tokens") for row in p2_rows)
    recomputed_total = sum_present(row.get("recomputed_prompt_tokens") for row in p2_rows)
    snapshots = [as_dict(row.get("kv_snapshot")) for row in p2_rows if isinstance(row.get("kv_snapshot"), dict)]
    latest_snapshot = snapshots[-1] if snapshots else None
    kv_usage_values = [
        number_or_none(row.get("kv_usage_after"))
        for row in p2_rows
        if number_or_none(row.get("kv_usage_after")) is not None
    ]
    return {
        "kv_cache_prompt_tokens": prompt_total,
        "kv_cache_cached_prompt_tokens": cached_total,
        "kv_cache_recomputed_prompt_tokens": recomputed_total,
        "kv_cache_reuse_rate": ratio_or_none(cached_total, prompt_total),
        "kv_cache_recomputed_rate": ratio_or_none(recomputed_total, prompt_total),
        "kv_usage_ratio_after_max": max(kv_usage_values) if kv_usage_values else None,
        "kv_snapshot": latest_snapshot,
    }


def build_totals(steps: list[dict[str, Any]], unassigned: list[dict[str, Any]]) -> dict[str, Any]:
    items = [item for step in steps for item in step["items"]] + unassigned
    model_items = [item for item in items if item.get("item_type") == "model"]
    tool_items = [item for item in items if item.get("item_type") == "tool"]
    expected_p2_items = [
        item for item in model_items if item.get("p2_match_status") != "p2_without_trajectory_step"
    ]
    matched_p2_items = [item for item in expected_p2_items if item.get("p2_match_status") == "matched"]
    lifecycle_complete = sum(
        1 for item in matched_p2_items if as_dict(item.get("p2")).get("lifecycle_status") == "complete"
    )
    return {
        "step_count": len(steps),
        "model_request_count": len(model_items),
        "tool_call_count": len(tool_items),
        "model_time_s": sum_numeric(item.get("duration_s") for item in model_items),
        "tool_time_s": sum_numeric(item.get("duration_s") for item in tool_items),
        "prompt_tokens": sum_present(item.get("prompt_tokens") for item in model_items),
        "completion_tokens": sum_present(item.get("completion_tokens") for item in model_items),
        "cached_tokens": sum_present(item.get("cached_tokens") for item in model_items),
        "kv_cache_reuse_rate_mean": mean_present(step.get("kv_cache_reuse_rate") for step in steps),
        "kv_usage_ratio_after_max": max_present(step.get("kv_usage_ratio_after_max") for step in steps),
        "tool_nonzero_return_count": sum(1 for item in tool_items if nonzero(item.get("returncode"))),
        "tool_timeout_count": sum(1 for item in tool_items if item.get("timed_out") is True),
        "p2_expected_model_request_count": len(expected_p2_items),
        "p2_matched_model_request_count": len(matched_p2_items),
        "p2_match_rate": ratio_or_none(len(matched_p2_items), len(expected_p2_items)),
        "p2_lifecycle_complete_count": lifecycle_complete,
        "p2_lifecycle_complete_rate": ratio_or_none(lifecycle_complete, len(matched_p2_items)),
        "p2_match_status_counts": count_values(item.get("p2_match_status") for item in model_items),
        "p2_match_method_counts": count_values(item.get("p2_match_method") for item in model_items),
        "p2_missing_reason_counts": count_values(item.get("p2_missing_reason") for item in model_items),
        "unassigned_item_count": len(unassigned),
    }


def build_p2_identity_audit(
    steps: list[dict[str, Any]],
    unassigned: list[dict[str, Any]],
    p2_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    p2_rows = p2_rows or []
    items = [item for step in steps for item in step["items"]] + unassigned
    model_items = [item for item in items if item.get("item_type") == "model"]
    expected = [
        item for item in model_items if item.get("p2_match_status") != "p2_without_trajectory_step"
    ]
    matched = [item for item in expected if item.get("p2_match_status") == "matched"]
    unmatched = [item for item in expected if item.get("p2_match_status") != "matched"]
    matched_ids = set(sorted_unique_text(stable_model_request_id(item) for item in matched))
    orphan_records = orphan_p2_request_audit_records(p2_rows, matched_ids)
    return {
        "schema_version": "1.0",
        "attribution_method": "request_id",
        "p2_request_count": len(p2_rows),
        "expected_p1_model_count": len(expected),
        "matched_count": len(matched),
        "unmatched_p1_count": len(unmatched),
        "orphan_p2_count": len(orphan_records),
        "matched_request_ids": sorted_unique_text(stable_model_request_id(item) for item in matched),
        "unmatched_request_ids": sorted_unique_text(stable_model_request_id(item) for item in unmatched),
        "orphan_request_ids": sorted_unique_text(record.get("request_id") for record in orphan_records),
        "matched_requests": [request_audit_record(item) for item in matched],
        "unmatched_p1_requests": [request_audit_record(item) for item in unmatched],
        "orphan_p2_requests": orphan_records,
        "status_counts": count_values(item.get("p2_match_status") for item in model_items),
        "method_counts": count_values(item.get("p2_match_method") for item in model_items),
    }


def orphan_p2_request_audit_records(
    p2_rows: list[dict[str, Any]],
    matched_request_ids: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in p2_rows:
        identity = canonical_p2_request_identity(row)
        request_id = identity.get("value")
        if request_id in matched_request_ids:
            continue
        records.append(
            {
                "request_id": request_id,
                "request_id_kind": identity.get("kind"),
                "request_id_aliases": p2_request_identity_aliases(row),
                "api_request_id": row.get("api_request_id"),
                "server_request_id": row.get("server_request_id"),
                "vllm_request_id": row.get("vllm_request_id"),
                "engine_request_id": row.get("engine_request_id"),
                "agent_request_id": row.get("agent_request_id"),
                "agentmon_model_request_id": row.get("agentmon_model_request_id"),
                "correlation_quality": row.get("correlation_quality"),
                "lifecycle_status": row.get("lifecycle_status"),
                "p2_missing_reason": "p2_request_has_no_matching_p1_model_span",
            }
        )
    return records


def request_audit_record(item: dict[str, Any]) -> dict[str, Any]:
    p2 = as_dict(item.get("p2"))
    return {
        "request_id": stable_model_request_id(item),
        "request_id_kind": first_value(p2.get("canonical_request_id_kind"), request_id_kind_from_item(item)),
        "span_id": item.get("span_id"),
        "step_id": item.get("step_id"),
        "response_id": first_value(item.get("response_id"), p2.get("response_id")),
        "api_request_id": first_value(item.get("api_request_id"), p2.get("api_request_id")),
        "vllm_request_id": first_value(p2.get("vllm_request_id"), p2.get("engine_request_id")),
        "p2_match_status": item.get("p2_match_status"),
        "p2_match_method": item.get("p2_match_method"),
        "p2_match_confidence": item.get("p2_match_confidence"),
        "p2_missing_reason": item.get("p2_missing_reason"),
        "lifecycle_status": p2.get("lifecycle_status"),
    }


def stable_model_request_id(item: dict[str, Any]) -> str | None:
    p2 = as_dict(item.get("p2"))
    return text_or_none(
        first_value(
            p2.get("canonical_request_id"),
            p2.get("request_id"),
            item.get("api_request_id"),
            item.get("response_id"),
            p2.get("api_request_id"),
            p2.get("vllm_request_id"),
            p2.get("engine_request_id"),
            item.get("span_id"),
            item.get("step_id"),
        )
    )


def request_id_kind_from_item(item: dict[str, Any]) -> str | None:
    p2 = as_dict(item.get("p2"))
    for key, value in (
        ("api_request_id", item.get("api_request_id")),
        ("response_id", item.get("response_id")),
        ("api_request_id", p2.get("api_request_id")),
        ("vllm_request_id", p2.get("vllm_request_id")),
        ("engine_request_id", p2.get("engine_request_id")),
        ("span_id", item.get("span_id")),
        ("step_id", item.get("step_id")),
    ):
        if value is not None and value != "":
            return key
    return None


def canonical_p2_request_identity(row: dict[str, Any]) -> dict[str, str | None]:
    for key in P2_CANONICAL_REQUEST_ID_PRIORITY:
        value = text_or_none(row.get(key))
        if value is not None:
            return {"kind": key, "value": value}
    return {"kind": None, "value": None}


def p2_request_identity_aliases(row: dict[str, Any]) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    canonical = canonical_p2_request_identity(row)
    for key in (*P2_IDENTITY_KEYS, "request_id"):
        value = text_or_none(canonical["value"] if key == "request_id" else row.get(key))
        if value is None:
            continue
        identity = (key, value)
        if identity in seen:
            continue
        seen.add(identity)
        aliases.append({"kind": key, "value": value})
    return aliases


def sorted_unique_text(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = text_or_none(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return sorted(output)


def build_warnings(steps: list[dict[str, Any]], unassigned: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not steps:
        warnings.append("no_agent_steps_found")
    if unassigned:
        warnings.append("items_without_step_id")
    model_items = [item for step in steps for item in step["items"] if item.get("item_type") == "model"]
    if model_items and all(not item.get("p2") for item in model_items):
        warnings.append("no_model_requests_matched_to_p2")
    elif any(item.get("p2_match_status") == "unmatched" for item in model_items):
        warnings.append("some_model_requests_unmatched_to_p2")
    if any(item.get("p2_match_status") == "multiple" for item in model_items):
        warnings.append("ambiguous_multiple_p2_requests_not_attributed")
    if any(
        as_dict(item.get("p2")).get("lifecycle_status") == "partial"
        for item in model_items
        if item.get("p2")
    ):
        warnings.append("some_p2_request_lifecycles_incomplete")
    return warnings
