"""Small synthetic fixtures for offline protocol acceptance, never real runtime evidence."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np

from .fixtures import synthetic_events, synthetic_records
from .io import write_json, write_jsonl
from .lifecycle import StateEvent
from .lifemem import prepare_records


def state(sid, allocation, kind="main", size=64, dependencies=(), request="request-0", device="sim:0"):
    return dict(state_id=sid, request_id=request, model_family="synthetic", state_kind=kind,
                context_version=0, producer_layer=2, consumer_layers=[4, 6], allocation_id=allocation,
                device=device, tier="synthetic_hbm", allocation_bytes=size, logical_bytes=size,
                dtype="float32", shape=[size // 4], source="synthetic", dependency_state_ids=list(dependencies))


def lifecycle_fixture():
    events = []

    def emit(action, sid=None, request="request-0", **data):
        i = len(events)
        events.append(StateEvent(f"e{i}", i, 1000 + 10 * i, "synthetic-clock", action,
                                 request, "synthetic", sid, data))

    emit("request.begin")
    emit("state.create", "main", state=state("main", "m"))
    emit("state.create", "index", state=state("index", "i", "index", 32, ["main"]))
    emit("state.consume", "index", consumer_layer=4, selected_rows_count=2)
    emit("state.consume", "index", consumer_layer=6)
    emit("request.begin", request="request-1")
    emit("state.share", "shared", request="request-1", parent_state_id="main",
         state=state("shared", "m", request="request-1"))
    emit("state.consume", "shared", request="request-1", consumer_layer=4)
    emit("request.pause")
    emit("state.move", "main", device="cpu", tier="synthetic_host", allocation_id="host-m", allocation_bytes=64)
    emit("request.resume")
    emit("state.move", "main", device="sim:0", tier="synthetic_hbm", allocation_id="m2", allocation_bytes=64)
    emit("state.update", "index", context_version=1)
    emit("state.consume", "index", consumer_layer=4)
    emit("state.release", "index")
    emit("state.release", "main")
    emit("state.release", "shared", request="request-1")
    emit("request.end")
    emit("request.end", request="request-1")
    return events


def cost_scope():
    return {"model_family": "synthetic", "runtime_version": "sim-v1", "device_class": "sim",
            "state_kind": "main", "size_bytes": 64, "dtype": "float32", "layout": "T_F",
            "context_tokens": 16, "batch_size": 1, "measurement_protocol": "synthetic-isolated-v1"}


def cost_trials():
    return [{"trial_id": f"cost-{op}", "sample_id": "calibration-sample", "split": "calibration",
             "source": "synthetic", "scope": cost_scope(), "operation": op, "operation_duration_ns": duration}
            for op, duration in [("load", 20), ("repair", 10), ("full_recompute", 100)]]


def recovery_case():
    return {"case_id": "edit-context", "sample_id": "heldout-sample", "source": "synthetic",
            "scope": cost_scope(), "candidates": [
                {"operation": "direct_reuse", "validity": "UNRESOLVED", "evidence_refs": []},
                {"operation": "repair", "validity": "VALIDATED", "evidence_source": "synthetic",
                 "evidence_refs": ["synthetic-known-transform"], "required_capabilities": ["repair"]},
                {"operation": "full_recompute", "validity": "VALIDATED", "evidence_source": "synthetic",
                 "evidence_refs": ["synthetic-reference"], "required_capabilities": ["recompute"]}]}


def write_fixture_bundle(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    prepared = prepare_records(synthetic_records(), dataset_id="synthetic", order="observed")
    requests = {r["request_id"]: r for r in prepared["requests"]}
    events = [asdict(e) for e in lifecycle_fixture()]
    for event in events:
        if "state" in event["data"]:
            event["data"]["state"]["context_version"] = requests[event["request_id"]]["context_version"]
        if event["action"] == "state.update":
            event["data"] = {"content_version": 1}
    write_jsonl(root / "state_events.jsonl", events)
    write_json(root / "cost_trials.json", cost_trials())
    write_json(root / "recovery_cases.json", [recovery_case()])
    write_jsonl(root / "requests.jsonl", prepared["requests"])
    write_jsonl(root / "agentmon_events.jsonl", synthetic_events())
    a = np.zeros((4, 3), dtype=np.float64)
    b = np.ones((4, 3), dtype=np.float64)
    corrected = np.full((4, 3), .5, dtype=np.float64)
    np.savez(root / "state_arrays.npz", a=a, b=b, corrected=corrected)
    compare = {"artifact": "state_arrays.npz", "keys": {"a": "a", "b": "b", "corrected": "corrected"},
               "identity": {"pair_id": "synthetic", "split": "test", "model_family": "synthetic",
                            "condition": "SYNTHETIC", "state_kind": "generic", "layer_id": 2},
               "representations": {k: "synthetic-feature-v1" for k in ("a", "b", "corrected")},
               "source": "synthetic", "mapping": {"row_a": [0, 2], "row_b": [0, 2], "row_corrected": [0, 2],
               "positions_a": [4, 12], "positions_b": [4, 12], "positions_corrected": [4, 12],
               "positions_semantics": "SYNTHETIC_RELATIVE",
               "evidence_ref": "synthetic-explicit-row-mapping"}}
    write_json(root / "state_comparisons.json", [compare])
    write_json(root / "dependency_graph.json", {"nodes": [
        {"state_id": "main", "depends_on": []}, {"state_id": "index", "depends_on": ["main"]},
        {"state_id": "routing", "depends_on": ["index"]}, {"state_id": "window", "depends_on": []}],
        "changed_state_ids": ["main"]})
    config = {"state_events": "state_events.jsonl", "cost_trials": "cost_trials.json",
              "recovery_cases": "recovery_cases.json", "state_comparisons": "state_comparisons.json",
              "dependency_graph": "dependency_graph.json", "requests": "requests.jsonl",
              "agentmon_events": "agentmon_events.jsonl", "capacities": {"sim:0": 96, "cpu": 64},
              "type_budgets": {"sim:0": {"main": 64, "index": 32}, "cpu": {"main": 64}},
              "policies": ["shared_lru", "type_partitioned_lru", "dependency_bundle_lru"],
              "available_capabilities": ["repair", "recompute"],
              "schedule": {"interarrival_ns": 100, "pause_after": 2, "pause_ns": 1000},
              "capsule_max_bytes": 45000}
    write_json(root / "config.json", config)
    return config
