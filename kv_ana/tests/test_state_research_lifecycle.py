from dataclasses import asdict, replace
import json

import pytest

from kvreuse_b5.state_research.lifecycle import EventRecorder, StateEvent, analyze_lifecycle
from kvreuse_b5.state_research.offline_fixtures import lifecycle_fixture, state
from kvreuse_b5.state_research.policy_replay import replay_demands


def test_shared_allocation_lifecycle_and_consumption_are_separate():
    report = analyze_lifecycle(lifecycle_fixture())
    assert report["peak_physical_bytes"] == 96
    shared = report["snapshots"][6]
    assert shared["logical_view_bytes_sum"] == 160
    assert shared["physical_allocated_bytes"] == 96
    assert report["snapshots"][-1]["physical_allocated_bytes"] == 0
    assert len(report["demands"]) == 4
    assert report["consumes"][0]["working_set_allocation_bytes"] == 96
    assert report["consumes"][0]["measured_read_bytes"] is None


def test_move_migrates_all_aliases_and_reports_two_pools_without_double_charge():
    report = analyze_lifecycle(lifecycle_fixture()[:10])
    states = {s["state_id"]: s for s in report["final_live_states"]}
    assert states["main"]["device"] == states["shared"]["device"] == "cpu"
    assert report["snapshots"][-1]["physical_allocated_bytes"] == 96
    assert {p["tier"]: p["allocated_bytes"] for p in report["snapshots"][-1]["per_device_tier"]} == {
        "synthetic_host": 64, "synthetic_hbm": 32}


def test_update_changes_all_alias_versions():
    events = lifecycle_fixture()[:7]
    events.append(StateEvent("update", 7, 1070, "synthetic-clock", "state.update", "request-0", "synthetic",
                             "main", {"context_version": 1}))
    report = analyze_lifecycle(events)
    assert all(s["context_version"] == 1 for s in report["final_live_states"] if s["state_kind"] == "main")


@pytest.mark.parametrize("change", ["duplicate", "sequence", "clock", "time", "consumer", "owner", "source"])
def test_bad_lifecycle_rejected(change):
    events = lifecycle_fixture()
    if change == "duplicate": events[3] = replace(events[3], event_id=events[2].event_id)
    if change == "sequence": events[3] = replace(events[3], sequence=1)
    if change == "clock": events[3] = replace(events[3], clock_domain="other")
    if change == "time": events[3] = replace(events[3], timestamp_ns=0)
    if change == "consumer": events[3] = replace(events[3], data={"consumer_layer": 100})
    if change == "owner": events[3] = replace(events[3], request_id="different")
    if change == "source": events[1] = replace(events[1], source="runtime")
    with pytest.raises(ValueError): analyze_lifecycle(events)


def test_cannot_consume_while_paused():
    events = lifecycle_fixture()[:9]
    events.append(StateEvent("illegal", 9, 1090, "synthetic-clock", "state.consume", "request-0", "synthetic",
                             "main", {"consumer_layer": 4}))
    with pytest.raises(ValueError, match="paused"): analyze_lifecycle(events)


def test_release_dependency_and_duplicate_release_rejected():
    events = lifecycle_fixture()[:3]
    events.append(StateEvent("release", 3, 1030, "synthetic-clock", "state.release", "request-0", "synthetic", "main"))
    with pytest.raises(ValueError, match="dependents"): analyze_lifecycle(events)
    events = lifecycle_fixture()[:17]
    events.append(StateEvent("release-twice", 17, 1170, "synthetic-clock", "state.release", "request-0", "synthetic", "main"))
    with pytest.raises(ValueError, match="unknown live state"): analyze_lifecycle(events)


def test_restore_is_explicit_new_generation_and_replica_counts_separately():
    events = [StateEvent("a", 0, 0, "c", "state.create", "request-0", "synthetic", "a", {"state": state("a", "m")}),
              StateEvent("b", 1, 0, "c", "state.restore", "request-0", "synthetic", "b",
                         {"state": state("b", "m", device="sim:1")})]
    assert analyze_lifecycle(events)["peak_physical_bytes"] == 128


def test_missing_dependency_rejected():
    event = StateEvent("a", 0, 0, "c", "state.create", "request-0", "synthetic", "a",
                       {"state": state("a", "a", dependencies=["absent"])})
    with pytest.raises(ValueError, match="missing live dependency"): analyze_lifecycle([event])


def test_recorder_disabled_no_directory_enabled_json_roundtrip(tmp_path):
    path = tmp_path / "not-created/events.jsonl"
    event = lifecycle_fixture()[0]
    EventRecorder(path).emit(event)
    assert not path.parent.exists()
    EventRecorder(path, enabled=True).emit(event)
    assert StateEvent(**json.loads(path.read_text())) == event


@pytest.mark.parametrize("policy", ["shared_lru", "type_partitioned_lru", "dependency_bundle_lru"])
def test_replay_budget_unique_work_and_no_timing(policy):
    demands = analyze_lifecycle(lifecycle_fixture())["demands"]
    result = replay_demands(demands, {"sim:0": 96}, policy=policy,
                            type_budgets={"sim:0": {"main": 64, "index": 32}})
    assert result["access_units"] == 7
    # The unchanged main state survives its observed host/device round trip in
    # this counterfactual retention policy. Only the updated index misses again.
    assert result["hits"] == 4 and result["misses"] == 3
    assert result["peak_resident_bytes"] <= 96
    assert result["timing_status"] == "NOT_MEASURED_NO_LATENCY_ESTIMATE"
    assert "latency_ns" not in result


def demand(index, *objects):
    return {"event_id": str(index), "request_id": "q", "source": "synthetic", "objects": [
        {"device": "sim:0", "allocation_id": name, "version": 0, "bytes": n, "state_kind": kind}
        for name, n, kind in objects]}


def test_future_accesses_cannot_change_replay_prefix():
    prefix = [demand(0, ("a", 4, "main")), demand(1, ("b", 4, "main"))]
    full = prefix + [demand(2, ("a", 4, "main"))]
    a = replay_demands(prefix, {"sim:0": 4})
    b = replay_demands(full, {"sim:0": 4})
    assert a["history"] == b["history"][:2]


def test_bundle_eviction_differs_from_individual_lru():
    events = [demand(0, ("a", 4, "main"), ("b", 4, "index")), demand(1, ("c", 4, "window")),
              demand(2, ("b", 4, "index"))]
    lru = replay_demands(events, {"sim:0": 8})
    joint = replay_demands(events, {"sim:0": 8}, policy="dependency_bundle_lru")
    assert lru["hits"] == 1 and joint["hits"] == 0
    assert len(joint["history"][1]["evicted"]) == 2


def test_oversized_working_set_bypasses_without_fake_residency():
    result = replay_demands([demand(0, ("a", 128, "main"))], {"sim:0": 64})
    assert result["bypass_bytes"] == 128
    assert result["peak_resident_bytes"] == 0
    assert result["miss_bytes"] == 128


def test_type_budget_isolation_and_missing_partition():
    result = replay_demands([demand(0, ("x", 4, "index"))], {"sim:0": 16},
                            policy="type_partitioned_lru", type_budgets={"sim:0": {"main": 16}})
    assert result["bypass_bytes"] == 4
    with pytest.raises(ValueError, match="fit physical"):
        replay_demands([], {"sim:0": 4}, policy="type_partitioned_lru", type_budgets={"sim:0": {"main": 8}})


def test_stale_version_cannot_hit():
    old = demand(0, ("a", 4, "main"))
    new = demand(1, ("a", 4, "main"))
    new["objects"][0]["version"] = 1
    result = replay_demands([old, new], {"sim:0": 8})
    assert result["hits"] == 0 and result["peak_resident_bytes"] == 4


def test_alias_type_does_not_change_physical_partition_or_duplicate_demand():
    events = [
        StateEvent("a", 0, 0, "c", "state.create", "request-0", "synthetic", "a", {"state": state("a", "m")}),
        StateEvent("use-a", 1, 1, "c", "state.consume", "request-0", "synthetic", "a", {"consumer_layer": 4}),
        StateEvent("b", 2, 2, "c", "state.share", "request-0", "synthetic", "b",
                   {"state": state("b", "m", "another-logical-view"), "parent_state_id": "a"}),
        StateEvent("use-b", 3, 3, "c", "state.consume", "request-0", "synthetic", "b", {"consumer_layer": 4}),
    ]
    report = analyze_lifecycle(events)
    assert report["peak_physical_bytes"] == 64
    assert all(d["objects"][0]["state_kind"] == "main" for d in report["demands"])
    assert replay_demands(report["demands"], {"sim:0": 64})["hits"] == 1
