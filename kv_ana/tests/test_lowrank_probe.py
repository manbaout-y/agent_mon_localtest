import numpy as np

from kvreuse_b5.data.synthetic import constant_residual_fixture, probe_fixture, rank4_fixture, shared_subspace_fixture
from kvreuse_b5.methods.base import FitContext
from kvreuse_b5.methods.lowrank import FrozenSubspaceOracleCoeff, PairLowRankOracle
from kvreuse_b5.methods.probe import ProbeFrozenBasis
from kvreuse_b5.methods.shared import SharedResidualOracle
from kvreuse_b5.metrics.residual import compute_residual_metrics


def explained(pair, method):
    result = method.apply(pair)
    return compute_residual_metrics(pair.a, pair.b, result.corrected).energy_explained


def test_constant_shared_residual_is_exact():
    pair = constant_residual_fixture()[0]
    assert explained(pair, SharedResidualOracle()) > 1 - 1e-12


def test_exact_rank4_oracle_and_rank2_gap():
    pair = rank4_fixture()[0]
    rank2 = explained(pair, PairLowRankOracle(2))
    rank4 = explained(pair, PairLowRankOracle(4))
    assert rank4 > 1 - 1e-12
    assert rank2 < rank4 - 1e-3


def test_frozen_shared_subspace_generalizes_without_test_fit():
    pairs = shared_subspace_fixture()
    train, test = pairs[:-1], pairs[-1]
    method = FrozenSubspaceOracleCoeff(4)
    method.fit(train, FitContext(frozenset(p.state_identity for p in train)))
    assert explained(test, method) > 1 - 1e-12


def test_probe_linear_depth_smooth_beats_nonsmooth_and_upper_bound():
    scores = {}
    for smooth in (True, False):
        pairs = probe_fixture(smooth)
        train, test = pairs[:-1], pairs[-1]
        context = FitContext(frozenset(p.state_identity for p in train))
        probe = ProbeFrozenBasis(3, 4)
        upper = FrozenSubspaceOracleCoeff(3)
        probe.fit(train, context)
        upper.fit(train, context)
        scores[smooth] = explained(test, probe)
        assert scores[smooth] <= explained(test, upper) + 1e-9
    assert scores[True] > 0.999999
    assert scores[False] < scores[True] - 0.2
