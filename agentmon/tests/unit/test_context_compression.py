from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from agentmon.adapters.mini_swe.context import AgentMonContext
from agentmon.adapters.mini_swe.context_compression import (
    MiniSweContextCompressor,
    group_mini_swe_messages,
)
from agentmon.adapters.mini_swe.event_sink import AdapterEventSink
from agentmon.adapters.mini_swe.model import InstrumentedModel
from agentmon.config import AgentMonConfig
from agentmon.context_compression import (
    ContextCompressionResult,
    ContextCompressionService,
    ContextCompressionTriggerResult,
    ContextMeasurement,
    ContextSummaryResult,
    register_context_compression_trigger,
    register_context_compression_strategy,
    unregister_context_compression_trigger,
    unregister_context_compression_strategy,
)
from agentmon.run_controller import build_child_env


class CapturingModel:
    model_name = "capturing-model"

    def __init__(self) -> None:
        self.messages = None

    def query(self, messages, **kwargs):
        self.messages = list(messages)
        return {
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 1,
                "total_tokens": 9,
            }
        }


class SummarizingModel:
    model_name = "summarizing-model"

    def __init__(self) -> None:
        self.calls = []

    def query(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        if messages and messages[0].get("role") == "system":
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Preserve the task and the earlier verified result."
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 42,
                    "completion_tokens": 8,
                    "total_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 10},
                },
            }
        return {
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 1,
                "total_tokens": 21,
            }
        }


class ContextCompressionTests(unittest.TestCase):
    def test_model_summary_strategy_replaces_old_groups_with_provider_output(self) -> None:
        cfg = compression_config(window_tokens=100)
        cfg.context_compression.strategy = "model_summary"
        context = ["system", "task", "old-a", "old-b", "recent"]

        decision = ContextCompressionService(cfg).prepare(
            context,
            metadata={
                "groups": [[0], [1], [2], [3], [4]],
                "required_group_ids": [0, 1],
                "item_token_estimates": [2, 2, 45, 30, 4],
                "measurement_quality": "exact_test_counter",
                "summary_provider": lambda request: ContextSummaryResult(
                    summary="verified summary",
                    request_id="summary-request",
                    model="test-model",
                    input_tokens=30,
                    output_tokens=4,
                    total_tokens=34,
                    cached_tokens=5,
                    uncached_prompt_tokens=25,
                    token_quality="exact_test_usage",
                ),
                "summary_item_factory": lambda summary: f"SUMMARY:{summary}",
                "summary_item_estimator": lambda item: 4,
            },
        )

        self.assertEqual(decision.status, "COMPLETE")
        self.assertEqual(
            decision.context,
            ["system", "task", "SUMMARY:verified summary", "recent"],
        )
        self.assertEqual(decision.summary.request_id, "summary-request")
        self.assertEqual(decision.telemetry()["context_summary_output_tokens"], 4)
        self.assertIsNotNone(decision.started_mono_ns)
        self.assertIsNotNone(decision.ended_mono_ns)
        self.assertIsNotNone(decision.duration_s)

    def test_instrumented_model_uses_model_generated_summary_and_tracks_usage(self) -> None:
        cfg = compression_config(window_tokens=120)
        cfg.context_compression.strategy = "model_summary"
        cfg.context_compression.estimator_chars_per_token = 1.0
        secret_old = "old-secret-" * 20
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {"role": "user", "content": secret_old},
            {"role": "assistant", "content": "recent result"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            sink = AdapterEventSink(
                AgentMonContext(
                    run_id="run-summary",
                    run_dir=Path(tmp),
                    events_path=Path(tmp) / "events.jsonl",
                )
            )
            inner = SummarizingModel()
            InstrumentedModel(inner, sink=sink, cfg=cfg).query(messages)
            sink.close()
            event_text = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            events = [json.loads(line) for line in event_text.splitlines()]

        self.assertEqual(len(inner.calls), 2)
        main_messages = inner.calls[-1][0]
        self.assertNotIn(messages[2], main_messages)
        self.assertTrue(
            any("Earlier context summary" in item.get("content", "") for item in main_messages)
        )
        decision = next(
            event
            for event in events
            if event["event_type"] == "context_compression.decision"
        )["payload"]
        self.assertEqual(decision["context_compression_strategy"], "model_summary")
        self.assertEqual(decision["context_summary_input_tokens"], 42)
        self.assertEqual(decision["context_summary_output_tokens"], 8)
        self.assertEqual(decision["context_summary_cached_tokens"], 10)
        self.assertEqual(decision["context_summary_uncached_prompt_tokens"], 32)
        self.assertNotIn(secret_old, event_text)

    def test_recent_groups_triggers_at_eighty_percent_and_targets_twenty_percent(self) -> None:
        cfg = compression_config(window_tokens=50)
        context = ["system", "task", "old", "assistant", "tool"]
        metadata = {
            "groups": [[0], [1], [2], [3, 4]],
            "required_group_ids": [0, 1],
            "item_token_estimates": [1, 1, 40, 3, 3],
            "measurement_quality": "exact_test_counter",
        }

        decision = ContextCompressionService(cfg).prepare(context, metadata=metadata)

        self.assertEqual(decision.status, "COMPLETE")
        self.assertEqual(decision.trigger_tokens, 40)
        self.assertEqual(decision.target_tokens, 10)
        self.assertEqual(decision.context, ["system", "task", "assistant", "tool"])
        self.assertEqual(decision.original.token_count, 48)
        self.assertEqual(decision.compressed.token_count, 8)
        self.assertTrue(decision.applied)
        self.assertTrue(decision.target_achieved)
        self.assertEqual(context, ["system", "task", "old", "assistant", "tool"])

    def test_context_below_trigger_is_unchanged(self) -> None:
        cfg = compression_config(window_tokens=50)
        context = ["system", "task", "recent"]
        decision = ContextCompressionService(cfg).prepare(
            context,
            metadata={
                "groups": [[0], [1], [2]],
                "required_group_ids": [0, 1],
                "item_token_estimates": [1, 1, 37],
                "measurement_quality": "exact_test_counter",
            },
        )

        self.assertEqual(decision.status, "NOT_TRIGGERED")
        self.assertIs(decision.context, context)
        self.assertFalse(decision.applied)

    def test_required_and_recent_groups_can_make_target_partial(self) -> None:
        cfg = compression_config(window_tokens=50)
        decision = ContextCompressionService(cfg).prepare(
            ["system", "task", "old", "assistant", "tool"],
            metadata={
                "groups": [[0], [1], [2], [3, 4]],
                "required_group_ids": [0, 1],
                "item_token_estimates": [8, 8, 30, 3, 3],
                "measurement_quality": "exact_test_counter",
            },
        )

        self.assertEqual(decision.status, "PARTIAL")
        self.assertFalse(decision.target_achieved)
        self.assertIn("required_or_recent_groups_exceed_target", decision.limitations)

    def test_runtime_master_switch_bypasses_compression(self) -> None:
        cfg = compression_config(window_tokens=50)
        cfg.runtime_optimization.enabled = False
        context = ["system", "task", "old"]

        decision = ContextCompressionService(cfg).prepare(context, metadata={})

        self.assertEqual(decision.status, "DISABLED")
        self.assertEqual(decision.reason, "runtime_optimization_disabled")
        self.assertIs(decision.context, context)

    def test_custom_strategy_can_be_registered_and_selected(self) -> None:
        class TestStrategy:
            name = "test_context_plugin"

            def __init__(self, cfg, options) -> None:
                self.options = options

            def measure(self, context, metadata):
                return ContextMeasurement(
                    token_count=len(context) * 10,
                    item_count=len(context),
                    quality="exact_test_plugin",
                )

            def compress(self, request):
                return ContextCompressionResult(
                    context=request.context[-1:],
                    reason=str(self.options["reason"]),
                )

        register_context_compression_strategy(
            "test_context_plugin",
            TestStrategy,
            replace=True,
        )
        try:
            cfg = compression_config(window_tokens=30)
            cfg.context_compression.strategy = "test_context_plugin"
            cfg.context_compression.strategy_options_json = '{"reason":"plugin_selected"}'

            decision = ContextCompressionService(cfg).prepare([1, 2, 3])

            self.assertEqual(decision.status, "PARTIAL")
            self.assertEqual(decision.reason, "plugin_selected")
            self.assertEqual(decision.context, [3])
            self.assertEqual(decision.compressed.quality, "exact_test_plugin")
        finally:
            unregister_context_compression_strategy("test_context_plugin")

    def test_custom_trigger_can_replace_token_ratio_policy(self) -> None:
        class AlwaysTrigger:
            name = "always_trigger_test"

            def __init__(self, cfg, options) -> None:
                self.reason = options["reason"]

            def evaluate(self, request):
                return ContextCompressionTriggerResult(
                    triggered=True,
                    reason=self.reason,
                )

        register_context_compression_trigger(
            "always_trigger_test",
            AlwaysTrigger,
            replace=True,
        )
        try:
            cfg = compression_config(window_tokens=100)
            cfg.context_compression.trigger_strategy = "always_trigger_test"
            cfg.context_compression.trigger_options_json = (
                '{"reason":"alternate_trigger_selected"}'
            )
            decision = ContextCompressionService(cfg).prepare(
                ["system", "task", "old", "assistant", "tool"],
                metadata={
                    "groups": [[0], [1], [2], [3, 4]],
                    "required_group_ids": [0, 1],
                    "item_token_estimates": [1, 1, 20, 2, 2],
                    "measurement_quality": "exact_test_counter",
                },
            )

            self.assertEqual(decision.status, "COMPLETE")
            self.assertTrue(decision.applied)
            self.assertEqual(decision.trigger_strategy, "always_trigger_test")
            self.assertEqual(
                decision.trigger_reason,
                "alternate_trigger_selected",
            )
            self.assertLess(decision.original.token_count, decision.trigger_tokens)
        finally:
            unregister_context_compression_trigger("always_trigger_test")

    def test_strategy_module_can_be_loaded_from_config(self) -> None:
        module_name = "agentmon_test_context_compression_plugin"
        strategy_name = "configured_module_plugin"
        trigger_name = "configured_module_trigger"
        module_source = f'''\
from agentmon.context_compression import (
    ContextCompressionResult,
    ContextCompressionTriggerResult,
    ContextMeasurement,
    register_context_compression_trigger,
    register_context_compression_strategy,
)

class ConfiguredTrigger:
    name = "{trigger_name}"

    def __init__(self, cfg, options):
        self.reason = options["reason"]

    def evaluate(self, request):
        return ContextCompressionTriggerResult(
            triggered=True,
            reason=self.reason,
        )

class ConfiguredStrategy:
    name = "{strategy_name}"

    def __init__(self, cfg, options):
        self.reason = options["reason"]

    def measure(self, context, metadata):
        return ContextMeasurement(
            token_count=len(context) * 10,
            item_count=len(context),
            quality="exact_dynamic_plugin",
        )

    def compress(self, request):
        return ContextCompressionResult(
            context=request.context[-1:],
            reason=self.reason,
        )

register_context_compression_strategy(
    "{strategy_name}",
    ConfiguredStrategy,
    replace=True,
)
register_context_compression_trigger(
    "{trigger_name}",
    ConfiguredTrigger,
    replace=True,
)
'''

        with tempfile.TemporaryDirectory() as tmp:
            module_path = Path(tmp) / f"{module_name}.py"
            module_path.write_text(module_source, encoding="utf-8")
            sys.path.insert(0, tmp)
            try:
                cfg = compression_config(window_tokens=30)
                cfg.context_compression.trigger_strategy = trigger_name
                cfg.context_compression.strategy = strategy_name
                cfg.context_compression.plugin_modules = [module_name]
                cfg.context_compression.trigger_options_json = (
                    '{"reason":"trigger_loaded_from_config"}'
                )
                cfg.context_compression.strategy_options_json = (
                    '{"reason":"loaded_from_config"}'
                )

                decision = ContextCompressionService(cfg).prepare([1, 2, 3])

                self.assertEqual(decision.reason, "loaded_from_config")
                self.assertEqual(decision.trigger_strategy, trigger_name)
                self.assertEqual(
                    decision.trigger_reason,
                    "trigger_loaded_from_config",
                )
                self.assertEqual(decision.context, [3])
                self.assertEqual(
                    decision.compressed.quality,
                    "exact_dynamic_plugin",
                )
            finally:
                sys.path.remove(tmp)
                sys.modules.pop(module_name, None)
                unregister_context_compression_trigger(trigger_name)
                unregister_context_compression_strategy(strategy_name)

    def test_mini_swe_grouping_keeps_assistant_and_tool_results_atomic(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "developer", "content": "policy"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call"}]},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "continue"},
        ]

        groups, required = group_mini_swe_messages(messages)

        self.assertEqual(groups, [[0], [1], [2], [3, 4], [5]])
        self.assertEqual(required, [0, 1, 2])

    def test_instrumented_model_compresses_only_request_copy_and_emits_no_content(self) -> None:
        cfg = compression_config(window_tokens=100)
        cfg.context_compression.estimator_chars_per_token = 1.0
        secret_old = "old-secret-" * 20
        secret_tool = "latest-tool-secret"
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {"role": "user", "content": secret_old},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "content": secret_tool, "tool_call_id": "call-1"},
        ]
        original = list(messages)

        with tempfile.TemporaryDirectory() as tmp:
            sink = AdapterEventSink(
                AgentMonContext(
                    run_id="run-compression",
                    run_dir=Path(tmp),
                    events_path=Path(tmp) / "events.jsonl",
                )
            )
            inner = CapturingModel()
            model = InstrumentedModel(inner, sink=sink, cfg=cfg)
            model.query(messages)
            sink.close()
            event_text = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            events = [json.loads(line) for line in event_text.splitlines()]

        self.assertEqual(messages, original)
        self.assertNotIn(messages[2], inner.messages)
        self.assertEqual(inner.messages[-2:], messages[-2:])
        decision = next(
            event
            for event in events
            if event["event_type"] == "context_compression.decision"
        )
        self.assertEqual(decision["payload"]["context_compression_status"], "PARTIAL")
        self.assertTrue(decision["payload"]["context_compression_applied"])
        self.assertNotIn(secret_old, event_text)
        self.assertNotIn(secret_tool, event_text)

    def test_child_process_reads_resolved_run_config(self) -> None:
        cfg = AgentMonConfig()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            env = build_child_env(cfg, "run-id", run_dir)

        self.assertEqual(
            env["AGENTMON_CONFIG"],
            str(run_dir / "config.resolved.toml"),
        )

    def test_mini_swe_non_sequence_context_stays_unresolved(self) -> None:
        cfg = compression_config(window_tokens=100)
        decision = MiniSweContextCompressor(cfg).prepare({"not": "messages"})

        self.assertEqual(decision.status, "UNRESOLVED")
        self.assertEqual(decision.reason, "context_token_measurement_unavailable")


def compression_config(*, window_tokens: int) -> AgentMonConfig:
    cfg = AgentMonConfig()
    cfg.context_compression.enabled = True
    cfg.context_compression.window_tokens = window_tokens
    cfg.context_compression.trigger_ratio = 0.80
    cfg.context_compression.target_ratio = 0.20
    cfg.context_compression.trigger_strategy = "token_ratio"
    cfg.context_compression.strategy = "recent_groups"
    cfg.context_compression.trigger_options_json = "{}"
    cfg.context_compression.strategy_options_json = '{"min_recent_groups":1}'
    return cfg


if __name__ == "__main__":
    unittest.main()
