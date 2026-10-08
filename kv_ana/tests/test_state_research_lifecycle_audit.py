from copy import deepcopy
from dataclasses import replace

import pytest
import numpy as np

from kvreuse_b5.state_research.lifecycle import StateEvent, analyze_lifecycle
from kvreuse_b5.state_research.offline_fixtures import state
from kvreuse_b5.state_research.policy_replay import replay_demands
from kvreuse_b5.state_research.state_inventory import StateInstance
from kvreuse_b5.state_research.snapshot_archive import write_snapshot


def event(index, action, sid="main", **data):
    return StateEvent(str(index), index, index, "synthetic-clock", action,
                      "request-0", "synthetic", sid, data)


def moved_trace():
    return [
        event(0, "state.create", state=state("main", "allocation-before")),
        event(1, "state.consume", consumer_layer=4),
        event(2, "state.move", device="cpu", tier="host", allocation_id="host-copy", allocation_bytes=64),
        event(3, "state.move", device="sim:0", tier="synthetic_hbm", allocation_id="allocation-after", allocation_bytes=64),
        event(4, "state.consume", consumer_layer=4),
    ]


@pytest.mark.parametrize("policy", ["shared_lru", "type_partitioned_lru", "dependency_bundle_lru"])
def test_address_migration_does_not_force_alternative_policy_content_miss(policy):
    report = analyze_lifecycle(moved_trace())
    demands = report["demands"]
    assert demands[0]["objects"][0]["allocation_id"] == "allocation-before"
    assert demands[1]["objects"][0]["allocation_id"] == "allocation-after"
    assert demands[0]["objects"][0]["replay_object_id"] == demands[1]["objects"][0]["replay_object_id"]
    assert demands[0]["objects"][0]["version"] == demands[1]["objects"][0]["version"]
    result = replay_demands(demands, {"sim:0": 64}, policy=policy,
                            type_budgets={"sim:0": {"main": 64}})
    assert result["hits"] == result["misses"] == 1
    assert "OBSERVED_ALLOCATION_DEVICES" in result["placement_scope"]
    assert result["history"][1]["observed_allocation_addresses"][0]["allocation_id"] == "allocation-after"


def test_consumption_on_other_device_is_not_faked_as_same_device_hit():
    trace = moved_trace()[:3] + [event(3, "state.consume", consumer_layer=4, consumer_device="cpu")]
    demands = analyze_lifecycle(trace)["demands"]
    assert demands[1]["consumer_device"] == "cpu"
    result = replay_demands(demands, {"sim:0": 64, "cpu": 64})
    assert result["hits"] == 0
    assert result["misses"] == 2


def test_same_address_reallocation_cannot_reuse_dead_content():
    trace = [event(0, "state.create", state=state("main", "m")),
             event(1, "state.consume", consumer_layer=4),
             event(2, "state.release"),
             event(3, "state.create", state=state("main", "m")),
             event(4, "state.consume", consumer_layer=4)]
    report = analyze_lifecycle(trace)
    assert report["demands"][0]["objects"][0]["replay_object_id"] != report["demands"][1]["objects"][0]["replay_object_id"]
    assert replay_demands(report["demands"], {"sim:0": 64})["hits"] == 0
    assert report["consumes"][1]["access_distance"] is None


def test_update_after_move_still_invalidates_content_hit():
    trace = moved_trace() + [event(5, "state.update", context_version=1),
                             event(6, "state.consume", consumer_layer=4)]
    result = replay_demands(analyze_lifecycle(trace)["demands"], {"sim:0": 64})
    assert (result["hits"], result["misses"]) == (1, 2)


def test_storage_resize_requires_readmission_not_descriptor_crash():
    trace = moved_trace()[:2] + [event(2, "state.move", device="sim:0", tier="synthetic_hbm",
                                     allocation_id="larger-backing", allocation_bytes=128),
                               event(3, "state.consume", consumer_layer=4)]
    result = replay_demands(analyze_lifecycle(trace)["demands"], {"sim:0": 128})
    assert (result["hits"], result["misses"]) == (0, 2)
    assert result["peak_resident_bytes"] == 128


@pytest.mark.parametrize("action", ["state.create", "state.restore"])
def test_implicit_alias_cannot_bypass_share_identity_validation(action):
    trace = [event(0, "state.create", state=state("main", "m")),
             event(1, action, "other", state=state("other", "m"))]
    with pytest.raises(ValueError, match="requires state.share"):
        analyze_lifecycle(trace)


def test_dependency_content_change_is_reported_until_explicit_refresh():
    trace = [event(0, "state.create", state=state("main", "m")),
             event(1, "state.create", "index", state=state("index", "i", "index", dependencies=["main"])),
             event(2, "state.consume", "index", consumer_layer=4),
             event(3, "state.update", context_version=1),
             event(4, "state.consume", "index", consumer_layer=4),
             event(5, "state.update", "index", context_version=1),
             event(6, "state.consume", "index", consumer_layer=4)]
    rows = analyze_lifecycle(trace)["consumes"]
    assert [r["dependency_revalidation_required_state_ids"] for r in rows] == [[], ["index"], []]
    assert rows[1]["dependency_status"] == "CONTENT_CHANGED_REVALIDATION_REQUIRED"
    # This is a trace of observed accesses, not an assertion that every changed
    # producer mathematically invalidates every possible consumer representation.
    assert rows[2]["dependency_status"] == "NO_OBSERVED_DEPENDENCY_CHANGE"


def test_dependency_storage_move_is_not_a_content_invalidation():
    trace = moved_trace()[:1] + [event(1, "state.create", "index",
                                     state=state("index", "i", "index", dependencies=["main"])),
                               event(2, "state.move", device="sim:0", tier="synthetic_hbm",
                                     allocation_id="new-address", allocation_bytes=64),
                               event(3, "state.consume", "index", consumer_layer=4)]
    assert analyze_lifecycle(trace)["consumes"][0]["dependency_revalidation_required_state_ids"] == []


def test_one_demand_cannot_contain_two_versions_of_one_cache_object():
    obj = {"device": "sim:0", "allocation_id": "m", "version": 0, "bytes": 64, "state_kind": "main"}
    other = deepcopy(obj)
    other["version"] = 1
    with pytest.raises(ValueError, match="multiple versions"):
        replay_demands([{"event_id": "e", "request_id": "r", "source": "synthetic", "objects": [obj, other]}],
                       {"sim:0": 128})


@pytest.mark.parametrize("field,value", [("allocation_bytes", 64.5), ("logical_bytes", True),
                                         ("context_version", -1), ("producer_layer", -1),
                                         ("consumer_layers", [1.5])])
def test_inventory_rejects_fractional_capacity_or_invalid_version_geometry(field, value):
    descriptor = state("s", "m")
    descriptor[field] = value
    with pytest.raises(ValueError):
        StateInstance(**descriptor)


def test_bad_snapshot_binding_is_rejected_before_writing_uninterpretable_tensor_file(tmp_path):
    instance = StateInstance(**state("s", "m"))
    root = tmp_path / "snapshot"
    with pytest.raises(TypeError):
        write_snapshot(root, [instance], {"s": np.zeros(16, dtype=np.float32)},
                       runtime_identity={"model_family": "synthetic", "runtime_version": "sim"},
                       bindings={"s": {"layout": "flat", "geometry_status": "SYNTHETIC",
                                        "slot_mapping": np.array([0, 1])}})
    assert not root.exists()


def test_consumption_cannot_upgrade_synthetic_state_to_runtime_evidence():
    trace = moved_trace()[:2]
    trace[1] = replace(trace[1], source="runtime")
    with pytest.raises(ValueError, match="state provenance"):
        analyze_lifecycle(trace)


def test_dependency_provenance_is_preserved_in_policy_report():
    index = state("index", "i", "index", dependencies=["main"])
    index["source"] = "provided"
    trace = [event(0, "state.create", state=state("main", "m")),
             replace(event(1, "state.create", "index", state=index), source="provided"),
             replace(event(2, "state.consume", "index", consumer_layer=4), source="provided")]
    demands = analyze_lifecycle(trace)["demands"]
    assert demands[0]["state_sources"] == ["provided", "synthetic"]
    assert replay_demands(demands, {"sim:0": 128})["sources"] == ["provided", "synthetic"]


def test_same_context_content_update_invalidates_dependencies_without_faking_prompt_change():
    trace = [event(0, "state.create", state=state("main", "m")),
             event(1, "state.create", "index", state=state("index", "i", "index", dependencies=["main"])),
             event(2, "state.update", content_version=1),
             event(3, "state.consume", "index", consumer_layer=4),
             event(4, "state.update", "index", content_version=1),
             event(5, "state.consume", "index", consumer_layer=4)]
    report = analyze_lifecycle(trace)
    assert [r["dependency_revalidation_required_state_ids"] for r in report["consumes"]] == [["index"], []]
    assert all(s["context_version"] == 0 and s["content_version"] == 1 for s in report["final_live_states"])
    assert report["version_semantics_status"] == "CONTEXT_AND_CONTENT_VERSIONS_SEPARATE"
    assert report["legacy_context_update_event_ids"] == []


def test_cross_request_share_preserves_independent_context_identity_on_content_update():
    base = state("main", "m")
    base["context_version"] = 1
    shared = state("alias", "m", request="request-1")
    shared["context_version"] = 2
    trace = [event(0, "state.create", state=base),
             replace(event(1, "state.share", "alias", state=shared, parent_state_id="main"), request_id="request-1"),
             event(2, "state.update", content_version=1)]
    report = analyze_lifecycle(trace)
    states = {s["state_id"]: s for s in report["final_live_states"]}
    assert states["main"]["context_version"] == 1 and states["alias"]["context_version"] == 2
    assert states["main"]["content_version"] == states["alias"]["content_version"] == 1
    trace.append(event(3, "state.update", content_version=2, context_version=3))
    states = {s["state_id"]: s for s in analyze_lifecycle(trace)["final_live_states"]}
    assert states["main"]["context_version"] == 3 and states["alias"]["context_version"] == 2


def test_legacy_context_only_update_is_disclosed_not_silently_modernized():
    report = analyze_lifecycle(moved_trace()[:2] + [event(2, "state.update", context_version=1)])
    assert report["version_semantics_status"] == "LEGACY_CONTEXT_CONTENT_AMBIGUITY"
    assert report["legacy_context_update_event_ids"] == ["2"]


def test_runtime_context_only_update_is_rejected_but_explicit_content_update_works():
    base = state("main", "m")
    base["source"] = "runtime"
    create = replace(event(0, "state.create", state=base), source="runtime")
    update = replace(event(1, "state.update", context_version=1), source="runtime")
    with pytest.raises(ValueError, match="requires explicit content_version"):
        analyze_lifecycle([create, update])
    report = analyze_lifecycle([create, replace(update, data={"content_version": 1})])
    assert report["final_live_states"][0]["context_version"] == 0
    assert report["final_live_states"][0]["content_version"] == 1


def test_content_version_must_increase_even_with_new_context():
    trace = [event(0, "state.create", state=state("main", "m")),
             event(1, "state.update", content_version=1),
             event(2, "state.update", content_version=1, context_version=2)]
    with pytest.raises(ValueError, match="strictly increasing content"):
        analyze_lifecycle(trace)
