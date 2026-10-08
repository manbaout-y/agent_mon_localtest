from __future__ import annotations

from collections import Counter


def replay_manifest(requests: list[dict]) -> dict:
    """An ordered request manifest, not a scheduler, timestamp estimator or model runner."""
    origin = min((r["request_time_ns"] for r in requests), default=0) if (
        requests and requests[0]["order_basis"] == "observed") else None
    return {
        "execution_status": "MANIFEST_ONLY",
        "ordering": requests[0]["order_basis"] if requests else "constructed",
        "time_semantics": "recorded request timestamps" if origin is not None else "input order; no timing evidence",
        "concurrency": None, "waiting_intervals": None, "service_durations": None,
        "entries": [{"order_index": r["order_index"], "request_id": r["request_id"],
                     "session_id": r["session_id"], "context_version": r["context_version"],
                     "release_offset_ns": r["request_time_ns"] - origin if origin is not None else None,
                     "payload_ref": {"file": "payloads.jsonl", "request_id": r["request_id"]}}
                    for r in requests],
    }


def representative_cases(transitions: list[dict], *, per_class: int = 3) -> list[dict]:
    if per_class < 1:
        raise ValueError("per_class must be positive")
    # Deterministic coverage, not optimization against target residuals or model outputs.
    counts, selected = Counter(), []
    for row in transitions:
        kind = row["classification"]
        if kind == "UNRESOLVED_ALIGNMENT" or counts[kind] >= per_class:
            continue
        counts[kind] += 1
        selected.append({"case_id": f"case:{len(selected)}", "transition_id": row["transition_id"],
                         "classification": kind, "old_request_id": row["old_request_id"],
                         "new_request_id": row["new_request_id"], "memory_key": row["memory_key"],
                         "sampling_rule": "first_adjacent_transitions_per_class",
                         "use": "MECHANISM_CASE_NOT_FREQUENCY_ESTIMATE",
                         "state_mapping_status": "REQUIRES_MODEL_SPECIFIC_MAPPING",
                         "split_group": row["memory_key"]})
    return selected
