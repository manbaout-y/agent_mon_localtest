import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.reporting.summary import build_aggregate, build_run_summary, percentile


class SummaryTests(unittest.TestCase):
    def test_success_summary_keeps_prefix_disabled_as_na(self) -> None:
        summary = build_run_summary(make_report(prefix_status="disabled"))

        self.assertEqual(summary["schema_version"], "1.0")
        self.assertTrue(summary["task_success"])
        self.assertTrue(summary["data_integrity_pass"])
        self.assertEqual(summary["run_status"], "success")
        self.assertEqual(summary["agent_exit_status"], "submitted")
        self.assertEqual(summary["model_requests"], 2)
        self.assertEqual(summary["tool_calls"], 2)
        self.assertEqual(summary["instrumentation_error_count"], 0)
        self.assertEqual(summary["command_nonzero_count"], 1)
        self.assertIsNone(summary["expected_test_failure_count"])
        self.assertEqual(summary["submission_control_flow_count"], 0)
        self.assertEqual(summary["tool_error_count"], 0)
        self.assertEqual(summary["command_nonzero_return_count"], 1)
        self.assertIsNone(summary["prefix_cache_hit_rate"])
        self.assertEqual(summary["prefix_cache_hit_rate_quality"], "disabled")
        self.assertEqual(summary["npu_peak_hbm_bytes"], 123)
        self.assertEqual(summary["npu_max_aicore_util_pct"], 80.0)
        self.assertEqual(summary["npu_hbm_quality"], "sampled")
        self.assertEqual(summary["npu_aicore_quality"], "sampled")
        self.assertEqual(summary["token_source"], "trajectory")
        self.assertEqual(summary["strict_failures"], [])

    def test_failed_run_and_integrity_issues_are_strict_failures(self) -> None:
        report = make_report(status="failed")
        report["event_log"].update(
            {
                "jsonl_invalid_count": 1,
                "duplicate_event_ids": 1,
                "p0_jsonl_sqlite_difference": 2,
                "event_count_mismatch": True,
            }
        )
        report["p1_spans"].update(
            {
                "open_span_count": 1,
                "abandoned_span_count": 1,
                "orphan_end_count": 1,
                "negative_duration_count": 1,
            }
        )

        summary = build_run_summary(report)

        self.assertFalse(summary["task_success"])
        self.assertFalse(summary["data_integrity_pass"])
        self.assertIn("run_status", summary["strict_failures"])
        self.assertIn("invalid_json", summary["strict_failures"])
        self.assertIn("duplicate_event_ids", summary["strict_failures"])
        self.assertIn("open_spans", summary["strict_failures"])
        self.assertIn("abandoned_spans", summary["strict_failures"])
        self.assertIn("orphan_end_spans", summary["strict_failures"])
        self.assertIn("negative_durations", summary["strict_failures"])
        self.assertIn("p0_jsonl_sqlite_mismatch", summary["strict_failures"])

    def test_null_npu_metrics_remain_null(self) -> None:
        report = make_report()
        report["ascend"] = {
            "status": "partial",
            "parser_confidence": None,
            "hbm_quality": "unavailable",
            "aicore_quality": "unavailable",
            "devices": {"0": {"peak_hbm_used_bytes": None, "max_aicore_util_pct": None}},
        }

        summary = build_run_summary(report)

        self.assertIsNone(summary["npu_peak_hbm_bytes"])
        self.assertIsNone(summary["npu_max_aicore_util_pct"])
        self.assertIsNone(summary["npu_parser_confidence"])
        self.assertEqual(summary["npu_quality"], "partial")
        self.assertEqual(summary["npu_hbm_quality"], "unavailable")
        self.assertEqual(summary["npu_aicore_quality"], "unavailable")

    def test_aggregate_uses_percentiles_and_coverage(self) -> None:
        reports = [
            make_report(run_id="r1", duration_s=1.0, prompt_tokens=10, generation_tokens=5, pss=100, kv=0.1),
            make_report(run_id="r2", duration_s=2.0, prompt_tokens=20, generation_tokens=10, pss=200, kv=0.2),
            make_report(run_id="r3", duration_s=100.0, prompt_tokens=None, generation_tokens=None, pss=None, kv=None),
        ]
        summaries = [build_run_summary(report) for report in reports]

        aggregate = build_aggregate(summaries, reports=reports)

        self.assertEqual(aggregate["run_count"], 3)
        self.assertEqual(aggregate["success_count"], 3)
        self.assertEqual(aggregate["task_success_count"], 3)
        self.assertEqual(aggregate["data_integrity_pass_count"], 3)
        self.assertEqual(aggregate["duration"]["p50"], 2.0)
        self.assertAlmostEqual(aggregate["duration"]["p90"], 80.4)
        self.assertEqual(aggregate["prompt_tokens"]["total"], 30)
        self.assertEqual(aggregate["prompt_tokens"]["sample_count"], 2)
        self.assertAlmostEqual(aggregate["prompt_tokens"]["coverage"], 2 / 3)
        self.assertEqual(aggregate["peak_pss_bytes"]["max"], 200)
        self.assertEqual(aggregate["kv_cache_usage_ratio_max"]["sample_count"], 2)
        self.assertEqual(aggregate["command_nonzero_count"]["total"], 3)
        self.assertEqual(aggregate["instrumentation_error_count"]["total"], 0)

    def test_percentile_empty_returns_none(self) -> None:
        self.assertIsNone(percentile([], 95))

    def test_summary_cli_outputs_json_csv_table_and_strict_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_report(tmp, make_report(run_id="run-ok"))

            json_exit, json_output = run_cli("summary", "--output-root", tmp, "run-ok", "--format", "json")
            csv_exit, csv_output = run_cli("summary", "--output-root", tmp, "run-ok", "--format", "csv")
            table_exit, table_output = run_cli("summary", "--output-root", tmp, "run-ok", "--format", "table")

        self.assertEqual(json_exit, 0)
        self.assertEqual(json.loads(json_output)["run_id"], "run-ok")
        self.assertEqual(csv_exit, 0)
        self.assertIn("run_id,label,task_success,data_integrity_pass", csv_output)
        self.assertEqual(table_exit, 0)
        self.assertIn("run_status", table_output)

        with tempfile.TemporaryDirectory() as tmp:
            write_report(tmp, make_report(run_id="run-failed", status="failed"))
            strict_exit, _ = run_cli("summary", "--output-root", tmp, "run-failed", "--strict")

        self.assertEqual(strict_exit, 1)

    def test_aggregate_cli_handles_empty_dir_and_missing_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_exit, empty_output = run_cli("aggregate", "--runs-root", tmp, "--format", "json")
            self.assertEqual(empty_exit, 0)
            self.assertEqual(json.loads(empty_output)["run_count"], 0)

            write_report(tmp, make_report(run_id="run-ok", label="keep"))
            (Path(tmp) / "missing-report").mkdir()
            agg_exit, agg_output = run_cli(
                "aggregate",
                "--runs-root",
                tmp,
                "--label",
                "keep",
                "--format",
                "json",
            )

        aggregate = json.loads(agg_output)
        self.assertEqual(agg_exit, 0)
        self.assertEqual(aggregate["run_count"], 1)
        self.assertEqual(len(aggregate["skipped_runs"]), 1)

    def test_aggregate_cli_csv_table_and_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_report(tmp, make_report(run_id="run-ok"))
            write_report(tmp, make_report(run_id="run-bad", status="failed"))

            csv_exit, csv_output = run_cli("aggregate", "--runs-root", tmp, "--format", "csv")
            table_exit, table_output = run_cli("aggregate", "--runs-root", tmp, "--format", "table")
            strict_exit, _ = run_cli("aggregate", "--runs-root", tmp, "--strict")

        self.assertEqual(csv_exit, 0)
        self.assertIn("run_count,success_count,failure_count", csv_output)
        self.assertEqual(table_exit, 0)
        self.assertIn("success_rate", table_output)
        self.assertEqual(strict_exit, 1)


def run_cli(*args: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = agentmon_main(list(args))
    return exit_code, stdout.getvalue()


def write_report(root: str, report: dict) -> Path:
    run_dir = Path(root) / report["run_id"]
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return run_dir


def make_report(
    *,
    run_id: str = "run-1",
    label: str | None = "calculator",
    status: str = "success",
    duration_s: float = 10.0,
    prefix_status: str = "enabled",
    prompt_tokens=100,
    generation_tokens=50,
    pss=4096,
    kv=0.4,
) -> dict:
    prefix_hit_rate = 0.75 if prefix_status == "enabled" else None
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "exit_code": 0 if status == "success" else 1,
        "duration_s": duration_s,
        "manifest": {
            "run_id": run_id,
            "label": label,
            "status": status,
            "prefix_caching_status": prefix_status,
            "start_wall_ns": 1_700_000_000_000_000_000,
        },
        "data_quality": {"proc": "complete", "vllm": "complete", "npu": "complete", "trajectory": "complete"},
        "agent": {
            "exit_status": "submitted",
            "mini_version": "2.4.3",
            "api_calls": 2,
            "assistant_turns": 3,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": generation_tokens,
            "cached_tokens": 7,
            "token_source": "trajectory" if prompt_tokens is not None or generation_tokens is not None else None,
            "token_quality": "exact" if prompt_tokens is not None or generation_tokens is not None else "unavailable",
            "token_missing_reason": None if prompt_tokens is not None or generation_tokens is not None else "trajectory_usage_fields_absent;p1_usage_fields_absent;p2_attribution_not_exclusive",
            "instance_cost": 0.12,
        },
        "event_log": {
            "jsonl_invalid_count": 0,
            "duplicate_event_ids": 0,
            "p0_jsonl_sqlite_difference": 0,
            "event_count_mismatch": False,
        },
        "p1_spans": {
            "status": "complete",
            "agent_steps": 2,
            "model_requests": 2,
            "tool_calls": 2,
            "open_span_count": 0,
            "abandoned_span_count": 0,
            "orphan_end_count": 0,
            "negative_duration_count": 0,
            "spans": [
                {"span_type": "model_request", "status": "success", "duration_s": 0.1},
                {"span_type": "model_request", "status": "success", "duration_s": 0.3},
                {"span_type": "tool_call", "status": "success", "duration_s": 1.0},
                {"span_type": "tool_call", "status": "failed", "duration_s": 2.0},
            ],
        },
        "tool_attribution": {
            "tool_calls": 2,
            "tool_failures": 1,
            "tools": [{"returncode": 0}, {"returncode": 2}],
        },
        "proc": {
            "agent_process_count_last": 0,
            "agent_process_count_max": 1 if pss is not None else None,
            "agent_process_count_active_last": 1 if pss is not None else None,
            "agent_peak_pss_bytes": pss,
            "agent_peak_rss_bytes": 8192 if pss is not None else None,
        },
        "kv_cache": {
            "prefix_cache_status": prefix_status,
            "prefix_cache_hit_rate": prefix_hit_rate,
            "prefix_cache_hit_rate_quality": "sampled" if prefix_status == "enabled" else "disabled",
            "kv_cache_usage_ratio_max": kv,
        },
        "vllm": {
            "counter_deltas": {
                "vllm:request_success_total": 4,
                "vllm:preemption_total": 1,
            }
        },
        "ascend": {
            "status": "complete",
            "parser_confidence": 0.9,
            "hbm_quality": "sampled",
            "aicore_quality": "sampled",
            "devices": {"0": {"peak_hbm_used_bytes": 123, "max_aicore_util_pct": 80.0}},
        },
        "vllm_p2": {
            "status": "complete",
            "request_count": 2,
            "capabilities": [{"visible_devices": ["0"], "visible_devices_source": "test"}],
        },
    }
