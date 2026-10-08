import json
import math

import numpy as np
import pytest

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import detector_fixture, probe_fixture, rank4_fixture, shared_subspace_fixture
from kvreuse_b5.data.types import StatePair
from kvreuse_b5.execution.artifacts import ArtifactStore, FrozenSubspaceArtifact, PrecomputedArtifactProvider
from kvreuse_b5.execution.config import ExperimentConfig
from kvreuse_b5.execution.runner import ExperimentRunner
from kvreuse_b5.methods.anchor import AnchorResidualCorrection
from kvreuse_b5.methods.base import CapabilityMissingError, CorrectionMethod, CorrectionResult, EvalContext, FitContext, MethodCapabilities, MethodCategory, MethodCost
from kvreuse_b5.methods.composite import CompositeCorrection
from kvreuse_b5.methods.detector import DetectorLayerCorrection
from kvreuse_b5.methods.position import apply_rope_shift
from kvreuse_b5.methods.selective import FixedEdgesPositionBinner, JointUnitOracle, NormalizedDepthBinner, RandomExpected, StaticLayerSelector
from kvreuse_b5.metrics.aggregate import aggregate_records
from kvreuse_b5.metrics.residual import compute_residual_metrics


def pair(
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
        pair_id,
        split,
        model,
        condition,
        state,
        layer,
        np.zeros_like(delta),
        delta,
        np.arange(len(delta)) if positions is None else np.asarray(positions),
        metadata or {},
    )


def config(output, methods):
    raw = {"version": "2.0", "experiment": {"split": "predefined"}, "output_dir": str(output), "methods": methods}
    return ExperimentConfig.from_mapping(raw)


def fit_context(train):
    return FitContext(frozenset(item.state_identity for item in train), "test-config")


# T21
def test_v2_state_identity_separates_model_condition_and_state():
    pairs = [
        pair("p1", "test", [[2.0]], model="q8", condition="PREFIX_ONLY", state="K"),
        pair("p1", "test", [[6.0]], model="q8", condition="PREFIX_ONLY", state="V"),
        pair("p1", "test", [[10.0]], model="q8", condition="POSITION_ONLY", state="K"),
        pair("p1", "test", [[12.0]], model="q35", condition="PREFIX_ONLY", state="K"),
    ]
    dataset = StatePairDataset(pairs)
    assert len({item.state_identity for item in dataset}) == 4
    assert len({item.sample_identity for item in dataset}) == 4


# T22
def test_v2_aggregation_rank_isolation(tmp_path):
    test_pair = rank4_fixture()[0]
    records, summary = ExperimentRunner(
        config(tmp_path / "rank", [{"name": "pair_lowrank_oracle", "ranks": [1, 2, 4]}])
    ).run([test_pair])
    rows = summary["global_summary"]
    assert [row["rank"] for row in rows] == [1, 2, 4]
    values = {row["rank"]: row["pooled_explained"] for row in rows}
    assert values[1] < values[2] < values[4]
    assert values[4] > 1 - 1e-12


# T23
def test_v2_aggregation_budget_isolation(tmp_path):
    delta = np.concatenate([np.full(10, 3.0), np.full(90, 1 / 3)])[:, None]
    records, summary = ExperimentRunner(
        config(tmp_path / "budget", [{"name": "joint_unit_oracle", "budgets": [0.01, 0.05, 0.10, 0.20]}])
    ).run([pair("budget", "test", delta)])
    rows = summary["budget_summary"]
    assert {row["requested_budget"] for row in rows} == {0.01, 0.05, 0.10, 0.20}
    assert len(rows) == 4


# T24
def test_v2_aggregation_probe_isolation(tmp_path):
    pairs = probe_fixture(True)
    _, summary = ExperimentRunner(
        config(tmp_path / "probe", [{"name": "probe_frozen_basis", "rank": 3, "probes": [1, 2, 4, 8]}])
    ).run(pairs)
    rows = summary["global_summary"]
    assert {row["probe_count"] for row in rows} == {1, 2, 4, 8}
    assert len(rows) == 4


# T25
def test_v2_frozen_basis_shared_artifact_single_svd(tmp_path):
    store = ArtifactStore()
    methods = [
        {"name": "frozen_lowrank_oracle", "ranks": [1, 2, 3, 4, 5]},
        {"name": "probe_frozen_basis", "ranks": [1, 2, 3, 4, 5], "probes": [1, 2, 4, 8]},
    ]
    ExperimentRunner(config(tmp_path / "shared", methods), artifact_store=store).run(shared_subspace_fixture())
    assert store.svd_fit_count == 1
    assert len(store.frozen_subspaces) == 1
    assert next(iter(store.frozen_subspaces.values())).max_rank == 5


# T26
def test_v2_pair_oracle_multi_rank_single_decomposition(tmp_path):
    store = ArtifactStore()
    ExperimentRunner(
        config(tmp_path / "pair-svd", [{"name": "pair_lowrank_oracle", "ranks": [1, 2, 4, 8, 16]}]),
        artifact_store=store,
    ).run(rank4_fixture())
    assert store.pair_svd_count == 1


def _rope_metadata(heads=2, head_dim=8, rotary_dim=8):
    return {
        "num_kv_heads": heads,
        "head_dim": head_dim,
        "rotary_dim": rotary_dim,
        "inv_freq": np.geomspace(1.0, 0.001, rotary_dim // 2),
        "flatten_layout": "T_HD",
        "rotary_layout": "interleaved",
    }


# T27
def test_v2_multi_head_rope_shift_is_head_local():
    rng = np.random.default_rng(27)
    raw = rng.normal(size=(9, 16))
    source = np.arange(9)
    target = source + 3
    zero = np.zeros(9)
    metadata = _rope_metadata(2, 8, 8)
    encoded_source = apply_rope_shift(raw, zero, source, metadata)
    expected = apply_rope_shift(raw, zero, target, metadata)
    moved = apply_rope_shift(encoded_source, source, target, metadata)
    errors = np.max(np.abs((moved - expected).reshape(9, 2, 8)), axis=(0, 2))
    assert np.all(errors < 1e-10)


# T28
def test_v2_partial_rotary_dim_preserves_each_head_tail():
    rng = np.random.default_rng(28)
    state = rng.normal(size=(7, 24))
    metadata = _rope_metadata(2, 12, 8)
    moved = apply_rope_shift(state, np.arange(7), np.arange(7) + 5, metadata).reshape(7, 2, 12)
    original = state.reshape(7, 2, 12)
    assert np.array_equal(moved[..., 8:], original[..., 8:])


# T29
def test_v2_static_layer_neutral_tie_is_order_independent():
    train = [pair("train", "train", np.ones((20, 1)))]
    method = StaticLayerSelector(0.25, "neutral_partial", config_seed=29)
    method.fit(train, fit_context(train))
    positions = np.arange(20)
    first = pair("same", "test", np.arange(1, 21)[:, None], positions=positions)
    permutation = np.array([10, 2, 18, 1, 7, 15, 4, 19, 0, 13, 6, 17, 9, 3, 12, 5, 16, 8, 14, 11])
    reordered = pair("same", "test", first.b[permutation], positions=positions[permutation])
    selected_first = set(first.positions[method.apply(first).diagnostics["selected_indices"]].tolist())
    selected_second = set(reordered.positions[method.apply(reordered).diagnostics["selected_indices"]].tolist())
    assert selected_first == selected_second
    assert selected_first != set(range(5))


# T30
def test_v2_group_atomic_layer_selector_records_overshoot():
    train = [pair("train", "train", np.full((10, 1), 3.0), layer=0), pair("train", "train", np.ones((10, 1)), layer=1)]
    test = [pair("test", "test", np.full((10, 1), 3.0), layer=0), pair("test", "test", np.ones((10, 1)), layer=1)]
    method = StaticLayerSelector(0.25, "group_atomic", config_seed=30)
    method.fit(train, fit_context(train))
    results = method.apply_group(test)
    diagnostic = next(iter(results.values())).diagnostics
    assert diagnostic["unit_budget"] == 5
    assert diagnostic["actual_budget"] == 0.5
    assert diagnostic["budget_overshoot"] == pytest.approx(0.25)
    assert sum(result.cost.exact_repaired_units for result in results.values()) == 10


# T31
def test_v2_fixed_edge_position_bins_use_x_start():
    positions = np.array([100, 115, 116, 131, 132, 164, 228, 356, 612, 1124])
    item = pair("bins", "test", np.ones((len(positions), 1)), positions=positions, metadata={"x_start_position": 100})
    binner = FixedEdgesPositionBinner([0, 16, 32, 64, 128, 256, 512, 1024, np.inf])
    assert binner.bins(item).tolist() == [0, 0, 1, 1, 2, 3, 4, 5, 6, 7]


# T32
def test_v2_normalized_position_bins_cover_depth():
    positions = np.array([100, 125, 150, 175, 200])
    item = pair("bins", "test", np.ones((5, 1)), positions=positions)
    assert NormalizedDepthBinner(4).bins(item).tolist() == [0, 1, 2, 3, 3]


def _exact_detector_method(train, count=2, budget=4 / 24):
    method = DetectorLayerCorrection(count, budget, selection="fixed", fixed_ids=list(range(count)), mode="exact_recompute")
    method.fit(train, fit_context(train))
    return method


# T33
def test_v2_detector_exact_recompute_corrects_detector_layers():
    pairs = detector_fixture()
    train = [item for item in pairs if item.split == "train"]
    test = [item for item in pairs if item.split == "test"]
    method = _exact_detector_method(train)
    results = method.apply_group(test)
    for item in test[:2]:
        result = results[item.state_identity]
        assert np.array_equal(result.corrected, item.b)
        assert result.cost.detector_recomputed_units == item.total_units


# T34
def test_v2_detector_cost_is_not_duplicated(tmp_path):
    pairs = detector_fixture()
    methods = [{"name": "detector_layer_selector", "detector_count": 2, "budget": 4 / 24, "selection": "fixed", "fixed_ids": [0, 1], "mode": "exact_recompute"}]
    ExperimentRunner(config(tmp_path / "detector", methods)).run(pairs)
    costs = json.loads((tmp_path / "detector" / "sample_costs.json").read_text())
    assert len(costs) == 1
    assert costs[0]["detector_recomputed_units"] == 2 * 24
    assert costs[0]["additional_repair_units"] == 2 * 4
    assert costs[0]["total_corrected_units"] == 56


# T35
def test_v2_external_detector_requires_real_provider():
    pairs = detector_fixture()
    train = [item for item in pairs if item.split == "train"]
    test = [item for item in pairs if item.split == "test"]
    method = DetectorLayerCorrection(1, 0.1, selection="fixed", fixed_ids=[0], mode="external_score")
    method.fit(train, fit_context(train))
    with pytest.raises(CapabilityMissingError, match="detector_score_provider"):
        method.apply_group(test)


# T36
def test_v2_anchor_missing_descriptor_is_capability_missing():
    train = [pair("anchor-train", "train", np.ones((5, 2)), metadata={"anchor_descriptor": np.array([0.0])})]
    method = AnchorResidualCorrection(1)
    method.fit(train, fit_context(train))
    with pytest.raises(CapabilityMissingError, match="anchor_descriptor"):
        method.apply(pair("anchor-test", "test", np.ones((5, 2))))


# T37
def test_v2_composite_rejects_metric_only_stage():
    with pytest.raises(ValueError, match="MetricOnly|metric-only"):
        CompositeCorrection([JointUnitOracle(0.5), RandomExpected(0.5)])


class HalfResidual(CorrectionMethod):
    name = "half_residual"
    category = MethodCategory.REFERENCE
    capabilities = MethodCapabilities(oracle=True, uses_full_target=True)

    def correct(self, item: StatePair, context: EvalContext):
        return CorrectionResult(item.a + 0.5 * item.residual, MethodCost(total_units=item.total_units))


# T38
def test_v2_composite_second_stage_uses_updated_residual():
    item = pair("composite", "test", np.array([4.0, 3.0, 2.0, 1.0])[:, None])
    result = CompositeCorrection([HalfResidual(), JointUnitOracle(0.25)]).apply(item)
    metric = compute_residual_metrics(item.a, item.b, result.corrected)
    assert metric.corrected_sse == pytest.approx(0.25 * (3**2 + 2**2 + 1**2))
    assert result.cost.exact_repaired_units == 1


def _summary_row(pair_id, baseline, corrected, status="OK"):
    return {
        "model_family": "m", "condition": "c", "state_kind": "K", "pair_id": pair_id, "layer_id": 0,
        "method": "m", "variant": "v", "method_config_hash": "m-config",
        "rank": None, "requested_budget": None, "probe_count": None,
        "anchor_count": None, "detector_count": None, "status": status, "baseline_sse": baseline,
        "corrected_sse": corrected, "energy_explained": float("nan") if status == "ZERO_RESIDUAL" else 1 - corrected / baseline,
    }


# T39
def test_v2_mixed_zero_nonzero_pooled_uses_only_valid_residual():
    summary = aggregate_records([_summary_row("valid", 10.0, 2.0), _summary_row("zero", 0.0, 0.0, "ZERO_RESIDUAL")])["global_summary"][0]
    assert summary["pooled_explained"] == pytest.approx(0.8)
    assert summary["valid_count"] == 1
    assert summary["zero_residual_count"] == 1


# T40
def test_v2_checkpoint_resume_across_k_v_prefix_position_and_models(tmp_path):
    specs = [
        ("q8", "PREFIX_ONLY", "K", 4.0),
        ("q8", "PREFIX_ONLY", "V", 36.0),
        ("q8", "POSITION_ONLY", "K", 100.0),
        ("q35", "PREFIX_ONLY", "K", 144.0),
    ]
    pairs = [pair("p1", "test", [[math.sqrt(sse)]], model=model, condition=condition, state=state) for model, condition, state, sse in specs]
    run_config = config(tmp_path / "identity", [{"name": "no_correction"}])
    runner = ExperimentRunner(run_config)
    first, _ = runner.run(pairs)
    resumed, _ = runner.run(pairs)
    assert json.dumps(first, sort_keys=True, allow_nan=True) == json.dumps(resumed, sort_keys=True, allow_nan=True)
    observed = {(row["model_family"], row["condition"], row["state_kind"]): row["baseline_sse"] for row in resumed}
    assert observed == {(model, condition, state): sse for model, condition, state, sse in specs}


def test_v2_checkpoint_rejects_changed_dataset_under_same_config(tmp_path):
    run_config = config(tmp_path / "dataset-fingerprint", [{"name": "no_correction"}])
    ExperimentRunner(run_config).run([pair("same", "test", [[1.0]])])
    with pytest.raises(ValueError, match="dataset fingerprint mismatch"):
        ExperimentRunner(run_config).run([pair("same", "test", [[2.0]])])


class StaticProvider(PrecomputedArtifactProvider):
    def __init__(self, artifact):
        self.artifact = artifact

    def frozen_subspace(self, fit_key, min_rank):
        return self.artifact if fit_key == self.artifact.fit_key else None


def test_v2_precomputed_artifact_provider_loads_without_svd():
    train = [pair("train", "train", np.ones((4, 3)))]
    basis = np.eye(3, 2)
    artifact = FrozenSubspaceArtifact(
        fit_key=train[0].fit_key,
        basis=basis,
        singular_values=np.ones(2),
        fit_pair_ids=frozenset(item.state_identity for item in train),
        fit_config_hash="precomputed",
        feature_dim=3,
        artifact_hash="precomputed-hash",
    )
    store = ArtifactStore(StaticProvider(artifact))
    store.prepare_frozen(train, 2, fit_context(train))
    assert store.svd_fit_count == 0
    assert store.frozen_for(train[0], 2).artifact_hash == "precomputed-hash"
