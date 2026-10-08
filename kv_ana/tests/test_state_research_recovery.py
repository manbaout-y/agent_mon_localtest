from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from kvreuse_b5.state_research.offline_fixtures import cost_scope, cost_trials, recovery_case, write_fixture_bundle
from kvreuse_b5.state_research.offline_suite import bounded_capsule, run_suite
from kvreuse_b5.state_research.recovery import CostProfiles, plan_recovery
from kvreuse_b5.state_research.scenarios import constructed_schedule
from kvreuse_b5.state_research.state_compare import compare_aligned_arrays, compare_npz, dependency_impact
from kvreuse_b5.state_research.snapshot_archive import write_snapshot
from kvreuse_b5.state_research.state_inventory import StateInstance
from kvreuse_b5.state_research.offline_fixtures import state


def test_cost_profile_uses_median_and_exact_scope():
    rows = cost_trials()
    rows.append({**rows[0], "trial_id": "another", "operation_duration_ns": 40})
    profiles = CostProfiles(rows)
    assert profiles.lookup(cost_scope(), "load", "synthetic")["median_operation_duration_ns"] == 30
    assert profiles.lookup({**cost_scope(), "size_bytes": 128}, "load", "synthetic") is None
    assert profiles.lookup(cost_scope(), "load", "runtime") is None


@pytest.mark.parametrize("field,value", [("split", "test"), ("source", "estimated"),
                                       ("operation_duration_ns", float("nan")), ("operation_duration_ns", -1)])
def test_invalid_calibration_rejected(field, value):
    rows = cost_trials()
    rows[0][field] = value
    with pytest.raises(ValueError): CostProfiles(rows)


def test_unproven_direct_reuse_blocked_and_best_costed_plan_is_not_execution():
    result = plan_recovery([recovery_case()], CostProfiles(cost_trials()), {"repair", "recompute"})[0]
    assert result["selected_path"] == "repair"
    assert result["candidates"][0]["status"] == "BLOCKED_VALIDITY_EVIDENCE"
    assert result["execution_status"] == "NOT_EXECUTED"


def test_missing_provider_never_becomes_a_surrogate():
    result = plan_recovery([recovery_case()], CostProfiles(cost_trials()), {"recompute"})[0]
    assert result["selected_path"] == "full_recompute"
    assert result["candidates"][1]["status"] == "CAPABILITY_MISSING"


def test_no_cost_does_not_mean_free():
    result = plan_recovery([recovery_case()], CostProfiles([]), {"repair", "recompute"})[0]
    assert result["status"] == "BLOCKED" and result["selected_path"] is None


def test_partial_cost_does_not_claim_global_optimum():
    result = plan_recovery([recovery_case()], CostProfiles(cost_trials()[-1:]), {"repair", "recompute"})[0]
    assert result["status"] == "PARTIAL_COST_COVERAGE"


def test_synthetic_validity_does_not_authorize_runtime_plan():
    case = recovery_case()
    case["source"] = "runtime"
    result = plan_recovery([case], CostProfiles(cost_trials()), {"repair", "recompute"})[0]
    assert result["selected_path"] is None
    assert result["candidates"][1]["status"] == "BLOCKED_EVIDENCE_SOURCE"


def test_fit_evaluation_overlap_rejected():
    case = recovery_case()
    case["sample_id"] = "calibration-sample"
    with pytest.raises(ValueError, match="contaminated"):
        plan_recovery([case], CostProfiles(cost_trials()), {"repair"})


def test_target_b_not_a_planner_input_signal():
    case = recovery_case()
    case["candidates"][1]["uses_target_b"] = True
    with pytest.raises(ValueError, match="target B"):
        plan_recovery([case], CostProfiles(cost_trials()), {"repair"})


def comparison_args():
    return {"mapping": {"row_a": [1, 0], "row_b": [0, 2], "row_corrected": [0, 2],
                        "positions_a": [4, 8], "positions_b": [4, 8], "positions_semantics": "EXPLICIT",
                        "evidence_ref": "fixture"},
            "identity": {"pair_id": "p", "split": "test", "model_family": "synthetic", "condition": "PREFIX_ONLY",
                         "state_kind": "generic", "layer_id": 2}, "source": "synthetic",
            "representation_a": "same", "representation_b": "same", "representation_corrected": "same"}


def test_explicit_distinct_rows_and_frozen_metrics():
    a = np.array([[1., 1.], [2., 2.], [3., 3.]])
    b = np.array([[3., 3.], [999., 999.], [2., 2.]])
    result = compare_aligned_arrays(a, b, b, **comparison_args())
    assert result["baseline_sse"] == 4 and result["corrected_sse"] == 0
    assert result["target_b_usage"] == "EVALUATION_ONLY"


def test_zero_degradation_is_not_swallowed():
    zero = np.zeros((3, 2))
    result = compare_aligned_arrays(zero, zero, np.ones_like(zero), **comparison_args())
    assert result["status"] == "ZERO_BASELINE_DEGRADED"
    assert result["corrected_sse"] == 4 and result["energy_explained"] is None


@pytest.mark.parametrize("change", ["representation", "duplicate_rows", "positions", "out_of_bounds", "quantized"])
def test_bad_artifact_mapping_never_inferred(change):
    kwargs = comparison_args()
    a = np.zeros((3, 2))
    if change == "representation": kwargs["representation_b"] = "another"
    if change == "duplicate_rows": kwargs["mapping"]["row_a"] = [0, 0]
    if change == "positions": kwargs["mapping"]["positions_b"] = [4, 9]
    if change == "out_of_bounds": kwargs["mapping"]["row_a"] = [0, 10]
    if change == "quantized": a = a.astype(np.int8)
    with pytest.raises(ValueError): compare_aligned_arrays(a, a, a, **kwargs)


def test_dependency_reachability_and_cycle():
    nodes = [{"state_id": "a", "depends_on": []}, {"state_id": "b", "depends_on": ["a"]},
             {"state_id": "c", "depends_on": ["b"]}]
    assert dependency_impact(nodes, ["a"])["potentially_invalidated"] == ["a", "b", "c"]
    nodes[0]["depends_on"] = ["c"]
    with pytest.raises(ValueError, match="cyclic"): dependency_impact(nodes, ["a"])


def test_schedule_is_explicit_constructed_no_service_time():
    rows = [{"request_id": str(i), "session_id": "s"} for i in range(3)]
    result = constructed_schedule(rows, interarrival_ns=10, pause_after=0, pause_ns=100)
    assert [r["release_offset_ns"] for r in result["requests"]] == [0, 110, 120]
    assert all(r["service_time_ns"] is None for r in result["requests"])
    assert result["order_basis"] == "CONSTRUCTED"


def test_full_offline_suite_and_npz_interchange(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    result = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert result["status"] == "OFFLINE_CHECKS_PASS"
    assert result["real_acceptance"] == "PENDING"
    assert len(result["policy_summary"]) == 3
    cases = json.loads((tmp_path / "in/state_comparisons.json").read_text())
    assert compare_npz(cases[0], tmp_path / "in")["energy_explained"] == .75
    with pytest.raises(ValueError, match="empty"): run_suite(config, tmp_path / "in", tmp_path / "out")


def test_missing_real_inputs_not_reported_as_pass(tmp_path):
    result = run_suite({}, tmp_path, tmp_path / "out")
    assert result["status"] == "PARTIAL"
    assert result["stages"]["lifecycle"]["status"] == "INPUT_MISSING"


def test_corrupted_trace_blocks_replay_but_other_offline_checks_continue(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    (tmp_path / "in/state_events.jsonl").write_text('{"action": "bad"}\n')
    result = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert result["status"] == "FAIL"
    assert result["stages"]["policy_replay"]["status"] == "BLOCKED_DEPENDENCY"
    assert result["stages"]["recovery"]["status"] == "OFFLINE_CHECK_PASS"


def test_capsule_hard_utf8_byte_bound():
    report = {"status": "PARTIAL", "stages": {str(i): {"status": "INPUT_MISSING", "reason": "缺失" * 50}
                                                 for i in range(30)}}
    text = bounded_capsule(report, 512)
    assert len(text.encode("utf-8")) <= 512
    assert "TRUNCATED" in text


def test_snapshot_aliases_preserve_arrays_but_not_duplicate_physical_accounting(tmp_path):
    states = [StateInstance(**state(sid, "shared")) for sid in ["one", "two"]]
    array = np.arange(16, dtype=np.float32)
    bindings = {s.state_id: {"layout": "synthetic_flat", "geometry_status": "SYNTHETIC"} for s in states}
    result = write_snapshot(tmp_path / "snapshot", states, {s.state_id: array for s in states},
                            runtime_identity={"model_family": "synthetic", "runtime_version": "sim-v1"}, bindings=bindings)
    assert result["accounting"]["physical_allocated_bytes"] == 64
    with np.load(tmp_path / "snapshot/state_views.npz", allow_pickle=False) as archive:
        assert np.array_equal(archive["state_1"], array)
    assert result["coverage_status"] == "PROVIDED_STATES_ONLY_NOT_PROOF_OF_ALL_RUNTIME_STATES"


def test_snapshot_quantized_values_not_silently_converted(tmp_path):
    descriptor = state("quant", "q", size=16)
    descriptor.update(dtype="int8", shape=[16])
    instance = StateInstance(**descriptor)
    result = write_snapshot(tmp_path / "q", [instance], {"quant": np.arange(16, dtype=np.int8)},
                            runtime_identity={"model_family": "synthetic", "runtime_version": "sim-v1"},
                            bindings={"quant": {"layout": "raw_int8", "geometry_status": "SYNTHETIC",
                                                "scale_ref": "externally-supplied-scale"}})
    assert result["states"][0]["state"]["dtype"] == "int8"


def test_snapshot_no_implicit_tensor_or_dtype_conversion(tmp_path):
    instance = StateInstance(**state("s", "a"))
    class DeviceTensor:
        def cpu(self):
            raise AssertionError("must not initiate a device transfer")
    for array in (DeviceTensor(), np.zeros(16, dtype=np.float64)):
        with pytest.raises(ValueError):
            write_snapshot(tmp_path / "bad", [instance], {"s": array},
                           runtime_identity={"model_family": "synthetic", "runtime_version": "sim-v1"},
                           bindings={"s": {"layout": "flat", "geometry_status": "SYNTHETIC"}})


def test_suite_rejects_cross_condition_split_leakage(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    path = tmp_path / "in/state_comparisons.json"
    cases = json.loads(path.read_text())
    another = deepcopy(cases[0])
    another["identity"].update(condition="OTHER", split="train")
    path.write_text(json.dumps(cases + [another]))
    result = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert result["stages"]["state_comparison"]["status"] == "FAIL"
    assert "split leakage" in result["stages"]["state_comparison"]["reason"]


def test_suite_empty_trace_and_ambiguous_joins_not_false_pass(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    (tmp_path / "in/state_events.jsonl").write_text('[]')
    (tmp_path / "in/agentmon_events.jsonl").write_text('{"event_id":"no-ids"}\n')
    result = run_suite(config, tmp_path / "in", tmp_path / "out")
    assert result["stages"]["lifecycle"]["status"] == "FAIL"
    assert result["stages"]["agentmon_correlation"]["status"] == "PARTIAL_OR_BLOCKED"


def test_full_offline_suite_does_not_import_model_frameworks(tmp_path):
    config = write_fixture_bundle(tmp_path / "in")
    code = '''
import sys, importlib.abc, json
from pathlib import Path
class Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'vllm', 'vllm_ascend'}:
            raise AssertionError('model framework imported: ' + fullname)
sys.meta_path.insert(0, Forbid())
from kvreuse_b5.state_research.offline_suite import run_suite
base = Path(sys.argv[1])
result = run_suite(json.loads((base/'config.json').read_text()), base, Path(sys.argv[2]))
assert result['status'] == 'OFFLINE_CHECKS_PASS'
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    subprocess.run([sys.executable, "-c", code, str(tmp_path / "in"), str(tmp_path / "out")],
                   env=env, check=True, capture_output=True, text=True)
