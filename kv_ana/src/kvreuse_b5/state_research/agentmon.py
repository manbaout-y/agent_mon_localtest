from __future__ import annotations

from collections import defaultdict


# These aliases are explicitly present in AgentMon's request contracts/P2 payloads.
ALIASES = {
    "model_request_id": "model_request_id", "agentmon_model_request_id": "model_request_id",
    "model_call_id": "model_request_id", "engine_request_id": "engine_request_id",
    "vllm_request_id": "engine_request_id", "api_request_id": "api_request_id",
    "request_attempt_id": "request_attempt_id", "client_request_id": "client_request_id",
    "trajectory_response_id": "trajectory_response_id",
}


def _keys(payload):
    return {(ALIASES[k], str(v)) for k, v in payload.items()
            if k in ALIASES and v is not None and str(v) != ""}


def correlate_events(requests: list[dict], events: list[dict]) -> dict:
    """Exact ID joins only. A request_received event may explicitly bridge two IDs."""
    # Engine/API IDs may be reused after a service restart. A bridge only establishes
    # an alias within the explicitly recorded service run, not across every run.
    index = defaultdict(lambda: defaultdict(set))
    request_by_id = {r["request_id"]: r for r in requests}
    if len(request_by_id) != len(requests):
        raise ValueError("AgentMon correlation requires unique request_id values")
    for r in requests:
        for key in _keys(r["agentmon"]):
            index[key][r["agentmon"].get("service_run_id")].add(r["request_id"])

    def lookup(key, service_run):
        scopes = index.get(key, {})
        if service_run is None:
            return set().union(*scopes.values()) if scopes else set()
        return scopes.get(None, set()) | scopes.get(service_run, set())

    def match(event):
        payload = event.get("payload") or {}
        keys = _keys(payload)
        service_run = event.get("run_id")
        sets = [lookup(k, service_run) for k in keys if lookup(k, service_run)]
        if not sets:
            return "UNMATCHED", set(), keys
        candidates = set.intersection(*(set(s) for s in sets))
        if not candidates:
            return "CONFLICT", set(), keys
        # An unknown alias must not override an explicitly different ID of the
        # same kind in the sidecar merely because another event ID matched.
        for kind, value in keys:
            candidates = {rid for rid in candidates
                          if not {v for k, v in _keys(request_by_id[rid]["agentmon"]) if k == kind}
                          or value in {v for k, v in _keys(request_by_id[rid]["agentmon"]) if k == kind}}
        if not candidates:
            return "CONFLICT", set(), keys
        agent_runs = {payload[name] for name in ("agentmon_run_id", "agent_run_id")
                      if payload.get(name) is not None}
        if len(agent_runs) > 1:
            return "CONFLICT", set(), keys
        agent_run = next(iter(agent_runs), None)
        if agent_run is not None:
            candidates = {rid for rid in candidates
                          if request_by_id[rid]["agentmon"].get("agent_run_id") in {None, agent_run}}
            if not candidates:
                return "CONFLICT", set(), keys
        if service_run is not None:
            candidates = {rid for rid in candidates
                          if request_by_id[rid]["agentmon"].get("service_run_id") in {None, service_run}}
            if not candidates:
                return "CONFLICT", set(), keys
        return "MATCHED" if len(candidates) == 1 else "AMBIGUOUS", candidates, keys

    # Only explicit request correlation events may teach the index an engine/API alias.
    # Multiple requests asserting the same new alias make it ambiguous on subsequent lookup.
    for _ in range(len(events) + 1):
        additions = defaultdict(set)
        for event in events:
            status, candidates, keys = match(event)
            if status == "MATCHED" and event.get("event_type") == "vllm.request.received":
                for key in keys:
                    service_run = event.get("run_id")
                    if not lookup(key, service_run):
                        additions[(key, service_run)].update(candidates)
        if not additions:
            break
        for (key, service_run), candidates in additions.items():
            index[key][service_run].update(candidates)

    rows = []
    for ordinal, event in enumerate(events):
        status, candidates, keys = match(event)
        rows.append({"event_index": ordinal, "event_id": event.get("event_id"),
                     "event_type": event.get("event_type"), "source": event.get("source"),
                     "request_id": next(iter(candidates)) if status == "MATCHED" else None,
                     "status": status, "matched_id_fields": sorted(
                         {k for k, v in keys if lookup((k, v), event.get("run_id"))}),
                     "evidence_quality": event.get("quality"),
                     "service_run_id": event.get("run_id"),
                     "semantics": "ID_CORRELATION_NOT_RESOURCE_OWNERSHIP"})
    return {"rows": rows, "matched": sum(r["status"] == "MATCHED" for r in rows),
            "unmatched": sum(r["status"] == "UNMATCHED" for r in rows),
            "ambiguous": sum(r["status"] == "AMBIGUOUS" for r in rows),
            "conflicts": sum(r["status"] == "CONFLICT" for r in rows)}
