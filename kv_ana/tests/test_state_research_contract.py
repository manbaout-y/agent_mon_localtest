from dataclasses import replace
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kvreuse_b5.state_research.agentmon import correlate_events
from kvreuse_b5.state_research.alignment import LocalFastTokenizerProvider
from kvreuse_b5.state_research.fixtures import synthetic_events, synthetic_records, synthetic_states
from kvreuse_b5.state_research.io import load_records, merge_sidecar
from kvreuse_b5.state_research.lifemem import prepare_records
from kvreuse_b5.state_research.prepare import main, prepare_dataset
from kvreuse_b5.state_research.replay import replay_manifest, representative_cases
from kvreuse_b5.state_research.state_inventory import account_states, capture_requirements


def prepared(records=None, **kwargs):
    return prepare_records(records if records is not None else synthetic_records(), dataset_id="fixture", **kwargs)


def test_observed_order_uses_timestamps_and_actual_interarrival():
    records = list(reversed(synthetic_records()))
    data = prepared(records, order="observed")
    assert data["requests"][0]["request_id"] == "request-0"
    assert all(t["elapsed_ns"] == 20 for t in data["transitions"])
    assert replay_manifest(data["requests"])["entries"][-1]["release_offset_ns"] == 80


def test_constructed_order_has_no_invented_timing():
    data = prepared(list(reversed(synthetic_records())))
    manifest = replay_manifest(data["requests"])
    assert manifest["ordering"] == "constructed"
    assert manifest["entries"][0]["request_id"] == "request-4"
    assert all(e["release_offset_ns"] is None for e in manifest["entries"])
    assert manifest["concurrency"] is None
    assert manifest["waiting_intervals"] is None


@pytest.mark.parametrize("field,value", [("request_time_ns", None), ("clock_domain", None), ("clock_domain", "another-host")])
def test_observed_time_needs_common_clock_and_complete_timestamps(field, value):
    records = synthetic_records()
    records[1]["research"][field] = value
    with pytest.raises(ValueError):
        prepared(records, order="observed")


def test_timestamp_ties_are_disclosed():
    records = synthetic_records()[:2]
    records[1]["research"]["request_time_ns"] = records[0]["research"]["request_time_ns"]
    assert all(t["timestamp_tied"] for t in prepared(records, order="observed")["transitions"])


def test_missing_request_is_a_gap_not_a_pairwise_jump():
    records = synthetic_records()[:3]
    records[1]["results"] = []
    data = prepared(records)
    assert all(t["intervening_session_requests"] == 1 for t in data["transitions"])


def test_duplicate_request_id_is_rejected():
    records = synthetic_records()
    records[1]["research"]["request_id"] = "request-0"
    with pytest.raises(ValueError, match="duplicate request_id"):
        prepared(records)


def test_agentmon_explicit_bridge_resolves_out_of_order_events():
    result = correlate_events(prepared()["requests"], synthetic_events())
    assert result["matched"] == 10
    assert result["rows"][0]["request_id"] == "request-0"
    assert result["rows"][0]["semantics"] == "ID_CORRELATION_NOT_RESOURCE_OWNERSHIP"


def test_agentmon_no_time_or_run_id_only_guess():
    event = {"run_id": "synthetic-agent", "monotonic_time_ns": 1000, "payload": {}}
    result = correlate_events(prepared()["requests"], [event])
    assert result["unmatched"] == 1


def test_agentmon_conflicting_ids_are_not_joined():
    requests = prepared()["requests"]
    requests[0]["agentmon"]["engine_request_id"] = "engine-0"
    event = {"payload": {"vllm_request_id": "engine-0", "agentmon_model_request_id": "model-1"}}
    result = correlate_events(requests, [event])
    assert result["conflicts"] == 1
    assert result["rows"][0]["request_id"] is None


def test_shared_alias_is_ambiguous_not_arbitrary():
    requests = prepared()["requests"]
    for r in requests:
        r["agentmon"]["engine_request_id"] = "reused-engine-id"
    result = correlate_events(requests, [{"payload": {"vllm_request_id": "reused-engine-id"}}])
    assert result["ambiguous"] == 1


def test_different_agent_runs_can_disambiguate_same_alias():
    requests = prepared()["requests"][:2]
    for i, r in enumerate(requests):
        r["agentmon"] = {"agent_run_id": f"run{i}", "model_request_id": "same"}
    result = correlate_events(requests, [{"payload": {"model_request_id": "same", "agentmon_run_id": "run1"}}])
    assert result["rows"][0]["request_id"] == "request-1"


def test_physical_aliases_deduplicated_but_device_replicas_counted():
    result = account_states(synthetic_states())
    assert result["logical_state_count"] == 3
    assert result["physical_allocation_count"] == 2
    assert result["physical_allocated_bytes"] == 400
    assert result["logical_view_bytes_sum"] == 384
    assert result["consumer_bindings"] == 6


def test_conflicting_allocation_bytes_rejected():
    states = synthetic_states()
    states[1] = replace(states[1], allocation_bytes=300)
    with pytest.raises(ValueError, match="inconsistent physical"):
        account_states(states)


def test_v41_catalog_is_unverified_and_v4_points_to_local_source():
    requirements = capture_requirements()
    assert requirements["DeepSeek-V4.1"]["status"] == "REQUIRES_RUNTIME_SCHEMA"
    root = Path(__file__).resolve().parents[1]
    source = (root / requirements["DeepSeek-V4"]["evidence_file"]).read_text()
    assert all(s["runtime_symbol"] in source for s in requirements["DeepSeek-V4"]["states"])


@pytest.mark.parametrize("format_name", ["array", "single", "container", "jsonl"])
def test_input_formats(tmp_path, format_name):
    record = synthetic_records()[0]
    values = {"array": json.dumps([record]), "single": json.dumps(record),
              "container": json.dumps({"records": [record]}),
              "jsonl": json.dumps(record) + "\n" + json.dumps(record)}
    path = tmp_path / "input.json"
    path.write_text(values[format_name])
    assert len(load_records(path)) == (2 if format_name == "jsonl" else 1)


def test_sidecar_conflict_not_silently_overwritten():
    with pytest.raises(ValueError, match="conflicts"):
        merge_sidecar(synthetic_records(), [{"record_index": 0, "research": {"request_id": "other"}}])


def test_sidecar_adds_explicit_metadata_without_changing_source():
    records = synthetic_records()
    for r in records:
        r.pop("research")
    original = deepcopy(records)
    output = merge_sidecar(records, [{"record_index": 0, "research": {"request_id": "explicit"}}])
    assert output[0]["research"]["request_id"] == "explicit"
    assert records == original


def test_cli_outputs_are_separated_and_existing_output_protected(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "out"
    source.write_text(json.dumps(synthetic_records(), ensure_ascii=False))
    assert main(["--input", str(source), "--output", str(output), "--dataset-id", "test"]) == 0
    assert (output / "replay_manifest.json").exists()
    assert (output / "capture_requirements.json").exists()
    summary = (output / "transition_summary.csv").read_text()
    assert "共同记忆" not in summary
    assert "共同记忆" in (output / "payloads.jsonl").read_text()
    with pytest.raises(ValueError, match="empty directory"):
        prepare_dataset(synthetic_records(), output, dataset_id="test")


def test_missing_full_prompt_report_is_partial_not_functional_success(tmp_path):
    records = synthetic_records()
    for r in records:
        r.pop("research")
    q = prepare_dataset(records, tmp_path / "out", dataset_id="legacy")
    assert q["full_prompt_count"] == 0
    assert q["token_aligned_pairs_for_state_review"] == 0
    assert q["research_readiness"]["repair_functional_validation"].startswith("REQUIRES")


def test_representative_cases_have_no_target_based_selection():
    data = prepared()
    cases = representative_cases(data["transitions"], per_class=1)
    assert len(cases) == len({c["classification"] for c in cases})
    assert all(c["state_mapping_status"] == "REQUIRES_MODEL_SPECIFIC_MAPPING" for c in cases)


def test_empty_dataset_rejected(tmp_path):
    with pytest.raises(ValueError, match="empty LifeMem"):
        prepare_dataset([], tmp_path / "out", dataset_id="empty")


def test_import_does_not_load_torch():
    result = subprocess.run([sys.executable, "-c", "import sys; import kvreuse_b5.state_research.prepare; assert 'torch' not in sys.modules"],
                            cwd=Path(__file__).resolve().parents[1], env={**__import__("os").environ, "PYTHONPATH": "src"},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_tokenizer_is_local_only_and_keeps_special_token_policy(tmp_path, monkeypatch):
    import types
    observed = {}

    class FakeTokenizer:
        is_fast = True

        def __call__(self, text, **kwargs):
            observed["encode"] = kwargs
            return {"input_ids": [7, 8], "offset_mapping": [(0, 1), (0, 1)]}

    class Auto:
        @staticmethod
        def from_pretrained(directory, **kwargs):
            observed["load"] = kwargs
            return FakeTokenizer()

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=Auto))
    provider = LocalFastTokenizerProvider(str(tmp_path), "fixed-revision")
    result = provider.tokenize("中", text_scope="full_prompt")
    result.validate("中", "full_prompt")
    assert observed["load"] == {"local_files_only": True, "trust_remote_code": False, "use_fast": True}
    assert observed["encode"] == {"add_special_tokens": False, "return_offsets_mapping": True, "truncation": False}
    assert result.source == "local_tokenizer"
    assert result.tokenizer_id.endswith("add_special_tokens=0")


def test_existing_runtime_tokens_win_over_optional_retokenizer():
    class MustNotCall:
        def tokenize(self, *args, **kwargs):
            raise AssertionError("must not override explicitly provided tokenization")

    data = prepared(tokenizer=MustNotCall())
    assert len(data["requests"]) == 5


def test_state_snapshot_dependencies_preserved_and_missing_dependencies_disclosed():
    states = synthetic_states()
    states[1] = replace(states[1], dependency_state_ids=("state-0", "outside-snapshot"))
    result = account_states(states)
    assert result["unresolved_dependency_ids"] == ["outside-snapshot"]
    assert {"producer_state_id": "state-0", "consumer_state_id": "state-1"} in result["dependency_edges"]


def test_state_snapshot_unknown_request_not_silently_attached(tmp_path):
    states = [replace(synthetic_states()[0], request_id="missing")]
    with pytest.raises(ValueError, match="absent from the prepared"):
        prepare_dataset(synthetic_records(), tmp_path / "out", dataset_id="fixture", states=states)


def test_agentmon_colliding_bridges_keep_engine_only_events_ambiguous():
    requests = prepared()["requests"]
    events = [
        {"event_type": "vllm.request.received", "payload": {"model_request_id": f"model-{i}", "vllm_request_id": "same-engine"}}
        for i in range(2)
    ] + [{"event_type": "vllm.kv.block_event", "payload": {"vllm_request_id": "same-engine"}}]
    result = correlate_events(requests, events)
    assert result["rows"][-1]["status"] == "AMBIGUOUS"


def test_synthetic_script_end_to_end(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/state_research_smoke.py"),
                             "--output", str(tmp_path / "smoke")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "NO_TOKEN_FALLBACK=PASS" in result.stdout
    assert "MODEL_EXECUTION=NOT_RUN" in result.stdout
    report = json.loads((tmp_path / "smoke/smoke_result.json").read_text())
    assert report["status"] == "PASS"
