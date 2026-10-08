import math

import numpy as np
import pytest

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import anchor_fixture, constant_residual_fixture, shared_subspace_fixture
from kvreuse_b5.data.types import StatePair
from kvreuse_b5.execution.config import ExperimentConfig, canonical_hash
from kvreuse_b5.execution.invariants import validate_result_invariants
from kvreuse_b5.execution.runner import ExperimentRunner
from kvreuse_b5.methods.base import CorrectionMethod, CorrectionResult, EvalContext, FitContext, MethodCost
from kvreuse_b5.methods.composite import CompositeCorrection
from kvreuse_b5.methods.lowrank import FrozenSubspaceOracleCoeff
from kvreuse_b5.methods.position import apply_rope_shift
from kvreuse_b5.methods.registry import MethodRegistry
from kvreuse_b5.methods.selective import JointUnitOracle
from kvreuse_b5.methods.shared import SharedResidualOracle
from kvreuse_b5.metrics.aggregate import aggregate_records
from kvreuse_b5.metrics.residual import compute_residual_metrics


def _pair(
    pair_id,
    split,
    delta,
    *,
    model="m",
    condition="PREFIX_ONLY",
    state="K",
    layer=0,
    positions=None,
    metadata=None,
):
    delta = np.asarray(delta, dtype=float)
    return StatePair(
        pair_id, split, model, condition, state, layer,
        np.zeros_like(delta), delta,
        np.arange(len(delta)) if positions is None else np.asarray(positions),
        metadata or {},
    )


def _config(output, methods, policy="error"):
    raw = {
        "version": "2.0.1",
        "experiment": {"split": "predefined", "zero_baseline_degradation_policy": policy},
        "output_dir": str(output),
        "methods": methods,
    }
    return ExperimentConfig.from_mapping(raw)


# PATCH-1 A
def test_v201_aggregation_separates_static_position_binners(tmp_path):
    positions = np.arange(32)
    train = _pair(
        "train", "train", np.linspace(0.1, 2.0, 32)[:, None], positions=positions,
        metadata={"x_start_position": 0},
    )
    test = _pair(
        "test", "test", np.linspace(0.2, 2.1, 32)[:, None], positions=positions,
        metadata={"x_start_position": 0},
    )
    methods = [
        {
            "name": "static_position", "budget": 0.1,
            "position_binner": {"type": "fixed_edges", "edges": [0, 16, 32, 64, 128, 256, 512, 1024, "inf"]},
        },
        {
            "name": "static_position", "budget": 0.1,
            "position_binner": {"type": "normalized_depth", "bin_count": 16},
        },
    ]
    records, summary = ExperimentRunner(_config(tmp_path / "binners", methods)).run([train, test])
    assert len(records) == 2
    assert len({row["method_config_hash"] for row in records}) == 2
    assert len(summary["global_summary"]) == 2
    assert summary["version"] == "2.0.1"


# PATCH-1 B
def test_v201_aggregation_separates_anchor_weighting(tmp_path):
    methods = [
        {"name": "anchor_residual", "k": 2, "weighting": "uniform"},
        {"name": "anchor_residual", "k": 2, "weighting": "inverse_distance"},
    ]
    records, summary = ExperimentRunner(_config(tmp_path / "anchor", methods)).run(anchor_fixture())
    assert len({row["method_config_hash"] for row in records}) == 2
    assert len(summary["global_summary"]) == 2


# PATCH-1 C
def test_v201_aggregation_separates_recursive_composite_config(tmp_path):
    methods = [
        {
            "name": "composite", "variant": "lowrank_then_selective",
            "stages": [
                {"name": "frozen_lowrank_oracle", "rank": 1},
                {"name": "joint_unit_oracle", "budget": 0.10},
            ],
        },
        {
            "name": "composite", "variant": "lowrank_then_selective",
            "stages": [
                {"name": "frozen_lowrank_oracle", "rank": 2},
                {"name": "joint_unit_oracle", "budget": 0.20},
            ],
        },
    ]
    records, summary = ExperimentRunner(_config(tmp_path / "composite", methods)).run(shared_subspace_fixture())
    assert {row["method"] for row in records} == {"composite"}
    assert {row["variant"] for row in records} == {"lowrank_then_selective"}
    assert len({row["method_config_hash"] for row in records}) == 2
    assert len(summary["global_summary"]) == 2
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def _rope_metadata(heads, head_dim, rotary_dim):
    return {
        "num_kv_heads": heads,
        "head_dim": head_dim,
        "rotary_dim": rotary_dim,
        "inv_freq": np.geomspace(1.0, 0.001, rotary_dim // 2),
        "flatten_layout": "T_HD",
        "rotary_layout": "split_half",
    }


# PATCH-2 A
def test_v201_split_half_multi_head_source_to_target():
    rng = np.random.default_rng(201)
    raw = rng.normal(size=(5, 16))
    source = np.arange(5)
    target = source + 3
    zero = np.zeros(5)
    metadata = _rope_metadata(2, 8, 8)
    encoded_source = apply_rope_shift(raw, zero, source, metadata)
    encoded_target = apply_rope_shift(raw, zero, target, metadata)
    corrected = apply_rope_shift(encoded_source, source, target, metadata)
    per_head_error = np.max(np.abs((corrected - encoded_target).reshape(5, 2, 8)), axis=(0, 2))
    assert np.all(per_head_error < 1e-10)


# PATCH-2 B
def test_v201_split_half_partial_rotary_preserves_tail():
    rng = np.random.default_rng(202)
    raw = rng.normal(size=(5, 4 * 12))
    source = np.arange(5)
    target = source + 4
    zero = np.zeros(5)
    metadata = _rope_metadata(4, 12, 8)
    encoded_source = apply_rope_shift(raw, zero, source, metadata)
    encoded_target = apply_rope_shift(raw, zero, target, metadata)
    corrected = apply_rope_shift(encoded_source, source, target, metadata)
    assert np.max(np.abs(corrected - encoded_target)) < 1e-10
    assert np.array_equal(
        corrected.reshape(5, 4, 12)[..., 8:],
        encoded_source.reshape(5, 4, 12)[..., 8:],
    )


# PATCH-2 C
def test_v201_rope_odd_rotary_dim_fails_fast():
    metadata = _rope_metadata(2, 8, 7)
    metadata["inv_freq"] = np.ones(3)
    with pytest.raises(ValueError, match="positive even"):
        apply_rope_shift(np.ones((5, 16)), np.arange(5), np.arange(5) + 1, metadata)


# PATCH-3 A
def test_v201_same_sample_layers_cannot_cross_split():
    pairs = [
        _pair("p1", "train", np.ones((4, 1)), model="q8", layer=0),
        _pair("p1", "test", np.ones((4, 1)), model="q8", layer=1),
    ]
    with pytest.raises(ValueError, match=r"sample_identity=.*train layer ids=\[0\].*test layer ids=\[1\]"):
        StatePairDataset(pairs)


# PATCH-3 B
def test_v201_same_sample_all_train_layers_pass():
    dataset = StatePairDataset([
        _pair("p1", "train", np.ones((4, 1)), model="q8", layer=0),
        _pair("p1", "train", np.ones((4, 1)), model="q8", layer=1),
    ])
    assert len(dataset.split()[0]) == 2


# PATCH-3 C
def test_v201_base_pair_cannot_cross_split_by_condition_or_state():
    pairs = [
        _pair("p1", "train", np.ones((4, 1)), model="q8", condition="PREFIX_ONLY", state="K"),
        _pair("p1", "test", np.ones((4, 1)), model="q8", condition="POSITION_ONLY", state="V"),
    ]
    with pytest.raises(ValueError, match="base_pair_identity=.*q8.*p1"):
        StatePairDataset(pairs)


# PATCH-4 A
def test_v201_shared_then_frozen_lowrank_fails_fast():
    with pytest.raises(ValueError, match="FrozenSubspace-based stage cannot appear"):
        CompositeCorrection([SharedResidualOracle(), FrozenSubspaceOracleCoeff(2)])


# PATCH-4 B
def test_v201_frozen_lowrank_then_selective_is_allowed():
    pairs = shared_subspace_fixture()
    train, test = pairs[:-1], pairs[-1]
    composite = CompositeCorrection([FrozenSubspaceOracleCoeff(2), JointUnitOracle(0.25)])
    composite.fit(train, FitContext(frozenset(item.state_identity for item in train)))
    result = composite.apply(test)
    assert result.corrected.shape == test.a.shape
    assert result.cost.exact_repaired_units == math.ceil(test.total_units * 0.25)


# PATCH-4 C
def test_v201_shared_then_selective_is_allowed():
    item = constant_residual_fixture()[0]
    composite = CompositeCorrection([SharedResidualOracle(), JointUnitOracle(0.25)])
    result = composite.apply(item)
    assert compute_residual_metrics(item.a, item.b, result.corrected).corrected_sse == pytest.approx(0.0)


# PATCH-5 A
def test_v201_zero_residual_unchanged_remains_zero_residual():
    zeros = np.zeros((4, 2))
    metric = compute_residual_metrics(zeros, zeros, zeros)
    assert metric.baseline_sse == 0.0
    assert metric.corrected_sse == 0.0
    assert metric.status == "ZERO_RESIDUAL"
    assert math.isnan(metric.energy_explained)


# PATCH-5 B
def test_v201_zero_baseline_degradation_preserves_corrected_sse():
    zeros = np.zeros((4, 2))
    metric = compute_residual_metrics(zeros, zeros, np.ones((4, 2)))
    assert metric.baseline_sse == 0.0
    assert metric.corrected_sse == 8.0
    assert metric.status == "ZERO_BASELINE_DEGRADED"
    assert math.isnan(metric.energy_explained)


def _aggregate_row(pair_id, baseline, corrected, status):
    return {
        "model_family": "m", "condition": "PREFIX_ONLY", "state_kind": "K",
        "pair_id": pair_id, "layer_id": 0, "method": "method", "variant": "variant",
        "method_config_hash": "config-hash", "rank": None, "requested_budget": None,
        "probe_count": None, "anchor_count": None, "detector_count": None,
        "baseline_sse": baseline, "corrected_sse": corrected, "status": status,
        "energy_explained": 1 - corrected / baseline if status == "OK" else float("nan"),
    }


# PATCH-5 C
def test_v201_mixed_zero_and_degraded_aggregation():
    rows = [
        _aggregate_row("normal", 10.0, 2.0, "OK"),
        _aggregate_row("zero", 0.0, 0.0, "ZERO_RESIDUAL"),
        _aggregate_row("degraded", 0.0, 8.0, "ZERO_BASELINE_DEGRADED"),
    ]
    summary = aggregate_records(rows)["global_summary"][0]
    assert summary["mean_explained"] == pytest.approx(0.8)
    assert summary["median_explained"] == pytest.approx(0.8)
    assert summary["pooled_explained"] == pytest.approx(0.8)
    assert summary["valid_count"] == 1
    assert summary["zero_residual_count"] == 1
    assert summary["zero_baseline_degraded_count"] == 1
    with pytest.raises(AssertionError, match="zero baseline correction degradation"):
        validate_result_invariants(
            [
                {
                    **rows[2],
                    "state_identity": ["m", "PREFIX_ONLY", "K", "degraded", 0],
                    "requested_budget": None,
                }
            ],
            zero_baseline_degradation_policy="error",
        )


class _BadZeroCorrection(CorrectionMethod):
    name = "bad_zero_correction"

    def correct(self, item: StatePair, context: EvalContext):
        return CorrectionResult(np.ones_like(item.a), MethodCost(total_units=item.total_units))


def test_v201_runner_default_policy_rejects_zero_degradation(tmp_path):
    registry = MethodRegistry()
    registry.register("bad_zero_correction", lambda method_config: [_BadZeroCorrection()])
    config = _config(tmp_path / "degradation-policy", [{"name": "bad_zero_correction"}])
    with pytest.raises(AssertionError, match="zero baseline correction degradation"):
        ExperimentRunner(config, registry=registry).run([_pair("zero", "test", np.zeros((4, 2)))])
