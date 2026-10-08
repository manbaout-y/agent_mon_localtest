from __future__ import annotations

import csv
from contextlib import redirect_stdout
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_analyze_steps.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_analyze_steps", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StepCsvAnalyzerTests(unittest.TestCase):
    def test_analyzer_classifies_model_tool_queue_and_quality(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "steps.csv"
            write_step_csv(path)

            analysis = module.analyze_step_csv(path, top_n=3)
            report = module.format_analysis(analysis, "report")
            csv_output = module.format_analysis(analysis, "csv")

        self.assertEqual(analysis["overview"]["run_count"], 2)
        self.assertEqual(analysis["overview"]["model_request_count"], 3)
        self.assertEqual(analysis["overview"]["tool_call_count"], 2)
        self.assertEqual(analysis["overview"]["model_extra_count"], 1)

        by_run = {row["run_id"]: row for row in analysis["run_summaries"]}
        self.assertIn("model_wait_bound", by_run["run-model"]["bottleneck"])
        self.assertIn("vllm_queue_pressure", by_run["run-model"]["bottleneck"])
        self.assertIn("decode_heavy", by_run["run-model"]["bottleneck"])
        self.assertIn("tool_wait_bound", by_run["run-tool"]["bottleneck"])
        self.assertEqual(by_run["run-tool"]["tool_nonzero_return_count"], 1)

        self.assertEqual(analysis["quality"]["model_extra_count"], 1)
        self.assertEqual(analysis["quality"]["alignment_quality_counts"]["ordinal_fallback"], 1)
        self.assertIn("Slowest model request", analysis["recommendations"][-2])
        self.assertIn("AgentMon step CSV automatic analysis", report)
        self.assertIn("run_id,step_count", csv_output)

    def test_analyzer_writes_derived_outputs(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "steps.csv"
            out_dir = base / "analysis"
            write_step_csv(path)

            analysis = module.analyze_step_csv(path, top_n=2)
            module.write_outputs(analysis, out_dir)

            self.assertTrue((out_dir / "analysis.json").exists())
            self.assertTrue((out_dir / "run_summary.csv").exists())
            self.assertTrue((out_dir / "model_hotspots.csv").exists())
            self.assertTrue((out_dir / "tool_hotspots.csv").exists())
            self.assertTrue((out_dir / "validation_checks.json").exists())
            self.assertTrue((out_dir / "data_quality.json").exists())
            self.assertTrue((out_dir / "confidence.json").exists())
            self.assertTrue((out_dir / "limitations.json").exists())
            self.assertTrue((out_dir / "metric_definition.json").exists())
            self.assertIn("run-model", (out_dir / "run_summary.csv").read_text(encoding="utf-8"))

    def test_analyzer_does_not_make_strong_diagnosis_from_low_coverage_fields(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "low-coverage.csv"
            write_low_coverage_csv(path)

            analysis = module.analyze_step_csv(path, top_n=3)

        summary = analysis["run_summaries"][0]
        self.assertIn("model_wait_bound", summary["bottleneck"])
        self.assertNotIn("vllm_queue_pressure", summary["bottleneck"])
        self.assertNotIn("low_cache_reuse", summary["bottleneck"])
        self.assertIsNone(summary["cache_reuse_rate"])
        self.assertEqual(summary["coverage"]["model_queue_s"], 0.25)
        self.assertEqual(summary["coverage"]["cached_prompt_tokens"], 0.0)
        self.assertIn("insufficient or unverified queue coverage", summary["warnings"])
        self.assertIn("insufficient cached-token coverage", summary["warnings"])

    def test_missing_metrics_remain_null_instead_of_zero(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "run_id": "run-missing",
                        "step_index": "1",
                        "step_id": "step-1",
                        "step_type": "agent_step",
                        "item_index": "1",
                        "item_type": "model",
                        "run_wall_s": "12",
                        "run_wall_source": "manifest.duration_ns",
                    }
                )

            analysis = module.analyze_step_csv(path)
            report = module.format_analysis(analysis, "report")

        summary = analysis["run_summaries"][0]
        self.assertIsNone(summary["run_model_e2e_sum_s"])
        self.assertIsNone(summary["run_model_queue_sum_s"])
        self.assertIsNone(summary["prompt_tokens"])
        self.assertIsNone(summary["cached_prompt_tokens"])
        self.assertIn("N/A", report)

    def test_analyzer_separates_explicit_prefill_and_shared_scheduler_pressure(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefill.csv"
            write_rows(
                path,
                [
                    {
                        "run_id": "run-prefill",
                        "step_index": "1",
                        "step_id": "step-1",
                        "step_type": "agent_step",
                        "response_id": "resp-1",
                        "api_request_id": "resp-1",
                        "item_type": "model",
                        "run_wall_s": "12",
                        "run_wall_source": "manifest.duration_ns",
                        "p2_match_status": "matched",
                        "p2_match_method": "api_request_id",
                        "p2_lifecycle_status": "complete",
                        "p2_e2e_s": "10",
                        "p2_queue_s": "1",
                        "p2_ttft_s": "6",
                        "p2_prefill_s": "4",
                        "p2_prefill_to_first_token_s": "5",
                        "p2_decode_s": "3",
                        "p2_queue_quality": "engine_process_monotonic",
                        "p2_ttft_quality": "same_host_cross_process_monotonic",
                        "p2_prefill_quality": "engine_process_monotonic",
                        "p2_decode_quality": "engine_process_monotonic",
                        "p2_e2e_quality": "api_process_monotonic",
                        "p2_generation_tokens": "30",
                        "model_scheduler_running_requests_max": "5",
                        "model_scheduler_waiting_requests_max": "2",
                        "model_scheduler_scheduled_tokens_max": "4096",
                        "model_scheduler_observation_scope": "shared_service_observation",
                    }
                ],
            )

            analysis = module.analyze_step_csv(path)

        summary = analysis["run_summaries"][0]
        self.assertEqual(summary["run_model_prefill_sum_s"], 4.0)
        self.assertEqual(summary["run_model_prefill_to_first_token_sum_s"], 5.0)
        self.assertIn("prefill_heavy", summary["bottleneck"])
        self.assertIn("ttft_heavy", summary["bottleneck"])
        self.assertIn("shared_scheduler_waiting_pressure", summary["bottleneck"])
        self.assertEqual(summary["shared_scheduler_waiting_requests_max"], 2.0)
        self.assertEqual(analysis["analysis_publication"]["status"], "PUBLISHABLE")

    def test_strict_returns_nonzero_for_low_p2_match_rate(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unmatched.csv"
            write_rows(
                path,
                [
                    {
                        "run_id": "run-unmatched",
                        "step_index": "1",
                        "step_id": "step-1",
                        "step_type": "agent_step",
                        "response_id": "resp-trajectory",
                        "api_request_id": "resp-p2",
                        "item_type": "model",
                        "run_wall_s": "3",
                        "p2_match_status": "unmatched",
                        "p2_missing_reason": "response_id_not_found",
                        "p2_lifecycle_status": "complete",
                        "p2_e2e_s": "2",
                    }
                ],
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                return_code = module.main([str(path), "--strict", "--format", "json"])

        self.assertEqual(return_code, 1)
        self.assertIn('"status": "UNPUBLISHABLE"', stdout.getvalue())


FIELDS = [
    "run_id",
    "step_index",
    "step_id",
    "step_type",
    "response_id",
    "api_request_id",
    "alignment_status",
    "alignment_quality",
    "item_index",
    "item_type",
    "status",
    "run_wall_s",
    "run_wall_source",
    "start_offset_s",
    "duration_s",
    "p2_e2e_s",
    "p2_match_status",
    "p2_match_method",
    "p2_match_confidence",
    "p2_missing_reason",
    "p2_lifecycle_status",
    "p2_queue_s",
    "p2_ttft_s",
    "p2_prefill_s",
    "p2_prefill_to_first_token_s",
    "p2_decode_s",
    "p2_queue_quality",
    "p2_ttft_quality",
    "p2_prefill_quality",
    "p2_decode_quality",
    "p2_e2e_quality",
    "p2_correlation_quality",
    "p2_finish_reason",
    "p2_prompt_tokens",
    "p2_cached_prompt_tokens",
    "p2_recomputed_prompt_tokens",
    "p2_generation_tokens",
    "p2_cache_reuse_rate",
    "p2_recomputed_prompt_rate",
    "p2_kv_usage_after",
    "p2_evicted_block_count",
    "step_agent_cpu_time_s",
    "step_target_peak_hbm_used_bytes",
    "step_target_avg_hbm_used_bytes",
    "step_target_max_aicore_util_pct",
    "step_target_avg_aicore_util_pct",
    "model_scheduler_running_requests_max",
    "model_scheduler_waiting_requests_max",
    "model_scheduler_scheduled_tokens_max",
    "model_scheduler_observation_scope",
    "tool_call_id",
    "tool_name",
    "command",
    "approx_tool_duration_s",
    "returncode",
    "timed_out",
    "tool_cpu_time_s",
    "tool_cpu_wall_ratio",
    "command_hash",
]


def write_step_csv(path: Path) -> None:
    rows = [
        {
            "run_id": "run-model",
            "step_index": "1",
            "step_id": "s1",
            "step_type": "agent_step",
            "response_id": "resp-1",
            "api_request_id": "resp-1",
            "alignment_status": "matched",
            "alignment_quality": "response_id",
            "item_index": "0",
            "item_type": "model",
            "status": "ok",
            "run_wall_s": "25",
            "run_wall_source": "manifest.duration_ns",
            "start_offset_s": "0",
            "duration_s": "20",
            "p2_e2e_s": "18",
            "p2_queue_s": "5",
            "p2_ttft_s": "2",
            "p2_decode_s": "11",
            "p2_queue_quality": "engine_process_monotonic",
            "p2_ttft_quality": "same_host_cross_process_monotonic",
            "p2_decode_quality": "engine_process_monotonic",
            "p2_e2e_quality": "api_process_monotonic",
            "p2_correlation_quality": "service_exclusive",
            "p2_finish_reason": "stop",
            "p2_prompt_tokens": "2000",
            "p2_cached_prompt_tokens": "100",
            "p2_recomputed_prompt_tokens": "900",
            "p2_generation_tokens": "100",
            "p2_cache_reuse_rate": "0.05",
            "p2_recomputed_prompt_rate": "0.45",
            "p2_kv_usage_after": "0.7",
            "p2_evicted_block_count": "2",
            "step_agent_cpu_time_s": "0.5",
            "step_target_peak_hbm_used_bytes": "100000",
            "step_target_avg_hbm_used_bytes": "90000",
            "step_target_max_aicore_util_pct": "85",
            "step_target_avg_aicore_util_pct": "70",
        },
        {
            "run_id": "run-model",
            "step_index": "2",
            "step_id": "s2",
            "step_type": "model_extra",
            "response_id": "resp-extra",
            "api_request_id": "resp-extra",
            "alignment_status": "p2_without_trajectory_step",
            "alignment_quality": "service_stream",
            "item_index": "0",
            "item_type": "model",
            "start_offset_s": "21",
            "duration_s": "4",
            "run_wall_s": "25",
            "run_wall_source": "manifest.duration_ns",
            "p2_e2e_s": "4",
            "p2_queue_s": "0.1",
            "p2_ttft_s": "1",
            "p2_decode_s": "2.9",
            "p2_queue_quality": "engine_process_monotonic",
            "p2_ttft_quality": "same_host_cross_process_monotonic",
            "p2_decode_quality": "engine_process_monotonic",
            "p2_e2e_quality": "api_process_monotonic",
            "p2_prompt_tokens": "200",
            "p2_generation_tokens": "20",
        },
        {
            "run_id": "run-tool",
            "step_index": "1",
            "step_id": "t1",
            "step_type": "agent_step",
            "alignment_status": "matched",
            "alignment_quality": "ordinal_fallback",
            "item_index": "0",
            "item_type": "model",
            "start_offset_s": "0",
            "duration_s": "30",
            "run_wall_s": "33",
            "run_wall_source": "manifest.duration_ns",
            "p2_e2e_s": "3",
            "p2_queue_s": "0.1",
            "p2_ttft_s": "1",
            "p2_decode_s": "1.9",
            "p2_queue_quality": "engine_process_monotonic",
            "p2_ttft_quality": "same_host_cross_process_monotonic",
            "p2_decode_quality": "engine_process_monotonic",
            "p2_e2e_quality": "api_process_monotonic",
            "p2_prompt_tokens": "500",
            "p2_generation_tokens": "40",
            "step_agent_cpu_time_s": "1",
        },
        {
            "run_id": "run-tool",
            "step_index": "1",
            "step_id": "t1",
            "step_type": "agent_step",
            "item_index": "1",
            "item_type": "tool",
            "start_offset_s": "3",
            "duration_s": "25",
            "tool_call_id": "tool-1",
            "tool_name": "bash",
            "command": "pytest -q",
            "approx_tool_duration_s": "22",
            "returncode": "1",
            "timed_out": "false",
            "tool_cpu_time_s": "15",
            "tool_cpu_wall_ratio": "0.68",
            "command_hash": "hash-pytest",
        },
        {
            "run_id": "run-tool",
            "step_index": "2",
            "step_id": "t2",
            "step_type": "agent_step",
            "item_index": "1",
            "item_type": "tool",
            "start_offset_s": "31",
            "duration_s": "2",
            "tool_call_id": "tool-2",
            "tool_name": "bash",
            "command": "grep -R foo .",
            "approx_tool_duration_s": "2",
            "returncode": "0",
            "timed_out": "false",
            "tool_cpu_time_s": "0.2",
            "tool_cpu_wall_ratio": "0.1",
            "command_hash": "hash-grep",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def write_low_coverage_csv(path: Path) -> None:
    rows = []
    for index in range(4):
        rows.append(
            {
                "run_id": "run-low-coverage",
                "step_index": str(index + 1),
                "step_id": f"s{index + 1}",
                "step_type": "agent_step",
                "response_id": f"resp-{index + 1}",
                "api_request_id": f"resp-{index + 1}",
                "alignment_status": "matched",
                "alignment_quality": "response_id",
                "item_index": "0",
                "item_type": "model",
                "start_offset_s": str(index * 10),
                "duration_s": "10",
                "run_wall_s": "40",
                "run_wall_source": "manifest.duration_ns",
                "p2_e2e_s": "9",
                "p2_queue_s": "9" if index == 0 else "",
                "p2_ttft_s": "1",
                "p2_decode_s": "7",
                "p2_queue_quality": "engine_process_monotonic" if index == 0 else "",
                "p2_ttft_quality": "same_host_cross_process_monotonic",
                "p2_decode_quality": "engine_process_monotonic",
                "p2_e2e_quality": "api_process_monotonic",
                "p2_prompt_tokens": "1000",
                "p2_generation_tokens": "100",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


if __name__ == "__main__":
    unittest.main()
