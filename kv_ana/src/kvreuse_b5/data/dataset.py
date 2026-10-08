from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .types import StatePair


@dataclass
class StatePairDataset:
    pairs: Sequence[StatePair]

    def __post_init__(self) -> None:
        seen: set[tuple[object, ...]] = set()
        sample_layers: dict[tuple[object, ...], dict[str, list[int]]] = {}
        base_pair_members: dict[tuple[object, ...], dict[str, list[tuple[str, str, int]]]] = {}
        for pair in self.pairs:
            if pair.state_identity in seen:
                raise ValueError(f"duplicate state_identity {pair.state_identity}")
            seen.add(pair.state_identity)
            sample_layers.setdefault(pair.sample_identity, {}).setdefault(pair.split, []).append(pair.layer_id)
            base_identity = (pair.model_family, pair.pair_id)
            base_pair_members.setdefault(base_identity, {}).setdefault(pair.split, []).append(
                (pair.condition, pair.state_kind, pair.layer_id)
            )
        for sample_identity, by_split in sample_layers.items():
            if len(by_split) > 1:
                raise ValueError(
                    f"split leakage for sample_identity={sample_identity}: "
                    f"train layer ids={sorted(by_split.get('train', []))}, "
                    f"test layer ids={sorted(by_split.get('test', []))}"
                )
        for base_identity, by_split in base_pair_members.items():
            if len(by_split) > 1:
                raise ValueError(
                    f"split leakage for base_pair_identity={base_identity}: "
                    f"train members={by_split.get('train', [])}, test members={by_split.get('test', [])}"
                )

    def __iter__(self) -> Iterator[StatePair]:
        return iter(self.pairs)

    def split(self) -> tuple[list[StatePair], list[StatePair]]:
        train = [p for p in self.pairs if p.split == "train"]
        test = [p for p in self.pairs if p.split == "test"]
        return train, test

    @classmethod
    def from_iterable(cls, pairs: Iterable[StatePair]) -> "StatePairDataset":
        return cls(list(pairs))
