from __future__ import annotations

from typing import Iterable

import numpy as np

from .types import StatePair, stable_matmul
from kvreuse_b5.methods.position import apply_rope_shift


def _pair(
    pair_id: str,
    split: str,
    delta: np.ndarray,
    *,
    layer: int = 0,
    positions: np.ndarray | None = None,
    metadata: dict | None = None,
    family: str = "synthetic",
    condition: str = "POSITION_ONLY",
    state_kind: str = "K",
) -> StatePair:
    delta = np.asarray(delta, dtype=np.float64)
    a = np.zeros_like(delta)
    return StatePair(
        pair_id=pair_id,
        split=split,
        model_family=family,
        condition=condition,
        state_kind=state_kind,
        layer_id=layer,
        a=a,
        b=a + delta,
        positions=np.arange(len(delta)) if positions is None else positions,
        metadata=metadata or {},
    )


def zero_residual_fixture() -> list[StatePair]:
    return [_pair("zero-test", "test", np.zeros((12, 8)), family="fixture_zero")]


def constant_residual_fixture() -> list[StatePair]:
    delta = np.repeat(np.arange(1, 9, dtype=float)[None, :], 16, axis=0)
    return [_pair("constant-test", "test", delta, family="fixture_constant")]


def rank4_fixture(seed: int = 1) -> list[StatePair]:
    rng = np.random.default_rng(seed)
    left = rng.normal(size=(32, 4)) * np.array([4.0, 3.0, 2.0, 1.0])
    right, _ = np.linalg.qr(rng.normal(size=(16, 4)))
    return [_pair("rank4-test", "test", stable_matmul(left, right.T), family="fixture_rank4")]


def shared_subspace_fixture(seed: int = 2) -> list[StatePair]:
    rng = np.random.default_rng(seed)
    basis, _ = np.linalg.qr(rng.normal(size=(12, 4)))
    pairs = []
    for index in range(4):
        coefficients = rng.normal(size=(24, 4))
        pairs.append(_pair(f"subspace-train-{index}", "train", stable_matmul(coefficients, basis.T), family="fixture_subspace"))
    pairs.append(_pair("subspace-test", "test", stable_matmul(rng.normal(size=(24, 4)), basis.T), family="fixture_subspace"))
    return pairs


def concentrated_energy_fixture() -> list[StatePair]:
    high = np.full(10, 3.0)  # total energy 90
    low = np.full(90, np.sqrt(1.0 / 9.0))  # total energy 10
    return [_pair("concentrated-test", "test", np.concatenate([high, low])[:, None], family="fixture_concentrated")]


def static_joint_fixture() -> list[StatePair]:
    pairs = []
    for split, samples in (("train", 4), ("test", 1)):
        for sample in range(samples):
            for layer, hot_start in ((0, 4), (1, 12), (2, 20)):
                delta = np.full((32, 3), 0.05)
                delta[hot_start : hot_start + 4] = 3.0
                pairs.append(
                    _pair(
                        f"joint-{split}-{sample}",
                        split,
                        delta,
                        layer=layer,
                        family="fixture_static_joint",
                        metadata={"dataset_group_id": f"joint-{split}-{sample}", "x_start_position": 0},
                    )
                )
    return pairs


def detector_fixture() -> list[StatePair]:
    pairs = []
    for split, samples in (("train", 3), ("test", 1)):
        for sample in range(samples):
            for layer in range(4):
                delta = np.full((24, 2), 0.1)
                delta[[3, 4, 17, 18]] = 2.0 + 0.2 * layer
                pairs.append(
                    _pair(
                        f"detector-{split}-{sample}",
                        split,
                        delta,
                        layer=layer,
                        family="fixture_detector",
                        metadata={"dataset_group_id": f"detector-{split}-{sample}"},
                    )
                )
    return pairs


def anchor_fixture() -> list[StatePair]:
    positions = np.linspace(0.0, 1.0, 20)
    pairs = []
    for index, descriptor in enumerate(np.linspace(-1.0, 1.0, 9)):
        delta = np.stack([descriptor * np.ones(20), descriptor * positions], axis=1)
        pairs.append(
            _pair(
                f"anchor-train-{index}",
                "train",
                delta,
                family="fixture_anchor",
                metadata={"anchor_descriptor": np.array([descriptor]), "anchor_descriptor_source": "synthetic_scalar"},
            )
        )
    target = 0.62
    delta = np.stack([target * np.ones(20), target * positions], axis=1)
    pairs.append(
        _pair(
            "anchor-test",
            "test",
            delta,
            family="fixture_anchor",
            metadata={"anchor_descriptor": np.array([target]), "anchor_descriptor_source": "synthetic_scalar"},
        )
    )
    return pairs


def rope_fixture(seed: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    raw = rng.normal(size=(10, 8))
    source = np.arange(10)
    target = source + np.array([0, 1, 2, 1, 3, 2, 4, 3, 5, 4])
    metadata = {
        "inv_freq": np.array([1.0, 0.1, 0.01, 0.001]),
        "num_kv_heads": 1,
        "head_dim": 8,
        "rotary_dim": 8,
        "flatten_layout": "T_HD",
        "rotary_layout": "interleaved",
    }
    zero = np.zeros_like(source)
    encoded_source = apply_rope_shift(raw, zero, source, metadata)
    encoded_target = apply_rope_shift(raw, zero, target, metadata)
    return raw, encoded_source, encoded_target, metadata, source, target


def probe_fixture(smooth: bool, seed: int = 4) -> list[StatePair]:
    rng = np.random.default_rng(seed)
    basis, _ = np.linalg.qr(rng.normal(size=(10, 3)))
    positions = np.arange(33)
    train = []
    for index in range(3):
        coefficients = rng.normal(size=(33, 3))
        train.append(_pair(f"probe-train-{smooth}-{index}", "train", stable_matmul(coefficients, basis.T), family=f"fixture_probe_{smooth}"))
    if smooth:
        depth = np.linspace(0.0, 1.0, 33)
        coefficients = np.stack([1 + depth, 2 - depth, -0.5 + 0.25 * depth], axis=1)
    else:
        coefficients = np.stack(
            [(-1.0) ** positions, np.sin(positions * 2.3), (positions % 3 == 0).astype(float)], axis=1
        )
    train.append(_pair(f"probe-test-{smooth}", "test", stable_matmul(coefficients, basis.T), family=f"fixture_probe_{smooth}"))
    return train


def smoke_dataset() -> list[StatePair]:
    """v2 multi-model/condition/state smoke with deliberately colliding pair/layer IDs."""
    rng = np.random.default_rng(10)
    basis, _ = np.linalg.qr(rng.normal(size=(8, 4)))
    pairs = []
    dimensions = [
        ("q8_like", "PREFIX_ONLY", "K"),
        ("q8_like", "PREFIX_ONLY", "V"),
        ("q8_like", "POSITION_ONLY", "K"),
        ("q35_like", "PREFIX_ONLY", "K"),
        ("q35_like", "POSITION_ONLY", "V"),
        ("compressed_like", "PREFIX_ONLY", "C4"),
        ("compressed_like", "POSITION_ONLY", "C4"),
    ]
    depth = np.linspace(0, 1, 24)
    for dimension_index, (family, condition, state_kind) in enumerate(dimensions):
        scale = 1.0 + 0.1 * dimension_index
        for index, descriptor in enumerate((-1.0, -0.3, 0.3, 1.0)):
            coefficients = np.stack(
                [descriptor + depth, 1 - depth, descriptor * depth, np.ones(24) * 0.2], axis=1
            ) * scale
            pairs.append(
                _pair(
                    f"train-{index}", "train", stable_matmul(coefficients, basis.T),
                    family=family, condition=condition, state_kind=state_kind,
                    metadata={"anchor_descriptor": np.array([descriptor]), "anchor_descriptor_source": "synthetic_scalar"},
                )
            )
        descriptor = 0.5
        coefficients = np.stack(
            [descriptor + depth, 1 - depth, descriptor * depth, np.ones(24) * 0.2], axis=1
        ) * scale
        pairs.append(
            _pair(
                "shared-test", "test", stable_matmul(coefficients, basis.T),
                family=family, condition=condition, state_kind=state_kind,
                metadata={"anchor_descriptor": np.array([descriptor]), "anchor_descriptor_source": "synthetic_scalar"},
            )
        )
        pairs.append(
            _pair(
                "shared-zero", "test", np.zeros((24, 8)),
                family=family, condition=condition, state_kind=state_kind,
                metadata={"anchor_descriptor": np.array([0.0]), "anchor_descriptor_source": "synthetic_scalar"},
            )
        )
    return pairs


def all_fixture_pairs() -> Iterable[StatePair]:
    for fixture in (
        zero_residual_fixture,
        constant_residual_fixture,
        rank4_fixture,
        shared_subspace_fixture,
        concentrated_energy_fixture,
        static_joint_fixture,
        detector_fixture,
        anchor_fixture,
    ):
        yield from fixture()
