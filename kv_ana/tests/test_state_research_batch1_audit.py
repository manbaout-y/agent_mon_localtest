"""Adversarial regressions for input identity and explicit state/trace evidence."""
from copy import deepcopy
from dataclasses import replace

import pytest

from kvreuse_b5.state_research.agentmon import correlate_events
from kvreuse_b5.state_research.fixtures import synthetic_records, synthetic_states
from kvreuse_b5.state_research.lifemem import prepare_records
from kvreuse_b5.state_research.prepare import prepare_dataset


def requests():
    return prepare_records(synthetic_records()[:2], dataset_id="audit")["requests"]


def bridge(model, service, engine="reused-engine"):
    return {"event_type": "vllm.request.received", "run_id": service,
            "payload": {"model_request_id": model, "vllm_request_id": engine}}


def test_engine_id_reuse_is_scoped_to_explicit_service_run():
    events = [
        {"run_id": "service-a", "payload": {"vllm_request_id": "reused-engine"}},
        {"run_id": "service-b", "payload": {"vllm_request_id": "reused-engine"}},
        bridge("model-0", "service-a"), bridge("model-1", "service-b"),
    ]
    result = correlate_events(requests(), events)
    assert result["matched"] == 4
    assert [r["request_id"] for r in result["rows"][:2]] == ["request-0", "request-1"]


def test_engine_alias_does_not_leak_into_a_different_service_run():
    events = [bridge("model-0", "service-a"),
              {"run_id": "service-b", "payload": {"vllm_request_id": "reused-engine"}}]
    assert correlate_events(requests(), events)["rows"][1]["status"] == "UNMATCHED"


def test_missing_service_run_is_ambiguous_if_engine_id_was_reused():
    events = [bridge("model-0", "service-a"), bridge("model-1", "service-b"),
              {"payload": {"vllm_request_id": "reused-engine"}}]
    assert correlate_events(requests(), events)["rows"][-1]["status"] == "AMBIGUOUS"


def test_agent_run_alias_is_respected_and_conflicting_aliases_fail_join():
    reqs = requests()
    for index, request in enumerate(reqs):
        request["agentmon"] = {"model_request_id": "shared", "agent_run_id": f"agent-{index}"}
    events = [{"payload": {"model_request_id": "shared", "agent_run_id": "agent-1"}},
              {"payload": {"model_request_id": "shared", "agent_run_id": "agent-1",
                           "agentmon_run_id": "agent-0"}}]
    rows = correlate_events(reqs, events)["rows"]
    assert rows[0]["request_id"] == "request-1"
    assert rows[1]["status"] == "CONFLICT"


def test_explicit_unknown_but_conflicting_alias_is_not_ignored():
    reqs = requests()
    reqs[0]["agentmon"]["engine_request_id"] = "known-engine"
    events = [{"payload": {"model_request_id": "model-0", "vllm_request_id": "wrong-engine"}}]
    assert correlate_events(reqs, events)["rows"][0]["status"] == "CONFLICT"


def test_duplicate_requests_cannot_silently_replace_correlation_identity():
    reqs = requests()
    reqs.append(deepcopy(reqs[0]))
    with pytest.raises(ValueError, match="unique request_id"):
        correlate_events(reqs, [])


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_conversations_are_isolated_not_cross_linked(value):
    records = synthetic_records()[:2]
    for record in records:
        record["conversation_id"] = value
    data = prepare_records(records, dataset_id="audit")
    assert data["transitions"] == []
    assert data["context_changes"] == []
    assert len({r["session_id"] for r in data["requests"]}) == 2


def test_real_conversation_cannot_collide_with_missing_session_marker():
    records = synthetic_records()[:2]
    records[0].pop("conversation_id")
    records[1]["conversation_id"] = "unlinked:request-0"
    data = prepare_records(records, dataset_id="audit")
    assert data["transitions"] == []
    assert data["context_changes"] == []


def test_memory_and_session_identities_are_dataset_scoped():
    a = prepare_records(synthetic_records()[:1], dataset_id="dataset-a")
    b = prepare_records(synthetic_records()[:1], dataset_id="dataset-b")
    assert a["requests"][0]["session_id"] != b["requests"][0]["session_id"]
    assert a["chunks"][0]["memory_key"] != b["chunks"][0]["memory_key"]
    assert a["requests"][0]["conversation_id"] == b["requests"][0]["conversation_id"]


def test_explicit_numeric_zero_request_id_is_preserved():
    records = synthetic_records()[:1]
    records[0]["research"]["request_id"] = 0
    assert prepare_records(records, dataset_id="audit")["requests"][0]["request_id"] == "0"


@pytest.mark.parametrize("changes,field", [({"model_family": "wrong-model"}, "model_family"),
                                        ({"context_version": 2}, "context_version")])
def test_snapshot_must_match_request_model_and_context(tmp_path, changes, field):
    state = replace(synthetic_states()[0], **changes)
    with pytest.raises(ValueError, match=field):
        prepare_dataset(synthetic_records(), tmp_path / "output", dataset_id="audit", states=[state])
    assert not (tmp_path / "output").exists()


def test_matching_snapshot_still_passes_without_inventing_runtime_evidence(tmp_path):
    report = prepare_dataset(synthetic_records(), tmp_path / "output", dataset_id="audit",
                             states=synthetic_states())
    assert report["validation"] == "PASS"
    assert report["token_aligned_pairs_for_state_review"] == 0
    assert report["research_readiness"]["repair_functional_validation"] == "REQUIRES_STATES_AND_RUNTIME_EXECUTION"
