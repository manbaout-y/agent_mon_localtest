"""Fixed-demand cache replay baselines. These policies never access future demands or B."""
from __future__ import annotations


POLICIES = {"shared_lru", "type_partitioned_lru", "dependency_bundle_lru"}


def replay_demands(demands: list[dict], capacities: dict[str, int], *, policy="shared_lru",
                   type_budgets: dict[str, dict[str, int]] | None = None) -> dict:
    if policy not in POLICIES or any(type(n) is not int or n < 0 for n in capacities.values()):
        raise ValueError("explicit policy and nonnegative per-device capacities required")
    type_budgets = type_budgets or {}
    if policy == "type_partitioned_lru":
        for device, capacity in capacities.items():
            budgets = type_budgets.get(device, {})
            if any(type(n) is not int or n < 0 for n in budgets.values()) or sum(budgets.values()) > capacity:
                raise ValueError("type budgets must fit physical capacity")
    resident, links, history, last_touch = {}, {}, [], {}
    requests = hits = misses = miss_bytes = evictions = bypass_bytes = peak = 0

    def key(obj):
        return obj["device"], obj["allocation_id"], obj["version"]

    def pool(obj):
        return (obj["device"], obj["state_kind"] if policy == "type_partitioned_lru" else None)

    def limit(p):
        return type_budgets.get(p[0], {}).get(p[1], 0) if p[1] is not None else capacities[p[0]]

    def used(p):
        return sum(o["bytes"] for o in resident.values() if pool(o) == p)

    def remove(k):
        resident.pop(k, None)
        for linked in links.pop(k, set()):
            links.get(linked, set()).discard(k)

    def bundle(k):
        result, pending = set(), [k]
        while pending:
            item = pending.pop()
            if item in result or item not in resident:
                continue
            result.add(item)
            pending.extend(links.get(item, set()))
        return result

    for step, demand in enumerate(demands):
        # Keep physical addresses in the lifecycle trace, but do not use allocator
        # churn as a content miss. Legacy caller-supplied demands retain their IDs.
        objects = {}
        allocation_addresses = {}
        for original in demand["objects"]:
            obj = dict(original)
            obj["allocation_id"] = obj.pop("replay_object_id", obj["allocation_id"])
            objects[key(obj)] = obj
            allocation_addresses[key(obj)] = original["allocation_id"]
        if len(objects) != len(demand["objects"]):
            raise ValueError("demand must deduplicate physical allocations")
        if len({k[:2] for k in objects}) != len(objects):
            raise ValueError("one demand cannot contain multiple versions of the same allocation")
        for k, obj in objects.items():
            if obj["device"] not in capacities or type(obj["bytes"]) is not int or obj["bytes"] < 0:
                raise ValueError("demand device/size not covered by capacity configuration")
            if k in resident and resident[k] != obj:
                if resident[k]["state_kind"] != obj["state_kind"]:
                    raise ValueError("allocation descriptor changed without a version change")
                # A larger/smaller backing extent requires a new admission even
                # when the logical content survived a storage move unchanged.
                remove(k)
            # An in-place update cannot leave an old generation as a valid hit.
            for old in list(resident):
                if old[:2] == k[:2] and old != k:
                    remove(old)
        present = set(objects) & set(resident)
        missing = set(objects) - present
        requests += len(objects)
        hits += len(present)
        misses += len(missing)
        miss_bytes += sum(objects[k]["bytes"] for k in missing)
        groups = {}
        for k, obj in objects.items():
            groups.setdefault(pool(obj), []).append(k)
        step_evicted, step_bypass = [], 0
        for p, group in groups.items():
            needed = sum(objects[k]["bytes"] for k in group)
            # Oversized working sets are served transiently, not magically resident.
            if needed > limit(p):
                step_bypass += sum(objects[k]["bytes"] for k in group if k in missing)
                continue
            extra = sum(objects[k]["bytes"] for k in group if k not in resident)
            while used(p) + extra > limit(p):
                candidates = [k for k, o in resident.items() if pool(o) == p and k not in objects]
                if not candidates:
                    raise ValueError("no legal eviction under protected working set")
                victim = min(candidates, key=lambda k: (last_touch[k], k))
                victims = bundle(victim) if policy == "dependency_bundle_lru" else {victim}
                # Current demand is protected; evict other members only when a group overlaps it.
                victims -= set(objects)
                for item in sorted(victims):
                    if item in resident:
                        remove(item)
                        step_evicted.append(list(item))
            for k in group:
                resident[k] = objects[k]
        for k in objects:
            last_touch[k] = step
        if policy == "dependency_bundle_lru":
            for p, group in groups.items():
                admitted = set(group) & set(resident)
                for k in admitted:
                    links.setdefault(k, set()).update(admitted - {k})
        evictions += len(step_evicted)
        bypass_bytes += step_bypass
        occupied = sum(o["bytes"] for o in resident.values())
        peak = max(peak, occupied)
        history.append({"event_id": demand["event_id"], "request_id": demand["request_id"],
                        "hits": len(present), "misses": len(missing),
                        "miss_bytes": sum(objects[k]["bytes"] for k in missing),
                        "bypass_bytes": step_bypass, "resident_bytes": occupied,
                        "evicted": step_evicted,
                        "observed_allocation_addresses": [{"device": k[0], "replay_object_id": k[1],
                                                           "allocation_id": address}
                                                          for k, address in allocation_addresses.items()],
                        "resident_by_device": {d: sum(o["bytes"] for o in resident.values() if o["device"] == d)
                                               for d in capacities}})
    return {"policy": policy, "experiment_class": "OFFLINE_FIXED_TRACE_POLICY_BASELINE",
            "sources": sorted({source for d in demands for source in [d["source"], *d.get("state_sources", [])]}),
            "access_units": requests,
            "hits": hits, "misses": misses, "miss_bytes": miss_bytes, "evictions": evictions,
            "bypass_bytes": bypass_bytes, "peak_resident_bytes": peak,
            "hit_fraction": hits / requests if requests else None, "history": history,
            "timing_status": "NOT_MEASURED_NO_LATENCY_ESTIMATE",
            "placement_scope": "OBSERVED_ALLOCATION_DEVICES; NOT_A_COUNTERFACTUAL_PLACEMENT_SCHEDULER",
            "semantics": "demand objects are whole allocations; miss bytes are a transfer proxy, not measured I/O; "
                         "empty initial cache; stable object identity across same-content moves; "
                         "observed device placement retained; runtime release decisions not replayed as policy decisions"}
