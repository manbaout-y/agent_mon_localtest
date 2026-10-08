import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentmon.cli import main as agentmon_main
from agentmon.events import Event
from agentmon.reporting.report import (
    build_report,
    build_report_quality_bundle,
    is_jsonl_only_trajectory_event,
    read_event_log,
    write_reports,
)
from agentmon.storage.jsonl import JsonlWriter
from agentmon.storage.sqlite import SqliteEventWriter
from agentmon.trajectory.mini_swe import summarize_trajectory


class P1ReportTests(unittest.TestCase):
    def test_write_reports_reuses_supplied_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "r", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            event_log = {
                "events": [],
                "total_lines": 0,
                "valid_lines": 0,
                "invalid_lines": 0,
                "path": str(run_dir / "events.jsonl"),
            }

            with patch("agentmon.reporting.report.read_event_log") as reader:
                report = write_reports(run_dir, event_log=event_log)

            reader.assert_not_called()
            self.assertEqual(report["event_count"], 0)

    def test_write_reports_emits_quality_bundle_and_blocks_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "r", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text('{"invalid":\n', encoding="utf-8")

            report = write_reports(run_dir)

            expected = {
                "report.json",
                "report.txt",
                "report.csv",
                "summary.json",
                "metric_definition.json",
                "data_quality.json",
                "confidence.json",
                "limitations.json",
                "validation_checks.json",
            }
            self.assertTrue(all((run_dir / name).exists() for name in expected))
            checks = json.loads((run_dir / "validation_checks.json").read_text(encoding="utf-8"))

        self.assertEqual(report["analysis_publication"]["status"], "UNPUBLISHABLE")
        self.assertIn("invalid_json_lines", checks["blocking_failures"])

    def test_default_report_p2_quality_gates_do_not_block_when_unconfigured(self) -> None:
        quality_bundle = build_report_quality_bundle(
            {
                "run_id": "r",
                "status": "success",
                "duration_s": 1.0,
                "event_log": {
                    "jsonl_invalid_lines": 0,
                    "duplicate_event_ids": 0,
                    "p0_jsonl_sqlite_mismatch": False,
                    "event_count_mismatch": False,
                },
                "p1_spans": {
                    "model_requests": 1,
                    "open_span_count": 0,
                    "abandoned_span_count": 0,
                    "negative_duration_count": 0,
                    "orphan_end_count": 0,
                },
                "vllm_p2": {
                    "status": "complete",
                    "request_count": 1,
                    "lifecycle_complete_rate": 1.0,
                },
                "client_server": {
                    "match_rate": 1.0,
                    "ambiguous_identifier_count": 0,
                },
                "semantic_integrity": {
                    "foreign_run_event_count": 0,
                    "span_outside_run_count": 0,
                    "invalid_token_value_count": 0,
                    "model_name_count": 1,
                },
            }
        )
        checks = quality_bundle["validation_checks"]
        checks_by_name = {check["name"]: check for check in checks["checks"]}

        self.assertEqual(checks["publishability_status"], "PUBLISHABLE")
        self.assertTrue(checks_by_name["p2_lifecycle_complete"]["passed"])
        self.assertTrue(checks_by_name["request_match_rate"]["passed"])
        self.assertIsNone(checks_by_name["p2_lifecycle_complete"]["details"]["minimum_required"])
        self.assertIsNone(checks_by_name["request_match_rate"]["details"]["minimum_required"])

    def test_quality_bundle_blocks_abandoned_spans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "r",
                        "status": "success",
                        "end_mono_ns": 2000,
                        "data_quality": {},
                    }
                ),
                encoding="utf-8",
            )
            event = {
                "event_id": "1",
                "run_id": "r",
                "source": "mini_swe",
                "event_type": "model_request.start",
                "wall_time_ns": 1,
                "monotonic_time_ns": 1000,
                "quality": "exact",
                "labels": {},
                "payload": {
                    "span_id": "model-1",
                    "span_type": "model_request",
                    "start_mono_ns": 1000,
                },
            }
            (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            report = write_reports(run_dir)
            checks = json.loads((run_dir / "validation_checks.json").read_text(encoding="utf-8"))

        self.assertEqual(report["p1_spans"]["abandoned_span_count"], 1)
        self.assertIn("abandoned_spans", checks["blocking_failures"])
        self.assertEqual(checks["publishability_status"], "UNPUBLISHABLE")

    def test_mini_swe_trajectory_schema_extracts_response_usage_once(self) -> None:
        summary = summarize_trajectory(
            {
                "trajectory_format": "mini-swe-agent-1.1",
                "info": {"mini_version": "2.4.3", "exit_status": "Submitted"},
                "messages": [
                    {
                        "role": "assistant",
                        "extra": {
                            "response": {
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 3,
                                    "prompt_tokens_details": {"cached_tokens": 2},
                                }
                            }
                        },
                    },
                    {"role": "assistant", "response": {"usage": {"input_tokens": 5, "output_tokens": 4}}},
                ],
            }
        )

        self.assertEqual(summary["prompt_tokens"], 15)
        self.assertEqual(summary["completion_tokens"], 7)
        self.assertEqual(summary["cached_tokens"], 2)

    def test_trajectory_prompt_completion_survive_missing_cached_tokens(self) -> None:
        summary = summarize_trajectory(
            {
                "trajectory_format": "mini-swe-agent-1.1",
                "messages": [{"role": "assistant", "usage": {"prompt_tokens": 10, "completion_tokens": 3}}],
            }
        )

        self.assertEqual(summary["prompt_tokens"], 10)
        self.assertEqual(summary["completion_tokens"], 3)
        self.assertIsNone(summary["cached_tokens"])

    def test_event_log_skips_invalid_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                '{"event_id":"1","run_id":"r","source":"x","event_type":"sample","wall_time_ns":1,"monotonic_time_ns":1,"quality":"exact","payload":{}}\n'
                '{"broken":\n',
                encoding="utf-8",
            )

            log = read_event_log(tmp)

        self.assertEqual(log["valid_lines"], 1)
        self.assertEqual(log["invalid_lines"], 1)

    def test_report_marks_unclosed_span_abandoned_after_run_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "r",
                        "status": "failed",
                        "end_mono_ns": 2000,
                        "data_quality": {},
                    }
                ),
                encoding="utf-8",
            )
            event = {
                "event_id": "1",
                "run_id": "r",
                "source": "mini_swe",
                "event_type": "tool_call.start",
                "wall_time_ns": 1,
                "monotonic_time_ns": 1000,
                "quality": "exact",
                "labels": {},
                "payload": {
                    "trace_id": "r",
                    "span_id": "tool-1",
                    "parent_span_id": "step-1",
                    "span_type": "tool_call",
                    "step_id": "step-1",
                    "start_mono_ns": 1000,
                },
            }
            (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            report = build_report(run_dir)

        self.assertEqual(report["p1_spans"]["abandoned_span_count"], 1)
        self.assertEqual(report["p1_spans"]["tool_calls"], 1)
        self.assertEqual(report["p1_spans"]["spans"][0]["status"], "abandoned")
        self.assertTrue(report["p1_spans"]["spans"][0]["duration_estimated"])

    def test_event_log_compares_sqlite_against_p0_events_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            run_dir = output_root / "r"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "r", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            p0_event = Event(run_id="r", source="controller", event_type="lifecycle", quality="exact")
            trajectory_error = Event(
                run_id="r",
                source="trajectory",
                event_type="error",
                quality="exact",
                payload={"message": "no trajectory file found", "status": "missing"},
            )
            jsonl_only_events = [
                Event(
                    run_id="r",
                    source="mini_swe",
                    event_type="tool_call.start",
                    quality="exact",
                    payload={
                        "span_id": "tool-1",
                        "span_type": "tool_call",
                        "start_mono_ns": 100,
                    },
                ),
                Event(
                    run_id="r",
                    source="mini_swe",
                    event_type="tool_call.end",
                    quality="exact",
                    payload={
                        "span_id": "tool-1",
                        "span_type": "tool_call",
                        "start_mono_ns": 100,
                        "end_mono_ns": 200,
                        "duration_ns": 100,
                    },
                ),
                *[
                    Event(run_id="r", source="mini_swe", event_type=event_type, quality="exact")
                    for event_type in (
                        "model_request.attempt.start",
                        "model_request.attempt.end",
                        "model_request.attempt.error",
                        "agent.action_batch.start",
                        "agent.action_batch.end",
                        "agent.action_batch.error",
                    )
                ],
                Event(
                    run_id="r",
                    source="trajectory",
                    event_type="sample",
                    quality="exact",
                    payload={
                        "raw_ref": "raw/trajectory/mini.traj.json",
                        "source_path": "/workspace/mini.traj.json",
                        "summary": {"status": "complete"},
                    },
                ),
                Event(
                    run_id="r",
                    source="vllm_p2",
                    event_type="vllm.adapter.capability",
                    quality="exact",
                    payload={"status": "supported", "details": {}},
                ),
            ]
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(p0_event)
                writer.write_event(trajectory_error)
                for event in jsonl_only_events:
                    writer.write_event(event)
            sqlite = SqliteEventWriter(run_dir / "metrics.sqlite3")
            sqlite.write_event(p0_event)
            sqlite.write_event(trajectory_error)
            sqlite.close()

            report = write_reports(run_dir)
            checks = json.loads(
                (run_dir / "validation_checks.json").read_text(encoding="utf-8")
            )
            with redirect_stdout(io.StringIO()):
                summary_exit = agentmon_main(
                    [
                        "summary",
                        "--output-root",
                        str(output_root),
                        "r",
                        "--format",
                        "json",
                        "--strict",
                    ]
                )

        event_log = report["event_log"]
        self.assertEqual(event_log["jsonl_total_count"], 12)
        self.assertEqual(event_log["jsonl_p0_count"], 2)
        self.assertEqual(event_log["jsonl_p1_count"], 8)
        self.assertEqual(event_log["jsonl_p2_count"], 1)
        self.assertEqual(event_log["jsonl_trajectory_count"], 1)
        self.assertEqual(event_log["sqlite_p0_count"], 2)
        self.assertEqual(event_log["p0_jsonl_sqlite_difference"], 0)
        self.assertFalse(event_log["event_count_mismatch"])
        self.assertEqual(checks["publishability_status"], "PUBLISHABLE")
        self.assertEqual(summary_exit, 0)

    def test_analysis_state_uses_post_run_trajectory_import_without_overwriting_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "r",
                        "status": "success",
                        "data_quality": {},
                        "finalization": {
                            "trajectory_status": "NOT_CONFIGURED",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(
                    Event(
                        run_id="r",
                        source="trajectory",
                        event_type="sample",
                        quality="estimated",
                        payload={
                            "raw_ref": "raw/trajectory/mini.traj.json",
                            "source_path": "/workspace/mini.traj.json",
                            "summary": {"status": "complete"},
                        },
                    )
                )

            report = write_reports(run_dir)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(report["finalization"]["trajectory_status"], "NOT_CONFIGURED")
        self.assertEqual(report["analysis_state"]["trajectory"]["status"], "IMPORTED")
        self.assertEqual(report["analysis_state"]["trajectory"]["runtime_finalization_status"], "NOT_CONFIGURED")
        self.assertEqual(report["analysis_state"]["trajectory"]["post_run_import_status"], "IMPORTED")
        self.assertEqual(summary["analysis_trajectory_status"], "IMPORTED")
        self.assertEqual(summary["runtime_finalization_trajectory_status"], "NOT_CONFIGURED")
        self.assertEqual(summary["post_run_trajectory_import_status"], "IMPORTED")

    def test_trajectory_jsonl_only_classifier_requires_import_summary_evidence(self) -> None:
        self.assertFalse(
            is_jsonl_only_trajectory_event(
                {
                    "source": "trajectory",
                    "event_type": "error",
                    "payload": {"status": "missing"},
                }
            )
        )
        self.assertFalse(
            is_jsonl_only_trajectory_event(
                {
                    "source": "trajectory",
                    "event_type": "sample",
                    "payload": {"summary": {}},
                }
            )
        )
        self.assertTrue(
            is_jsonl_only_trajectory_event(
                {
                    "source": "trajectory",
                    "event_type": "sample",
                    "payload": {
                        "raw_ref": "raw/trajectory/mini.traj.json",
                        "source_path": "/workspace/mini.traj.json",
                        "summary": {},
                    },
                }
            )
        )

    def test_trajectory_summary_written_by_run_sink_does_not_create_false_p0_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            run_dir = output_root / "r"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "r", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            p0_event = Event(run_id="r", source="controller", event_type="lifecycle", quality="exact")
            trajectory_summary = Event(
                run_id="r",
                source="trajectory",
                event_type="sample",
                quality="estimated",
                payload={
                    "raw_ref": "raw/trajectory/mini.traj.json",
                    "source_path": "/workspace/mini.traj.json",
                    "summary": {"status": "complete"},
                },
            )
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(p0_event)
                writer.write_event(trajectory_summary)
            sqlite = SqliteEventWriter(run_dir / "metrics.sqlite3")
            sqlite.write_event(p0_event)
            sqlite.write_event(trajectory_summary)
            sqlite.close()

            report = write_reports(run_dir)
            with redirect_stdout(io.StringIO()):
                summary_exit = agentmon_main(
                    [
                        "summary",
                        "--output-root",
                        str(output_root),
                        "r",
                        "--format",
                        "json",
                        "--strict",
                    ]
                )

        event_log = report["event_log"]
        self.assertEqual(event_log["jsonl_total_count"], 2)
        self.assertEqual(event_log["jsonl_p0_count"], 2)
        self.assertEqual(event_log["jsonl_trajectory_count"], 0)
        self.assertEqual(event_log["sqlite_p0_count"], 2)
        self.assertEqual(event_log["p0_jsonl_sqlite_difference"], 0)
        self.assertFalse(event_log["event_count_mismatch"])
        self.assertEqual(report["analysis_publication"]["status"], "PUBLISHABLE")
        self.assertEqual(summary_exit, 0)

    def test_agent_summary_falls_back_to_p1_model_request_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(json.dumps({"run_id": "r", "status": "success"}), encoding="utf-8")
            events = [
                {
                    "event_id": "1",
                    "run_id": "r",
                    "source": "trajectory",
                    "event_type": "sample",
                    "wall_time_ns": 1,
                    "monotonic_time_ns": 1,
                    "quality": "exact",
                    "payload": {"summary": {"status": "complete", "prompt_tokens": None, "completion_tokens": None}},
                },
                {
                    "event_id": "2",
                    "run_id": "r",
                    "source": "mini_swe",
                    "event_type": "model_request.end",
                    "wall_time_ns": 2,
                    "monotonic_time_ns": 2,
                    "quality": "exact",
                    "payload": {
                        "span_id": "model-1",
                        "span_type": "model_request",
                        "status": "success",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 3,
                            "prompt_tokens_details": {"cached_tokens": 2},
                        },
                    },
                },
            ]
            (run_dir / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

            report = build_report(run_dir)

        self.assertEqual(report["agent"]["prompt_tokens"], 10)
        self.assertEqual(report["agent"]["completion_tokens"], 3)
        self.assertEqual(report["agent"]["cached_tokens"], 2)
        self.assertEqual(report["agent"]["token_source"], "p1_model_request")
        self.assertEqual(report["agent"]["token_quality"], "exact")

    def test_agent_summary_marks_missing_token_source_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(json.dumps({"run_id": "r", "status": "success"}), encoding="utf-8")
            event = {
                "event_id": "1",
                "run_id": "r",
                "source": "trajectory",
                "event_type": "sample",
                "wall_time_ns": 1,
                "monotonic_time_ns": 1,
                "quality": "exact",
                "payload": {"summary": {"status": "complete", "prompt_tokens": None, "completion_tokens": None}},
            }
            (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            report = build_report(run_dir)

        self.assertIsNone(report["agent"]["prompt_tokens"])
        self.assertEqual(report["agent"]["token_source"], "unavailable")
        self.assertEqual(report["agent"]["token_quality"], "unavailable")
        self.assertIn("trajectory_usage_fields_absent", report["agent"]["token_missing_reason"])

    def test_proc_summary_keeps_last_max_and_active_last_process_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(json.dumps({"run_id": "r", "status": "success"}), encoding="utf-8")
            events = [
                proc_sample("1", process_count=1, pss=100, rss=200),
                proc_sample("2", process_count=3, pss=300, rss=400),
                proc_sample("3", process_count=0, pss=0, rss=0),
            ]
            (run_dir / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

            report = build_report(run_dir)

        self.assertEqual(report["proc"]["agent_process_count_last"], 0)
        self.assertEqual(report["proc"]["agent_process_count_max"], 3)
        self.assertEqual(report["proc"]["agent_process_count_active_last"], 3)
        self.assertEqual(report["proc"]["agent_peak_pss_bytes"], 300)

    def test_ascend_summary_marks_unparsed_metrics_partial_not_zero_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(json.dumps({"run_id": "r", "status": "success"}), encoding="utf-8")
            event = {
                "event_id": "1",
                "run_id": "r",
                "source": "ascend",
                "event_type": "sample",
                "wall_time_ns": 1,
                "monotonic_time_ns": 1,
                "quality": "sampled",
                "payload": {
                    "status": "supported",
                    "parser_confidence": 0.6,
                    "devices": [{"device_id": "0"}],
                },
            }
            (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            report = build_report(run_dir)

        self.assertEqual(report["ascend"]["status"], "partial")
        self.assertEqual(report["ascend"]["hbm_quality"], "unavailable")
        self.assertEqual(report["ascend"]["aicore_quality"], "unavailable")
        self.assertIsNone(report["ascend"]["devices"]["0"]["peak_hbm_used_bytes"])
        self.assertIsNone(report["ascend"]["devices"]["0"]["max_aicore_util_pct"])


def proc_sample(event_id: str, *, process_count: int, pss: int, rss: int) -> dict:
    return {
        "event_id": event_id,
        "run_id": "r",
        "source": "proc",
        "event_type": "sample",
        "wall_time_ns": int(event_id),
        "monotonic_time_ns": int(event_id),
        "quality": "sampled",
        "payload": {
            "aggregate": {
                "agent": {
                    "process_count": process_count,
                    "pss_bytes": pss,
                    "rss_bytes": rss,
                    "pss_quality": "exact",
                }
            }
        },
    }
