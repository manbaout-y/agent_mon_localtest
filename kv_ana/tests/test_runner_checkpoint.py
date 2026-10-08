import json

import pytest

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import smoke_dataset
from kvreuse_b5.execution.config import ExperimentConfig
from kvreuse_b5.execution.runner import ExperimentRunner


def make_config(output, extra=None):
    raw = {
        "experiment": {"split": "predefined"},
        "output_dir": str(output),
        "methods": [
            {"name": "no_correction"},
            {"name": "frozen_lowrank_oracle", "ranks": [4]},
            {"name": "probe_frozen_basis", "ranks": [4], "probes": [4]},
            {"name": "joint_unit_oracle", "budgets": [1.0]},
            {"name": "query_aware_selection"},
        ],
    }
    if extra:
        raw.update(extra)
    return ExperimentConfig.from_mapping(raw)


def test_runner_checkpoint_resume_and_capability_report(tmp_path):
    config = make_config(tmp_path / "run")
    runner = ExperimentRunner(config)
    records, summary = runner.run(StatePairDataset(smoke_dataset()))
    assert any(r["status"] == "ZERO_RESIDUAL" for r in records)
    assert any(r["status"] == "CAPABILITY_MISSING" for r in records)
    assert all(r["config_hash"] == config.config_hash for r in records)
    assert (tmp_path / "run" / "results.jsonl").exists()
    resumed, _ = runner.run(StatePairDataset(smoke_dataset()))
    assert json.dumps(resumed, allow_nan=True, sort_keys=True) == json.dumps(records, allow_nan=True, sort_keys=True)


def test_config_hash_mismatch_refuses_reuse(tmp_path):
    output = tmp_path / "run"
    ExperimentRunner(make_config(output)).run(StatePairDataset(smoke_dataset()))
    changed = make_config(output, {"note": "changes hash"})
    with pytest.raises(ValueError, match="config hash mismatch"):
        ExperimentRunner(changed).run(StatePairDataset(smoke_dataset()))
