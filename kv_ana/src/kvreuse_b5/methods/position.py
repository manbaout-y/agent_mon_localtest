from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from kvreuse_b5.data.types import StatePair, as_numpy
from .base import CorrectionMethod, CorrectionResult, EvalContext, MethodCapabilities, MethodCost


@dataclass(frozen=True)
class RoPEMetadata:
    inv_freq: np.ndarray
    num_kv_heads: int
    head_dim: int
    rotary_dim: int
    flatten_layout: str
    rotary_layout: str
    rope_theta: float | None = None
    rope_scaling: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RoPEMetadata":
        required = {"inv_freq", "num_kv_heads", "head_dim", "rotary_dim", "flatten_layout", "rotary_layout"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"RoPE metadata missing {sorted(missing)}")
        metadata = cls(
            as_numpy(value["inv_freq"], dtype=float).reshape(-1),
            int(value["num_kv_heads"]),
            int(value["head_dim"]),
            int(value["rotary_dim"]),
            str(value["flatten_layout"]),
            str(value["rotary_layout"]),
            float(value["rope_theta"]) if value.get("rope_theta") is not None else None,
            value.get("rope_scaling"),
        )
        if metadata.num_kv_heads <= 0 or metadata.head_dim <= 0:
            raise ValueError("num_kv_heads and head_dim must be positive")
        if metadata.rotary_dim <= 0 or metadata.rotary_dim % 2:
            raise ValueError("rotary_dim must be a positive even integer")
        if len(metadata.inv_freq) != metadata.rotary_dim // 2:
            raise ValueError("inv_freq length must equal rotary_dim / 2")
        if metadata.rotary_dim > metadata.head_dim:
            raise ValueError("rotary_dim cannot exceed head_dim")
        if metadata.flatten_layout != "T_HD":
            raise ValueError("v2 supports only explicit flatten_layout='T_HD'")
        if metadata.rotary_layout not in {"interleaved", "split_half"}:
            raise ValueError("rotary_layout must be interleaved or split_half")
        return metadata


def _rotate(values: np.ndarray, angles: np.ndarray, layout: str) -> np.ndarray:
    cos = np.cos(angles)[:, None, :]
    sin = np.sin(angles)[:, None, :]
    output = values.copy()
    if layout == "interleaved":
        first, second = values[..., 0::2], values[..., 1::2]
        output[..., 0::2] = first * cos - second * sin
        output[..., 1::2] = first * sin + second * cos
    else:
        half = values.shape[-1] // 2
        first, second = values[..., :half], values[..., half:]
        output[..., :half] = first * cos - second * sin
        output[..., half:] = first * sin + second * cos
    return output


def apply_rope_shift(
    state: np.ndarray,
    source_positions: np.ndarray,
    target_positions: np.ndarray,
    rope_metadata: RoPEMetadata | Mapping[str, Any],
) -> np.ndarray:
    """Move an already RoPE-encoded [T,F] state from source to target positions."""
    state = as_numpy(state)
    if not np.issubdtype(state.dtype, np.floating):
        raise ValueError("RoPE state must have a floating dtype")
    state = state.copy()
    source = as_numpy(source_positions, dtype=float).reshape(-1)
    target = as_numpy(target_positions, dtype=float).reshape(-1)
    metadata = rope_metadata if isinstance(rope_metadata, RoPEMetadata) else RoPEMetadata.from_mapping(rope_metadata)
    if state.ndim != 2 or len(source) != len(state) or len(target) != len(state):
        raise ValueError("state must be [T,F] and source/target positions must be [T]")
    expected_features = metadata.num_kv_heads * metadata.head_dim
    if state.shape[1] != expected_features:
        raise ValueError(
            f"flattened feature dimension {state.shape[1]} != num_kv_heads*head_dim {expected_features}"
        )
    angles = (target - source)[:, None] * metadata.inv_freq[None, :]
    heads = state.reshape(len(state), metadata.num_kv_heads, metadata.head_dim)
    heads[..., : metadata.rotary_dim] = _rotate(
        heads[..., : metadata.rotary_dim], angles, metadata.rotary_layout
    )
    return heads.reshape(len(state), expected_features)


class PositionCorrectionBase(ABC):
    @abstractmethod
    def transform(self, state: np.ndarray, source_positions: np.ndarray, target_positions: np.ndarray, metadata):
        raise NotImplementedError


class RoPEPositionCorrection(CorrectionMethod, PositionCorrectionBase):
    name = "rope_position"
    capabilities = MethodCapabilities(requires_position_metadata=True)

    def transform(self, state, source_positions, target_positions, metadata):
        return apply_rope_shift(state, source_positions, target_positions, metadata)

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        metadata = pair.metadata["rope_metadata"]
        if "source_positions" not in metadata or "target_positions" not in metadata:
            raise ValueError("rope_metadata must explicitly provide source_positions and target_positions")
        corrected = self.transform(
            pair.a, metadata["source_positions"], metadata["target_positions"], metadata
        )
        return CorrectionResult(corrected, MethodCost(total_units=pair.total_units))
