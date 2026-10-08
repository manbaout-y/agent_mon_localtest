from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StateInstance:
    """A logical view with request-local context and allocation-wide content versions.

    allocation_bytes describes its entire physical allocation, not just this view.
    """

    state_id: str
    request_id: str
    model_family: str
    state_kind: str
    context_version: int
    producer_layer: int
    consumer_layers: tuple[int, ...]
    allocation_id: str
    device: str
    tier: str
    allocation_bytes: int
    logical_bytes: int
    dtype: str
    shape: tuple[int, ...]
    source: str
    artifact_path: str | None = None
    valid_range: tuple[int, int] | None = None
    positions_semantics: str | None = None
    mapping_ref: str | None = None
    dependency_state_ids: tuple[str, ...] = ()
    # Context identity and mutable state contents are independent: an index or
    # recursive state can be refreshed many times for one unchanged prompt.
    content_version: int = 0

    def __post_init__(self):
        if not all((self.state_id, self.request_id, self.allocation_id, self.device, self.state_kind)):
            raise ValueError("state/request/allocation/device/kind identities are required")
        if self.source not in {"runtime", "provided", "synthetic"}:
            raise ValueError("state inventory requires explicit source")
        if any(type(n) is not int or n < 0 for n in (self.allocation_bytes, self.logical_bytes)):
            raise ValueError("allocation_bytes and logical_bytes must be nonnegative integers")
        if self.logical_bytes > self.allocation_bytes:
            raise ValueError("logical_bytes must be within the full allocation_bytes")
        if type(self.context_version) is not int or self.context_version < 0:
            raise ValueError("context_version must be a nonnegative integer")
        if type(self.content_version) is not int or self.content_version < 0:
            raise ValueError("content_version must be a nonnegative integer")
        if any(type(n) is not int or n < 0 for n in (self.producer_layer, *self.consumer_layers)):
            raise ValueError("producer and consumer layers must be nonnegative integers")
        if any(type(n) is not int or n < 0 for n in self.shape):
            raise ValueError("state shape must contain nonnegative integer dimensions")


def account_states(states: list[StateInstance]) -> dict:
    """Input must be a single snapshot; lifetimes must not be summed as simultaneous memory."""
    identities, allocations, pools = set(), {}, {}
    for state in states:
        if state.state_id in identities:
            raise ValueError(f"duplicate state_id in snapshot: {state.state_id}")
        identities.add(state.state_id)
        key = state.device, state.allocation_id
        descriptor = state.allocation_bytes, state.tier, state.source, state.content_version
        if key in allocations and allocations[key] != descriptor:
            raise ValueError(f"inconsistent physical allocation descriptor: {key}")
        if key not in allocations:
            pool = state.device, state.tier
            pools[pool] = pools.get(pool, 0) + state.allocation_bytes
        allocations[key] = descriptor
    return {
        "logical_state_count": len(states), "physical_allocation_count": len(allocations),
        "logical_view_bytes_sum": sum(s.logical_bytes for s in states),
        "physical_allocated_bytes": sum(x[0] for x in allocations.values()),
        "consumer_bindings": sum(len(set(s.consumer_layers)) for s in states),
        "per_device_tier": [{"device": device, "tier": tier, "allocated_bytes": size}
                            for (device, tier), size in sorted(pools.items())],
        "source": sorted({s.source for s in states}),
        "dependency_edges": [{"producer_state_id": dependency, "consumer_state_id": s.state_id}
                             for s in states for dependency in s.dependency_state_ids],
        "unresolved_dependency_ids": sorted({dependency for s in states for dependency in s.dependency_state_ids
                                               if dependency not in identities}),
        "semantics": "single snapshot; aliases deduplicated per device/allocation; replicas counted separately",
    }


def capture_requirements() -> dict:
    runtime = "runtime_references/dsv4_runtime_official_reference_v0221/official/vllm-ascend/vllm_ascend/attention/dsa_v1.py"
    symbols = [
        ("compressed_main", "compress_kv_cache", "ratio, slot mapping, valid compressed rows"),
        ("local_window", "swa_kv_cache", "window bounds, slot mapping, valid tokens"),
        ("compressor_working_state", "state_cache", "unfinished compression state and request ownership"),
        ("indexer_working_state", "indexer_state_cache", "indexer update/recovery dependencies"),
        ("indexer_keys", "indexer_k_cache", "index geometry and associated scale"),
        ("indexer_scales", "indexer_scale_cache", "quantization layout and scale binding"),
        ("indexer_full", "indexer_full_cache", "enabled path and actual allocated shape"),
    ]
    return {
        "catalog_status": "SOURCE_INVENTORY_NOT_RUNTIME_CAPTURE",
        "DeepSeek-V4": {
            "runtime": "vllm-ascend v0.22.1rc1", "evidence_file": runtime,
            "evidence_lines": [1732, 2353],
            "states": [{"state_kind": kind, "runtime_symbol": symbol, "required_geometry": geometry,
                        "status": "SYMBOL_CONFIRMED_LAYOUT_REQUIRES_RUNTIME"}
                       for kind, symbol, geometry in symbols],
            "generated_artifact": "compressed_kv is pre-scatter output; not a full live-cache snapshot",
        },
        "DeepSeek-V4.1": {
            "status": "REQUIRES_RUNTIME_SCHEMA",
            "requirements": ["shared main state producer/consumers", "shared index/routing objects",
                             "local window and replay dependencies", "actual quantization layout/scales"],
            "note": "research requirements only; no V4 field names or ratios are assumed for V4.1",
        },
        "runtime_events_required": ["state.create", "state.consume", "state.share", "state.update",
                                    "state.move", "state.release", "state.restore"],
        "common_fields": ["request_id", "engine_request_id", "context_version", "content_version", "state_id", "state_kind",
                          "producer_layer", "consumer_layers", "allocation_id", "device", "tier",
                          "shape", "dtype", "logical_bytes", "allocation_bytes", "valid_range",
                          "slot_mapping_ref", "timestamp_ns", "clock_domain", "measurement_quality"],
        "cost_measurements_required": ["load", "replay", "index_rebuild", "repair", "full_recompute"],
        "availability": "NOT_COLLECTED_BY_THIS_TOOL",
    }


def state_dicts(states):
    return [asdict(s) for s in states]
