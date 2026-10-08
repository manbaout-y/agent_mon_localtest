"""Torch-free state lifecycle contract and offline reconstruction, not a runtime hook."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .state_inventory import StateInstance, account_states


EVENT_SCHEMA_VERSION = "heterogeneous-state-events-v1"
STATE_ACTIONS = {"state.create", "state.share", "state.update", "state.consume",
                 "state.move", "state.release", "state.restore"}
REQUEST_ACTIONS = {"request.begin", "request.pause", "request.resume", "request.end"}


@dataclass(frozen=True)
class StateEvent:
    event_id: str
    sequence: int
    timestamp_ns: int
    clock_domain: str
    action: str
    request_id: str
    source: str
    state_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    agent_run_id: str | None = None
    model_request_id: str | None = None
    engine_request_id: str | None = None
    schema_version: str = EVENT_SCHEMA_VERSION

    def __post_init__(self):
        if self.schema_version != EVENT_SCHEMA_VERSION or self.action not in STATE_ACTIONS | REQUEST_ACTIONS:
            raise ValueError("unknown event schema/action")
        if not self.event_id or not self.request_id or not self.clock_domain:
            raise ValueError("event/request/clock identities are required")
        if any(type(n) is not int or n < 0 for n in (self.sequence, self.timestamp_ns)):
            raise ValueError("sequence and timestamp_ns must be nonnegative integers")
        if self.source not in {"runtime", "provided", "synthetic"}:
            raise ValueError("explicit event source required")
        if self.action in STATE_ACTIONS and not self.state_id:
            raise ValueError("state action requires state_id")


class EventRecorder:
    """Opt-in JSONL sink for a future explicit hook. Disabled mode performs no I/O."""

    def __init__(self, path=None, *, enabled=False):
        self.path, self.enabled = path, enabled

    def emit(self, event: StateEvent):
        if not self.enabled:
            return
        import json
        from pathlib import Path
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, allow_nan=False) + "\n")


def analyze_lifecycle(events: list[StateEvent]) -> dict:
    """Validate one ordered clock domain. Never infer consumption from mere allocation."""
    live, phases, generations, allocations, allocation_kinds = {}, {}, {}, {}, {}
    replay_ids, dependency_versions = {}, {}
    legacy_context_updates = []
    seen, sequences, clock, previous_time = set(), set(), None, -1
    snapshots, demands, consumes, last_access = [], [], [], {}
    peak_by_pool, peak_bytes, previous_sequence = {}, 0, -1

    def allocation_key(state):
        return state.device, state.allocation_id

    def closure(state_id, visiting=None):
        visiting = set() if visiting is None else visiting
        if state_id in visiting:
            raise ValueError("cyclic state dependency")
        if state_id not in live:
            raise ValueError(f"missing live dependency: {state_id}")
        state = live[state_id]
        result = {state_id}
        for dep in state.dependency_state_ids:
            result |= closure(dep, visiting | {state_id})
        return result

    def dependency_signature(state):
        return {dep: (replay_ids[allocation_key(live[dep])], generations[allocation_key(live[dep])])
                for dep in state.dependency_state_ids}

    for event in events:
        if event.event_id in seen or event.sequence in sequences or event.sequence <= previous_sequence:
            raise ValueError("event IDs and ordered sequences must be unique")
        if clock is not None and clock != event.clock_domain:
            raise ValueError("mixed clock domains require an explicit upstream clock conversion")
        if event.timestamp_ns < previous_time:
            raise ValueError("event timestamps must be monotonic")
        seen.add(event.event_id)
        sequences.add(event.sequence)
        clock, previous_time, previous_sequence = event.clock_domain, event.timestamp_ns, event.sequence
        action, sid, data, request = event.action, event.state_id, event.data, event.request_id
        if action in REQUEST_ACTIONS:
            old = phases.get(request)
            required = {"request.begin": None, "request.pause": "running",
                        "request.resume": "paused", "request.end": "running"}[action]
            if old != required:
                raise ValueError(f"invalid request phase {old} -> {action}")
            phases[request] = {"request.begin": "running", "request.pause": "paused",
                               "request.resume": "running", "request.end": "ended"}[action]
        elif action in {"state.create", "state.restore", "state.share"}:
            state = StateInstance(**data["state"])
            if sid in live or state.state_id != sid or state.request_id != request or state.source != event.source:
                raise ValueError("created state identity/source conflicts with event")
            if action == "state.share":
                parent = live[data["parent_state_id"]]
                # The two logical views can belong to different request contexts
                # while referring to the same shared prefix/state contents.
                fields = ("device", "allocation_id", "allocation_bytes", "tier", "model_family", "content_version")
                if any(getattr(parent, k) != getattr(state, k) for k in fields):
                    raise ValueError("shared view must reference the same allocation and content version")
            key = allocation_key(state)
            if key in allocations and action != "state.share":
                raise ValueError("an existing allocation requires state.share, not create/restore")
            if key not in allocations:
                generations[key] = state.content_version
                allocation_kinds[key] = state.state_kind
                # Allocation addresses are not content identities. A physical move must
                # not impose the observed runtime's eviction choices on another policy.
                replay_ids[key] = f"allocation:{event.event_id}"
            live[sid] = state
            closure(sid)
            dependency_versions[sid] = dependency_signature(state)
        else:
            if sid not in live:
                raise ValueError(f"{action}: unknown live state {sid}")
            state = live[sid]
            if state.request_id != request:
                raise ValueError("request must own its state view; create an explicit share view first")
            if state.source != event.source:
                raise ValueError("state action source must match its live state provenance")
            key = allocation_key(state)
            aliases = [s for s in live.values() if allocation_key(s) == key]
            if action == "state.release":
                dependents = [s.state_id for s in live.values() if sid in s.dependency_state_ids]
                if dependents:
                    raise ValueError(f"release would invalidate live dependents: {dependents}")
                del live[sid]
                dependency_versions.pop(sid)
                last_access.pop(sid, None)
            elif action == "state.update":
                legacy_update = "content_version" not in data
                if "content_version" in data:
                    version = data["content_version"]
                    context = data.get("context_version")
                    if "context_version" in data and (type(context) is not int or context < 0):
                        raise ValueError("explicit context_version must be a nonnegative integer")
                else:
                    if event.source == "runtime":
                        raise ValueError("runtime state.update requires explicit content_version; "
                                         "context_version identifies the prompt, not state contents")
                    context = data.get("context_version")
                    if type(context) is not int or context <= max(s.context_version for s in aliases):
                        raise ValueError("legacy context-only update requires an increasing context_version; "
                                         "use content_version for same-context updates")
                    version = generations[key] + 1
                    legacy_context_updates.append(event.event_id)
                if type(version) is not int or version <= generations[key]:
                    raise ValueError("update requires a strictly increasing content version")
                generations[key] = version
                for alias in aliases:
                    # Contents are shared; request-context provenance belongs to
                    # each view. Do not rewrite another request's context ID.
                    new_context = context if legacy_update or (alias.state_id == sid and context is not None) \
                        else alias.context_version
                    live[alias.state_id] = replace(alias, context_version=new_context, content_version=version)
                for alias in aliases:
                    dependency_versions[alias.state_id] = dependency_signature(live[alias.state_id])
            elif action == "state.move":
                destination = data["device"], data["allocation_id"]
                if destination != key and destination in allocations:
                    raise ValueError("move destination already allocated; use an explicit share instead")
                if data["allocation_bytes"] < max(s.logical_bytes for s in aliases):
                    raise ValueError("destination cannot hold logical views")
                generations[destination] = generations[key]
                replay_ids[destination] = replay_ids[key]
                allocation_kinds[destination] = allocation_kinds[key]
                for alias in aliases:
                    live[alias.state_id] = replace(alias, device=data["device"], tier=data["tier"],
                        allocation_id=data["allocation_id"], allocation_bytes=data["allocation_bytes"])
            elif action == "state.consume":
                if phases.get(request) in {"paused", "ended"}:
                    raise ValueError("cannot consume for a paused/ended request")
                for field_name in ("selected_rows_count", "measured_read_bytes"):
                    if field_name in data and (type(data[field_name]) is not int or data[field_name] < 0):
                        raise ValueError(f"{field_name} must be a nonnegative integer")
                layer = data["consumer_layer"]
                if layer not in state.consumer_layers:
                    raise ValueError("consumer_layer not declared on state view")
                ids = closure(sid)
                unique = {}
                for dependency in sorted(ids):
                    obj = live[dependency]
                    k = allocation_key(obj)
                    unique[k] = {"allocation_id": k[1], "device": k[0], "version": generations[k],
                                 "replay_object_id": replay_ids[k],
                                 "bytes": obj.allocation_bytes,
                                 "state_kind": allocation_kinds[k]}
                changed = sorted(dependency for dependency in ids
                                 if dependency_versions[dependency] != dependency_signature(live[dependency]))
                index = len(demands)
                demands.append({"event_id": event.event_id, "request_id": request,
                                "timestamp_ns": event.timestamp_ns, "source": event.source,
                                "state_sources": sorted({live[dependency].source for dependency in ids}),
                                "objects": list(unique.values()), "dependency_state_ids": sorted(ids),
                                "consumer_device": data.get("consumer_device"),
                                "placement_semantics": "OBSERVED_ALLOCATION_DEVICES_NOT_COUNTERFACTUAL_PLACEMENT",
                                "dependency_revalidation_required_state_ids": changed})
                consumes.append({"event_id": event.event_id, "state_id": sid, "consumer_layer": layer,
                                 "access_distance": None if sid not in last_access else index - last_access[sid],
                                 "working_set_allocation_bytes": sum(o["bytes"] for o in unique.values()),
                                 "logical_selected_rows": data.get("selected_rows_count"),
                                 "measured_read_bytes": data.get("measured_read_bytes"),
                                 "read_bytes_status": "MEASURED" if "measured_read_bytes" in data else "UNAVAILABLE",
                                 "dependency_revalidation_required_state_ids": changed,
                                 "dependency_status": "CONTENT_CHANGED_REVALIDATION_REQUIRED" if changed
                                                       else "NO_OBSERVED_DEPENDENCY_CHANGE"})
                last_access[sid] = index
        snapshot = account_states(list(live.values()))
        allocations = {allocation_key(s): s for s in live.values()}
        peak_bytes = max(peak_bytes, snapshot["physical_allocated_bytes"])
        for pool in snapshot["per_device_tier"]:
            key = pool["device"], pool["tier"]
            peak_by_pool[key] = max(peak_by_pool.get(key, 0), pool["allocated_bytes"])
        snapshots.append({"event_id": event.event_id, "timestamp_ns": event.timestamp_ns,
                          "action": action, **snapshot})
    return {"schema_version": EVENT_SCHEMA_VERSION, "event_count": len(events),
            "sources": sorted({e.source for e in events}), "clock_domain": clock,
            "peak_physical_bytes": peak_bytes,
            "peak_by_pool": [{"device": d, "tier": t, "bytes": n} for (d, t), n in sorted(peak_by_pool.items())],
            "snapshots": snapshots, "demands": demands, "consumes": consumes,
            "version_semantics_status": "LEGACY_CONTEXT_CONTENT_AMBIGUITY" if legacy_context_updates
                                        else "CONTEXT_AND_CONTENT_VERSIONS_SEPARATE",
            "legacy_context_update_event_ids": legacy_context_updates,
            "final_live_states": [asdict(s) for s in live.values()], "request_phases": phases,
            "semantics": "physical allocation != logical demand != measured device traffic; no latency estimate"}
