from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentmon.reporting.report import build_report
from agentmon.reporting.summary import build_run_summary


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_step_sequence.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_step_sequence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StepSequenceTests(unittest.TestCase):
    def test_step_sequence_projects_request_attributed_moe_routing_summary(self) -> None:
        module = load_module()
        events = sequence_events()
        events.append(
            event(
                "vllm.moe.routing.summary",
                source="vllm_p2",
                mono=ns(3.1),
                payload={
                    "agent_request_id": "model-1",
                    "vllm_request_id": "vllm-1",
                    "status": "COMPLETE",
                    "mode": "summary",
                    "routed_token_count": 12,
                    "assignment_count": 96,
                    "max_expert_share": 0.25,
                    "min_normalized_entropy": 0.8,
                    "max_load_coefficient_of_variation": 0.4,
                    "token_alignment": "MATCH",
                },
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-moe-step"
            run_dir.mkdir()
            write_events(run_dir / "events.jsonl", events)
            analysis = module.build_step_sequence(run_dir)

        model = next(
            item
            for step in analysis["steps"]
            for item in step["items"]
            if item["item_type"] == "model"
        )
        self.assertEqual(model["p2"]["moe_routing_status"], "complete")
        self.assertEqual(model["p2"]["moe_routed_token_count"], 12)
        self.assertEqual(model["p2"]["moe_token_alignment_counts"], {"MATCH": 1})

    def test_step_sequence_reuses_supplied_event_logs(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-seq"
            run_dir.mkdir()
            empty_log = {"events": [], "invalid_lines": 0}

            with patch.object(module, "read_jsonl") as reader:
                analysis = module.build_step_sequence(
                    run_dir,
                    run_event_log=empty_log,
                    service_event_log=empty_log,
                )

            reader.assert_not_called()
            self.assertEqual(analysis["invalid_json_lines"], 0)

    def test_step_sequence_correlates_model_tool_and_p2(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-seq"
            run_dir.mkdir()
            write_events(run_dir / "events.jsonl", sequence_events())

            analysis = module.build_step_sequence(run_dir)
            csv_output = module.format_output(analysis, "csv")
            table_output = module.format_output(analysis, "table")
            report = build_report(run_dir)
            summary = build_run_summary(report)

        self.assertEqual(analysis["run_id"], "run-seq")
        self.assertEqual(analysis["totals"]["step_count"], 1)
        self.assertEqual(analysis["totals"]["model_request_count"], 1)
        self.assertEqual(analysis["totals"]["tool_call_count"], 1)
        self.assertEqual(analysis["totals"]["prompt_tokens"], 10)
        self.assertEqual(analysis["totals"]["completion_tokens"], 3)
        self.assertEqual(analysis["totals"]["tool_nonzero_return_count"], 1)
        step = analysis["steps"][0]
        self.assertEqual(step["step_id"], "step-1")
        self.assertEqual(step["model_request_count"], 1)
        self.assertEqual(step["tool_call_count"], 1)
        model = step["items"][0]
        tool = step["items"][1]
        p2 = model["p2"]
        self.assertEqual(model["item_type"], "model")
        self.assertEqual(p2["vllm_request_id"], "vllm-1")
        self.assertAlmostEqual(p2["e2e_s"], 2.5)
        self.assertAlmostEqual(p2["cache_reuse_rate"], 0.2)
        self.assertAlmostEqual(p2["kv_usage_after"], 0.25)
        self.assertEqual(p2["allocated_block_count"], 8)
        self.assertEqual(p2["kv_snapshot"]["prefix_cache_status"], "enabled")
        self.assertAlmostEqual(p2["kv_snapshot"]["prefix_cache_hit_rate"], 0.5)
        self.assertEqual(p2["kv_snapshot"]["kv_pool_allocated_bytes_per_rank"], 4096)
        self.assertAlmostEqual(step["kv_cache_reuse_rate"], 0.2)
        self.assertAlmostEqual(step["kv_usage_ratio_after_max"], 0.25)
        self.assertAlmostEqual(step["agent_proc"]["cpu_time_s"], 0.45)
        self.assertAlmostEqual(model["vllm_proc"]["cpu_time_s"], 0.3)
        self.assertAlmostEqual(tool["proc"]["cpu_time_s"], 0.25)
        self.assertAlmostEqual(tool["proc"]["sampled_cpu_time_s"], 0.2)
        self.assertEqual(tool["proc"]["cpu_time_quality"], "rusage_children_delta")
        self.assertEqual(step["agent_proc"]["peak_pss_bytes"], 1700)
        self.assertEqual(tool["proc"]["peak_rss_bytes"], 650)
        self.assertEqual(tool["proc"]["peak_pss_bytes"], 550)
        self.assertEqual(tool["proc"]["tool_resource_sample_count"], 3)
        self.assertEqual(tool["proc"]["tool_resource_quality"], "sampled_process_group")
        self.assertEqual(step["npu"]["target_peak_hbm_used_bytes"], 7000)
        self.assertEqual(step["npu"]["target_max_aicore_util_pct"], 60)
        self.assertEqual(step["npu"]["target_max_temperature_c"], 72)
        self.assertEqual(step["npu"]["target_avg_power_w"], 430)
        self.assertEqual(step["npu"]["target_min_aicore_frequency_mhz"], 1400)
        self.assertEqual(step["npu"]["target_max_hbm_bandwidth_bytes_per_s"], 3000)
        self.assertEqual(analysis["resource_attribution"]["ascend_target_devices"], ["0", "1"])
        self.assertEqual(tool["tool_name"], "bash")
        self.assertEqual(tool["returncode"], 1)
        self.assertIn("p2_cache_reuse_rate", csv_output)
        self.assertIn("tool_cpu_time_s", csv_output)
        self.assertIn("step_target_peak_hbm_used_bytes", csv_output)
        self.assertIn("p2_e2e_s", csv_output)
        self.assertIn("p2_exact_ttft_s", csv_output)
        self.assertIn("p2_proxy_ttft_s", csv_output)
        self.assertIn("model_call_id", csv_output)
        self.assertIn("context_prompt_tokens_delta", csv_output)
        self.assertIn("context_compression_removed_tokens", csv_output)
        self.assertIn("p2_scheduler_iteration_count", csv_output)
        self.assertIn("client_model_call_to_server_received_s", csv_output)
        self.assertEqual(model["context_change_type"], "append")
        self.assertEqual(model["context_prompt_tokens"], 10)
        self.assertEqual(model["context_compression_status"], "COMPLETE")
        self.assertEqual(
            model["context_compression_trigger_strategy"],
            "token_ratio",
        )
        self.assertEqual(
            model["context_compression_trigger_reason"],
            "context_reached_token_ratio_trigger",
        )
        self.assertEqual(model["context_compression_removed_tokens"], 100)
        self.assertEqual(model["retry_count"], 1)
        self.assertEqual(p2["prefill_boundary_quality"], "engine_execution_observed")
        self.assertIsNotNone(p2["exact_prefill_s"])
        # This legacy fixture has no clock metadata on first_token and no
        # engine_finished boundary, so it must not be promoted to exact data.
        self.assertIsNone(p2["exact_ttft_s"])
        self.assertIsNone(p2["exact_decode_s"])
        self.assertEqual(p2["scheduler_iteration_count"], 1)
        self.assertEqual(p2["cached_block_count"], 4)
        self.assertEqual(p2["freed_block_count"], 7)
        self.assertEqual(p2["kv_lifecycle_event_count"], 1)
        self.assertEqual(p2["kv_lifecycle_operation_counts"], {"cache": 1})
        self.assertAlmostEqual(p2["client_model_call_to_server_received_s"], 0.1)
        self.assertAlmostEqual(p2["server_finished_to_client_return_s"], 0.1)
        self.assertEqual(step["step_semantic_phase"], "model_then_tool")
        self.assertEqual(report["client_server"]["matched_request_count"], 1)
        self.assertAlmostEqual(
            report["client_server"]["client_model_call_to_server_received"]["mean"],
            0.1,
        )
        self.assertEqual(report["p1_spans"]["workload"]["retry"]["retry_total"], 1)
        compression = report["p1_spans"]["workload"]["context"]["compression"]
        self.assertEqual(compression["applied_request_count"], 1)
        self.assertEqual(compression["removed_tokens_total"], 100)
        request = report["vllm_p2"]["requests"][0]
        self.assertEqual(request["freed_block_count"], 7)
        self.assertEqual(request["kv_lifecycle_operation_counts"], {"cache": 1})
        self.assertEqual(summary["model_retry_total"], 1)
        self.assertEqual(summary["context_compression_applied_count"], 1)
        self.assertAlmostEqual(summary["client_server_match_rate"], 1.0)
        self.assertIn("reuse=20.00%", table_output)
        self.assertIn("agent_cpu=0.450s", table_output)
        self.assertIn("hbm=6.84 KiB", table_output)
        self.assertIn("Step  1 step-1", table_output)

    def test_step_sequence_outputs_stable_request_ids_and_p2_identity_audit(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-p2-audit"
            run_dir.mkdir()
            write_events(
                run_dir / "events.jsonl",
                [
                    *sequence_events(),
                    *p2_request_events("resp-orphan", start_s=5.0),
                ],
            )

            analysis = module.build_step_sequence(run_dir)

        model = next(
            item
            for step in analysis["steps"]
            for item in step["items"]
            if item["item_type"] == "model"
        )
        p2 = model["p2"]
        self.assertEqual(p2["canonical_request_id"], "vllm-1")
        self.assertEqual(p2["request_id"], "vllm-1")
        self.assertEqual(p2["request_id_kind"], "vllm_request_id")
        self.assertIn({"kind": "agent_request_id", "value": "model-1"}, p2["request_id_aliases"])

        audit = analysis["p2_identity_audit"]
        self.assertEqual(audit["p2_request_count"], 2)
        self.assertEqual(audit["matched_count"], 1)
        self.assertEqual(audit["unmatched_p1_count"], 0)
        self.assertEqual(audit["orphan_p2_count"], 1)
        self.assertEqual(audit["matched_request_ids"], ["vllm-1"])
        self.assertEqual(audit["orphan_request_ids"], ["resp-orphan"])
        self.assertEqual(
            audit["orphan_p2_requests"][0]["p2_missing_reason"],
            "p2_request_has_no_matching_p1_model_span",
        )

    def test_step_sequence_falls_back_to_trajectory_and_p2_by_response_id(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-fallback"
            run_dir.mkdir()
            write_events(
                run_dir / "events.jsonl",
                [
                    kv_snapshot_event(ns(9.0)),
                    *p2_request_events("resp-a", start_s=10.0, prompt=100, cached=25, generation=5),
                    *p2_request_events("resp-b", start_s=20.0, prompt=120, cached=60, generation=6),
                ],
            )
            write_trajectory(
                run_dir,
                [
                    trajectory_step("resp-a", "call-a", "pytest -q", returncode=0, tool_timestamp_s=10012.25),
                    trajectory_step("resp-b", "call-b", "git diff", returncode=1, tool_timestamp_s=10022.25),
                ],
            )

            analysis = module.build_step_sequence(run_dir)
            csv_output = module.format_output(analysis, "csv")
            table_output = module.format_output(analysis, "table")

        self.assertEqual(analysis["sequence_source"], "mini_swe_trajectory_p2_fallback")
        self.assertEqual(analysis["totals"]["step_count"], 2)
        self.assertEqual(analysis["totals"]["model_request_count"], 2)
        self.assertEqual(analysis["totals"]["tool_call_count"], 2)
        self.assertEqual(analysis["totals"]["prompt_tokens"], 220)
        self.assertEqual(analysis["totals"]["completion_tokens"], 11)
        first = analysis["steps"][0]
        self.assertEqual(first["step_type"], "agent_step")
        self.assertEqual(first["alignment_status"], "matched")
        self.assertEqual(first["alignment_quality"], "response_id")
        self.assertEqual(first["response_id"], "resp-a")
        self.assertEqual(first["api_request_id"], "resp-a")
        model = first["items"][0]
        tool = first["items"][1]
        self.assertEqual(model["p2"]["api_request_id"], "resp-a")
        self.assertAlmostEqual(model["p2"]["queue_s"], 0.1)
        self.assertAlmostEqual(model["p2"]["ttft_s"], 0.5)
        self.assertAlmostEqual(model["p2"]["decode_s"], 1.0)
        self.assertEqual(model["p2"]["ttft_quality"], "same_host_cross_process_monotonic")
        self.assertEqual(model["p2"]["decode_quality"], "engine_process_monotonic")
        self.assertEqual(model["p2"]["e2e_quality"], "api_process_monotonic")
        self.assertAlmostEqual(model["p2"]["cache_reuse_rate"], 0.25)
        self.assertEqual(model["p2"]["finish_reason"], "stop")
        self.assertEqual(tool["command"], "pytest -q")
        self.assertEqual(tool["returncode"], 0)
        self.assertAlmostEqual(tool["approx_tool_duration_s"], 0.75)
        self.assertIn("alignment_quality", csv_output)
        self.assertIn("approx_tool_duration_s", csv_output)
        self.assertIn("type=agent_step", table_output)
        self.assertIn("api=resp-a", table_output)
        self.assertIn("cmd=pytest -q", table_output)

    def test_step_sequence_outputs_extra_p2_request_without_trajectory_step(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-extra"
            run_dir.mkdir()
            write_events(
                run_dir / "events.jsonl",
                [
                    *p2_request_events("resp-a", start_s=10.0),
                    *p2_request_events("resp-extra", start_s=30.0),
                ],
            )
            write_trajectory(
                run_dir,
                [trajectory_step("resp-a", "call-a", "echo ok", returncode=0, tool_timestamp_s=10012.25)],
            )

            analysis = module.build_step_sequence(run_dir)

        self.assertEqual(analysis["totals"]["step_count"], 2)
        self.assertEqual([step["step_type"] for step in analysis["steps"]], ["agent_step", "model_extra"])
        extra = analysis["steps"][1]
        self.assertEqual(extra["api_request_id"], "resp-extra")
        self.assertEqual(extra["alignment_status"], "p2_without_trajectory_step")
        self.assertEqual(extra["items"][0]["p2"]["api_request_id"], "resp-extra")

    def test_duplicate_api_id_is_reported_as_multiple_without_cross_attribution(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-ambiguous"
            run_dir.mkdir()
            first = p2_request_events("resp-shared", start_s=10.0)
            second = p2_request_events("resp-shared", start_s=20.0)
            for row in second:
                payload = row["payload"]
                if payload.get("vllm_request_id"):
                    payload["vllm_request_id"] = "resp-shared-engine-b"
            write_events(run_dir / "events.jsonl", [*first, *second])
            write_trajectory(
                run_dir,
                [trajectory_step("resp-shared", "call-a", "echo ok", returncode=0, tool_timestamp_s=10022.0)],
            )

            analysis = module.build_step_sequence(run_dir)

        trajectory = next(step for step in analysis["steps"] if step["step_type"] == "agent_step")
        extras = [step for step in analysis["steps"] if step["step_type"] == "model_extra"]
        self.assertEqual(trajectory["alignment_status"], "multiple")
        self.assertEqual(trajectory["items"][0]["p2_match_status"], "multiple")
        self.assertEqual(len(extras), 2)
        self.assertEqual(analysis["totals"]["p2_matched_model_request_count"], 0)
        self.assertIn("ambiguous_multiple_p2_requests_not_attributed", analysis["warnings"])

    def test_step_sequence_uses_ordinal_fallback_only_when_response_id_missing(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-ordinal"
            run_dir.mkdir()
            write_events(run_dir / "events.jsonl", p2_request_events("resp-a", start_s=10.0))
            write_trajectory(
                run_dir,
                [trajectory_step(None, "call-a", "python -m pytest", returncode=0, tool_timestamp_s=10012.25)],
            )

            analysis = module.build_step_sequence(run_dir)

        self.assertEqual(analysis["totals"]["step_count"], 1)
        step = analysis["steps"][0]
        self.assertEqual(step["alignment_status"], "matched")
        self.assertEqual(step["alignment_quality"], "ordinal_fallback")
        self.assertEqual(step["api_request_id"], "resp-a")

    def test_trajectory_fallback_preserves_explicit_timeout_classification(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-timeout"
            run_dir.mkdir()
            write_events(run_dir / "events.jsonl", p2_request_events("resp-timeout", start_s=10.0))
            write_trajectory(
                run_dir,
                [
                    trajectory_step(
                        "resp-timeout",
                        "call-timeout",
                        "python -c 'import time; time.sleep(60)'",
                        returncode=-9,
                        tool_timestamp_s=10041.5,
                        exception_info="Command timed out after 30 seconds",
                    )
                ],
            )

            analysis = module.build_step_sequence(run_dir)

        tool = analysis["steps"][0]["items"][1]
        self.assertTrue(tool["timed_out"])
        self.assertEqual(tool["tool_status"], "timeout")
        self.assertEqual(tool["timeout_limit_s"], 30.0)
        self.assertEqual(tool["exception_type"], "TimeoutExpired")

    def test_trajectory_without_p2_keeps_missing_durations_null(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-no-p2"
            run_dir.mkdir()
            write_events(run_dir / "events.jsonl", [])
            write_trajectory(
                run_dir,
                [trajectory_step("resp-no-p2", "call-no-p2", "echo ok", returncode=0, tool_timestamp_s=10001)],
            )

            analysis = module.build_step_sequence(run_dir)

        self.assertEqual(analysis["totals"]["step_count"], 1)
        self.assertIsNone(analysis["totals"]["model_time_s"])
        self.assertIsNone(analysis["totals"]["tool_time_s"])
        self.assertEqual(analysis["totals"]["prompt_tokens"], 10)
        self.assertEqual(analysis["totals"]["completion_tokens"], 3)


def write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def event(
    event_type: str,
    *,
    source: str = "mini_swe",
    payload: dict | None = None,
    mono: int = 0,
    wall: int | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"{event_type}-{mono}",
        "run_id": "run-seq",
        "source": source,
        "event_type": event_type,
        "quality": "exact",
        "wall_time_ns": mono if wall is None else wall,
        "monotonic_time_ns": mono,
        "labels": {},
        "payload": payload or {},
    }


def ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def epoch_ns(seconds: float) -> int:
    return ns(10000.0 + seconds)


def write_trajectory(run_dir: Path, steps: list[dict]) -> None:
    trajectory_dir = run_dir / "raw" / "trajectory"
    trajectory_dir.mkdir(parents=True)
    messages = []
    for step in steps:
        response = {"created": step["response_created"], "usage": step["usage"]}
        if step["response_id"] is not None:
            response["id"] = step["response_id"]
        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": step["tool_call_id"],
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
                "extra": {
                    "response": response,
                    "actions": [{"command": step["command"]}],
                },
            }
        )
        tool_extra = {
            "returncode": step["returncode"],
            "timestamp": step["tool_timestamp_s"],
            "raw_output": "done\n",
        }
        if step.get("exception_info") is not None:
            tool_extra["exception_info"] = step["exception_info"]
        messages.append({"role": "tool", "tool_call_id": step["tool_call_id"], "extra": tool_extra})
    (trajectory_dir / "mini.traj.json").write_text(json.dumps({"messages": messages}), encoding="utf-8")


def trajectory_step(
    response_id: str | None,
    tool_call_id: str,
    command: str,
    *,
    returncode: int,
    tool_timestamp_s: float,
    exception_info: str | None = None,
) -> dict:
    return {
        "response_id": response_id,
        "response_created": 10000.0,
        "tool_call_id": tool_call_id,
        "command": command,
        "returncode": returncode,
        "tool_timestamp_s": tool_timestamp_s,
        "exception_info": exception_info,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 1},
        },
    }


def kv_snapshot_event(mono: int) -> dict:
    return event(
        "vllm.kv.snapshot",
        source="vllm_p2",
        mono=mono,
        payload={
            "prefix_cache_status": "enabled",
            "prefix_cache_queries": 10,
            "prefix_cache_hits": 5,
            "kv_usage_ratio": 0.2,
        },
    )


def p2_request_events(
    api_request_id: str,
    *,
    start_s: float,
    prompt: int = 10,
    cached: int = 1,
    generation: int = 3,
) -> list[dict]:
    vllm_request_id = f"{api_request_id}-engine"
    return [
        event(
            "vllm.request.received",
            source="vllm_p2",
            mono=ns(start_s),
            wall=epoch_ns(start_s),
            payload={
                **p2_clock(100),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "received_ns": ns(start_s),
            },
        ),
        event(
            "vllm.request.queued",
            source="vllm_p2",
            mono=ns(start_s + 0.1),
            wall=epoch_ns(start_s + 0.1),
            payload={
                **p2_clock(200),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "queued_ns": ns(start_s + 0.1),
            },
        ),
        event(
            "vllm.request.first_scheduled",
            source="vllm_p2",
            mono=ns(start_s + 0.2),
            wall=epoch_ns(start_s + 0.2),
            payload={
                **p2_clock(200),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "first_scheduled_ns": ns(start_s + 0.2),
            },
        ),
        event(
            "vllm.request.first_token",
            source="vllm_p2",
            mono=ns(start_s + 0.5),
            wall=epoch_ns(start_s + 0.5),
            payload={
                **p2_clock(200),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "first_token_ns": ns(start_s + 0.5),
                "first_token_timestamp_source": "engine_core_output",
                "first_token_timestamp_quality": "engine_process_monotonic",
            },
        ),
        event(
            "vllm.request.engine_finished",
            source="vllm_p2",
            mono=ns(start_s + 1.5),
            wall=epoch_ns(start_s + 1.5),
            payload={
                **p2_clock(200),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "engine_finished_ns": ns(start_s + 1.5),
                "engine_finished_timestamp_source": "engine_core_output",
                "engine_finished_timestamp_quality": "engine_process_monotonic",
            },
        ),
        event(
            "vllm.request.finished",
            source="vllm_p2",
            mono=ns(start_s + 1.5),
            wall=epoch_ns(start_s + 1.5),
            payload={
                **p2_clock(100),
                "api_request_id": api_request_id,
                "vllm_request_id": vllm_request_id,
                "finished_ns": ns(start_s + 1.5),
                "prompt_tokens": prompt,
                "cached_prompt_tokens": cached,
                "computed_prefill_tokens": prompt - cached,
                "generation_tokens": generation,
                "finish_reason": "stop",
            },
        ),
    ]


def p2_clock(pid: int) -> dict:
    return {
        "source_pid": pid,
        "source_host": "test-host",
        "clock_domain": "test-host:CLOCK_MONOTONIC",
    }


def sequence_events() -> list[dict]:
    return [
        event(
            "agent_step.start",
            mono=ns(0.0),
            payload={"span_id": "step-1", "span_type": "agent_step", "start_mono_ns": ns(0.0)},
        ),
        event(
            "model_request.start",
            mono=ns(0.1),
            payload={
                **p2_clock(300),
                "span_id": "model-1",
                "span_type": "model_request",
                "step_id": "step-1",
                "parent_span_id": "step-1",
                "start_mono_ns": ns(0.1),
                "model_name": "qwen",
                "agentmon_model_request_id": "model-1",
                "request_id": "model-1",
                "context_message_count": 4,
                "context_message_delta": 2,
                "context_input_chars": 100,
                "context_input_chars_delta": 40,
                "context_token_estimate": 25,
                "context_token_estimate_delta": 10,
                "context_token_estimate_quality": "estimated_chars_div_4",
                "context_change_type": "append",
                "context_compression_status": "COMPLETE",
                "context_compression_trigger_strategy": "token_ratio",
                "context_compression_trigger_reason": (
                    "context_reached_token_ratio_trigger"
                ),
                "context_compression_strategy": "recent_groups",
                "context_compression_applied": True,
                "context_compression_original_tokens": 120,
                "context_compression_compressed_tokens": 20,
                "context_compression_removed_tokens": 100,
                "context_compression_target_tokens": 20,
                "context_compression_target_achieved": True,
                "context_compression_measurement_quality": "exact_test_counter",
                "context_compression_ratio": 1 / 6,
                "context_compression_limitations": ["semantic_summary_not_generated"],
                "context_compression_prohibited_conclusions": [
                    "size_reduction_does_not_prove_semantic_equivalence"
                ],
                "tool_result_message_count": 1,
                "tool_result_chars": 20,
                "tool_result_tokens_estimated": 5,
                "tool_result_token_quality": "estimated_chars_div_4",
                "agent_semantic_phase": "post_tool_reasoning",
            },
        ),
        event(
            "vllm.adapter.capability",
            source="vllm_p2",
            mono=ns(0.12),
            payload={"visible_devices": ["0", "1"]},
        ),
        event(
            "vllm.kv.snapshot",
            source="vllm_p2",
            mono=ns(0.15),
            payload={
                "prefix_cache_status": "enabled",
                "prefix_cache_queries": 4,
                "prefix_cache_hits": 2,
                "kv_usage_ratio": 0.20,
                "kv_pool": {
                    "allocated_pool_bytes_per_rank": 4096,
                    "bytes_per_block_per_rank": 512,
                    "source_quality": "complete",
                },
            },
        ),
        event(
            "vllm.request.received",
            source="vllm_p2",
            mono=ns(0.2),
            payload={
                **p2_clock(100),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "correlation_quality": "direct",
                "received_ns": ns(0.2),
            },
        ),
        event(
            "vllm.request.queued",
            source="vllm_p2",
            mono=ns(0.25),
            payload={
                **p2_clock(200),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "queued_ns": ns(0.25),
            },
        ),
        event(
            "vllm.request.first_scheduled",
            source="vllm_p2",
            mono=ns(0.3),
            payload={
                **p2_clock(200),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "first_scheduled_ns": ns(0.3),
            },
        ),
        event(
            "vllm.request.scheduler_iteration",
            source="vllm_p2",
            mono=ns(0.31),
            payload={
                **p2_clock(200),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "scheduler_iteration_id": 1,
                "scheduler_phase": "prefill",
                "scheduled_token_count": 10,
                "prefill_completion_candidate": True,
                "batch_request_count": 2,
                "mixed_prefill_decode_batch": True,
            },
        ),
        event(
            "vllm.scheduler.iteration.scheduled",
            source="vllm_p2",
            mono=ns(0.31),
            payload={
                **p2_clock(200),
                "scheduler_iteration_id": 1,
                "batch_request_count": 2,
                "prefill_request_count": 1,
                "decode_request_count": 1,
                "mixed_prefill_decode_batch": True,
                "continuous_batching_observed": True,
                "scheduled_tokens": 11,
                "running_requests_after_schedule": 2,
                "waiting_requests_after_schedule": 0,
            },
        ),
        event(
            "vllm.request.prefill_completed",
            source="vllm_p2",
            mono=ns(0.9),
            payload={
                **p2_clock(200),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "prefill_completed_ns": ns(0.9),
                "prefill_boundary_source": "scheduler.update_from_output",
                "prefill_boundary_quality": "engine_execution_observed",
            },
        ),
        proc_event(
            ns(0.2),
            [
                proc_sample(pid=100, role="agent", starttime=10, utime=10, stime=5, rss=1000, pss=900),
                proc_sample(pid=200, role="vllm", starttime=20, utime=100, stime=50, rss=10000, pss=9000),
            ],
        ),
        ascend_event(
            ns(0.2),
            [
                {"device_id": "0", "hbm_used_bytes": 1000, "aicore_util_pct": 10, "temperature_c": 60, "power_w": 200, "aicore_frequency_mhz": 1500, "hbm_bandwidth_bytes_per_s": 1000},
                {"device_id": "1", "hbm_used_bytes": 2000, "aicore_util_pct": 20, "temperature_c": 62, "power_w": 210, "aicore_frequency_mhz": 1450, "hbm_bandwidth_bytes_per_s": 1200},
            ],
        ),
        event(
            "vllm.request.first_token",
            source="vllm_p2",
            mono=ns(1.0),
            payload={
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "correlation_quality": "direct",
                "first_token_ns": ns(1.0),
            },
        ),
        proc_event(
            ns(2.6),
            [
                proc_sample(pid=100, role="agent", starttime=10, utime=15, stime=6, rss=1200, pss=1000),
                proc_sample(pid=200, role="vllm", starttime=20, utime=120, stime=60, rss=12000, pss=10000),
            ],
        ),
        ascend_event(
            ns(2.6),
            [
                {"device_id": "0", "hbm_used_bytes": 3000, "aicore_util_pct": 50, "temperature_c": 70, "power_w": 220, "aicore_frequency_mhz": 1400, "hbm_bandwidth_bytes_per_s": 1400},
                {"device_id": "1", "hbm_used_bytes": 4000, "aicore_util_pct": 60, "temperature_c": 72, "power_w": 230, "aicore_frequency_mhz": 1420, "hbm_bandwidth_bytes_per_s": 1600},
            ],
        ),
        event(
            "vllm.kv.block_event",
            source="vllm_p2",
            mono=ns(2.0),
            payload={
                "request_id": "vllm-1",
                "vllm_request_id": "vllm-1",
                "allocated_block_count": 8,
                "cached_block_count": 4,
                "operation": "cache",
                "freed_block_count": 7,
                "evicted_block_count": 1,
                "kv_usage_before": 0.20,
                "kv_usage_after": 0.25,
                "kv_quality": "exact",
            },
        ),
        event(
            "vllm.request.finished",
            source="vllm_p2",
            mono=ns(2.7),
            payload={
                **p2_clock(100),
                "agent_request_id": "model-1",
                "vllm_request_id": "vllm-1",
                "correlation_quality": "direct",
                "finished_ns": ns(2.7),
                "prompt_tokens": 10,
                "cached_prompt_tokens": 2,
                "recomputed_prompt_tokens": 1,
                "generation_tokens": 3,
            },
        ),
        event(
            "model_request.end",
            mono=ns(2.8),
            payload={
                **p2_clock(300),
                "span_id": "model-1",
                "span_type": "model_request",
                "step_id": "step-1",
                "parent_span_id": "step-1",
                "end_mono_ns": ns(2.8),
                "duration_ns": ns(2.7),
                "status": "success",
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cached_tokens": 1},
                "context_prompt_tokens": 10,
                "context_prompt_tokens_delta": 2,
                "transport_attempt_count": 2,
                "retry_count": 1,
                "retry_observation_quality": "exact_instrumented_litellm_transport",
            },
        ),
        event(
            "tool_call.start",
            mono=ns(3.0),
            payload={
                "span_id": "tool-1",
                "tool_call_id": "tool-1",
                "span_type": "tool_call",
                "step_id": "step-1",
                "parent_span_id": "step-1",
                "start_mono_ns": ns(3.0),
                "tool_name": "bash",
                "executable": "bash",
                "command_hash": "abc",
            },
        ),
        proc_event(
            ns(3.05),
            [
                proc_sample(pid=100, role="agent", starttime=10, utime=20, stime=8, rss=1300, pss=1100),
                proc_sample(pid=300, role="agent", starttime=30, utime=0, stime=0, rss=400, pss=300, pgid=3000),
            ],
        ),
        event(
            "tool_call.end",
            mono=ns(3.4),
            payload={
                "span_id": "tool-1",
                "tool_call_id": "tool-1",
                "span_type": "tool_call",
                "step_id": "step-1",
                "parent_span_id": "step-1",
                "end_mono_ns": ns(3.4),
                "duration_ns": ns(0.4),
                "status": "failed",
                "returncode": 1,
                "tool_status": "nonzero_return",
                "cpu_time_s": 0.25,
                "cpu_user_time_s": 0.20,
                "cpu_system_time_s": 0.05,
                "cpu_time_quality": "rusage_children_delta",
                "tool_resource_sample_count": 3,
                "tool_resource_sample_interval_s": 0.1,
                "tool_resource_quality": "sampled_process_group",
                "tool_pss_quality": "exact",
                "tool_peak_rss_bytes": 650,
                "tool_peak_pss_bytes": 550,
                "tool_read_bytes_delta": 24,
                "tool_write_bytes_delta": 12,
                "pid": 300,
                "pgid": 3000,
                "output_bytes": 12,
                "attribution_quality": "exact_pid_boundary",
            },
        ),
        proc_event(
            ns(3.35),
            [
                proc_sample(pid=100, role="agent", starttime=10, utime=30, stime=10, rss=1400, pss=1200),
                proc_sample(pid=300, role="agent", starttime=30, utime=20, stime=0, rss=600, pss=500, pgid=3000),
            ],
        ),
        event(
            "agent_step.end",
            mono=ns(3.5),
            payload={
                "span_id": "step-1",
                "span_type": "agent_step",
                "end_mono_ns": ns(3.5),
                "duration_ns": ns(3.5),
                "status": "success",
                "step_semantic_phase": "model_then_tool",
                "step_action_count": 1,
            },
        ),
    ]


def proc_event(mono: int, processes: list[dict]) -> dict:
    return event(
        "sample",
        source="proc",
        mono=mono,
        payload={
            "processes": processes,
            "aggregate": {"agent": {}, "vllm": {}, "clk_tck": 100},
        },
    )


def proc_sample(
    *,
    pid: int,
    role: str,
    starttime: int,
    utime: int,
    stime: int,
    rss: int,
    pss: int,
    pgid: int | None = None,
) -> dict:
    return {
        "pid": pid,
        "role": role,
        "starttime_ticks": starttime,
        "pgid": pgid if pgid is not None else pid,
        "utime_ticks": utime,
        "stime_ticks": stime,
        "rss_bytes": rss,
        "pss_bytes": pss,
        "read_bytes": 0,
        "write_bytes": 0,
    }


def ascend_event(mono: int, devices: list[dict]) -> dict:
    return event(
        "sample",
        source="ascend",
        mono=mono,
        payload={
            "status": "supported",
            "parser_confidence": 0.8,
            "devices": devices,
        },
    )
