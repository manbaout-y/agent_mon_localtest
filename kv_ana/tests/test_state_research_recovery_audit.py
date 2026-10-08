"""Adversarial checks for comparison scope and externally supplied state alignment."""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

from kvreuse_b5.state_research.offline_fixtures import cost_scope, cost_trials, recovery_case
from kvreuse_b5.state_research.recovery import CostProfiles, plan_recovery
from kvreuse_b5.state_research.state_compare import compare_aligned_arrays, compare_npz


def runtime_scope():
    return {**cost_scope(), "condition": "PREFIX_ONLY", "layer_id": 2, "compress_ratio": 4,
            "operation_unit": "layer_state_rows", "unit_count": 16}


def runtime_trial(variant="shift", **parameters):
    return {**cost_trials()[1], "source": "runtime", "scope": runtime_scope(),
            "variant": variant, "parameters": parameters}


def runtime_case(variant="shift", **parameters):
    candidate = {**recovery_case()["candidates"][1], "variant": variant, "parameters": parameters,
                 "evidence_source": "runtime", "uses_target_b": False}
    return {**recovery_case(), "source": "runtime", "scope": runtime_scope(), "candidates": [candidate]}


@pytest.mark.parametrize("field,value", [("layer_id", 4), ("compress_ratio", 128),
                                         ("condition", "POSITION_ONLY"), ("unit_count", 1),
                                         ("operation_unit", "full_request"), ("transfer_tier", "host_to_device")])
def test_cost_scope_does_not_discard_experiment_dimensions(field, value):
    first = runtime_trial()
    second = deepcopy(first)
    second.update(trial_id="second", operation_duration_ns=1000)
    second["scope"][field] = value
    profiles = CostProfiles([first, second])
    result = profiles.lookup(first["scope"], "repair", "runtime", variant="shift")
    assert result["median_operation_duration_ns"] == 10
    assert result["trial_count"] == 1


def test_cost_variant_and_parameters_do_not_share_a_median():
    trials = [runtime_trial("shift", rank=1), runtime_trial("shift", rank=4), runtime_trial("replace", rank=1)]
    for i, trial in enumerate(trials):
        trial.update(trial_id=str(i), operation_duration_ns=10 + 100 * i)
    profiles = CostProfiles(trials)
    result = plan_recovery([runtime_case("shift", rank=4)], profiles, {"repair"})[0]
    assert result["candidates"][0]["cost"]["median_operation_duration_ns"] == 110
    assert result["selected_variant"] == "shift"
    assert result["selected_parameters"] == {"rank": 4}


def test_canonical_scope_and_parameters_ignore_serialization_order():
    trial = runtime_trial("shift", rank=4, budget=.1)
    scope = dict(reversed(list(trial["scope"].items())))
    result = CostProfiles([trial]).lookup(scope, "repair", "runtime", variant="shift",
                                          parameters={"budget": .1, "rank": 4})
    assert result["trial_count"] == 1


@pytest.mark.parametrize("missing", ["layer_id", "compress_ratio", "condition", "operation_unit", "unit_count"])
def test_incomplete_runtime_granularity_cannot_be_ready(missing):
    trial, case = runtime_trial(), runtime_case()
    del trial["scope"][missing]
    del case["scope"][missing]
    result = plan_recovery([case], CostProfiles([trial]), {"repair"})[0]
    assert result["status"] == "BLOCKED"
    assert result["candidates"][0]["status"] == "BLOCKED_INPUT_CONTRACT"
    assert result["selected_path"] is None


@pytest.mark.parametrize("missing", ["variant", "uses_target_b", "required_capabilities"])
def test_incomplete_runtime_candidate_contract_cannot_be_ready(missing):
    case = runtime_case()
    del case["candidates"][0][missing]
    result = plan_recovery([case], CostProfiles([runtime_trial()]), {"repair"})[0]
    assert result["status"] == "BLOCKED"


def test_top_level_target_b_declaration_is_not_ignored():
    case = runtime_case()
    case["uses_target_b"] = True
    with pytest.raises(ValueError, match="target B"):
        plan_recovery([case], CostProfiles([runtime_trial()]), {"repair"})


def test_unresolved_runtime_alternative_is_not_complete_plan():
    case = runtime_case()
    case["candidates"].append({"operation": "direct_reuse", "validity": "UNRESOLVED", "evidence_refs": []})
    result = plan_recovery([case], CostProfiles([runtime_trial()]), {"repair"})[0]
    assert result["selected_path"] == "repair"
    assert result["status"] == "PARTIAL_CANDIDATE_COVERAGE"
    assert result["candidate_evidence_complete"] is False
    assert result["runtime_execution_ready"] is False


def comparison(source="runtime"):
    return {"mapping": {"row_a": [0, 1], "row_b": [0, 1], "row_corrected": [1, 0],
                        "positions_a": [4, 8], "positions_b": [4, 8], "positions_corrected": [4, 8],
                        "positions_semantics": "X_RELATIVE_ANCHOR", "evidence_ref": "explicit-mapping"},
            "identity": {"pair_id": 1, "split": "test", "model_family": "ds", "condition": "PREFIX_ONLY",
                         "state_kind": "C4", "layer_id": 2}, "source": source,
            "representation_a": "C4", "representation_b": "C4", "representation_corrected": "C4"}


def test_distinct_corrected_row_indices_require_matching_canonical_coordinates():
    a, b = np.zeros((2, 1)), np.array([[1.], [2.]])
    args = comparison()
    good = compare_aligned_arrays(a, b, b[::-1], **args)
    assert good["corrected_sse"] == 0 and good["canonical_mapping_verified"]
    args["mapping"]["positions_corrected"] = [8, 4]
    with pytest.raises(ValueError, match="canonical positions"):
        compare_aligned_arrays(a, b, b[::-1], **args)


@pytest.mark.parametrize("source", ["runtime", "provided"])
def test_real_comparison_requires_explicit_corrected_coordinates(source):
    args = comparison(source)
    del args["mapping"]["positions_corrected"]
    with pytest.raises(ValueError, match="positions_corrected"):
        compare_aligned_arrays(np.zeros((2, 1)), np.zeros((2, 1)), np.zeros((2, 1)), **args)


def test_legacy_synthetic_coordinates_are_explicitly_unverified():
    args = comparison("synthetic")
    del args["mapping"]["positions_corrected"]
    result = compare_aligned_arrays(np.zeros((2, 1)), np.zeros((2, 1)), np.zeros((2, 1)), **args)
    assert not result["canonical_mapping_verified"]
    assert result["mapping_status"].endswith("UNVERIFIED")


def test_duplicate_canonical_positions_rejected():
    args = comparison()
    for name in ("positions_a", "positions_b", "positions_corrected"):
        args["mapping"][name] = [4, 4]
    with pytest.raises(ValueError, match="duplicate canonical"):
        compare_aligned_arrays(np.zeros((2, 1)), np.zeros((2, 1)), np.zeros((2, 1)), **args)


def test_corrupted_npz_is_a_case_level_value_error(tmp_path):
    path = tmp_path / "broken.npz"
    with ZipFile(path, "w") as archive:
        archive.writestr("a.npy", b"not a numpy array")
    case = {"artifact": path.name, "keys": {key: key for key in ("a", "b", "corrected")}}
    with pytest.raises(ValueError, match="cannot read state artifact"):
        compare_npz(case, tmp_path)
