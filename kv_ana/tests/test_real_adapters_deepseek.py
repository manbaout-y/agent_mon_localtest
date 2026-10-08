from __future__ import annotations

import csv
import pickle
from pathlib import Path

import numpy as np
import pytest

from kvreuse_b5.adapters import DeepSeekPrefixC4Adapter


TEST_LAYERS = (2, 4)


def _entry(
    layer: int,
    ratio: int,
    value_offset: float,
    *,
    actual_seq_lengths_query=(0, 1199),
    start_pos=(0,),
    hidden_states_shape=(1199, 4096),
) -> dict:
    rows = 6
    values = np.arange(rows * 512, dtype=np.float32).reshape(rows, 512) + value_offset
    return {
        "layer": layer,
        "compress_ratio": ratio,
        "part": 0,
        "compressed_kv": values,
        "slot_mapping": np.stack((np.arange(rows), np.arange(rows)), axis=1),
        "actual_seq_lengths_query": list(actual_seq_lengths_query),
        "start_pos": list(start_pos),
        "hidden_states_shape": tuple(hidden_states_shape),
    }


def _write_payloads(
    root: Path,
    pair_ids=(0, 1),
    *,
    actual_seq_lengths_query=(0, 1199),
    start_pos=(0,),
    hidden_states_shape=(1199, 4096),
) -> None:
    for pair_id in pair_ids:
        for side, side_offset in (("A", 0.0), ("B", 10000.0)):
            layers = [
                _entry(2, 4, pair_id * 100000 + side_offset, actual_seq_lengths_query=actual_seq_lengths_query, start_pos=start_pos, hidden_states_shape=hidden_states_shape),
                _entry(4, 4, pair_id * 100000 + side_offset + 20000, actual_seq_lengths_query=actual_seq_lengths_query, start_pos=start_pos, hidden_states_shape=hidden_states_shape),
                _entry(2, 128, pair_id * 100000 + side_offset + 40000, actual_seq_lengths_query=actual_seq_lengths_query, start_pos=start_pos, hidden_states_shape=hidden_states_shape),
            ]
            payload = {"tag": f"P{pair_id:04d}_PREFIX_{side}", "num_raw_parts": 3, "layers": layers}
            with (root / f"P{pair_id:04d}_PREFIX_{side}.pt").open("wb") as handle:
                pickle.dump(payload, handle)


def _write_mapping(path: Path, *, pair_ids=(0, 1), anchor_mismatch=False) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("pair_id", "layer", "row_a", "row_b", "x_rel_anchor_a", "x_rel_anchor_b"))
        for pair_id in pair_ids:
            for layer in TEST_LAYERS:
                writer.writerow((pair_id, layer, 1, 2, 10, 99 if anchor_mismatch and pair_id == 0 and layer == 2 else 10))
                writer.writerow((pair_id, layer, 3, 4, 11, 11))


def _adapter(root: Path, mapping: Path) -> DeepSeekPrefixC4Adapter:
    return DeepSeekPrefixC4Adapter(
        root, mapping, expected_layers=TEST_LAYERS, expected_num_raw_parts=3, payload_loader=_pickle_load
    )


def _pickle_load(path: Path) -> dict:
    with path.open("rb") as handle:
        return pickle.load(handle)


def test_deepseek_prefix_a_b_pairing(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping)
    pairs = _adapter(tmp_path, mapping).load_pair(1)
    assert len(pairs) == 2
    assert all(pair.pair_id == 1 and pair.condition == "PREFIX_ONLY" for pair in pairs)
    assert pairs[0].metadata["source_file_a"].endswith("P0001_PREFIX_A.pt")
    assert pairs[0].metadata["source_file_b"].endswith("P0001_PREFIX_B.pt")
    assert pairs[0].metadata["packed_schema_a"]["actual_seq_lengths_query"] == (0, 1199)
    assert pairs[0].metadata["packed_schema_a"]["start_pos"] == (0,)


def test_deepseek_filters_to_c4_layers(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping)
    pairs = _adapter(tmp_path, mapping).load_pair(0)
    assert [pair.layer_id for pair in pairs] == [2, 4]
    assert all(pair.metadata["compress_ratio"] == 4 for pair in pairs)


def test_deepseek_strict_row_extraction_reads_row_a_and_row_b_separately(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping)
    pair = _adapter(tmp_path, mapping).load_pair(0)[0]
    payload_a = _pickle_load(tmp_path / "P0000_PREFIX_A.pt")
    payload_b = _pickle_load(tmp_path / "P0000_PREFIX_B.pt")
    expected_a = payload_a["layers"][0]["compressed_kv"][[1, 3]]
    expected_b = payload_b["layers"][0]["compressed_kv"][[2, 4]]
    np.testing.assert_array_equal(pair.a, expected_a)
    np.testing.assert_array_equal(pair.b, expected_b)
    assert pair.metadata["row_a"] == [1, 3]
    assert pair.metadata["row_b"] == [2, 4]


def test_deepseek_uses_canonical_x_rel_positions(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping)
    pair = _adapter(tmp_path, mapping).load_pair(0)[0]
    np.testing.assert_array_equal(pair.positions, [10, 11])
    assert pair.metadata["x_rel_anchor"] == [10, 11]
    assert pair.metadata["positions_semantics"] == "X_REL_ANCHOR_STRICT_MAPPING"


def test_deepseek_anchor_mismatch_fails(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, anchor_mismatch=True)
    with pytest.raises(ValueError, match="x_rel_anchor_a.*x_rel_anchor_b"):
        _adapter(tmp_path, mapping).load_pair(0)


def test_deepseek_missing_mapping_fails(tmp_path: Path):
    _write_payloads(tmp_path)
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    with pytest.raises(ValueError, match="no strict mapping rows"):
        _adapter(tmp_path, mapping).load_pair(1)


def test_deepseek_missing_prefix_side_fails(tmp_path: Path):
    _write_payloads(tmp_path, pair_ids=(0,))
    (tmp_path / "P0000_PREFIX_B.pt").unlink()
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    with pytest.raises(FileNotFoundError, match="PREFIX_B"):
        _adapter(tmp_path, mapping).load_pair(0)


def test_deepseek_real_query_length_and_start_pos_schema_passes(tmp_path: Path):
    _write_payloads(
        tmp_path,
        pair_ids=(0,),
        actual_seq_lengths_query=(0, 1199),
        start_pos=(0,),
        hidden_states_shape=(1199, 4096),
    )
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    assert len(_adapter(tmp_path, mapping).load_pair(0)) == 2


def test_deepseek_start_pos_nonzero_fails(tmp_path: Path):
    _write_payloads(tmp_path, pair_ids=(0,), start_pos=(1,))
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    with pytest.raises(ValueError, match="start_pos expected value 0"):
        _adapter(tmp_path, mapping).load_pair(0)


def test_deepseek_query_length_vector_wrong_length_fails(tmp_path: Path):
    _write_payloads(tmp_path, pair_ids=(0,), actual_seq_lengths_query=(1199,))
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    with pytest.raises(ValueError, match="actual_seq_lengths_query expected length 2"):
        _adapter(tmp_path, mapping).load_pair(0)


def test_deepseek_query_length_hidden_shape_mismatch_fails(tmp_path: Path):
    _write_payloads(
        tmp_path,
        pair_ids=(0,),
        actual_seq_lengths_query=(0, 1198),
        hidden_states_shape=(1199, 4096),
    )
    mapping = tmp_path / "mapping.csv"; _write_mapping(mapping, pair_ids=(0,))
    with pytest.raises(ValueError, match=r"actual_seq_lengths_query\[-1\].*hidden_states_shape\[0\]"):
        _adapter(tmp_path, mapping).load_pair(0)
