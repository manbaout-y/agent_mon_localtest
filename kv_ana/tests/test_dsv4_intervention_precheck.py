from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SOURCE = PROJECT_ROOT / "runtime_intervention" / "dsv4_precheck_v1"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intervention = load_module(
    "dsv4_state_intervention_v1_test", BUNDLE_SOURCE / "dsv4_state_intervention_v1.py"
)


def environment(tmp_path: Path, mode: str, spec: dict) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tag = tmp_path / "tag.txt"
    tag.write_text(spec["request_tag"] + "\n", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return {
        "DSV4_STATE_INTERVENTION": mode,
        "DSV4_STATE_INTERVENTION_TAG_FILE": str(tag),
        "DSV4_STATE_INTERVENTION_SPEC": str(spec_path),
        "DSV4_STATE_INTERVENTION_TRACE": str(tmp_path / "trace.jsonl"),
    }


def base_spec(mode: str) -> dict:
    return {
        "request_tag": "PAIR0_PREFIX_A",
        "experiment_class": "CAUSAL_PRECHECK",
        "mode": mode,
        "target_layer": 2,
        "compress_ratio": 4,
        "source_artifact": None,
        "source_rows": [],
        "target_rows": [1, 3],
        "expected_tensor_shape": [4, 2],
        "expected_slot_mapping": [[0, 0], [0, 1], [0, 2], [0, 3]],
        "metadata": {},
    }


def test_identity_and_zero_selected_are_traced_without_torch(tmp_path: Path):
    slot_mapping = [[0, 0], [0, 1], [0, 2], [0, 3]]
    identity_spec = base_spec("identity")
    identity_env = environment(tmp_path / "identity", "identity", identity_spec)
    values = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]
    original = [row[:] for row in values]

    result = intervention.apply_runtime_intervention(
        compressed_kv=values,
        slot_mapping=slot_mapping,
        layer_name="model.layers.2.self_attn.attn",
        compress_ratio=4,
        mode="identity",
        environ=identity_env,
    )
    assert result == original
    record = json.loads(Path(identity_env["DSV4_STATE_INTERVENTION_TRACE"]).read_text())
    assert record["applied"] is True
    assert record["delta_norm"] == 0.0
    assert record["slot_mapping_match"] is True

    zero_spec = base_spec("zero_selected")
    zero_env = environment(tmp_path / "zero", "zero_selected", zero_spec)
    values = [row[:] for row in original]
    intervention.apply_runtime_intervention(
        compressed_kv=values,
        slot_mapping=slot_mapping,
        layer_name="model.layers.2.self_attn.attn",
        compress_ratio=4,
        mode="zero_selected",
        environ=zero_env,
    )
    assert values[1] == [0, 0]
    assert values[3] == [0, 0]
    record = json.loads(Path(zero_env["DSV4_STATE_INTERVENTION_TRACE"]).read_text())
    assert record["delta_norm"] > 0


def test_external_requires_exact_shape_layer_ratio_rows_and_slot_mapping(tmp_path: Path):
    artifact = tmp_path / "source.pt"
    artifact.write_bytes(b"injected-loader")
    spec = base_spec("external")
    spec["source_artifact"] = str(artifact)
    spec["source_rows"] = [0, 2]
    env = environment(tmp_path / "external", "external", spec)
    runtime = [[1, 1], [2, 2], [3, 3], [4, 4]]
    source = {
        "layer": 2,
        "compress_ratio": 4,
        "compressed_kv": [[10, 11], [20, 21], [30, 31], [40, 41]],
    }

    intervention.apply_runtime_intervention(
        compressed_kv=runtime,
        slot_mapping=[[0, 0], [0, 1], [0, 2], [0, 3]],
        layer_name="model.layers.2.self_attn.attn",
        compress_ratio=4,
        mode="external",
        environ=env,
        artifact_loader=lambda _: source,
    )
    assert runtime[1] == [10, 11]
    assert runtime[3] == [30, 31]

    bad = base_spec("external")
    bad["source_artifact"] = str(artifact)
    bad["source_rows"] = [0, 2]
    bad["expected_slot_mapping"] = [[9, 9]]
    bad_env = environment(tmp_path / "bad", "external", bad)
    with pytest.raises(intervention.InterventionError, match="slot_mapping"):
        intervention.apply_runtime_intervention(
            compressed_kv=[[1, 1], [2, 2], [3, 3], [4, 4]],
            slot_mapping=[[0, 0], [0, 1], [0, 2], [0, 3]],
            layer_name="model.layers.2.self_attn.attn",
            compress_ratio=4,
            mode="external",
            environ=bad_env,
            artifact_loader=lambda _: source,
        )


def test_out_of_scope_layer_is_visible_skip(tmp_path: Path):
    spec = base_spec("identity")
    env = environment(tmp_path, "identity", spec)
    values = [[1, 2], [3, 4], [5, 6], [7, 8]]
    intervention.apply_runtime_intervention(
        compressed_kv=values,
        slot_mapping=[],
        layer_name="model.layers.4.self_attn.attn",
        compress_ratio=4,
        mode="identity",
        environ=env,
    )
    record = json.loads(Path(env["DSV4_STATE_INTERVENTION_TRACE"]).read_text())
    assert record["applied"] is False
    assert record["skip_reason"] == "out_of_scope_layer_or_ratio"


def test_patch_and_restore_use_sha_and_anchor_guards(tmp_path: Path):
    patch_runtime = load_module("patch_runtime_test", BUNDLE_SOURCE / "patch_runtime.py")
    target = tmp_path / "dsa_v1.py"
    target.write_text(
        "class Runtime:\n"
        "    def forward(self, compressed_kv, compressor_prefill_metadata, layer_name):\n"
        "            # [DSV4_KV_DUMP] LifeMem compressed-KV experiment.\n"
        "            captured = compressed_kv\n"
        "            if compressed_kv.numel() == 0:\n"
        "                compressed_kv = None\n"
        "            return compressed_kv\n",
        encoding="utf-8",
    )
    baseline_sha = patch_runtime.sha256(target)
    patch_runtime.BASELINE_SHA256 = baseline_sha
    patch_runtime.apply_patch(target, BUNDLE_SOURCE)
    patched = target.read_text(encoding="utf-8")
    assert patched.count(patch_runtime.PATCH_BEGIN) == 1
    assert patched.index(patch_runtime.DUMP_ANCHOR) < patched.index(patch_runtime.PATCH_BEGIN)
    assert patched.index(patch_runtime.PATCH_BEGIN) < patched.index("if compressed_kv.numel() == 0:")
    patch_runtime.restore_patch(target, BUNDLE_SOURCE)
    assert patch_runtime.sha256(target) == baseline_sha

    target.write_text(target.read_text() + "# changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA mismatch"):
        patch_runtime.apply_patch(target, BUNDLE_SOURCE)
