import numpy as np

from kvreuse_b5.data.synthetic import anchor_fixture, concentrated_energy_fixture, detector_fixture, static_joint_fixture
from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.execution.config import ExperimentConfig
from kvreuse_b5.execution.runner import ExperimentRunner
from kvreuse_b5.methods.anchor import AnchorResidualCorrection
from kvreuse_b5.methods.base import FitContext
from kvreuse_b5.methods.detector import DetectorLayerSelector
from kvreuse_b5.methods.selective import JointUnitOracle, RandomExpected, StaticLayerPositionSelector, UniformAbsoluteBinner
from kvreuse_b5.metrics.residual import compute_residual_metrics


def explained(pair, method):
    result = method.apply(pair)
    return compute_residual_metrics(
        pair.a,
        pair.b,
        result.corrected,
        result.corrected_sse_override,
        result.corrected_sse_multiplier,
    ).energy_explained


def test_joint_oracle_finds_90_percent_energy_and_full_budget_repairs_all():
    pair = concentrated_energy_fixture()[0]
    assert explained(pair, JointUnitOracle(0.10)) > 0.899
    assert explained(pair, JointUnitOracle(0.10)) > explained(pair, RandomExpected(0.10)) + 0.7
    assert explained(pair, JointUnitOracle(1.0)) > 1 - 1e-12


def test_static_layer_position_pattern_generalizes():
    pairs = static_joint_fixture()
    train = [p for p in pairs if p.split == "train"]
    test = [p for p in pairs if p.split == "test"]
    method = StaticLayerPositionSelector(0.125, UniformAbsoluteBinner(1))
    method.fit(train, FitContext(frozenset(p.state_identity for p in train)))
    assert min(explained(pair, method) for pair in test) > 0.99


def test_detector_correlated_layers():
    pairs = detector_fixture()
    train = [p for p in pairs if p.split == "train"]
    test = [p for p in pairs if p.split == "test"]
    selector = DetectorLayerSelector(1, 4 / 24)
    selector.fit(train, FitContext(frozenset(p.state_identity for p in train)))
    result = selector.evaluate(test)
    assert result.top_k_position_recall == 1.0
    assert result.oracle_unit_energy_coverage > 0.99
    assert result.repair_energy_explained > 0.95


def test_detector_runs_grouped_and_checkpoints_per_layer(tmp_path):
    raw = {
        "experiment": {"split": "predefined"},
        "output_dir": str(tmp_path / "detector"),
        "methods": [
            {"name": "detector_layer_selector", "detector_count": 1, "budget": 4 / 24}
        ],
    }
    records, _ = ExperimentRunner(ExperimentConfig.from_mapping(raw)).run(
        StatePairDataset(detector_fixture())
    )
    assert len(records) == 4
    assert all(record["status"] == "OK" for record in records)
    assert all(record["detector_layers"] == 1 for record in records)
    assert all(record["diagnostics"]["top_k_position_recall"] == 1.0 for record in records)


def test_anchor_knn_beats_distant_anchor():
    pairs = anchor_fixture()
    train, test = pairs[:-1], pairs[-1]
    context = FitContext(frozenset(p.state_identity for p in train))
    nearest = AnchorResidualCorrection(1)
    nearest.fit(train, context)
    near_score = explained(test, nearest)
    # Deliberately compare to the most distant train anchor as a deterministic random-anchor baseline.
    distant_delta = train[0].residual
    distant_corrected = test.a + distant_delta
    distant_score = compute_residual_metrics(test.a, test.b, distant_corrected).energy_explained
    assert near_score > distant_score
