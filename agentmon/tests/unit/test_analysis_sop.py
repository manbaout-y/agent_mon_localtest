from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from agentmon.analysis.analyzers import analyze_p1_capture, analyze_scheduler, analyze_throughput
from agentmon.analysis.compare import compare_bundles
from agentmon.analysis.config import default_analysis_config
from agentmon.analysis.io import read_bundle_table, write_json, write_table
from agentmon.analysis.normalize import append_scheduler_and_resource_tables
from agentmon.analysis.report_validation import verify_model_report
from agentmon.analysis.schemas import load_metric_definitions
from agentmon.analysis.statistics import interval_union, time_weighted_summary, wilson_interval
from agentmon.analysis.validators import p2_status, token_timeseries_status, validate_pipeline
from agentmon.cli import main as agentmon_main


class AnalysisSopTests(unittest.TestCase):
    def test_schema_registry_loads_metric_semantics(self) -> None:
        metrics = load_metric_definitions("agentmon_default")

        self.assertIn("whole_workload_generation_tok_s", metrics)
        metric = metrics["active_model_spans"]
        self.assertEqual(metric.data_level, "span")
        self.assertTrue(metric.time_weighted)
        self.assertFalse(metrics["whole_workload_generation_tok_s"].summable)

    def test_time_weighted_and_interval_union_are_deterministic(self) -> None:
        merged, total = interval_union([(0, 2), (1, 3), (5, 6)])
        summary = time_weighted_summary([(0, 2, 1), (2, 4, 3)])

        self.assertEqual(merged, [(0.0, 3.0), (5.0, 6.0)])
        self.assertEqual(total, 4.0)
        self.assertEqual(summary["mean"], 2.0)
        self.assertEqual(summary["max"], 3)

    def test_wilson_interval_uses_configured_confidence(self) -> None:
        lower_80, upper_80 = wilson_interval(50, 100, confidence=0.80)
        lower_99, upper_99 = wilson_interval(50, 100, confidence=0.99)

        self.assertGreater(lower_80, lower_99)
        self.assertLess(upper_80, upper_99)

    def test_p2_gate_counts_error_requests_only_when_evidence_eligible(self) -> None:
        tables = {
            "requests": [
                {"request_status": "success", "p2_eligible": True, "p2_matched": True, "lifecycle_complete": True},
                {"request_status": "error", "p2_eligible": False, "p2_matched": False, "lifecycle_complete": False},
            ]
        }

        self.assertEqual(p2_status(tables, default_analysis_config()), "PASS")
        tables["requests"][1]["p2_eligible"] = True
        self.assertEqual(p2_status(tables, default_analysis_config()), "PARTIAL")

    def test_p1_capture_audit_matches_trajectory_attempts_to_p1_requests(self) -> None:
        outputs = analyze_p1_capture(
            {
                "trajectories": [
                    {
                        "run_id": "run-a",
                        "task_id": "task-a",
                        "event_type": "trajectory.model_response",
                        "source_path": "/tmp/traj.json",
                        "source_line": 7,
                        "payload_json": json.dumps({"response_id": "resp-a", "messages": []}),
                    }
                ],
                "requests": [
                    {
                        "run_id": "run-a",
                        "task_id": "task-a",
                        "request_attempt_id": "req-a",
                        "trajectory_response_id": "resp-a",
                        "api_request_id": "resp-a",
                        "p1_span_id": "span-a",
                    }
                ],
            },
            default_analysis_config(),
        )

        self.assertEqual(outputs["p1_capture_summary"][0]["status"], "PASS")
        self.assertEqual(outputs["p1_capture_audit"][0]["status"], "CAPTURED")
        self.assertEqual(outputs["p1_capture_audit"][0]["p1_span_id"], "span-a")

    def test_token_timeseries_requires_actual_generated_deltas_for_steady_state(self) -> None:
        tables = {
            "batches": [{"trial_id": "t", "batch_id": "b", "configured_concurrency": 2, "phase_timings": {"workload_phase_s": 10.0}}],
            "runs": [
                {
                    "trial_id": "t",
                    "batch_id": "b",
                    "configured_concurrency": 2,
                    "execution_success": True,
                    "publishable": True,
                    "model_request_attempt_count": 1,
                    "model_request_success_count": 1,
                    "generation_tokens_sum": 20,
                    "prompt_tokens_sum": 10,
                }
            ],
            "scheduler_iterations": [
                {
                    "trial_id": "t",
                    "batch_id": "b",
                    "configured_concurrency": 2,
                    "sample_time": 1_000_000_000,
                    "scheduled_token_count": 5,
                }
            ],
        }

        steady = analyze_throughput(tables, default_analysis_config())["steady_state_throughput"][0]
        scheduler = analyze_scheduler(tables, default_analysis_config())["token_timeseries"][0]

        self.assertEqual(steady["status"], "UNAVAILABLE")
        self.assertEqual(scheduler["status"], "UNAVAILABLE")
        self.assertEqual(token_timeseries_status(tables), "UNRESOLVED")
        tables["scheduler_iterations"].append(
            {
                "trial_id": "t",
                "batch_id": "b",
                "configured_concurrency": 2,
                "sample_time": 2_000_000_000,
                "actual_generated_tokens_delta": 10,
            }
        )
        tables["scheduler_iterations"][0]["actual_generated_tokens_delta"] = 5
        steady = analyze_throughput(tables, default_analysis_config())["steady_state_throughput"][0]
        self.assertEqual(steady["token_timeseries_status"], "PASS")
        self.assertIsNotNone(steady["steady_state_generation_tok_s"])
        self.assertEqual(token_timeseries_status(tables), "PASS")

    def test_deep_p2_scheduler_membership_and_kv_events_are_standardized(self) -> None:
        tables = {
            "scheduler_iterations": [],
            "request_batch_membership": [],
            "kv_block_events": [],
            "resource_samples": [],
            "request_id_aliases": [
                {
                    "run_id": "run-1",
                    "alias_value": "engine-1",
                    "request_attempt_id": "run-1:model-1",
                }
            ],
        }
        context = {
            "experiment_id": "exp",
            "trial_id": "trial",
            "batch_id": "batch",
            "configured_concurrency": 2,
            "task_id": "task-1",
        }
        events = [
            {
                "event_id": "scheduled",
                "source": "vllm_p2",
                "event_type": "vllm.scheduler.iteration.scheduled",
                "monotonic_time_ns": 100,
                "payload": {
                    "scheduler_iteration_id": 7,
                    "schedule_start_ns": 100,
                    "schedule_end_ns": 110,
                    "batch_request_count": 1,
                    "scheduled_tokens": 5,
                    "scheduled_prefill_tokens": 5,
                    "scheduled_decode_tokens": 0,
                },
            },
            {
                "event_id": "completed",
                "source": "vllm_p2",
                "event_type": "vllm.scheduler.iteration.completed",
                "monotonic_time_ns": 150,
                "payload": {
                    "scheduler_iteration_id": 7,
                    "schedule_end_ns": 110,
                    "execution_completed_ns": 150,
                    "schedule_to_execution_complete_ns": 40,
                },
            },
            {
                "event_id": "member-start",
                "source": "vllm_p2",
                "event_type": "vllm.request.scheduler_iteration",
                "payload": {
                    "vllm_request_id": "engine-1",
                    "scheduler_iteration_id": 7,
                    "scheduler_phase": "prefill",
                    "scheduled_token_count": 5,
                    "schedule_start_ns": 100,
                    "schedule_end_ns": 110,
                },
            },
            {
                "event_id": "member-end",
                "source": "vllm_p2",
                "event_type": "vllm.request.scheduler_iteration_completed",
                "payload": {
                    "vllm_request_id": "engine-1",
                    "scheduler_iteration_id": 7,
                    "batch_enter_ns": 110,
                    "batch_exit_ns": 150,
                    "batch_residence_ns": 40,
                },
            },
            {
                "event_id": "kv",
                "source": "vllm_p2",
                "event_type": "vllm.kv.block_event",
                "monotonic_time_ns": 120,
                "payload": {
                    "vllm_request_id": "engine-1",
                    "operation": "allocate",
                    "allocated_block_count": 3,
                    "cache_hit_block_count": 2,
                    "kv_quality": "exact_hook",
                },
            },
        ]

        append_scheduler_and_resource_tables(
            tables,
            context,
            "run-1",
            events,
            {"system_samples": []},
            set(),
        )

        self.assertEqual(len(tables["scheduler_iterations"]), 1)
        self.assertEqual(tables["scheduler_iterations"][0]["scheduled_prefill_tokens"], 5)
        self.assertEqual(len(tables["request_batch_membership"]), 1)
        membership = tables["request_batch_membership"][0]
        self.assertEqual(membership["request_attempt_id"], "run-1:model-1")
        self.assertEqual(membership["batch_residence_s"], 40 / 1_000_000_000)
        self.assertEqual(tables["kv_block_events"][0]["allocated_block_count"], 3)

        analyzed = analyze_scheduler(tables, default_analysis_config())
        self.assertEqual(analyzed["kv_analysis"][0]["status"], "PASS")
        self.assertEqual(
            analyzed["kv_analysis"][0]["cache_hit_block_count_total"],
            2,
        )

    def test_pipeline_validation_prefers_batch_phase_timings(self) -> None:
        rows = validate_pipeline(
            {
                "batches": [
                    {
                        "trial_id": "trial",
                        "batch_id": "batch",
                        "phase_timings": {
                            "preprocessing_s": 1.0,
                            "workload_phase_s": 5.0,
                            "p2_index_s": 1.0,
                            "analysis_phase_s": 2.0,
                            "finalization_s": 1.0,
                            "pipeline_wall_s": 10.0,
                        },
                        "pipeline_phase_validation": {"status": "PASS", "absolute_error_s": 0.0, "relative_error": 0.0},
                    }
                ],
                "worker_intervals": [],
            },
            default_analysis_config(),
        )

        self.assertEqual(rows[0]["status"], "PASS")
        self.assertEqual(rows[0]["evidence_source"], "batch_phase_events")

    def test_pipeline_estimate_cannot_be_reported_as_direct_pass(self) -> None:
        rows = validate_pipeline(
            {
                "batches": [],
                "worker_intervals": [
                    {
                        "trial_id": "trial",
                        "batch_id": "batch",
                        "start_time": 0,
                        "end_time": 1_000_000_000,
                        "duration_s": 1.0,
                    }
                ],
            },
            default_analysis_config(),
        )

        self.assertEqual(rows[0]["status"], "PARTIAL")
        self.assertEqual(rows[0]["timestamp_quality"], "ESTIMATED")

    def test_analysis_run_all_cli_creates_bundle_and_model_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            bundle = Path(tmp) / "bundle"
            write_synthetic_experiment(root, concurrency=3, task_count=2)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "analysis",
                        "run-all",
                        "--input",
                        str(root),
                        "--output",
                        str(bundle),
                        "--output-format",
                        "csv",
                        "--no-figures",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("EXPERIMENTS_FOUND=1", output)
            self.assertIn("RUNS_PARSED=2", output)
            manifest = json.loads((bundle / "manifest" / "experiment_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["configured_concurrency"], [3])
            self.assertTrue((bundle / "model_input_pack" / "report_prompt.md").is_file())
            self.assertEqual(len(read_bundle_table(bundle, "01_normalized", "runs")), 2)
            self.assertTrue((bundle / "03_analysis" / "throughput_summary.csv.gz").is_file())
            optimizer_manifest = json.loads(
                (bundle / "04_evidence" / "optimizer_experiment" / "optimizer_harness_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(optimizer_manifest["artifact_type"], "optimizer_harness")
            self.assertEqual(optimizer_manifest["batch_count"], 1)

    def test_analysis_run_all_materializes_contract_semantics_quality_gate_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            bundle = Path(tmp) / "bundle"
            contract_path = Path(tmp) / "experiment-contract.json"
            write_synthetic_experiment(root, concurrency=3, task_count=2)
            write_json(contract_path, explicit_contract(load_levels=[3], task_count=2))

            with redirect_stdout(io.StringIO()):
                exit_code = agentmon_main(
                    [
                        "analysis",
                        "run-all",
                        "--input",
                        str(root),
                        "--output",
                        str(bundle),
                        "--output-format",
                        "csv",
                        "--no-figures",
                        "--experiment-contract",
                        str(contract_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            contract = json.loads((bundle / "00_inventory" / "experiment_contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["contract_status"], "PASS")
            self.assertEqual(contract["contract_source"], "EXPLICIT_CONTRACT")
            semantics = json.loads((bundle / "02_semantics" / "semantics_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(semantics["status"], "PASS")
            gate = json.loads((bundle / "02_validation" / "data_quality_gate.json").read_text(encoding="utf-8"))
            self.assertIn(gate["gate_status"], {"PASS", "PARTIAL", "FAIL"})
            self.assertIn("forbidden_conclusions", gate)
            handoff = json.loads((bundle / "05_handoff" / "handoff_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(handoff["handoff_scope"], "basic_analysis_only")
            self.assertTrue((bundle / "model_input_pack" / "data_quality_gate.json").is_file())
            self.assertTrue((bundle / "model_input_pack" / "handoff_manifest.json").is_file())
            self.assertTrue((bundle / "model_input_pack" / "observation_register.csv.gz").is_file())
            self.assertFalse((bundle / "06_deep_analysis").exists())

    def test_missing_experiment_contract_is_recorded_as_partial_not_imputed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            bundle = Path(tmp) / "bundle"
            write_synthetic_experiment(root, concurrency=2, task_count=1)

            with redirect_stdout(io.StringIO()):
                exit_code = agentmon_main(
                    [
                        "analysis",
                        "prepare",
                        "--input",
                        str(root),
                        "--output",
                        str(bundle),
                        "--output-format",
                        "csv",
                    ]
                )

            self.assertEqual(exit_code, 0)
            contract = json.loads((bundle / "00_inventory" / "experiment_contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["contract_status"], "PARTIAL")
            self.assertIn("throughput_denominator", contract["missing_required_fields"])
            validation_rows = read_bundle_table(bundle, "00_inventory", "contract_validation")
            self.assertTrue(any(row.get("field_name") == "throughput_denominator" and row.get("value_status") == "UNKNOWN" for row in validation_rows))

    def test_analysis_fast_smoke_parallel_skips_heavy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            bundle = Path(tmp) / "bundle"
            write_synthetic_experiment(root, concurrency=3, task_count=2)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "analysis",
                        "run-all",
                        "--input",
                        str(root),
                        "--output",
                        str(bundle),
                        "--output-format",
                        "csv",
                        "--no-figures",
                        "--jobs",
                        "2",
                        "--fast-smoke",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("RUNS_PARSED=2", stdout.getvalue())
            self.assertEqual(len(read_bundle_table(bundle, "01_normalized", "runs")), 2)
            self.assertEqual(len(read_bundle_table(bundle, "01_normalized", "requests")), 2)
            self.assertEqual(len(read_bundle_table(bundle, "01_normalized", "event_inventory")), 0)
            self.assertTrue((bundle / "04_evidence" / "skip_reason.json").is_file())
            self.assertTrue((bundle / "04_evidence" / "optimizer_experiment" / "skip_reason.json").is_file())
            self.assertTrue((bundle / "05_handoff" / "skip_reason.json").is_file())
            self.assertTrue((bundle / "model_input_pack" / "skip_reason.json").is_file())

    def test_contract_template_cli_writes_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "contract-template.json"
            with redirect_stdout(io.StringIO()):
                exit_code = agentmon_main(["analysis", "build-contract-template", "--output", str(output)])

            self.assertEqual(exit_code, 0)
            template = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("throughput_denominator", template)
            self.assertIn("expected_data_sources", template)

    def test_report_validator_flags_forbidden_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            pack.mkdir()
            write_json(pack / "forbidden_conclusions.json", [{"statement": "Do not confirm physical NPU mapping"}])
            write_json(pack / "analysis_ready.json", {"module_status": {"npu_mapping": "UNRESOLVED"}})
            write_json(pack / "verified_facts.json", [{"value": 12}])
            report = Path(tmp) / "report.md"
            report.write_text("We confirm physical NPU mapping and the hardware bottleneck caused 77 failures.\n", encoding="utf-8")

            result = verify_model_report(model_pack=pack, report_path=report, output_dir=Path(tmp) / "validation")

            self.assertEqual(result["status"], "FAIL")
            self.assertGreater(result["forbidden_claim_count"], 0)
            self.assertTrue((Path(tmp) / "validation" / "forbidden_claims.csv.gz").is_file())

    def test_report_validator_flags_metric_unit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / "metric_dictionary.md").write_text(
                "| Metric | Level | Unit | Clock | Aggregation | Notes |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| `request_throughput` | run | request/s | monotonic | mean | rate |\n"
                "| `task_throughput` | run | task/hour | monotonic | mean | rate |\n",
                encoding="utf-8",
            )
            write_json(pack / "analysis_ready.json", {"overall_status": "PASS", "module_status": {}})
            report = root / "report.md"
            report.write_text("request_throughput is 10 task/hour.\n", encoding="utf-8")
            output = root / "validation"
            result = verify_model_report(model_pack=pack, report_path=report, output_dir=output)
            semantic = read_bundle_table(output, "", "semantic_mismatches")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any(row.get("reason") == "metric_unit_mismatch" for row in semantic))

    def test_compare_marks_different_models_non_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = make_minimal_bundle(Path(tmp) / "a", model_name="model-a", concurrency=1, throughput=10.0)
            b = make_minimal_bundle(Path(tmp) / "b", model_name="model-b", concurrency=1, throughput=12.0)
            out = Path(tmp) / "cmp"

            result = compare_bundles(bundle_paths=[a, b], output_root=out, output_format="csv")

            self.assertEqual(result["status"], "PARTIAL")
            self.assertIn("model_name", {row["dimension"] for row in result["non_comparable_dimensions"]})
            self.assertTrue((out / "normalized_deltas.csv.gz").is_file())


def explicit_contract(*, load_levels: list[int], task_count: int) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "objective": "Measure standard load-analysis outputs for a synthetic experiment.",
        "research_questions": [
            {
                "question_id": "rq.load",
                "question": "How do standardized throughput and lifecycle metrics behave across configured load levels?",
                "analysis_scope": "basic_load_analysis",
            }
        ],
        "independent_variables": [{"name": "configured_concurrency"}],
        "load_levels": load_levels,
        "control_variables": {"model_name": "synthetic"},
        "baseline": {"configured_concurrency": load_levels[0]},
        "task_set": {"task_count": task_count, "selection_rule": "synthetic_fixture"},
        "repetitions": {"runs_per_load_level": task_count},
        "key_config": {"model_name": "synthetic"},
        "success_definition": "execution_success is true",
        "availability_definition": "A run is available when its events.jsonl is readable and normalized.",
        "throughput_numerator": "generation_tokens_sum",
        "throughput_denominator": "workload_phase_s",
        "batch_time_window": "workload_start_mono_ns to workload_end_mono_ns",
        "comparison_unit": "batch",
        "fixed_task_pairing": True,
    }


def write_synthetic_experiment(root: Path, *, concurrency: int, task_count: int) -> None:
    batch = root / "trial-one" / f"c{concurrency}"
    batch.mkdir(parents=True)
    (batch / "batch-manifest.json").write_text(
        json.dumps({"concurrency": concurrency, "task_count": task_count, "trial_id": "trial-one", "model_name": "synthetic"}) + "\n",
        encoding="utf-8",
    )
    lines = ["task_id\trun_id\trun_dir\tworker_id\texecution_success\tworkload_start_mono_ns\tworkload_end_mono_ns"]
    for index in range(task_count):
        run_id = f"run-{index}"
        run_dir = batch / run_id
        run_dir.mkdir()
        lines.append(f"task-{index}\t{run_id}\t{run_dir}\tw{index}\ttrue\t1000000000\t5000000000")
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "success",
                    "start_mono_ns": 1_000_000_000,
                    "end_mono_ns": 5_000_000_000,
                    "duration_ns": 4_000_000_000,
                    "agentmon_version": "test",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        events = [
            {
                "schema_version": "1.0",
                "event_id": f"{run_id}-start",
                "run_id": run_id,
                "source": "mini_swe",
                "event_type": "model_request.start",
                "quality": "exact",
                "wall_time_ns": 1,
                "monotonic_time_ns": 1_100_000_000,
                "payload": {"span_id": f"model-{index}", "span_type": "model_request", "model_call_id": f"model-{index}"},
            },
            {
                "schema_version": "1.0",
                "event_id": f"{run_id}-end",
                "run_id": run_id,
                "source": "mini_swe",
                "event_type": "model_request.end",
                "quality": "exact",
                "wall_time_ns": 2,
                "monotonic_time_ns": 2_100_000_000,
                "payload": {
                    "span_id": f"model-{index}",
                    "span_type": "model_request",
                    "duration_ns": 1_000_000_000,
                    "status": "success",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                },
            },
        ]
        (run_dir / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    (batch / "batch-results.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_minimal_bundle(path: Path, *, model_name: str, concurrency: int, throughput: float) -> Path:
    (path / "manifest").mkdir(parents=True)
    (path / "02_validation").mkdir()
    write_json(
        path / "manifest" / "experiment_manifest.json",
        {"schema_version": "1.0", "model_name": model_name, "configured_concurrency": [concurrency]},
    )
    write_json(path / "02_validation" / "analysis_ready.json", {"overall_status": "PASS", "module_status": {}})
    write_table(
        path / "03_analysis",
        "throughput_summary",
        [{"configured_concurrency": concurrency, "whole_workload_generation_tok_s": throughput, "task_throughput_per_hour": throughput, "request_throughput_per_second": throughput}],
        output_format="csv",
    )
    write_table(
        path / "01_normalized",
        "runs",
        [{"task_id": "task-a", "run_id": f"{model_name}-run", "run_wall_s": throughput}],
        output_format="csv",
    )
    return path
