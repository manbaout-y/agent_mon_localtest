from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from kvreuse_b5.data.dataset import StatePairDataset
from kvreuse_b5.data.synthetic import smoke_dataset
from kvreuse_b5.execution.config import ExperimentConfig
from kvreuse_b5.execution.runner import ExperimentRunner
from kvreuse_b5.observability import (
    KVTraceEvent,
    ObservabilityWriter,
    TraceAction,
    TraceCollector,
    TraceSource,
)


def _event(**overrides):
    values = {
        "run_id": "run",
        "method": "method",
        "method_config_hash": "config",
        "model_family": "model",
        "condition": "PREFIX_ONLY",
        "state_kind": "K",
        "pair_id": "pair",
        "step_index": 0,
        "layer_id": 2,
        "action": TraceAction.RECOMPUTE,
        "positions": (1, 3),
        "observed_units": 2,
        "recomputed_units": 2,
        "source": TraceSource.FULL_EXACT,
        "request_id": None,
        "agent_run_id": None,
        "agent_step_id": None,
        "model_request_id": None,
        "reuse_request_id": None,
        "chunk_id": None,
        "metadata": {
            "total_available_units": 8,
            "total_positions": 4,
            "total_layer_position_units": 8,
            "available_scope": "sample",
        },
    }
    values.update(overrides)
    return KVTraceEvent(**values)


def test_trace_event_json_schema_and_nullable_runtime_ids() -> None:
    payload = _event().to_dict()
    assert json.loads(json.dumps(payload))["action"] == "recompute"
    assert payload["source"] == "full_exact"
    assert payload["request_id"] is None and payload["agent_run_id"] is None
    with pytest.raises(ValueError, match="invalid trace action"):
        _event(action="invented_action")


def test_zero_applied_correction_norm_remains_explicit_in_summary() -> None:
    collector = TraceCollector("summary", run_id="run")
    collector.record(_event(
        action=TraceAction.CORRECT,
        observed_units=0,
        recomputed_units=0,
        corrected_units=2,
        correction_norm=0.0,
        applied_correction_norm=0.0,
        estimated_residual_mean_norm=3.0,
    ))
    row = collector.summary_events()[0]
    assert row["correction_norm"] == 0.0
    assert row["applied_correction_norm"] == 0.0
    assert row["estimated_residual_mean_norm"] == 3.0


def test_off_collector_records_nothing_and_writes_nothing(tmp_path: Path) -> None:
    collector = TraceCollector("off", run_id="run", trace_dir=tmp_path)
    assert collector.record(_event()) is None
    assert collector.summary_events() == [] and collector.cost_rows() == []
    manifest = ObservabilityWriter(tmp_path).write(collector)
    assert manifest["level"] == "off"
    assert list(tmp_path.iterdir()) == []


def test_summary_aggregation_and_repeated_unique_cost() -> None:
    collector = TraceCollector("summary", run_id="run")
    collector.record(_event())
    collector.record(_event(step_index=1, positions=(1, 3)))
    summary = collector.summary_events()
    assert len(summary) == 1
    assert summary[0]["recomputed_units"] == 4
    assert summary[0]["positions"] is None
    assert summary[0]["metadata"]["event_count"] == 2
    cost = collector.cost_rows()[0]
    assert cost["recomputed_units_additive"] == 4
    assert cost["recomputed_units_unique_layer_position"] == 2
    assert cost["unique_positions"] == 2
    assert cost["recomputed_fraction_additive"] == 0.5
    assert cost["recomputed_fraction_unique"] == 0.25
    assert cost["recomputed_layer_position_fraction_additive"] == 0.5
    assert cost["recomputed_layer_position_fraction_unique"] == 0.25


def test_summary_strips_position_arrays_but_detail_preserves_them() -> None:
    collector = TraceCollector("detail", run_id="run")
    collector.record(_event(
        action=TraceAction.RETIRE,
        positions=(1, 3),
        observed_units=0,
        recomputed_units=0,
        metadata={
            "total_available_units": 8,
            "total_positions": 4,
            "total_layer_position_units": 8,
            "available_scope": "sample",
            "selected_before": [0, 1, 2],
            "selected_after": [0, 2],
            "retire_positions": [1],
            "newly_selected_positions": [2],
            "detail": {"selector_score_by_position": [0.0, 0.0, 1.0, 1.0]},
        },
    ))
    summary_metadata = collector.summary_events()[0]["metadata"]
    assert summary_metadata["selected_before_count"] == 3
    assert summary_metadata["selected_after_count"] == 2
    assert summary_metadata["retired_count"] == 1
    assert summary_metadata["newly_selected_count"] == 1
    assert "selected_before" not in summary_metadata
    assert "retire_positions" not in summary_metadata
    assert "detail" not in summary_metadata
    detail_metadata = collector.detail_events()[0]["metadata"]
    assert detail_metadata["selected_before"] == [0, 1, 2]
    assert detail_metadata["detail"]["selector_score_by_position"] == [0.0, 0.0, 1.0, 1.0]


def test_position_and_layer_position_denominators_are_not_mixed() -> None:
    collector = TraceCollector("summary", run_id="run")
    collector.record(_event(
        action=TraceAction.SELECT,
        positions=(0, 1),
        selected_units=2,
        observed_units=0,
        recomputed_units=0,
    ))
    collector.record(_event(layer_id=2, positions=(0, 1)))
    collector.record(_event(layer_id=3, positions=(0, 1)))
    cost = collector.cost_rows()[0]
    assert cost["selected_unique_positions"] == 2
    assert cost["selected_position_fraction"] == 2 / 4
    assert cost["recomputed_layer_position_units_additive"] == 4
    assert cost["recomputed_layer_position_units_unique"] == 4
    assert cost["recomputed_layer_position_fraction_unique"] == 4 / 8
    assert cost["selected_position_fraction"] != 2 / 8


def test_detail_preserves_positions_and_writer_compresses(tmp_path: Path) -> None:
    collector = TraceCollector("detail", run_id="run", trace_dir=tmp_path)
    collector.record(_event())
    manifest = ObservabilityWriter(tmp_path).write(collector)
    assert manifest["summary_event_count"] == 1
    assert manifest["detail_event_count"] == 1
    with gzip.open(tmp_path / "trace_detail.jsonl.gz", "rt", encoding="utf-8") as handle:
        row = json.loads(handle.readline())
    assert row["positions"] == [1, 3]
    assert (tmp_path / "trace_summary.jsonl").is_file()
    assert (tmp_path / "cost_accounting.csv").is_file()
    assert (tmp_path / "forensic_summary.csv").is_file()
    assert (tmp_path / "run_manifest.json").is_file()
    assert (tmp_path / "trace_schema.json").is_file()


def test_experiment_runner_observability_on_off_preserves_metrics_and_method_hash(tmp_path: Path, monkeypatch) -> None:
    def run(output: Path, level: str):
        monkeypatch.setenv("KVREUSE_TRACE", level)
        config = ExperimentConfig.from_mapping({
            "experiment": {"split": "predefined"},
            "output_dir": str(output),
            "methods": [{"name": "no_correction"}, {"name": "probe_mean", "probes": [2]}],
        })
        records, _ = ExperimentRunner(config).run(StatePairDataset(smoke_dataset()))
        return sorted(records, key=lambda row: (repr(row["state_identity"]), row["method"]))

    off = run(tmp_path / "off", "off")
    detail = run(tmp_path / "detail", "detail")
    assert len(off) == len(detail)
    for left, right in zip(off, detail):
        for field in ("state_identity", "method", "method_config_hash", "baseline_sse", "corrected_sse", "status"):
            assert left[field] == right[field]
        if left["energy_explained"] is None:
            assert right["energy_explained"] is None
        else:
            assert left["energy_explained"] == pytest.approx(right["energy_explained"], nan_ok=True)
    assert not (tmp_path / "off" / "observability").exists()
    assert (tmp_path / "detail" / "observability" / "trace_detail.jsonl.gz").is_file()
