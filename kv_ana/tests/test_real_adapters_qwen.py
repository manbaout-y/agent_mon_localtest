from __future__ import annotations

from pathlib import Path
import pickle

import numpy as np
import pytest

from kvreuse_b5.adapters import QWEN35_ATTENTION_LAYERS, QwenRawKVAdapter, flatten_qwen_state


def _raw(heads: int, tokens: int, head_dim: int, offset: float = 0.0) -> np.ndarray:
    values = np.arange(heads * tokens * head_dim, dtype=np.float32).reshape(1, heads, tokens, head_dim)
    return values + offset


def _pickle_load(path: Path) -> dict:
    with path.open("rb") as handle:
        return pickle.load(handle)


def _write_qwen(
    root: Path,
    *,
    model_family: str,
    pair_id: int = 1,
    condition: str = "POSITION_ONLY",
    tokens: int = 2,
    payload_condition: str | None = None,
    x_tokens_b: int | None = None,
    shape_mismatch: bool = False,
) -> Path:
    if model_family == "Qwen3-8B":
        heads, head_dim = 8, 128
        layer_ids = tuple(range(36))
        layers = []
        for index in layer_ids:
            k_b = _raw(heads, tokens + (1 if shape_mismatch and index == 0 else 0), head_dim, 10)
            layers.append(
                {
                    "slot_index": index,
                    "k_a": _raw(heads, tokens, head_dim),
                    "v_a": _raw(heads, tokens, head_dim, 100),
                    "k_b": k_b,
                    "v_b": _raw(heads, tokens, head_dim, 200),
                }
            )
        extra = {}
    else:
        heads, head_dim = 4, 256
        layer_ids = QWEN35_ATTENTION_LAYERS
        layers = []
        for index, layer_id in enumerate(layer_ids):
            k_b = _raw(heads, tokens + (1 if shape_mismatch and index == 0 else 0), head_dim, 10)
            layers.append(
                {
                    "layer": layer_id,
                    "full_attention_index": index,
                    "k_a": _raw(heads, tokens, head_dim),
                    "v_a": _raw(heads, tokens, head_dim, 100),
                    "k_b": k_b,
                    "v_b": _raw(heads, tokens, head_dim, 200),
                }
            )
        extra = {"model_family": model_family, "measured_attention_layers": list(layer_ids)}
    payload = {
        "format_version": 1,
        "base_runner": "synthetic",
        "pair_index": pair_id,
        "condition": payload_condition or condition,
        "token_start_a": 101,
        "token_end_a": 101 + tokens,
        "token_start_b": 301,
        "token_end_b": 301 + tokens,
        "x_tokens_a": tokens,
        "x_tokens_b": tokens if x_tokens_b is None else x_tokens_b,
        "position_offset_a": 17,
        "position_offset_b": 43,
        "layer_slots": len(layer_ids),
        "layers": layers,
        **extra,
    }
    path = root / f"pair_{pair_id:06d}_{condition}.pt"
    with path.open("wb") as handle:
        pickle.dump(payload, handle)
    return path


def test_q8_flatten_exact():
    raw = _raw(8, 3, 128)
    flat = flatten_qwen_state(raw, num_kv_heads=8, head_dim=128)
    assert flat.shape == (3, 1024)
    for token, head, dim in ((0, 0, 0), (1, 3, 17), (2, 7, 127)):
        assert flat[token, head * 128 + dim] == float(raw[0, head, token, dim])


def test_q35_flatten_exact():
    raw = _raw(4, 3, 256)
    flat = flatten_qwen_state(raw, num_kv_heads=4, head_dim=256)
    assert flat.shape == (3, 1024)
    for token, head, dim in ((0, 0, 0), (1, 2, 71), (2, 3, 255)):
        assert flat[token, head * 256 + dim] == float(raw[0, head, token, dim])


def test_q35_uses_actual_layer_id_not_attention_index(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3.5-27B")
    pairs = QwenRawKVAdapter(tmp_path, "Qwen3.5-27B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")
    assert sorted({pair.layer_id for pair in pairs}) == list(QWEN35_ATTENTION_LAYERS)
    layer3 = next(pair for pair in pairs if pair.layer_id == 3 and pair.state_kind == "K")
    assert layer3.metadata["layer_source_fields"] == {"layer": 3, "full_attention_index": 0}


def test_qwen_emits_independent_k_and_v_pairs(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3-8B")
    pairs = QwenRawKVAdapter(tmp_path, "Qwen3-8B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")
    assert len(pairs) == 72
    k_pair = next(pair for pair in pairs if pair.layer_id == 0 and pair.state_kind == "K")
    v_pair = next(pair for pair in pairs if pair.layer_id == 0 and pair.state_kind == "V")
    assert not np.array_equal(k_pair.a, v_pair.a)
    assert k_pair.state_identity != v_pair.state_identity


def test_qwen_preserves_position_offsets_and_relative_position_semantics(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3-8B", tokens=3)
    pair = QwenRawKVAdapter(tmp_path, "Qwen3-8B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")[0]
    assert pair.metadata["position_offset_a"] == 17
    assert pair.metadata["position_offset_b"] == 43
    assert pair.metadata["positions_semantics"] == "X_RELATIVE_TOKEN_INDEX"
    np.testing.assert_array_equal(pair.positions, [0, 1, 2])


def test_qwen_filename_payload_condition_mismatch_fails(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3-8B", payload_condition="PREFIX_ONLY")
    with pytest.raises(ValueError, match="filename identity"):
        QwenRawKVAdapter(tmp_path, "Qwen3-8B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")


def test_qwen_shape_mismatch_fails(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3-8B", shape_mismatch=True)
    with pytest.raises(ValueError, match="K A/B shape mismatch"):
        QwenRawKVAdapter(tmp_path, "Qwen3-8B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")


def test_qwen_x_tokens_mismatch_fails(tmp_path: Path):
    _write_qwen(tmp_path, model_family="Qwen3.5-27B", x_tokens_b=9)
    with pytest.raises(ValueError, match="x_tokens_a/x_tokens_b"):
        QwenRawKVAdapter(tmp_path, "Qwen3.5-27B", payload_loader=_pickle_load).load_pair(1, "POSITION_ONLY")
