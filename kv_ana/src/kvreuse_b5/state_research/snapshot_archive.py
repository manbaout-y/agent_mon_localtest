"""Save explicitly supplied CPU state views; no model hooks, tensor inference, or device transfer."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from .io import write_json
from .state_inventory import StateInstance, account_states


def write_snapshot(root: Path, states: list[StateInstance], arrays: dict[str, np.ndarray], *,
                   runtime_identity: dict, bindings: dict[str, dict]) -> dict:
    """NPZ preserves native NumPy dtypes. bf16/device tensors need an explicit upstream serializer.

    Bindings carry externally supplied geometry, valid regions, quantization scales and routing refs.
    Disk views may duplicate shared storage; physical accounting still deduplicates allocation IDs.
    """
    if set(arrays) != {s.state_id for s in states} or set(bindings) != set(arrays):
        raise ValueError("every state requires an explicit array and geometry binding")
    if not runtime_identity.get("model_family") or not runtime_identity.get("runtime_version"):
        raise ValueError("explicit runtime identity required; V4.1 layout is never inferred")
    accounting = account_states(states)
    payload, descriptors = {}, []
    for index, state in enumerate(states):
        array = arrays[state.state_id]
        if not isinstance(array, np.ndarray) or array.dtype.hasobject:
            raise ValueError("snapshot writer accepts only CPU NumPy numeric arrays, no implicit tensor conversion")
        if array.dtype.kind not in "buifc" or tuple(array.shape) != tuple(state.shape):
            raise ValueError("native array dtype/shape not covered by state descriptor")
        if str(array.dtype) != state.dtype or array.nbytes != state.logical_bytes:
            raise ValueError("array dtype/nbytes mismatch; do not silently cast or estimate quantized storage")
        if state.model_family != runtime_identity["model_family"]:
            raise ValueError("model identity mismatch")
        binding = bindings[state.state_id]
        if not binding.get("layout") or not binding.get("geometry_status"):
            raise ValueError("layout and geometry_status must be explicit")
        key = f"state_{index}"
        payload[key] = array
        descriptors.append({"state": asdict(state), "array_key": key, "binding": binding})
    manifest = {"schema_version": "heterogeneous-state-snapshot-v1", "runtime_identity": runtime_identity,
                "arrays_file": "state_views.npz", "states": descriptors, "accounting": accounting,
                "capture_status": "CALLER_SUPPLIED_CPU_SNAPSHOT",
                "coverage_status": "PROVIDED_STATES_ONLY_NOT_PROOF_OF_ALL_RUNTIME_STATES",
                "semantics": "logical views serialized separately; disk bytes are not physical device occupancy"}
    # A non-JSON geometry binding must not strand a large NPZ without the
    # manifest needed to interpret it (or prevent retry in this output folder).
    json.dumps(manifest, allow_nan=False)
    if root.exists() and any(root.iterdir()):
        raise ValueError("snapshot output must be new/empty")
    root.mkdir(parents=True, exist_ok=True)
    np.savez(root / "state_views.npz", **payload)
    write_json(root / "manifest.json", manifest)
    return manifest
