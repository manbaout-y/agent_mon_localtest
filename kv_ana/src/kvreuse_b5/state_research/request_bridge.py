"""Content-free HTTP evidence and explicit request/state joins.

This is not a runtime hook. HTTP messages do not prove server tokenization,
engine identity, allocation ownership, or complete state coverage.
"""
from __future__ import annotations

from copy import deepcopy


SCHEMA_VERSION = "agentmon-kvreuse-request-v1"


def request_from_wire(payload, headers, *, attempt_id, request_purpose,
                      model_family, context_version, source="http_interception"):
    """Build one record per actual transport attempt; never save prompt text.

    The caller supplies context_version after request preparation (including
    optional compression). It must not substitute the original trajectory.
    """
    h = {k.lower(): str(v) for k, v in headers.items()}
    call = h.get("x-agentmon-model-call-id") or h.get("x-agentmon-request-id")
    run = h.get("x-agentmon-run-id")
    if not attempt_id or not call or not run or not model_family:
        raise ValueError("explicit attempt, model-call, agent-run and model identity required")
    if request_purpose not in {"agent", "context_summary", "preflight"}:
        raise ValueError("explicit request purpose required")
    if type(context_version) is not int or context_version < 0:
        raise ValueError("context_version must be a nonnegative integer")
    if source not in {"http_interception", "synthetic"}:
        raise ValueError("HTTP evidence must not be labelled runtime capture")
    messages = payload["messages"]
    # Whitelist controls rather than exporting arbitrary request bodies, headers,
    # tool schemas or authentication material. Null means not present on wire.
    controls = {k: deepcopy(payload.get(k)) for k in (
        "temperature", "max_tokens", "max_completion_tokens", "stream",
        "chat_template_kwargs", "tool_choice")}
    return {
        "schema_version": SCHEMA_VERSION, "request_id": str(attempt_id),
        "request_purpose": request_purpose, "model_family": model_family,
        "served_model": payload["model"], "context_version": context_version,
        "source": source, "effective_controls": controls,
        "message_count": len(messages),
        "agentmon": {"agent_run_id": run, "model_request_id": call,
                     "request_attempt_id": str(attempt_id),
                     "client_request_id": h.get("x-agentmon-client-request-id")},
        "tokenization_status": "SERVER_INPUT_IDS_NOT_CAPTURED",
        "state_capture_status": "NOT_CAPTURED",
        "text_scope": "POST_PREPARATION_HTTP_MESSAGES_METADATA_ONLY",
    }


def bind_state_captures(requests, bindings, captures):
    """Join only explicit attempt -> (service-run, engine-request) bindings.

    bindings must originate at the actual runtime boundary; synthetic evidence
    is allowed for tests but cannot authorize runtime-labelled captures.
    Input records are not mutated. No time-nearest or layer/shape inference.
    """
    attempts = {}
    for request in requests:
        aid = request["agentmon"]["request_attempt_id"]
        if aid in attempts:
            raise ValueError("duplicate request_attempt_id")
        attempts[aid] = request
    index = {}
    for binding in bindings:
        key = (binding["service_run_id"], binding["engine_request_id"])
        if not all(key) or binding["source"] not in {"runtime", "synthetic"}:
            raise ValueError("binding requires service/engine identity and explicit source")
        if binding["request_attempt_id"] not in attempts:
            raise ValueError("binding references unknown request attempt")
        if key in index and index[key] != binding:
            raise ValueError("conflicting service/engine request binding")
        index[key] = binding
    result = []
    for capture in captures:
        binding = index.get((capture.get("service_run_id"), capture.get("engine_request_id")))
        row = {"capture_id": capture["capture_id"], "request_id": None,
               "status": "BLOCKED_REQUEST_MAPPING", "source": capture.get("source"),
               "semantics": "REQUEST_LINK_NOT_PROOF_OF_STATE_COVERAGE_OR_OWNERSHIP"}
        if binding:
            request = attempts[binding["request_attempt_id"]]
            row["request_id"] = request["request_id"]
            mismatch = (capture.get("model_family") != request["model_family"] or
                        capture.get("context_version") != request["context_version"])
            if mismatch:
                row["status"] = "CONFLICT_MODEL_OR_CONTEXT"
            elif capture.get("source") not in {"runtime", "synthetic"}:
                row["status"] = "BLOCKED_CAPTURE_SOURCE"
            elif capture["source"] == "runtime" and (
                    binding["source"] != "runtime" or request["source"] == "synthetic"):
                row["status"] = "BLOCKED_SYNTHETIC_RUNTIME_PROMOTION"
            elif not capture.get("runtime_identity"):
                row["status"] = "BLOCKED_RUNTIME_IDENTITY"
            else:
                row["status"] = "MATCHED"
                row["request_purpose"] = request["request_purpose"]
        result.append(row)
    return result
