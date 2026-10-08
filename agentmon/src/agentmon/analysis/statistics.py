from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Iterable

from agentmon.analysis.io import float_or_none


def percentile(values: Iterable[Any], q: float) -> float | None:
    nums = sorted(value for value in (float_or_none(item) for item in values) if value is not None and math.isfinite(value))
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    rank = (len(nums) - 1) * q
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return nums[lower]
    weight = rank - lower
    return nums[lower] * (1.0 - weight) + nums[upper] * weight


def mean(values: Iterable[Any]) -> float | None:
    nums = [value for value in (float_or_none(item) for item in values) if value is not None and math.isfinite(value)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def median(values: Iterable[Any]) -> float | None:
    return percentile(values, 0.5)


def iqr(values: Iterable[Any]) -> float | None:
    p25 = percentile(values, 0.25)
    p75 = percentile(values, 0.75)
    if p25 is None or p75 is None:
        return None
    return p75 - p25


def safe_div(num: Any, den: Any) -> float | None:
    numerator = float_or_none(num)
    denominator = float_or_none(den)
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def relative_error(observed: Any, expected: Any) -> float | None:
    obs = float_or_none(observed)
    exp = float_or_none(expected)
    if obs is None or exp is None:
        return None
    denom = max(abs(obs), abs(exp), 1e-12)
    return abs(obs - exp) / denom


def interval_union(intervals: Iterable[tuple[float, float]]) -> tuple[list[tuple[float, float]], float]:
    cleaned = sorted((float(start), float(end)) for start, end in intervals if end is not None and start is not None and end > start)
    if not cleaned:
        return [], 0.0
    merged: list[tuple[float, float]] = []
    cur_start, cur_end = cleaned[0]
    for start, end in cleaned[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return merged, sum(end - start for start, end in merged)


def overlap_duration(a: Iterable[tuple[float, float]], b: Iterable[tuple[float, float]]) -> float:
    left, _ = interval_union(a)
    right, _ = interval_union(b)
    total = 0.0
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            total += end - start
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return total


def time_weighted_summary(intervals: Iterable[tuple[float, float, float]]) -> dict[str, float | None]:
    rows = [(start, end, value) for start, end, value in intervals if end > start]
    if not rows:
        return {"mean": None, "median": None, "p90": None, "p95": None, "max": None, "integral": None}
    integral = sum((end - start) * value for start, end, value in rows)
    duration = sum(end - start for start, end, _ in rows)
    weighted_values = sorted((value, end - start) for start, end, value in rows)

    def weighted_percentile(q: float) -> float:
        elapsed = 0.0
        for value, weight in weighted_values:
            elapsed += weight
            if elapsed >= q * duration:
                return value
        return weighted_values[-1][0]

    return {
        "mean": integral / duration if duration > 0 else None,
        "median": weighted_percentile(0.50),
        "p90": weighted_percentile(0.90),
        "p95": weighted_percentile(0.95),
        "max": weighted_values[-1][0],
        "integral": integral,
    }


def concurrency_series(intervals: Iterable[tuple[float, float]]) -> list[dict[str, float]]:
    points: list[tuple[float, int]] = []
    for start, end in intervals:
        if end <= start:
            continue
        points.append((start, 1))
        points.append((end, -1))
    if not points:
        return []
    points.sort(key=lambda item: (item[0], -item[1]))
    rows: list[dict[str, float]] = []
    active = 0
    last_t = points[0][0]
    for t, delta in points:
        if t > last_t:
            rows.append({"start_s": last_t, "end_s": t, "value": float(active)})
        active += delta
        last_t = t
    return rows


def wilson_interval(numerator: int, denominator: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    if denominator <= 0:
        return None, None
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    phat = numerator / denominator
    denom = 1 + z * z / denominator
    center = (phat + z * z / (2 * denominator)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * denominator)) / denominator) / denom
    return max(0.0, center - margin), min(1.0, center + margin)
