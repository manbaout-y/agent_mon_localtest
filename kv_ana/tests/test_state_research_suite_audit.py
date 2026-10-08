"""Integration adversaries: passing an empty or unrelated experiment is not acceptance."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from kvreuse_b5.state_research.offline_fixtures import write_fixture_bundle
from kvreuse_b5.state_research.offline_suite import bounded_capsule, run_suite


def read(path):
    return json.loads(path.read_text())


def test_zero_consumption_cannot_pass_policy_experiment(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("\n".join(json.dumps(e) for e in events if e["action"] != "state.consume") + "\n")
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["status"] == "PARTIAL"
    assert report["stages"]["policy_replay"]["status"] == "INPUT_MISSING"
    assert report["policy_summary"] == []


@pytest.mark.parametrize("policies", [[], ["shared_lru", "shared_lru"]])
def test_empty_or_duplicate_policy_list_rejected(tmp_path, policies):
    config = write_fixture_bundle(tmp_path / "in")
    config["policies"] = policies
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["policy_replay"]["status"] == "FAIL"
    assert report["status"] == "FAIL"


def test_bad_policy_retains_other_policy_results(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    config["policies"] = ["shared_lru", "nonexistent"]
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["status"] == "FAIL"
    assert len(report["policy_summary"]) == 1
    assert read(tmp_path / "out/policy_errors.json")[0]["policy"] == "nonexistent"
    assert report["stages"]["recovery"]["status"] == "OFFLINE_CHECK_PASS"


def test_bad_artifact_preserves_good_state_comparison(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_comparisons.json"
    cases = read(path)
    bad = deepcopy(cases[0])
    bad["identity"]["pair_id"] = "missing-artifact-pair"
    bad["artifact"] = "missing.npz"
    path.write_text(json.dumps(cases + [bad]))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["status"] == "FAIL"
    assert len(read(tmp_path / "out/state_comparison.json")) == 1
    assert len(read(tmp_path / "out/state_comparison_errors.json")) == 1


def test_bad_recovery_case_preserves_good_plan(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/recovery_cases.json"
    cases = read(path)
    bad = deepcopy(cases[0])
    bad["case_id"] = "leaking"
    bad["candidates"][1]["uses_target_b"] = True
    path.write_text(json.dumps(cases + [bad]))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["status"] == "FAIL"
    rows = read(tmp_path / "out/recovery_plans.json")
    assert rows[0]["selected_path"] == "repair"
    assert rows[1]["status"] == "FAIL"


def test_manifest_linkage_is_not_assumed(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("\n".join(json.dumps(r) for r in rows if r["request_id"] != "request-0"))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["lifecycle"]["status"] == "FAIL"
    assert "absent from manifest" in report["stages"]["lifecycle"]["reason"]
    assert report["stages"]["policy_replay"]["status"] == "BLOCKED_DEPENDENCY"


def test_duplicate_manifest_request_is_not_silently_collapsed(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/requests.jsonl"
    text = path.read_text()
    path.write_text(text + text.splitlines()[0] + "\n")
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["agentmon_correlation"]["status"] == "FAIL"
    assert report["stages"]["lifecycle"]["status"] == "FAIL"


@pytest.mark.parametrize("budget", [0, 500, 45001, True, "45000"])
def test_capsule_hard_limit_checked_before_output(tmp_path, budget):
    with pytest.raises(ValueError, match="512 to 45000"):
        run_suite({"capsule_max_bytes": budget}, tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_capsule_utf8_limit_and_evidence_not_hidden(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["status"] == "OFFLINE_CHECKS_PASS"
    assert report["evidence_sources"]["lifecycle"] == ["synthetic"]
    assert "NOT_DYNAMIC" in report["research_status"]["resource_optimization"]
    assert report["research_status"]["v4_1_runtime"] == "SCHEMA_AND_CAPTURE_NOT_IMPLEMENTED"
    capsule = (tmp_path / "out/capsule.txt").read_text()
    assert "EVIDENCE_SOURCE[lifecycle]=synthetic" in capsule
    oversized = {"status": "PARTIAL", "stages": {str(i): {"status": "FAIL", "reason": "误" * 1000} for i in range(80)}}
    assert len(bounded_capsule(oversized, 45000).encode("utf-8")) <= 45000


def test_legacy_synthetic_mapping_is_not_reported_as_verified(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_comparisons.json"
    cases = read(path)
    cases[0]["mapping"].pop("positions_corrected")
    path.write_text(json.dumps(cases))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["state_comparison"]["status"] == "PARTIAL_OR_BLOCKED"
    assert read(tmp_path / "out/state_comparison.json")[0]["energy_explained"] == .75


def test_runtime_cost_variant_contract_cannot_report_ready_when_missing(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    for name in ("recovery_cases.json", "cost_trials.json"):
        path = tmp_path / "in" / name
        rows = read(path)
        for row in rows:
            row["source"] = "runtime"
            for c in row.get("candidates", []):
                c["evidence_source"] = "runtime"
        path.write_text(json.dumps(rows))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["recovery"]["status"] == "PARTIAL_OR_BLOCKED"
    plan = read(tmp_path / "out/recovery_plans.json")[0]
    assert plan["selected_path"] is None
    assert plan["candidates"][1]["status"] == "BLOCKED_INPUT_CONTRACT"


@pytest.mark.parametrize("field,value", [("model_family", "wrong-model"), ("context_version", 999)])
def test_state_request_manifest_model_and_context_are_checked(tmp_path, field, value):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[1]["data"]["state"][field] = value
    path.write_text("\n".join(json.dumps(e) for e in events))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["lifecycle"]["status"] == "FAIL"
    assert field in report["stages"]["lifecycle"]["reason"]


def test_legacy_context_only_updates_are_visible_as_partial(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    for event in events:
        if event["action"] == "state.update":
            event["data"] = {"context_version": 2}
    path.write_text("\n".join(json.dumps(e) for e in events))
    report = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert report["stages"]["lifecycle"]["status"] == "PARTIAL_OR_BLOCKED"
    assert "conflates" in report["stages"]["lifecycle"]["reason"]
