from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from kvreuse_b5.data.types import StatePair

from .common import load_torch_payload, require_keys


QWEN35_ATTENTION_LAYERS = (3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47, 51, 55, 59, 63)
QWEN_CONDITIONS = ("PREFIX_ONLY", "POSITION_ONLY", "REAL")
_QWEN_FILENAME = re.compile(r"^pair_(\d{6})_(PREFIX_ONLY|POSITION_ONLY|REAL)\.pt$")


@dataclass(frozen=True)
class QwenRawKVSchema:
    model_family: str
    num_kv_heads: int
    head_dim: int
    layer_id_field: str
    expected_layer_ids: tuple[int, ...]

    @property
    def feature_dim(self) -> int:
        return self.num_kv_heads * self.head_dim


QWEN_SCHEMAS = {
    "Qwen3-8B": QwenRawKVSchema("Qwen3-8B", 8, 128, "slot_index", tuple(range(36))),
    "Qwen3.5-27B": QwenRawKVSchema("Qwen3.5-27B", 4, 256, "layer", QWEN35_ATTENTION_LAYERS),
}


def flatten_qwen_state(raw: Any, *, num_kv_heads: int, head_dim: int, where: str = "tensor") -> np.ndarray:
    """Convert [1,H,T,D] to float32 [T,H*D] without crossing head boundaries."""
    expected_prefix = (1, num_kv_heads)
    if getattr(raw, "ndim", None) != 4 or tuple(raw.shape[:2]) != expected_prefix or raw.shape[3] != head_dim:
        raise ValueError(
            f"{where}: expected [1,{num_kv_heads},T,{head_dim}], got {tuple(raw.shape)}"
        )
    if hasattr(raw, "detach"):
        value = raw.detach().cpu()[0].permute(1, 0, 2).contiguous().reshape(
            raw.shape[2], num_kv_heads * head_dim
        )
        if hasattr(value, "float"):
            value = value.float()
        return np.asarray(value.numpy(), dtype=np.float32)
    value = np.asarray(raw)
    return value[0].transpose(1, 0, 2).reshape(value.shape[2], num_kv_heads * head_dim).astype(
        np.float32, copy=False
    )


class QwenRawKVAdapter:
    def __init__(
        self,
        root: str | Path,
        model_family: str,
        *,
        payload_loader: Callable[[Path], dict[str, Any]] = load_torch_payload,
    ):
        if model_family not in QWEN_SCHEMAS:
            raise ValueError(f"unsupported Qwen model_family {model_family!r}; expected one of {sorted(QWEN_SCHEMAS)}")
        self.root = Path(root)
        self.schema = QWEN_SCHEMAS[model_family]
        self._payload_loader = payload_loader

    def file_for(self, pair_id: int, condition: str) -> Path:
        if condition not in QWEN_CONDITIONS:
            raise ValueError(f"unsupported Qwen condition {condition!r}")
        return self.root / f"pair_{int(pair_id):06d}_{condition}.pt"

    def load_pair(self, pair_id: int, condition: str, *, split: str = "test") -> list[StatePair]:
        path = self.file_for(pair_id, condition)
        match = _QWEN_FILENAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"unexpected Qwen filename {path.name!r}")
        filename_pair, filename_condition = int(match.group(1)), match.group(2)
        payload = self._payload_loader(path)
        require_keys(
            payload,
            (
                "format_version", "pair_index", "condition", "token_start_a", "token_end_a",
                "token_start_b", "token_end_b", "x_tokens_a", "x_tokens_b",
                "position_offset_a", "position_offset_b", "layer_slots", "layers",
            ),
            where=str(path),
        )
        payload_pair = int(payload["pair_index"])
        payload_condition = str(payload["condition"])
        if (filename_pair, filename_condition) != (payload_pair, payload_condition):
            raise ValueError(
                f"{path}: filename identity {(filename_pair, filename_condition)} != payload identity {(payload_pair, payload_condition)}"
            )
        if payload_pair != int(pair_id) or payload_condition != condition:
            raise ValueError(f"{path}: requested pair/condition does not match payload")
        if int(payload["format_version"]) != 1:
            raise ValueError(f"{path}: unsupported format_version {payload['format_version']!r}")
        payload_model = payload.get("model_family")
        if payload_model is not None and payload_model != self.schema.model_family:
            raise ValueError(f"{path}: payload model_family {payload_model!r} != {self.schema.model_family!r}")

        layers = payload["layers"]
        if not isinstance(layers, Sequence) or isinstance(layers, (str, bytes)):
            raise ValueError(f"{path}: layers must be a sequence")
        expected_count = len(self.schema.expected_layer_ids)
        if int(payload["layer_slots"]) != expected_count or len(layers) != expected_count:
            raise ValueError(
                f"{path}: expected {expected_count} layer slots, got layer_slots={payload['layer_slots']} entries={len(layers)}"
            )
        if self.schema.model_family == "Qwen3.5-27B":
            measured = tuple(int(value) for value in payload.get("measured_attention_layers", ()))
            if measured != self.schema.expected_layer_ids:
                raise ValueError(f"{path}: measured_attention_layers mismatch: {measured}")

        results: list[StatePair] = []
        observed_layer_ids: list[int] = []
        for entry_index, layer_entry in enumerate(layers):
            if not isinstance(layer_entry, dict):
                raise ValueError(f"{path}: layer entry {entry_index} is not a dict")
            required = (self.schema.layer_id_field, "k_a", "v_a", "k_b", "v_b")
            if self.schema.model_family == "Qwen3.5-27B":
                required = required + ("full_attention_index",)
            require_keys(layer_entry, required, where=f"{path}:layer[{entry_index}]")
            layer_id = int(layer_entry[self.schema.layer_id_field])
            observed_layer_ids.append(layer_id)
            if self.schema.model_family == "Qwen3-8B" and layer_id != entry_index:
                raise ValueError(f"{path}: slot_index {layer_id} != entry index {entry_index}")
            if self.schema.model_family == "Qwen3.5-27B" and int(layer_entry["full_attention_index"]) != entry_index:
                raise ValueError(f"{path}: full_attention_index mismatch at entry {entry_index}")

            flat: dict[str, np.ndarray] = {}
            raw_shapes: dict[str, tuple[int, ...]] = {}
            raw_dtypes: dict[str, str] = {}
            for name in ("k_a", "v_a", "k_b", "v_b"):
                raw = layer_entry[name]
                raw_shapes[name] = tuple(int(value) for value in raw.shape)
                raw_dtypes[name] = str(raw.dtype)
                flat[name] = flatten_qwen_state(
                    raw,
                    num_kv_heads=self.schema.num_kv_heads,
                    head_dim=self.schema.head_dim,
                    where=f"{path}:layer[{entry_index}].{name}",
                )
            if flat["k_a"].shape != flat["k_b"].shape:
                raise ValueError(f"{path}: layer {layer_id} K A/B shape mismatch")
            if flat["v_a"].shape != flat["v_b"].shape:
                raise ValueError(f"{path}: layer {layer_id} V A/B shape mismatch")
            lengths = {flat[name].shape[0] for name in flat}
            if len(lengths) != 1:
                raise ValueError(f"{path}: layer {layer_id} K/V token lengths differ")
            token_count = lengths.pop()
            if int(payload["x_tokens_a"]) != token_count or int(payload["x_tokens_b"]) != token_count:
                raise ValueError(
                    f"{path}: x_tokens_a/x_tokens_b must both equal T={token_count}, got {payload['x_tokens_a']}/{payload['x_tokens_b']}"
                )
            positions = np.arange(token_count, dtype=np.int64)
            layer_source_fields = {key: layer_entry[key] for key in ("slot_index", "layer", "full_attention_index") if key in layer_entry}
            common_metadata = {
                "source_file": str(path),
                "pair_index": payload_pair,
                "condition": payload_condition,
                "token_start_a": int(payload["token_start_a"]),
                "token_end_a": int(payload["token_end_a"]),
                "token_start_b": int(payload["token_start_b"]),
                "token_end_b": int(payload["token_end_b"]),
                "x_tokens_a": int(payload["x_tokens_a"]),
                "x_tokens_b": int(payload["x_tokens_b"]),
                "position_offset_a": int(payload["position_offset_a"]),
                "position_offset_b": int(payload["position_offset_b"]),
                "positions_semantics": "X_RELATIVE_TOKEN_INDEX",
                "flatten_layout": "T_HD",
                "num_kv_heads": self.schema.num_kv_heads,
                "head_dim": self.schema.head_dim,
                "feature_dim": self.schema.feature_dim,
                "layer_source_fields": layer_source_fields,
            }
            for kind, left, right in (("K", "k_a", "k_b"), ("V", "v_a", "v_b")):
                metadata = dict(common_metadata)
                metadata.update({"raw_shape": raw_shapes[left], "raw_dtype": raw_dtypes[left], "raw_shapes": raw_shapes})
                results.append(
                    StatePair(
                        pair_id=payload_pair,
                        split=split,
                        model_family=self.schema.model_family,
                        condition=payload_condition,
                        state_kind=kind,
                        layer_id=layer_id,
                        a=flat[left],
                        b=flat[right],
                        positions=positions,
                        metadata=metadata,
                    )
                )

        if tuple(observed_layer_ids) != self.schema.expected_layer_ids:
            raise ValueError(
                f"{path}: actual layer IDs {tuple(observed_layer_ids)} != expected {self.schema.expected_layer_ids}"
            )
        return results
