from __future__ import annotations

from typing import Iterable


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> tuple[list[tuple[int, int]], int]:
    cleaned = sorted((start, end) for start, end in intervals if end > start)
    if not cleaned:
        return [], 0

    merged: list[tuple[int, int]] = []
    cur_start, cur_end = cleaned[0]
    for start, end in cleaned[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    total = sum(end - start for start, end in merged)
    return merged, total
