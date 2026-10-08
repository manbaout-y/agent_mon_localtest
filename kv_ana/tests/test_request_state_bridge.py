from copy import deepcopy

import pytest

from kvreuse_b5.state_research.request_bridge import request_from_wire, bind_state_captures


def request(attempt="a1", purpose="agent", source="http_interception"):
    return request_from_wire(
        {"model": "deepseek-v41", "messages": [{"role": "user", "content": "PRIVATE"}],
         "chat_template_kwargs": {"thinking": False}, "max_tokens": 128},
        {"X-AgentMon-Run-Id": "run", "X-AgentMon-Model-Call-Id": "call",
         "Authorization": "SECRET"}, attempt_id=attempt, request_purpose=purpose,
        model_family="DeepSeek-V4.1", context_version=1, source=source)


def binding(attempt="a1", service="s1", source="runtime"):
    return dict(request_attempt_id=attempt, service_run_id=service,
                engine_request_id="e1", source=source)


def capture(service="s1", **kwargs):
    return dict(capture_id="c1", service_run_id=service, engine_request_id="e1",
                model_family="DeepSeek-V4.1", context_version=1,
                runtime_identity={"revision": "explicit-test-revision"},
                **({"source": "runtime"} | kwargs))


def test_content_free_post_preparation_evidence():
    r = request()
    assert "PRIVATE" not in str(r) and "SECRET" not in str(r)
    assert r["effective_controls"]["chat_template_kwargs"] == {"thinking": False}
    assert r["state_capture_status"] == "NOT_CAPTURED"
    assert r["tokenization_status"] == "SERVER_INPUT_IDS_NOT_CAPTURED"


def test_retry_and_summary_have_distinct_transport_identity():
    requests = [request(), request("a2"), request("summary", "context_summary")]
    rows = bind_state_captures(requests, [binding("a2")], [capture()])
    assert rows[0]["request_id"] == "a2"
    assert rows[0]["status"] == "MATCHED"
    rows = bind_state_captures(requests, [binding("summary")], [capture()])
    assert rows[0]["request_purpose"] == "context_summary"


def test_service_restart_does_not_reuse_engine_binding():
    rows = bind_state_captures([request()], [binding()], [capture("s2")])
    assert rows[0]["status"] == "BLOCKED_REQUEST_MAPPING"


def test_missing_runtime_binding_is_not_guessed():
    assert bind_state_captures([request()], [], [capture()])[0]["status"] == "BLOCKED_REQUEST_MAPPING"


@pytest.mark.parametrize("field,value", [("model_family", "DeepSeek-V4"), ("context_version", 2)])
def test_capture_model_and_context_must_match(field, value):
    c = capture(); c[field] = value
    assert bind_state_captures([request()], [binding()], [c])[0]["status"] == "CONFLICT_MODEL_OR_CONTEXT"


def test_synthetic_binding_cannot_become_runtime_evidence():
    assert bind_state_captures([request()], [binding(source="synthetic")], [capture()])[0]["status"] == "BLOCKED_SYNTHETIC_RUNTIME_PROMOTION"


def test_offline_synthetic_end_to_end():
    r = request(source="synthetic"); original = deepcopy(r)
    assert bind_state_captures([r], [binding(source="synthetic")], [capture(source="synthetic")])[0]["status"] == "MATCHED"
    assert r == original


def test_missing_runtime_identity_is_blocked():
    c = capture(); c["runtime_identity"] = {}
    assert bind_state_captures([request()], [binding()], [c])[0]["status"] == "BLOCKED_RUNTIME_IDENTITY"


def test_conflicting_binding_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        bind_state_captures([request(), request("a2")], [binding(), binding("a2")], [])


def test_duplicate_attempt_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        bind_state_captures([request(), request()], [], [])
