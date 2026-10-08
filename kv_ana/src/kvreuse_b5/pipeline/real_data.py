from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from kvreuse_b5.adapters import DeepSeekPrefixC4Adapter, QwenRawKVAdapter
from kvreuse_b5.adapters.deepseek_c4 import STRICT_MAPPING_COLUMNS, StrictMappingRow
from kvreuse_b5.data.types import StatePair


@dataclass(frozen=True, order=True)
class RealTrack:
    model_family: str
    condition: str
    state_kind: str

    @property
    def slug(self) -> str:
        return f"{self.model_family}__{self.condition}__{self.state_kind}".replace("/", "_")


HISTORICAL_TRACKS = (
    RealTrack("DeepSeek-V4", "PREFIX_ONLY", "C4"),
    RealTrack("Qwen3-8B", "PREFIX_ONLY", "K"),
    RealTrack("Qwen3-8B", "PREFIX_ONLY", "V"),
    RealTrack("Qwen3-8B", "POSITION_ONLY", "K"),
    RealTrack("Qwen3-8B", "POSITION_ONLY", "V"),
    RealTrack("Qwen3.5-27B", "PREFIX_ONLY", "K"),
    RealTrack("Qwen3.5-27B", "PREFIX_ONLY", "V"),
    RealTrack("Qwen3.5-27B", "POSITION_ONLY", "K"),
    RealTrack("Qwen3.5-27B", "POSITION_ONLY", "V"),
)


class _IndexedDeepSeekPrefixC4Adapter(DeepSeekPrefixC4Adapter):
    """Pipeline-local mapping index; extraction semantics remain adapter-identical."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._strict_index: dict[int, dict[int, list[StrictMappingRow]]] | None = None

    @staticmethod
    def _pair_number(value: str) -> int:
        text = value.strip()
        return int(text[1:] if text.upper().startswith("P") else text)

    def _build_index(self) -> dict[int, dict[int, list[StrictMappingRow]]]:
        if not self.mapping_file.is_file():
            raise FileNotFoundError(self.mapping_file)
        index: dict[int, dict[int, list[StrictMappingRow]]] = {}
        with self.mapping_file.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != STRICT_MAPPING_COLUMNS:
                raise ValueError(f"{self.mapping_file}: expected columns {STRICT_MAPPING_COLUMNS}, got {tuple(reader.fieldnames or ())}")
            for line_number, row in enumerate(reader, start=2):
                pair_id = self._pair_number(row["pair_id"])
                layer = int(row["layer"])
                if layer not in self.expected_layers:
                    raise ValueError(f"{self.mapping_file}:{line_number}: unsupported C4 layer {layer}")
                anchor_a, anchor_b = int(row["x_rel_anchor_a"]), int(row["x_rel_anchor_b"])
                if anchor_a != anchor_b:
                    raise ValueError(f"{self.mapping_file}:{line_number}: x_rel_anchor_a {anchor_a} != x_rel_anchor_b {anchor_b}")
                grouped = index.setdefault(pair_id, {value: [] for value in self.expected_layers})
                grouped[layer].append(StrictMappingRow(layer, int(row["row_a"]), int(row["row_b"]), anchor_a))
        return index

    def _mapping_rows(self, pair_id: int) -> dict[int, list[StrictMappingRow]]:
        if self._strict_index is None:
            self._strict_index = self._build_index()
        grouped = self._strict_index.get(int(pair_id))
        if grouped is None:
            raise ValueError(f"{self.mapping_file}: pair {pair_id} has no strict mapping rows")
        missing = [layer for layer, rows in grouped.items() if not rows]
        if missing:
            raise ValueError(f"{self.mapping_file}: pair {pair_id} has no strict mapping rows for layers {missing}")
        return grouped


class RealStateSource:
    def __init__(self, q8_root: str | Path, q35_root: str | Path, deepseek_root: str | Path, deepseek_mapping: str | Path):
        self.q8 = QwenRawKVAdapter(q8_root, "Qwen3-8B")
        self.q35 = QwenRawKVAdapter(q35_root, "Qwen3.5-27B")
        self.deepseek = _IndexedDeepSeekPrefixC4Adapter(deepseek_root, deepseek_mapping)

    def load(self, track: RealTrack, pair_id: int, *, split: str = "test") -> list[StatePair]:
        if track.model_family == "DeepSeek-V4":
            pairs = self.deepseek.load_pair(pair_id, split=split)
        elif track.model_family == "Qwen3-8B":
            pairs = self.q8.load_pair(pair_id, track.condition, split=split)
        elif track.model_family == "Qwen3.5-27B":
            pairs = self.q35.load_pair(pair_id, track.condition, split=split)
        else:
            raise ValueError(f"unknown real track model {track.model_family}")
        selected = [pair for pair in pairs if pair.state_kind == track.state_kind]
        if not selected:
            raise ValueError(f"no states for track {track}")
        return sorted(selected, key=lambda pair: pair.layer_id)

    def load_qwen_kv(self, model_family: str, condition: str, pair_id: int, *, split: str = "test") -> dict[str, list[StatePair]]:
        adapter = self.q8 if model_family == "Qwen3-8B" else self.q35
        pairs = adapter.load_pair(pair_id, condition, split=split)
        return {
            kind: sorted((pair for pair in pairs if pair.state_kind == kind), key=lambda pair: pair.layer_id)
            for kind in ("K", "V")
        }

    def load_all_historical(self, pair_id: int, *, split: str = "test") -> dict[RealTrack, list[StatePair]]:
        """Load each physical Qwen payload once while exposing all nine tracks."""
        output: dict[RealTrack, list[StatePair]] = {}
        deepseek = self.deepseek.load_pair(pair_id, split=split)
        output[RealTrack("DeepSeek-V4", "PREFIX_ONLY", "C4")] = sorted(deepseek, key=lambda pair: pair.layer_id)
        for model in ("Qwen3-8B", "Qwen3.5-27B"):
            for condition in ("PREFIX_ONLY", "POSITION_ONLY"):
                grouped = self.load_qwen_kv(model, condition, pair_id, split=split)
                for kind in ("K", "V"):
                    output[RealTrack(model, condition, kind)] = grouped[kind]
        return output
