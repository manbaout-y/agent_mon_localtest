from __future__ import annotations

from .base import CorrectionMethod, CorrectionResult, EvalContext, MethodCost
from kvreuse_b5.data.types import StatePair


class NoCorrection(CorrectionMethod):
    name = "no_correction"

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        return CorrectionResult(pair.a.copy(), MethodCost(total_units=pair.total_units))

