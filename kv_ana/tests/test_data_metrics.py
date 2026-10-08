import math

import numpy as np
import pytest

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import zero_residual_fixture
from kvreuse_b5.data.types import StatePair
from kvreuse_b5.metrics.aggregate import aggregate_records
from kvreuse_b5.metrics.residual import compute_residual_metrics
from kvreuse_b5.methods.baseline import NoCorrection


def test_shape_and_finite_fail_fast():
    with pytest.raises(ValueError, match="shape mismatch"):
        StatePair("bad", "test", "m", "c", "K", 0, np.zeros((2, 3)), np.zeros((3, 3)), np.arange(2))
    with pytest.raises(ValueError, match="NaN or Inf"):
        StatePair("bad", "test", "m", "c", "K", 0, np.array([[np.nan]]), np.zeros((1, 1)), np.arange(1))


def test_train_test_identity_overlap_rejected():
    kwargs = dict(model_family="m", condition="c", state_kind="K", layer_id=0, a=np.zeros((2, 2)), b=np.ones((2, 2)), positions=np.arange(2))
    with pytest.raises(ValueError, match="duplicate state_identity"):
        StatePairDataset([StatePair("same", "train", **kwargs), StatePair("same", "test", **kwargs)])


def test_zero_residual_is_nan_and_aggregate_excludes_it():
    pair = zero_residual_fixture()[0]
    result = NoCorrection().apply(pair)
    metric = compute_residual_metrics(pair.a, pair.b, result.corrected)
    assert metric.status == "ZERO_RESIDUAL"
    assert math.isnan(metric.energy_explained)
    row = {
        "model_family": "m", "condition": "c", "state_kind": "K", "method": "m", "variant": "v",
        "method_config_hash": "m-config",
        "rank": None, "requested_budget": None, "probe_count": None, "anchor_count": None,
        "detector_count": None, "layer_id": 0, "status": "ZERO_RESIDUAL",
        "energy_explained": float("nan"), "baseline_sse": 0.0, "corrected_sse": 0.0,
    }
    summary = aggregate_records([row])["global_summary"][0]
    assert summary["valid_count"] == 0
    assert summary["pooled_explained"] is None
