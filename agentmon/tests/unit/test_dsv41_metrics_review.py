from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agentmon.analysis.kv_metrics_review import review_metrics
from agentmon.adapters.inference.vllm_018.version import is_supported_vllm_version

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT.parent / "docs/dsv41_metrics_review_20261008/evidence/user_metrics.prom"


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review(text=None, **kwargs):
    return review_metrics(SNAPSHOT.read_text(encoding="utf-8") if text is None else text,
                          model="deepseek-v41", engine="0", **kwargs)


def test_observed_snapshot_does_not_promote_runtime_capture():
    result = review()
    assert not result["issues"]
    assert result["values"]["prefix_cache_hits_total"] == 256
    assert result["derived"]["prefix_counter_ratio"] == pytest.approx(256 / 781)
    assert result["block_size_investigation"]["resolution"] == "UNRESOLVED"
    assert result["request_kv_events"] == "NOT_CAPTURED"
    assert result["kv_offload_observed"] == "NOT_PROVEN"
    assert result["cache_config_labels"]["kv_offloading_size"] == "None"


def test_capacity_mismatch_is_arithmetic_not_layout_or_bug():
    result = review()
    assert result["derived"]["num_gpu_blocks_times_block_size"] == 430336
    assert result["derived"]["capacity_matches_simple_product"] is False
    assert result["derived"]["capacity_div_reference_max_model_len"] == pytest.approx(4.9641914368)
    assert result["derived"]["actual_max_num_seqs"] == "UNRESOLVED"
    assert result["block_size_investigation"]["physical_layout"] == "NOT_CAPTURED"


def test_exact_scope_no_other_model_or_engine_leak():
    text = SNAPSHOT.read_text(encoding="utf-8")
    other = text.replace('engine="0"', 'engine="1"').replace("781.0", "5000.0")
    assert review(text + other)["derived"]["prefix_counter_ratio"] == pytest.approx(256 / 781)
    assert review(other)["values"]["prefix_cache_queries_total"] is None


@pytest.mark.parametrize("replacement", ["NaN", "+Inf", "-1.0"])
def test_nonfinite_negative_counter_unknown(replacement):
    result = review(SNAPSHOT.read_text(encoding="utf-8").replace("781.0", replacement))
    assert result["derived"]["prefix_counter_ratio"] is None
    assert result["issues"]


def test_duplicate_series_and_zero_denominator_are_not_hit_rate():
    text = SNAPSHOT.read_text(encoding="utf-8")
    result = review(text + text)
    assert result["derived"]["prefix_counter_ratio"] is None
    assert result["cache_config_labels"] == {}
    assert review(text.replace("781.0", "0.0"))["derived"]["prefix_counter_ratio"] is None


def test_equal_block_label_still_requires_physical_validation():
    result = review(historical_block_size=32)
    assert result["block_size_investigation"]["discrepancy_observed"] is False
    assert result["block_size_investigation"]["resolution"] == "UNRESOLVED"


def test_invalid_info_sample_unknown():
    text = SNAPSHOT.read_text(encoding="utf-8").replace('} 1.0', '} 0.0')
    assert review(text)["cache_config_labels"] == {}


def test_offline_cli_preserves_bytes_unknown_time_and_refuses_overwrite(tmp_path):
    module = script("dsv41_metrics_review")
    out = tmp_path / "review"
    args = ["--metrics", str(SNAPSHOT), "--output-dir", str(out), "--provenance", "USER_PROVIDED_OUTPUT"]
    assert module.main(args) == 0
    assert (out / "metrics.prom").read_bytes() == SNAPSHOT.read_bytes()
    assert json.loads((out / "metrics_review.json").read_text())["provenance"]["collected_at_utc"] == "UNKNOWN"
    with pytest.raises(FileExistsError):
        module.main(args)


def test_source_ast_does_not_import_or_execute_target(tmp_path):
    module = script("dsv41_kv_source_snapshot")
    target = tmp_path / "scheduler.py"
    target.write_text("raise RuntimeError('must never run')\nclass Scheduler:\n def schedule(self):\n  self.manager.allocate_slots(None)\n", encoding="utf-8")
    row = module.read_file(target, "scheduler.py")
    assert row["status"] == "SOURCE_READ"
    assert row["definitions"][1]["name"] == "Scheduler.schedule"
    assert row["lexical_calls"][0]["line"] == 4
    assert row["lexical_calls"][0]["resolution"] == "LEXICAL_CALL_NOT_RUNTIME_DISPATCH"
    assert module.read_file(tmp_path / "missing.py", "missing.py")["status"] == "MISSING"
    target.write_text("invalid Python !!!", encoding="utf-8")
    assert module.read_file(target, "scheduler.py")["status"] == "SOURCE_ERROR"


def test_missing_source_not_fake_chain_and_render_preserves_output(tmp_path):
    module = script("dsv41_kv_source_snapshot")
    result = module.collect(tmp_path / "absent")
    assert all(f["status"] == "MISSING" for f in result["files"])
    assert result["runtime_dispatch"] == "UNVERIFIED"
    assert result["hook_installed"] is False
    out = tmp_path / "inventory"
    module.render(result, out)
    assert "MISSING" in (out / "source_inventory.md").read_text()
    with pytest.raises(FileExistsError):
        module.render(result, out)


def test_old_p2_version_guard_unchanged():
    assert is_supported_vllm_version("0.18.0+local")
    assert not is_supported_vllm_version("0.27.1+empty")
