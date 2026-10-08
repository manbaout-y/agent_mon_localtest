from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManifestSample:
    sample_order: int
    pair_id: int
    split: str


def load_lifemem_manifest(path: str | Path) -> list[ManifestSample]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = [
            ManifestSample(int(row["sample_order"]), int(row["pair_id"]), row["split"])
            for row in csv.DictReader(handle)
        ]
    if len(rows) != 48 or [row.sample_order for row in rows] != list(range(48)):
        raise ValueError("LifeMem manifest must contain sample_order 0..47 exactly once")
    if len({row.pair_id for row in rows}) != 48:
        raise ValueError("LifeMem manifest pair IDs are not unique")
    for row in rows:
        expected = "test" if row.sample_order % 3 == 2 else "train"
        if row.split != expected:
            raise ValueError(f"pair {row.pair_id}: split {row.split!r} != canonical {expected!r}")
    if sum(row.split == "train" for row in rows) != 32 or sum(row.split == "test" for row in rows) != 16:
        raise ValueError("LifeMem manifest split must be 32 train / 16 test")
    return rows
