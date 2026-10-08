from __future__ import annotations

from .base import CorrectionMethod, CorrectionResult, EvalContext, MethodCapabilities, MethodCategory, MethodCost
from kvreuse_b5.data.types import StatePair


class SharedResidualOracle(CorrectionMethod):
    name = "shared_residual_oracle"
    category = MethodCategory.REFERENCE
    capabilities = MethodCapabilities(oracle=True, uses_full_target=True)

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        mean = pair.residual.mean(axis=0, keepdims=True)
        return CorrectionResult(pair.a + mean, MethodCost(total_units=pair.total_units))
