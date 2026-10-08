from __future__ import annotations

from typing import Sequence
from hashlib import sha256

from kvreuse_b5.data.types import StatePair
from .base import (
    CorrectionMethod,
    CorrectionResult,
    EvalContext,
    FitContext,
    MethodCapabilities,
    MethodCategory,
    MethodCost,
)


def _combined_capabilities(methods: Sequence[CorrectionMethod]) -> MethodCapabilities:
    fields = MethodCapabilities.__dataclass_fields__
    return MethodCapabilities(**{name: any(getattr(m.capabilities, name) for m in methods) for name in fields})


class CompositeCorrection(CorrectionMethod):
    name = "composite"

    def __init__(self, methods: Sequence[CorrectionMethod], variant: str | None = None):
        if not methods:
            raise ValueError("composite requires at least one method")
        metric_only = [method.name for method in methods if not method.materializes_state]
        if metric_only:
            raise ValueError(
                "CompositeCorrection accepts only MaterializedCorrectionStage; "
                f"metric-only stage(s) rejected: {metric_only}"
            )
        invalid_frozen_stages = [
            (index, method.name)
            for index, method in enumerate(methods)
            if index > 0 and getattr(method, "uses_frozen_subspace", False)
        ]
        if invalid_frozen_stages:
            raise ValueError(
                "FrozenSubspace-based stage cannot appear after a residual-changing stage because its "
                "artifact was fit on the original training residual. Stage-scoped artifacts are not "
                f"implemented in v2.0.1. Invalid stages: {invalid_frozen_stages}"
            )
        self.methods = list(methods)
        self.variant = variant or "_then_".join(method.name for method in methods)
        self.capabilities = _combined_capabilities(methods)
        self.category = (
            MethodCategory.REFERENCE
            if self.capabilities.oracle or self.capabilities.uses_full_target
            else MethodCategory.OFFLINE_TRAINABLE
            if self.capabilities.needs_fit
            else MethodCategory.DEPLOYMENT_ESTIMATOR
        )
        super().__init__()

    def fit(self, pairs: Sequence[StatePair], context: FitContext) -> None:
        current = list(pairs)
        for method in self.methods:
            if method.capabilities.needs_fit:
                method.fit(current, context)
            transformed = []
            for pair in current:
                result = method.apply(pair, EvalContext(split="train"))
                transformed.append(pair.with_a(result.corrected))
            current = transformed
        self.freeze()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        current = pair
        total_cost = MethodCost(total_units=pair.total_units)
        diagnostics = {"stages": []}
        override = None
        multiplier = None
        artifact_hashes = []
        for method in self.methods:
            result = method.apply(current, context)
            total_cost = total_cost.add(result.cost)
            diagnostics["stages"].append(
                {"method": method.name, "variant": method.variant, "diagnostics": result.diagnostics}
            )
            current = current.with_a(result.corrected)
            override = result.corrected_sse_override
            multiplier = result.corrected_sse_multiplier
            if result.artifact_hash:
                artifact_hashes.append(result.artifact_hash)
        artifact_hash = sha256("|".join(artifact_hashes).encode()).hexdigest() if artifact_hashes else None
        return CorrectionResult(current.a, total_cost, diagnostics, override, multiplier, artifact_hash)

    def output_parameters(self):
        return {"stages": [dict(name=m.name, **m.output_parameters()) for m in self.methods]}
