from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from kvreuse_b5.data.types import StatePair

from .common import load_torch_payload, require_keys


DEEPSEEK_C4_LAYERS = tuple(range(2, 43, 2))
STRICT_MAPPING_COLUMNS = ("pair_id", "layer", "row_a", "row_b", "x_rel_anchor_a", "x_rel_anchor_b")


@dataclass(frozen=True)
class StrictMappingRow:
    layer: int
    row_a: int
    row_b: int
    x_rel_anchor: int


def _canonical_pair_id(value: int | str) -> int:
    text = str(value).strip()
    if text.upper().startswith("P"):
        text = text[1:]
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"invalid DeepSeek pair_id {value!r}") from exc


class DeepSeekPrefixC4Adapter:
    def __init__(
        self,
        root: str | Path,
        mapping_file: str | Path,
        *,
        expected_layers: tuple[int, ...] = DEEPSEEK_C4_LAYERS,
        expected_num_raw_parts: int = 41,
        payload_loader: Callable[[Path], dict[str, Any]] = load_torch_payload,
    ):
        self.root = Path(root)
        self.mapping_file = Path(mapping_file)
        self.expected_layers = tuple(expected_layers)
        self.expected_num_raw_parts = int(expected_num_raw_parts)
        self._payload_loader = payload_loader

    def files_for(self, pair_id: int | str) -> tuple[Path, Path]:
        numeric = _canonical_pair_id(pair_id)
        stem = f"P{numeric:04d}"
        return self.root / f"{stem}_PREFIX_A.pt", self.root / f"{stem}_PREFIX_B.pt"

    @staticmethod
    def _integer_tuple_1d(value: Any, *, field: str, path: Path, layer: int) -> tuple[int, ...]:
        ndim = getattr(value, "ndim", None)
        if ndim is not None and int(ndim) != 1:
            raise ValueError(f"{path}: layer {layer} {field} expected one-dimensional input, got ndim={ndim}")
        try:
            return tuple(int(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: layer {layer} {field} must be a one-dimensional integer sequence") from exc

    def _mapping_rows(self, pair_id: int) -> dict[int, list[StrictMappingRow]]:
        if not self.mapping_file.is_file():
            raise FileNotFoundError(self.mapping_file)
        grouped: dict[int, list[StrictMappingRow]] = {layer: [] for layer in self.expected_layers}
        with self.mapping_file.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != STRICT_MAPPING_COLUMNS:
                raise ValueError(
                    f"{self.mapping_file}: expected columns {STRICT_MAPPING_COLUMNS}, got {tuple(reader.fieldnames or ())}"
                )
            for line_number, row in enumerate(reader, start=2):
                if _canonical_pair_id(row["pair_id"]) != pair_id:
                    continue
                layer = int(row["layer"])
                if layer not in grouped:
                    raise ValueError(f"{self.mapping_file}:{line_number}: unsupported C4 layer {layer}")
                anchor_a, anchor_b = int(row["x_rel_anchor_a"]), int(row["x_rel_anchor_b"])
                if anchor_a != anchor_b:
                    raise ValueError(
                        f"{self.mapping_file}:{line_number}: x_rel_anchor_a {anchor_a} != x_rel_anchor_b {anchor_b}"
                    )
                grouped[layer].append(
                    StrictMappingRow(layer, int(row["row_a"]), int(row["row_b"]), anchor_a)
                )
        missing = [layer for layer, rows in grouped.items() if not rows]
        if missing:
            raise ValueError(f"{self.mapping_file}: pair {pair_id} has no strict mapping rows for layers {missing}")
        return grouped

    def _c4_layers(self, payload: dict[str, Any], *, path: Path, expected_tag: str) -> dict[int, dict[str, Any]]:
        require_keys(payload, ("tag", "num_raw_parts", "layers"), where=str(path))
        if str(payload["tag"]) != expected_tag:
            raise ValueError(f"{path}: payload tag {payload['tag']!r} != filename tag {expected_tag!r}")
        if int(payload["num_raw_parts"]) != self.expected_num_raw_parts or len(payload["layers"]) != self.expected_num_raw_parts:
            raise ValueError(
                f"{path}: expected {self.expected_num_raw_parts} raw parts, got num_raw_parts={payload['num_raw_parts']} entries={len(payload['layers'])}"
            )
        layers: dict[int, dict[str, Any]] = {}
        for index, entry in enumerate(payload["layers"]):
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: layer entry {index} is not a dict")
            require_keys(
                entry,
                (
                    "layer", "compress_ratio", "part", "compressed_kv", "slot_mapping",
                    "actual_seq_lengths_query", "start_pos", "hidden_states_shape",
                ),
                where=f"{path}:layer[{index}]",
            )
            if int(entry["compress_ratio"]) != 4:
                continue
            layer = int(entry["layer"])
            if layer not in self.expected_layers:
                raise ValueError(f"{path}: unexpected ratio-4 layer {layer}")
            if int(entry["part"]) != 0:
                raise ValueError(f"{path}: layer {layer} ratio-4 part must be 0")
            if layer in layers:
                raise ValueError(f"{path}: duplicate ratio-4 layer {layer}")
            compressed = entry["compressed_kv"]
            slot_mapping = entry["slot_mapping"]
            if getattr(compressed, "ndim", None) != 2 or int(compressed.shape[1]) != 512:
                raise ValueError(f"{path}: layer {layer} compressed_kv must be [N,512], got {tuple(compressed.shape)}")
            if getattr(slot_mapping, "ndim", None) != 2 or tuple(slot_mapping.shape) != (compressed.shape[0], 2):
                raise ValueError(f"{path}: layer {layer} slot_mapping must be [N,2]")
            query_lengths = self._integer_tuple_1d(
                entry["actual_seq_lengths_query"], field="actual_seq_lengths_query", path=path, layer=layer
            )
            start_pos = self._integer_tuple_1d(entry["start_pos"], field="start_pos", path=path, layer=layer)
            if len(query_lengths) != 2:
                raise ValueError(
                    f"{path}: layer {layer} actual_seq_lengths_query expected length 2, got length {len(query_lengths)} value {query_lengths}"
                )
            if query_lengths[-1] <= 0:
                raise ValueError(
                    f"{path}: layer {layer} actual_seq_lengths_query final value must be positive, got {query_lengths[-1]}"
                )
            if len(start_pos) != 1:
                raise ValueError(
                    f"{path}: layer {layer} start_pos expected length 1, got length {len(start_pos)} value {start_pos}"
                )
            if start_pos[0] != 0:
                raise ValueError(f"{path}: layer {layer} start_pos expected value 0, got {start_pos[0]}")
            hidden_shape = entry["hidden_states_shape"]
            if not isinstance(hidden_shape, tuple):
                raise ValueError(f"{path}: layer {layer} hidden_states_shape must be a tuple")
            if len(hidden_shape) < 1 or int(hidden_shape[0]) <= 0:
                raise ValueError(f"{path}: layer {layer} hidden_states_shape[0] must be positive, got {hidden_shape}")
            if query_lengths[-1] != int(hidden_shape[0]):
                raise ValueError(
                    f"{path}: layer {layer} actual_seq_lengths_query[-1] {query_lengths[-1]} != hidden_states_shape[0] {int(hidden_shape[0])}"
                )
            layers[layer] = entry
        if tuple(sorted(layers)) != tuple(sorted(self.expected_layers)):
            raise ValueError(f"{path}: C4 layers {tuple(sorted(layers))} != expected {self.expected_layers}")
        return layers

    def load_pair(self, pair_id: int | str, *, split: str = "test") -> list[StatePair]:
        numeric = _canonical_pair_id(pair_id)
        path_a, path_b = self.files_for(numeric)
        tag_prefix = f"P{numeric:04d}_PREFIX"
        payload_a = self._payload_loader(path_a)
        payload_b = self._payload_loader(path_b)
        layers_a = self._c4_layers(payload_a, path=path_a, expected_tag=f"{tag_prefix}_A")
        layers_b = self._c4_layers(payload_b, path=path_b, expected_tag=f"{tag_prefix}_B")
        mapping = self._mapping_rows(numeric)

        results: list[StatePair] = []
        for layer in self.expected_layers:
            rows = mapping[layer]
            row_a = np.asarray([row.row_a for row in rows], dtype=np.int64)
            row_b = np.asarray([row.row_b for row in rows], dtype=np.int64)
            positions = np.asarray([row.x_rel_anchor for row in rows], dtype=np.int64)
            compressed_a = layers_a[layer]["compressed_kv"]
            compressed_b = layers_b[layer]["compressed_kv"]
            if np.any(row_a < 0) or np.any(row_a >= int(compressed_a.shape[0])):
                raise ValueError(f"{self.mapping_file}: pair {numeric} layer {layer} row_a out of bounds")
            if np.any(row_b < 0) or np.any(row_b >= int(compressed_b.shape[0])):
                raise ValueError(f"{self.mapping_file}: pair {numeric} layer {layer} row_b out of bounds")
            selected_a = compressed_a[row_a.tolist()]
            selected_b = compressed_b[row_b.tolist()]
            if hasattr(selected_a, "detach"):
                selected_a = selected_a.detach().cpu().float().numpy()
                selected_b = selected_b.detach().cpu().float().numpy()
            selected_a = np.asarray(selected_a, dtype=np.float32)
            selected_b = np.asarray(selected_b, dtype=np.float32)
            metadata = {
                "source_file_a": str(path_a),
                "source_file_b": str(path_b),
                "mapping_file": str(self.mapping_file),
                "mapping_row_count": len(rows),
                "row_a": row_a.tolist(),
                "row_b": row_b.tolist(),
                "x_rel_anchor": positions.tolist(),
                "compress_ratio": 4,
                "positions_semantics": "X_REL_ANCHOR_STRICT_MAPPING",
                "feature_dim": 512,
                "packed_schema_a": {
                    "actual_seq_lengths_query": self._integer_tuple_1d(
                        layers_a[layer]["actual_seq_lengths_query"],
                        field="actual_seq_lengths_query", path=path_a, layer=layer,
                    ),
                    "start_pos": self._integer_tuple_1d(
                        layers_a[layer]["start_pos"], field="start_pos", path=path_a, layer=layer,
                    ),
                    "hidden_states_shape": tuple(int(value) for value in layers_a[layer]["hidden_states_shape"]),
                    "slot_mapping_shape": tuple(int(value) for value in layers_a[layer]["slot_mapping"].shape),
                },
                "packed_schema_b": {
                    "actual_seq_lengths_query": self._integer_tuple_1d(
                        layers_b[layer]["actual_seq_lengths_query"],
                        field="actual_seq_lengths_query", path=path_b, layer=layer,
                    ),
                    "start_pos": self._integer_tuple_1d(
                        layers_b[layer]["start_pos"], field="start_pos", path=path_b, layer=layer,
                    ),
                    "hidden_states_shape": tuple(int(value) for value in layers_b[layer]["hidden_states_shape"]),
                    "slot_mapping_shape": tuple(int(value) for value in layers_b[layer]["slot_mapping"].shape),
                },
            }
            results.append(
                StatePair(
                    pair_id=numeric,
                    split=split,
                    model_family="DeepSeek-V4",
                    condition="PREFIX_ONLY",
                    state_kind="C4",
                    layer_id=layer,
                    a=selected_a,
                    b=selected_b,
                    positions=positions,
                    metadata=metadata,
                )
            )
        return results
