import json
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agentmon.adapters.mini_swe.agent import InstrumentedAgentMixin
from agentmon.adapters.mini_swe.context import AgentMonContext
from agentmon.adapters.mini_swe.environment import (
    InstrumentedEnvironment,
    InstrumentedLocalEnvironment,
    MiniSweLocalExecutionMixin,
    ToolProcessObserver,
)
from agentmon.adapters.mini_swe.event_sink import AdapterEventSink
from agentmon.adapters.mini_swe.model import InstrumentedModel, InstrumentedModelMixin
from agentmon.adapters.mini_swe.runtime_control import (
    RuntimeControlledLocalEnvironment,
    RuntimeControlledModel,
    RuntimeControlRejected,
)
from agentmon.config import load_config
from agentmon.reporting.report import build_report


class FakeModel:
    model_name = "fake-model"

    def __init__(self) -> None:
        self.kwargs = None

    def query(self, messages, **kwargs):
        self.kwargs = kwargs
        return {"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}


class FakeHeaderModel(FakeModel):
    def query(self, messages, *, extra_headers=None, **kwargs):
        self.kwargs = {"extra_headers": extra_headers, **kwargs}
        return {"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}


class FakeEnvironment:
    def execute(self, action):
        return {"returncode": 0, "stdout": "ok", "stderr": ""}


class BaseAgent:
    n_calls = 0
    exit_status = "submitted"

    def run(self):
        return "run-ok"

    def step(self):
        self.n_calls += 1
        return "step-ok"

    def query(self, **kwargs):
        return kwargs

    def execute_actions(self, actions):
        return actions

    def serialize(self):
        return {"info": {}}


class Submitted(Exception):
    def __init__(self) -> None:
        super().__init__("submitted")
        self.messages = [
            {
                "role": "assistant",
                "content": "hidden submission",
                "extra": {"exit_status": "Submitted"},
            }
        ]


class SubmittedAgent(BaseAgent):
    def step(self):
        self.n_calls += 1
        raise Submitted()


class FakeInstrumentedAgent(InstrumentedAgentMixin, BaseAgent):
    pass


class FakeSubmittedInstrumentedAgent(InstrumentedAgentMixin, SubmittedAgent):
    pass


class RetryAwareBaseModel:
    model_name = "retry-aware"

    def __init__(self) -> None:
        self.transport_calls = 0

    def query(self, messages, **kwargs):
        for attempt in range(2):
            try:
                return self._query(messages, **kwargs)
            except RuntimeError:
                if attempt == 1:
                    raise
        raise AssertionError("unreachable")

    def _query(self, messages, **kwargs):
        self.transport_calls += 1
        if self.transport_calls == 1:
            raise RuntimeError("transient")
        return {
            "extra": {
                "response": {
                    "id": "chatcmpl-workload",
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 7,
                        "total_tokens": 127,
                    },
                }
            }
        }


class RetryAwareInstrumentedModel(InstrumentedModelMixin, RetryAwareBaseModel):
    pass


class WorkloadBaseAgent(BaseAgent):
    def __init__(self) -> None:
        self.n_calls = 0
        self.n_consecutive_format_errors = 0
        self.messages = [{"role": "user", "content": "task"}]

    def step(self):
        self.n_calls += 1
        message = {
            "role": "assistant",
            "content": "",
            "extra": {"actions": [{"type": "bash"}, {"type": "bash"}]},
        }
        self.messages.append(message)
        return self.execute_actions(message)

    def execute_actions(self, message):
        outputs = [
            {"role": "tool", "content": "one"},
            {"role": "tool", "content": "two"},
        ]
        self.messages.extend(outputs)
        return outputs


class WorkloadInstrumentedAgent(InstrumentedAgentMixin, WorkloadBaseAgent):
    pass


class FakeMiniSweLocalEnvironment(MiniSweLocalExecutionMixin):
    def __init__(self, tmp: str, sink: AdapterEventSink) -> None:
        self.config = SimpleNamespace(cwd=tmp, env={}, timeout=5)
        self.agentmon_sink = sink
        self.checked_result = None

    def _check_finished(self, output):
        output.get("output", "")
        self.checked_result = output


class FakeMiniSweLocalEnvironmentWithFailingChecker(FakeMiniSweLocalEnvironment):
    def _check_finished(self, output):
        self.checked_result = output
        raise RuntimeError("checker stopped execution")


class FakeMiniSweLocalEnvironmentWithSubmittedChecker(FakeMiniSweLocalEnvironment):
    def _check_finished(self, output):
        self.checked_result = output
        raise Submitted()


class P1AdapterTests(unittest.TestCase):
    def make_sink(self, tmp: str) -> AdapterEventSink:
        context = AgentMonContext(
            run_id="run-p1",
            run_dir=Path(tmp),
            events_path=Path(tmp) / "events.jsonl",
            step_id="step-existing",
        )
        return AdapterEventSink(context)

    def read_events(self, tmp: str):
        return [
            json.loads(line)
            for line in (Path(tmp) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_runtime_controlled_model_rejects_when_gate_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(config_path=write_runtime_control_config(Path(tmp), control_mode="enforce"))
            model = RuntimeControlledModel(
                FakeModel(),
                cfg,
                current_inflight_requests=12,
            )

            with self.assertRaises(RuntimeControlRejected):
                model.query([{"role": "user", "content": "hello"}])

    def test_runtime_controlled_local_environment_executes_through_tool_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(config_path=write_runtime_control_config(Path(tmp), control_mode="enforce"))
            env = RuntimeControlledLocalEnvironment(cfg)

            result = env.execute([sys.executable, "-c", "print('mini-swe-runtime')"])

            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["output"], "mini-swe-runtime\n")
            self.assertTrue(result["runtime_control"]["accepted"])
            self.assertEqual(result["runtime_control"]["lease"]["pool_id"], "default")

    def test_instrumented_model_emits_span_and_injects_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            fake = FakeHeaderModel()
            model = InstrumentedModel(fake, sink=sink)

            response = model.query([{"role": "user", "content": "hello"}])
            sink.close()

            self.assertEqual(response["usage"]["total_tokens"], 7)
            self.assertIn("extra_headers", fake.kwargs)
            self.assertEqual(fake.kwargs["extra_headers"]["X-AgentMon-Run-Id"], "run-p1")
            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("model_request.start", event_types)
            self.assertIn("model_request.end", event_types)
            self.assertNotIn("span_start", event_types)
            span_start = next(event for event in events if event["event_type"] == "model_request.start")
            span_end = next(event for event in events if event["event_type"] == "model_request.end")
            self.assertEqual(span_start["payload"]["trace_id"], "run-p1")
            self.assertEqual(span_end["payload"]["span_id"], span_start["payload"]["span_id"])
            self.assertLess(span_start["payload"]["sequence"], span_end["payload"]["sequence"])
            start = next(event for event in events if event["event_type"] == "model_request.start")
            self.assertEqual(start["payload"]["message_count"], 1)
            self.assertEqual(start["payload"]["input_chars"], 5)
            self.assertNotIn("hello", json.dumps(start))

    def test_header_injection_skips_var_keyword_only_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            fake = FakeModel()
            model = InstrumentedModel(fake, sink=sink)

            model.query([{"role": "user", "content": "hello"}])
            sink.close()

            self.assertNotIn("extra_headers", fake.kwargs)
            start = next(event for event in self.read_events(tmp) if event["event_type"] == "model_request.start")
            self.assertFalse(start["payload"]["headers_injected"])

    def test_model_workload_tracks_context_tool_results_and_transport_retries_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            model = RetryAwareInstrumentedModel()
            model.agentmon_sink = sink
            messages = [
                {"role": "system", "content": "system secret"},
                {"role": "user", "content": "task secret"},
                {"role": "assistant", "content": "", "extra": {"actions": [{"command": "hidden"}]}},
                {"role": "tool", "content": "tool secret output"},
            ]

            response = model.query(messages)
            sink.close()

            self.assertEqual(response["extra"]["response"]["id"], "chatcmpl-workload")
            events = self.read_events(tmp)
            start = next(event for event in events if event["event_type"] == "model_request.start")
            end = next(event for event in events if event["event_type"] == "model_request.end")
            self.assertEqual(start["payload"]["tool_result_message_count"], 1)
            self.assertGreater(start["payload"]["tool_result_tokens_estimated"], 0)
            self.assertEqual(start["payload"]["tool_result_token_quality"], "estimated_chars_div_4")
            self.assertEqual(start["payload"]["agent_semantic_phase"], "post_tool_reasoning")
            self.assertEqual(end["payload"]["usage"]["prompt_tokens"], 120)
            self.assertEqual(end["payload"]["context_prompt_tokens"], 120)
            self.assertEqual(end["payload"]["transport_attempt_count"], 2)
            self.assertEqual(end["payload"]["retry_count"], 1)
            self.assertEqual(end["payload"]["retry_observation_quality"], "exact_instrumented_litellm_transport")
            attempts = [event for event in events if event["event_type"].startswith("model_request.attempt.")]
            self.assertEqual([event["payload"]["retry_index"] for event in attempts if event["event_type"].endswith("start")], [0, 1])
            serialized = json.dumps(events)
            self.assertNotIn("system secret", serialized)
            self.assertNotIn("task secret", serialized)
            self.assertNotIn("tool secret output", serialized)
            self.assertNotIn("hidden", serialized)

    def test_model_workload_detects_structural_truncation_and_explicit_summarization_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            fake = FakeModel()
            model = InstrumentedModel(fake, sink=sink)
            first = [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
                {"role": "tool", "content": "t"},
            ]
            second = [
                {"role": "system", "content": "summary", "extra": {"summarization_applied": True}},
                {"role": "user", "content": "u2"},
            ]

            model.query(first)
            model.query(second)
            sink.close()

            starts = [event for event in self.read_events(tmp) if event["event_type"] == "model_request.start"]
            current = starts[-1]["payload"]
            self.assertEqual(current["context_change_type"], "truncation")
            self.assertTrue(current["context_truncation_detected"])
            self.assertTrue(current["context_summarization_detected"])
            self.assertGreater(current["context_removed_message_count"], 0)

    def test_instrumented_environment_decorator_does_not_store_action_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = InstrumentedEnvironment(FakeEnvironment(), sink=sink)

            result = env.execute("echo secret")
            sink.close()

            self.assertEqual(result["returncode"], 0)
            events = self.read_events(tmp)
            payload = next(event for event in events if event["event_type"] == "tool_call.start")["payload"]
            self.assertIn("command_hash", payload)
            self.assertNotIn("echo secret", json.dumps(events))

    def test_instrumented_local_environment_records_pid_pgid_and_output_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = InstrumentedLocalEnvironment(cwd=tmp, sink=sink)

            result = env.execute("printf hi")
            sink.close()

            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["stdout"], "hi")
            events = self.read_events(tmp)
            process_start = next(event for event in events if event["event_type"] == "tool_call.process_start")
            tool_end = next(event for event in events if event["event_type"] == "tool_call.end")
            self.assertIsInstance(process_start["payload"]["pid"], int)
            self.assertIsInstance(process_start["payload"]["pgid"], int)
            self.assertEqual(process_start["payload"]["tool_name"], "printf")
            self.assertNotIn("printf hi", json.dumps(events))
            self.assertEqual(tool_end["payload"]["stdout_bytes"], 2)
            self.assertEqual(tool_end["payload"]["attribution_quality"], "exact_pid_boundary")
            self.assertEqual(tool_end["payload"]["cpu_time_quality"], "rusage_children_delta")
            self.assertGreaterEqual(tool_end["payload"]["cpu_time_s"], 0.0)
            self.assertAlmostEqual(
                tool_end["payload"]["cpu_time_s"],
                tool_end["payload"]["cpu_user_time_s"] + tool_end["payload"]["cpu_system_time_s"],
            )
            self.assertIn("tool_resource_sample_count", tool_end["payload"])
            self.assertIn(tool_end["payload"]["tool_resource_quality"], {"sampled_process_group", "unavailable"})

    def test_tool_process_observer_aggregates_process_group_memory_and_io(self) -> None:
        stats = {
            10: {"pid": 10, "ppid": 1, "pgrp": 10, "starttime_ticks": 100, "rss_bytes": 100},
            11: {"pid": 11, "ppid": 10, "pgrp": 10, "starttime_ticks": 110, "rss_bytes": 200},
        }
        io_values = {
            10: [{"read_bytes": 100, "write_bytes": 20}, {"read_bytes": 130, "write_bytes": 30}],
            11: [{"read_bytes": 50, "write_bytes": 5}, {"read_bytes": 70, "write_bytes": 15}],
        }
        calls = {10: 0, 11: 0}

        def fake_io(pid):
            index = calls[pid]
            calls[pid] += 1
            return io_values[pid][index]

        with patch.dict("os.environ", {"AGENTMON_TOOL_SAMPLE_INTERVAL_SECONDS": "0"}), patch(
            "agentmon.adapters.mini_swe.environment.list_proc_stats",
            return_value=stats,
        ), patch(
            "agentmon.adapters.mini_swe.environment.read_smaps_rollup",
            side_effect=lambda pid: {"Pss": 50 if pid == 10 else 100},
        ), patch(
            "agentmon.adapters.mini_swe.environment.read_proc_io",
            side_effect=fake_io,
        ):
            observer = ToolProcessObserver(10, 10)
            observer._sample()
            observer._sample()
            summary = observer.summary()

        self.assertEqual(summary["tool_resource_sample_count"], 2)
        self.assertEqual(summary["tool_peak_rss_bytes"], 300)
        self.assertEqual(summary["tool_peak_pss_bytes"], 150)
        self.assertEqual(summary["tool_read_bytes_delta"], 50)
        self.assertEqual(summary["tool_write_bytes_delta"], 20)
        self.assertEqual(summary["tool_resource_quality"], "sampled_process_group")

    def test_instrumented_local_environment_classifies_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = InstrumentedLocalEnvironment(cwd=tmp, sink=sink, timeout=0.01)

            result = env.execute([sys.executable, "-c", "import time; time.sleep(1)"])
            sink.close()

            self.assertTrue(result["timed_out"])
            tool_end = next(event for event in self.read_events(tmp) if event["event_type"] == "tool_call.end")
            payload = tool_end["payload"]
            self.assertEqual(payload["tool_status"], "timeout")
            self.assertEqual(payload["timeout_limit_s"], 0.01)
            self.assertIn(payload["termination_signal"], {15, 9})
            self.assertEqual(payload["exception_type"], "TimeoutExpired")

    def test_instrumented_local_environment_keeps_nonzero_as_tool_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = InstrumentedLocalEnvironment(cwd=tmp, sink=sink)

            result = env.execute([sys.executable, "-c", "raise SystemExit(7)"])
            sink.close()

            self.assertEqual(result["returncode"], 7)
            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("tool_call.end", event_types)
            self.assertNotIn("tool_call.error", event_types)
            payload = next(event for event in events if event["event_type"] == "tool_call.end")["payload"]
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["tool_status"], "nonzero_return")
            attribution = build_report(Path(tmp))["tool_attribution"]
            self.assertEqual(attribution["command_nonzero_return_count"], 1)
            self.assertEqual(attribution["tool_timeout_count"], 0)
            self.assertEqual(attribution["tool_instrumentation_error_count"], 0)

    def test_mini_swe_local_environment_passes_result_mapping_to_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = FakeMiniSweLocalEnvironment(tmp, sink)

            result = env.execute({"command": "printf hi"})
            sink.close()

            self.assertIs(env.checked_result, result)
            self.assertEqual(result["output"], "hi")
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["exception_info"], "")
            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("tool_call.end", event_types)
            self.assertNotIn("tool_call.error", event_types)
            tool_start = next(event for event in events if event["event_type"] == "tool_call.start")
            process_start = next(event for event in events if event["event_type"] == "tool_call.process_start")
            tool_end = next(event for event in events if event["event_type"] == "tool_call.end")
            self.assertIn("command_hash", tool_start["payload"])
            self.assertIsInstance(process_start["payload"]["pid"], int)
            self.assertIsInstance(process_start["payload"]["pgid"], int)
            self.assertIsInstance(tool_end["payload"]["pid"], int)
            self.assertIsInstance(tool_end["payload"]["pgid"], int)
            self.assertIn("pid_starttime_ticks", tool_end["payload"])
            self.assertEqual(tool_end["payload"]["output_bytes"], 2)
            self.assertFalse(tool_end["payload"]["timed_out"])
            self.assertEqual(tool_end["payload"]["returncode"], 0)
            self.assertEqual(tool_end["payload"]["cpu_time_quality"], "rusage_children_delta")
            self.assertGreaterEqual(tool_end["payload"]["cpu_time_s"], 0.0)

    def test_mini_swe_local_environment_preserves_checker_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = FakeMiniSweLocalEnvironmentWithFailingChecker(tmp, sink)

            with self.assertRaisesRegex(RuntimeError, "checker stopped execution"):
                env.execute({"command": "printf hi"})
            sink.close()

            self.assertIsInstance(env.checked_result, dict)
            events = self.read_events(tmp)
            self.assertIn("tool_call.error", [event["event_type"] for event in events])
            tool_error = next(event for event in events if event["event_type"] == "tool_call.error")
            self.assertEqual(tool_error["payload"]["status"], "error")
            self.assertEqual(tool_error["payload"]["error_type"], "RuntimeError")
            attribution = build_report(Path(tmp))["tool_attribution"]
            self.assertEqual(attribution["tool_instrumentation_error_count"], 1)
            self.assertEqual(attribution["command_nonzero_return_count"], 0)

    def test_mini_swe_local_environment_treats_submitted_checker_as_control_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            env = FakeMiniSweLocalEnvironmentWithSubmittedChecker(tmp, sink)

            with self.assertRaises(Submitted):
                env.execute({"command": "printf hi"})
            sink.close()

            self.assertIsInstance(env.checked_result, dict)
            self.assertEqual(env.checked_result["output"], "hi")
            self.assertEqual(env.checked_result["returncode"], 0)
            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("tool_call.end", event_types)
            self.assertNotIn("tool_call.error", event_types)
            tool_end = next(event for event in events if event["event_type"] == "tool_call.end")
            self.assertEqual(tool_end["payload"]["status"], "submitted")
            self.assertEqual(tool_end["payload"]["terminal_reason"], "submitted")
            self.assertEqual(tool_end["payload"]["returncode"], 0)
            self.assertNotIn("hidden submission", json.dumps(events))

    def test_agent_mixin_emits_spans_and_adds_serialize_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            agent = FakeInstrumentedAgent()
            agent.agentmon_sink = sink
            agent.agentmon_current_step_id = None

            self.assertEqual(agent.run(), "run-ok")
            self.assertEqual(agent.step(), "step-ok")
            serialized = agent.serialize()
            sink.close()

            self.assertEqual(serialized["info"]["agentmon"]["run_id"], "run-p1")
            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("run.start", event_types)
            self.assertIn("run.end", event_types)
            self.assertIn("agent_step.start", event_types)
            self.assertIn("agent_step.end", event_types)
            step_start = next(event for event in events if event["event_type"] == "agent_step.start")
            self.assertEqual(step_start["payload"]["parent_span_id"], "run:run-p1")

    def test_agent_step_records_semantic_phase_action_fanout_and_path_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            agent = WorkloadInstrumentedAgent()
            agent.agentmon_sink = sink
            agent.agentmon_current_step_id = None

            result = agent.step()
            sink.close()

            self.assertEqual(len(result), 2)
            events = self.read_events(tmp)
            step_end = next(event for event in events if event["event_type"] == "agent_step.end")
            self.assertEqual(step_end["payload"]["step_semantic_phase"], "model_then_tool")
            self.assertEqual(step_end["payload"]["step_action_count"], 2)
            self.assertTrue(step_end["payload"]["path_expansion_observed"])
            self.assertEqual(step_end["payload"]["path_expansion_count"], 1)
            action_start = next(event for event in events if event["event_type"] == "agent.action_batch.start")
            action_end = next(event for event in events if event["event_type"] == "agent.action_batch.end")
            self.assertEqual(action_start["payload"]["action_types"], ["bash", "bash"])
            self.assertEqual(action_end["payload"]["observation_message_count"], 2)

    def test_agent_step_treats_submitted_exception_as_control_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            agent = FakeSubmittedInstrumentedAgent()
            agent.agentmon_sink = sink
            agent.agentmon_current_step_id = None

            with self.assertRaises(Submitted):
                agent.step()
            sink.close()

            events = self.read_events(tmp)
            event_types = [event["event_type"] for event in events]
            self.assertIn("agent_step.end", event_types)
            self.assertNotIn("agent_step.error", event_types)
            step_end = next(event for event in events if event["event_type"] == "agent_step.end")
            self.assertEqual(step_end["payload"]["status"], "submitted")
            self.assertEqual(step_end["payload"]["terminal_reason"], "submitted")
            self.assertEqual(step_end["payload"]["exit_status"], "Submitted")
            self.assertEqual(step_end["payload"]["n_calls"], 1)
            self.assertNotIn("hidden submission", json.dumps(events))

    def test_nested_span_parent_id_uses_agent_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = self.make_sink(tmp)
            with sink.span(span_id="step-1", span_type="agent_step", name="agent.step"):
                with sink.span(span_id="tool-1", span_type="tool_call", name="tool"):
                    pass
            sink.close()

            events = self.read_events(tmp)
            tool_start = next(
                event
                for event in events
                if event["event_type"] == "tool_call.start" and event["payload"]["span_id"] == "tool-1"
            )
            self.assertEqual(tool_start["payload"]["parent_span_id"], "step-1")
            step_start = next(event for event in events if event["event_type"] == "agent_step.start")
            self.assertEqual(step_start["payload"]["parent_span_id"], "run:run-p1")


def write_runtime_control_config(root: Path, *, control_mode: str = "advise") -> Path:
    config = root / "agentmon-runtime-control.toml"
    config.write_text(
        f"""
[general]
output_root = "{root / "runs"}"

[vllm]
metrics_url = "http://127.0.0.1:1/metrics"
api_base = "http://127.0.0.1:1/v1"
models_url = "http://127.0.0.1:1/v1/models"
request_timeout_seconds = 0.05

[ascend]
npu_smi = "{root / "missing-npu-smi"}"
command_timeout_seconds = 0.05

[scheduler]
max_running_runs = 4

[control]
mode = "{control_mode}"

[model_gate]
max_inflight_requests = 12
moderate_pressure_target = 6
high_pressure_target = 2
min_inflight_requests = 1

[tool_pool]
default_max_concurrent = 8
cpu_heavy_max_concurrent = 3
io_heavy_max_concurrent = 2
high_priority_max_concurrent = 1
""".strip(),
        encoding="utf-8",
    )
    return config
