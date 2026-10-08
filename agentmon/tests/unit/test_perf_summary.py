import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "agentmon_perf_summary.py"
SPEC = importlib.util.spec_from_file_location("agentmon_perf_summary", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
perf_summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(perf_summary)


class PerfSummaryTests(unittest.TestCase):
    def test_perf_summary_uses_canonical_proc_and_ascend_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_report(
                run_dir,
                {
                    "run_id": "run-perf",
                    "status": "success",
                    "exit_code": 0,
                    "duration_s": 12.5,
                    "data_quality": {"proc": "complete", "npu": "complete"},
                    "agent": {"status": "complete", "message_count": 2},
                    "p1_spans": {"status": "complete", "agent_steps": 1, "model_requests": 1, "tool_calls": 1},
                    "proc": {
                        "status": "complete",
                        "sample_count": 3,
                        "agent_process_count_last": 0,
                        "agent_process_count_max": 2,
                        "agent_process_count_active_last": 1,
                        "agent_peak_pss_bytes": 123,
                        "agent_peak_rss_bytes": 456,
                        "agent_pss_quality": "exact",
                    },
                    "ascend": {
                        "status": "complete",
                        "sample_count": 2,
                        "parser_confidence": 0.9,
                        "hbm_quality": "sampled",
                        "aicore_quality": "sampled",
                        "devices": {
                            "0": {"peak_hbm_used_bytes": 2048, "max_aicore_util_pct": 2.0},
                            "1": {"peak_hbm_used_bytes": 2048, "max_aicore_util_pct": 2.0},
                            "4": {"peak_hbm_used_bytes": 1024, "max_aicore_util_pct": 87.5},
                        },
                    },
                    "vllm": {"status": "complete", "sample_count": 2, "counter_deltas": {"x": 1}},
                    "kv_cache": {
                        "prefix_cache_status": "disabled",
                        "prefix_cache_hit_rate": None,
                        "prefix_cache_hit_rate_quality": "disabled",
                    },
                },
            )

            summary = perf_summary.build_perf_summary(run_dir, ascend_devices="0,1")
            table = perf_summary.format_summary(summary, "table")
            csv_text = perf_summary.format_summary(summary, "csv")
            json_text = perf_summary.format_summary(summary, "json")

        self.assertEqual(summary["proc_agent_peak_pss_bytes"], 123)
        self.assertEqual(summary["proc_agent_peak_rss_bytes"], 456)
        self.assertEqual(summary["proc_agent_process_count_last"], 0)
        self.assertEqual(summary["proc_agent_process_count_max"], 2)
        self.assertEqual(summary["proc_agent_process_count_active_last"], 1)
        self.assertEqual(summary["ascend_target_devices"], ["0", "1"])
        self.assertEqual(summary["ascend_target_attribution_status"], "complete")
        self.assertEqual(summary["ascend_peak_hbm_used_bytes"], 2048)
        self.assertEqual(summary["ascend_max_aicore_util_pct"], 2.0)
        self.assertEqual(summary["ascend_host_peak_hbm_used_bytes"], 2048)
        self.assertEqual(summary["ascend_host_max_aicore_util_pct"], 87.5)
        self.assertEqual(summary["ascend_hbm_quality"], "sampled")
        self.assertEqual(summary["ascend_aicore_quality"], "sampled")
        self.assertIsNone(summary["kv_cache_hit_rate"])
        self.assertIn("proc_agent_peak_pss_bytes | 123", table)
        self.assertIn("ascend_peak_hbm_used_bytes | 2048", table)
        self.assertIn("ascend_host_max_aicore_util_pct | 87.5", table)
        self.assertIn("proc_agent_peak_pss_bytes,123", csv_text)
        self.assertEqual(json.loads(json_text)["ascend_peak_hbm_used_bytes"], 2048)

    def test_perf_summary_without_target_devices_keeps_target_metrics_na(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_report(
                run_dir,
                {
                    "run_id": "run-perf",
                    "status": "success",
                    "agent": {},
                    "p1_spans": {},
                    "proc": {},
                    "ascend": {
                        "status": "complete",
                        "devices": {
                            "0": {"peak_hbm_used_bytes": 2048, "max_aicore_util_pct": 2.0},
                            "4": {"peak_hbm_used_bytes": 1024, "max_aicore_util_pct": 87.5},
                        },
                    },
                    "vllm": {},
                    "kv_cache": {},
                    "data_quality": {},
                },
            )

            summary = perf_summary.build_perf_summary(run_dir)
            table = perf_summary.format_summary(summary, "table")

        self.assertIsNone(summary["ascend_peak_hbm_used_bytes"])
        self.assertIsNone(summary["ascend_max_aicore_util_pct"])
        self.assertEqual(summary["ascend_host_max_aicore_util_pct"], 87.5)
        self.assertEqual(summary["ascend_target_attribution_status"], "unavailable")
        self.assertIn("ascend_peak_hbm_used_bytes | N/A", table)

    def test_perf_summary_reads_service_scoped_p2_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            write_report(
                run_dir,
                {
                    "run_id": "run-p2",
                    "status": "success",
                    "exit_code": 0,
                    "agent": {},
                    "p1_spans": {},
                    "proc": {},
                    "ascend": {},
                    "vllm": {},
                    "kv_cache": {},
                    "data_quality": {},
                },
            )
            p2_events = Path(tmp) / "p2-events.jsonl"
            write_jsonl(
                p2_events,
                [
                    p2_event("vllm.request.received", {"vllm_request_id": "req-1", "received_ns": 1_000}),
                    p2_event("vllm.request.queued", {"vllm_request_id": "req-1", "queued_ns": 2_000}),
                    p2_event("vllm.request.first_scheduled", {"vllm_request_id": "req-1", "first_scheduled_ns": 5_000, "prompt_tokens": 10}),
                    p2_event(
                        "vllm.request.finished",
                        {
                            "vllm_request_id": "req-1",
                            "finished_ns": 11_000,
                            "generation_tokens": 3,
                            "cached_prompt_tokens": 1,
                            "preemption_count": 0,
                        },
                    ),
                    p2_event(
                        "vllm.kv.snapshot",
                        {
                            "prefix_cache_status": "disabled",
                            "prefix_cache_queries": None,
                            "prefix_cache_hits": None,
                            "kv_usage_ratio": 0.25,
                        },
                    ),
                ],
            )

            summary = perf_summary.build_perf_summary(
                run_dir,
                p2_events=p2_events,
                p2_attribution_quality="service_exclusive",
            )
            table = perf_summary.format_summary(summary, "table")

        self.assertEqual(summary["p2_correlation_scope"], "service_stream")
        self.assertEqual(summary["p2_attribution_quality"], "service_exclusive")
        self.assertEqual(summary["p2_request_count"], 1)
        self.assertEqual(summary["p2_prompt_tokens_total"], 10)
        self.assertEqual(summary["p2_cached_prompt_tokens_total"], 1)
        self.assertEqual(summary["p2_generation_tokens_total"], 3)
        self.assertEqual(summary["p2_kv_usage_peak"], 0.25)
        self.assertEqual(summary["p2_prefix_cache_status"], "disabled")
        self.assertIsNone(summary["p2_prefix_cache_hit_rate"])
        self.assertIn("p2_prefix_cache_hit_rate | N/A", table)
        self.assertIsNone(summary["agent_prompt_tokens"])
        self.assertEqual(summary["agent_token_source"], "unavailable")
        self.assertIn("p2_attribution_not_exclusive", summary["agent_token_missing_reason"])

    def test_perf_summary_p2_service_exclusive_can_backfill_agent_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            write_report(
                run_dir,
                {
                    "run_id": "run-p2",
                    "status": "success",
                    "agent": {"prompt_tokens": None, "completion_tokens": None, "cached_tokens": None},
                    "p1_spans": {},
                    "proc": {},
                    "ascend": {},
                    "vllm": {},
                    "kv_cache": {},
                    "data_quality": {},
                },
            )
            p2_events = Path(tmp) / "p2-events.jsonl"
            write_jsonl(
                p2_events,
                [
                    p2_event("vllm.request.received", {"vllm_request_id": "req-1", "received_ns": 1_000}),
                    p2_event("vllm.request.finished", {"vllm_request_id": "req-1", "finished_ns": 2_000, "prompt_tokens": 10, "generation_tokens": 3}),
                ],
            )

            summary = perf_summary.build_perf_summary(run_dir, p2_events=p2_events, p2_service_exclusive=True)

        self.assertEqual(summary["agent_prompt_tokens"], 10)
        self.assertEqual(summary["agent_completion_tokens"], 3)
        self.assertEqual(summary["agent_token_source"], "p2_service_exclusive")
        self.assertEqual(summary["agent_token_quality"], "inferred_service_exclusive")


def write_report(run_dir: Path, report: dict[str, object]) -> None:
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def p2_event(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": f"id-{event_type}-{len(json.dumps(payload, sort_keys=True))}",
        "run_id": "p2-stream",
        "source": "vllm_p2",
        "event_type": event_type,
        "wall_time_ns": 1,
        "monotonic_time_ns": 1,
        "quality": "exact",
        "labels": {},
        "payload": payload,
    }
