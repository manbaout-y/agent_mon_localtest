from __future__ import annotations

from itertools import product
from typing import Any, Callable, Iterable, Mapping

from .anchor import AnchorResidualCorrection
from kvreuse_b5.execution.artifacts import ArtifactStore
from .base import CorrectionMethod
from .baseline import NoCorrection
from .composite import CompositeCorrection
from .detector import DetectorLayerCorrection
from .lowrank import FrozenSubspace, FrozenSubspaceOracleCoeff, PairLowRankOracle
from .placeholders import InterfaceOnlyMethod, PLACEHOLDER_CAPABILITIES
from .position import RoPEPositionCorrection
from .probe import ProbeFrozenBasis, ProbeMean
from .selective import (
    BoundaryFirst,
    JointUnitOracle,
    FixedEdgesPositionBinner,
    NormalizedDepthBinner,
    RandomExpected,
    StaticLayerPositionSelector,
    StaticLayerSelector,
    StaticPositionSelector,
    UniformAbsoluteBinner,
)
from .shared import SharedResidualOracle


Builder = Callable[[Mapping[str, Any]], list[CorrectionMethod]]


def _values(config: Mapping[str, Any], plural: str, singular: str, default: Iterable[Any]) -> list[Any]:
    value = config.get(plural, config.get(singular, list(default)))
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _binner(config: Mapping[str, Any]):
    spec = config.get("position_binner")
    if spec is None:
        mode = config.get("position_mode", "normalized_depth_bins")
        if mode == "normalized_depth_bins":
            return NormalizedDepthBinner(int(config.get("bin_count", 16)))
        if mode == "absolute_relative_position":
            return UniformAbsoluteBinner(float(config.get("width", 1)))
        raise ValueError(f"unknown position_mode {mode!r}")
    kind = spec.get("type")
    if kind == "fixed_edges":
        edges = [float("inf") if value == "inf" else float(value) for value in spec["edges"]]
        return FixedEdgesPositionBinner(edges)
    if kind == "normalized_depth":
        return NormalizedDepthBinner(int(spec.get("bin_count", 16)))
    if kind == "uniform_absolute":
        return UniformAbsoluteBinner(float(spec["width"]))
    raise ValueError(f"unknown position_binner type {kind!r}")


class MethodRegistry:
    def __init__(self):
        self._builders: dict[str, Builder] = {}

    def register(self, name: str, builder: Builder) -> None:
        if name in self._builders:
            raise KeyError(f"method already registered: {name}")
        self._builders[name] = builder

    def create(self, config: Mapping[str, Any]) -> list[CorrectionMethod]:
        name = str(config["name"])
        if name not in self._builders:
            raise KeyError(f"unknown method {name!r}; available={sorted(self._builders)}")
        return self._builders[name](config)

    def build_all(self, configs: Iterable[Mapping[str, Any]]) -> list[CorrectionMethod]:
        return [method for config in configs for method in self.create(config)]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))


def default_registry(artifact_store: ArtifactStore | None = None) -> MethodRegistry:
    artifact_store = artifact_store or ArtifactStore()
    registry = MethodRegistry()
    registry.register("no_correction", lambda c: [NoCorrection()])
    registry.register("shared_residual_oracle", lambda c: [SharedResidualOracle()])
    registry.register(
        "pair_lowrank_oracle",
        lambda c: [PairLowRankOracle(r, artifact_store) for r in _values(c, "ranks", "rank", [1, 2, 4, 8, 16])],
    )
    registry.register(
        "frozen_lowrank_oracle",
        lambda c: [FrozenSubspaceOracleCoeff(r, artifact_store=artifact_store) for r in _values(c, "ranks", "rank", [1, 2, 4, 8, 16])],
    )
    registry.register(
        "probe_mean", lambda c: [ProbeMean(p) for p in _values(c, "probes", "probe_count", [1, 2, 4, 8])]
    )
    registry.register(
        "probe_frozen_basis",
        lambda c: [
            ProbeFrozenBasis(r, p, subspace=FrozenSubspace(r, artifact_store))
            for r, p in product(
                _values(c, "ranks", "rank", [4]), _values(c, "probes", "probe_count", [1, 2, 4, 8])
            )
        ],
    )
    registry.register(
        "anchor_residual",
        lambda c: [
            AnchorResidualCorrection(k, str(c.get("weighting", "uniform")))
            for k in _values(c, "k", "anchor_count", [1, 2, 4, 8])
        ],
    )
    registry.register(
        "random_expected",
        lambda c: [
            RandomExpected(b, int(c.get("config_seed", 0)))
            for b in _values(c, "budgets", "budget", [0.01, 0.05, 0.1, 0.2])
        ],
    )
    for name, cls in {
        "boundary_first": BoundaryFirst,
        "joint_unit_oracle": JointUnitOracle,
    }.items():
        registry.register(
            name,
            lambda c, cls=cls: [
                cls(b, g, int(c.get("config_seed", 0)))
                for b, g in product(
                    _values(c, "budgets", "budget", [0.01, 0.05, 0.1, 0.2]),
                    _values(c, "granularities", "granularity", ["neutral_partial"]),
                )
            ],
        )
    # Static layer has explicit atomic/neutral variants too.
    registry.register(
        "static_layer",
        lambda c: [
            StaticLayerSelector(b, g, int(c.get("config_seed", 0)))
            for b, g in product(
                _values(c, "budgets", "budget", [0.01, 0.05, 0.1, 0.2]),
                _values(c, "granularities", "granularity", ["neutral_partial"]),
            )
        ],
    )
    for name, cls in {"static_position": StaticPositionSelector, "static_joint": StaticLayerPositionSelector}.items():
        registry.register(
            name,
            lambda c, cls=cls: [
                cls(b, _binner(c), g, int(c.get("config_seed", 0)))
                for b, g in product(
                    _values(c, "budgets", "budget", [0.01, 0.05, 0.1, 0.2]),
                    _values(c, "granularities", "granularity", ["neutral_partial"]),
                )
            ],
        )
    registry.register("rope_position", lambda c: [RoPEPositionCorrection()])
    registry.register(
        "detector_layer_selector",
        lambda c: [
            DetectorLayerCorrection(
                int(count),
                float(budget),
                str(c.get("selection", "train_selected")),
                c.get("fixed_ids"),
                str(c.get("mode", "exact_recompute")),
                None,
                int(c.get("config_seed", 0)),
            )
            for count, budget in product(
                _values(c, "detector_counts", "detector_count", [1, 2, 4]),
                _values(c, "budgets", "budget", [0.01, 0.05, 0.1, 0.2]),
            )
        ],
    )
    for name, caps in PLACEHOLDER_CAPABILITIES.items():
        registry.register(name, lambda c, name=name, caps=caps: [InterfaceOnlyMethod(name, caps)])
    def composite_builder(config):
        stages = config.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ValueError("composite config requires a non-empty stages list")
        choices = [registry.create(stage) for stage in stages]
        return [
            CompositeCorrection(combo, str(config.get("variant", "_then_".join(m.name for m in combo))))
            for combo in product(*choices)
        ]
    registry.register("composite", composite_builder)
    return registry
