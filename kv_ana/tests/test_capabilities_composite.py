import numpy as np
import pytest

from kvreuse_b5.data.synthetic import constant_residual_fixture, smoke_dataset
from kvreuse_b5.methods.base import CapabilityMissingError, FitContext, MethodCategory
from kvreuse_b5.methods.composite import CompositeCorrection
from kvreuse_b5.methods.lowrank import FrozenSubspaceOracleCoeff
from kvreuse_b5.methods.placeholders import InterfaceOnlyMethod, PLACEHOLDER_CAPABILITIES
from kvreuse_b5.methods.probe import ProbeMean
from kvreuse_b5.methods.registry import default_registry
from kvreuse_b5.methods.selective import JointUnitOracle
from kvreuse_b5.methods.shared import SharedResidualOracle
from kvreuse_b5.metrics.residual import compute_residual_metrics


def test_registry_contains_required_interface_only_families():
    names = set(default_registry().names)
    assert {"a3", "prophetkv", "qcfuse", "cache_craft_query", "kvlink", "kv_packet", "c2kv", "recache", "fusionrag", "daf", "mepic", "cachetune"} <= names


def test_interface_only_reports_missing_instead_of_metric():
    pair = constant_residual_fixture()[0]
    method = InterfaceOnlyMethod("a3", PLACEHOLDER_CAPABILITIES["a3"])
    with pytest.raises(CapabilityMissingError, match="query"):
        method.apply(pair)


def test_composite_applies_second_stage_to_first_residual_and_accumulates_cost():
    pair = constant_residual_fixture()[0]
    composite = CompositeCorrection([ProbeMean(1), JointUnitOracle(0.25)], "probe_then_selective")
    result = composite.apply(pair)
    metric = compute_residual_metrics(pair.a, pair.b, result.corrected)
    assert metric.energy_explained > 1 - 1e-12
    assert result.cost.observed_probe_units == 1
    assert result.cost.exact_repaired_units == 4
    assert composite.category == MethodCategory.REFERENCE


def test_composite_registry_configuration():
    methods = default_registry().create(
        {
            "name": "composite",
            "variant": "shared_then_selective",
            "stages": [
                {"name": "shared_residual_oracle"},
                {"name": "joint_unit_oracle", "budget": 0.1},
            ],
        }
    )
    assert len(methods) == 1
    assert methods[0].variant == "shared_then_selective"


def test_fit_rejects_test_pair():
    pairs = smoke_dataset()
    train = [p for p in pairs if p.split == "train"]
    test = [p for p in pairs if p.split == "test"]
    method = FrozenSubspaceOracleCoeff(2)
    with pytest.raises(ValueError, match="non-train"):
        method.fit(train + test[:1], FitContext(frozenset(p.state_identity for p in train)))
