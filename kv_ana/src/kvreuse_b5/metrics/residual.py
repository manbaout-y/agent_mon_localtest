from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ResidualMetrics:
    baseline_sse: float
    corrected_sse: float
    energy_explained: float
    status: str


def compute_residual_metrics(
    a: np.ndarray,
    b: np.ndarray,
    corrected: np.ndarray,
    corrected_sse_override: float | None = None,
    corrected_sse_multiplier: float | None = None,
    zero_tolerance: float = 1e-12,
    degradation_tolerance: float = 1e-12,
) -> ResidualMetrics:
    baseline_sse = float(np.sum((b.astype(float) - a.astype(float)) ** 2))
    if corrected_sse_override is not None and corrected_sse_multiplier is not None:
        raise ValueError("only one corrected SSE override may be provided")
    if corrected_sse_override is not None:
        corrected_sse = float(corrected_sse_override)
    elif corrected_sse_multiplier is not None:
        corrected_sse = baseline_sse * float(corrected_sse_multiplier)
    else:
        corrected_sse = float(np.sum((b.astype(float) - corrected.astype(float)) ** 2))
    if not np.isfinite(baseline_sse) or not np.isfinite(corrected_sse):
        raise ValueError("metric SSE contains NaN or Inf")
    tolerance = 1e-10 * max(1.0, baseline_sse)
    if corrected_sse < 0 and abs(corrected_sse) <= tolerance:
        corrected_sse = 0.0
    if baseline_sse <= zero_tolerance:
        status = "ZERO_RESIDUAL" if corrected_sse <= degradation_tolerance else "ZERO_BASELINE_DEGRADED"
        return ResidualMetrics(baseline_sse, corrected_sse, float("nan"), status)
    return ResidualMetrics(baseline_sse, corrected_sse, 1.0 - corrected_sse / baseline_sse, "OK")
