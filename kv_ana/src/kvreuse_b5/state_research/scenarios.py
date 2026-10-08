"""Explicit constructed workload schedules; no invented natural concurrency measurements."""
from __future__ import annotations


def constructed_schedule(requests: list[dict], *, interarrival_ns: int, pause_after: int | None = None,
                         pause_ns: int = 0) -> dict:
    if type(interarrival_ns) is not int or interarrival_ns <= 0 or type(pause_ns) is not int or pause_ns < 0:
        raise ValueError("explicit positive interarrival and nonnegative pause required")
    if pause_after is not None and (type(pause_after) is not int or not 0 <= pause_after < len(requests)):
        raise ValueError("pause_after must be a request index")
    seen, rows = set(), []
    for index, request in enumerate(requests):
        if request["request_id"] in seen:
            raise ValueError("duplicate workload request_id")
        seen.add(request["request_id"])
        offset = index * interarrival_ns + (pause_ns if pause_after is not None and index > pause_after else 0)
        rows.append({"request_id": request["request_id"], "session_id": request["session_id"],
                     "release_offset_ns": offset, "payload_ref": request.get("payload_ref"),
                     "service_time_ns": None, "completion_ns": None})
    return {"order_basis": "CONSTRUCTED", "time_source": "USER_SCENARIO_PARAMETERS",
            "semantics": "arrival gap, not measured inference pause; concurrency requires service-time observations",
            "parameters": {"interarrival_ns": interarrival_ns, "pause_after": pause_after, "pause_ns": pause_ns},
            "requests": rows, "execution_status": "MANIFEST_ONLY"}
