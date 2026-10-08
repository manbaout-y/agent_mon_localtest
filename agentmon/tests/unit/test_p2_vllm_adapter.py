import json
import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from agentmon.adapters.vllm_018.events import VllmP2EventSink
from agentmon.adapters.vllm_018 import hook as p2_hook
from agentmon.adapters.vllm_018.kv import calculate_kv_pool
from agentmon.adapters.vllm_018 import version as p2_version
from agentmon.adapters.vllm_018.version import UnsupportedVllmVersion, require_supported_vllm_version
from agentmon.events import Event
from agentmon.reporting.report import build_report
from agentmon.reporting.summary import build_aggregate, build_run_summary
from agentmon.storage.jsonl import JsonlWriter
from agentmon.storage.sqlite import SqliteEventWriter


class P2VllmAdapterTests(unittest.TestCase):
    def test_deep_collection_defaults_on_and_can_be_explicitly_disabled(self) -> None:
        old_deep = os.environ.pop("AGENTMON_VLLM_P2_DEEP", None)
        try:
            self.assertTrue(
                p2_hook.environment_flag("AGENTMON_VLLM_P2_DEEP", default=True)
            )
            os.environ["AGENTMON_VLLM_P2_DEEP"] = "off"
            self.assertFalse(
                p2_hook.environment_flag("AGENTMON_VLLM_P2_DEEP", default=True)
            )
        finally:
            if old_deep is None:
                os.environ.pop("AGENTMON_VLLM_P2_DEEP", None)
            else:
                os.environ["AGENTMON_VLLM_P2_DEEP"] = old_deep

    def test_request_lifecycle_dedup_state_is_bounded(self) -> None:
        sink = VllmP2EventSink(run_id="run-bounded")
        state = p2_hook.VllmP2HookState(sink=sink)
        try:
            for index in range(p2_hook.MAX_REQUEST_STATE_ENTRIES + 100):
                self.assertTrue(state.mark_seen("seen_first_token", f"request-{index}"))
            self.assertLessEqual(len(state.seen_first_token), p2_hook.MAX_REQUEST_STATE_ENTRIES)
        finally:
            sink.close()

    def test_engine_core_step_paths_handle_none_empty_and_preserve_identity(self) -> None:
        class Core:
            def __init__(self, result: object) -> None:
                self.result = result

            def step(self) -> object:
                return self.result

            def step_with_batch_queue(self) -> object:
                return self.result

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-async-empty", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Core.step = p2_hook.wrap_engine_core_step(state)(Core.step)
            Core.step_with_batch_queue = p2_hook.wrap_engine_core_step_with_batch_queue(state)(
                Core.step_with_batch_queue
            )
            try:
                for result in ((None, True), ({}, False), ({0: None}, True)):
                    core = Core(result)
                    self.assertIs(core.step(), result)
                    self.assertIs(core.step_with_batch_queue(), result)
            finally:
                sink.close()

            rows = read_event_rows(events_path)

        self.assertFalse(any(row["event_type"].startswith("vllm.request.") for row in rows))

    def test_engine_core_async_path_accumulates_tokens_across_engines_and_requests(self) -> None:
        class Core:
            def __init__(self) -> None:
                self.results: list[object] = []

            def step_with_batch_queue(self) -> object:
                return self.results.pop(0)

        def output(request_id: str, tokens: list[int], finish_reason: str | None = None) -> object:
            return types.SimpleNamespace(
                request_id=request_id,
                new_token_ids=tokens,
                finish_reason=finish_reason,
                finished=False,
                num_cached_tokens=0,
            )

        first = (
            {
                0: types.SimpleNamespace(
                    scheduler_stats=None,
                    outputs=[output("req-a", [1]), output("req-b", [2, 3])],
                ),
                1: types.SimpleNamespace(
                    scheduler_stats=None,
                    outputs=[output("req-c", [4])],
                ),
            },
            True,
        )
        second = (
            {
                0: types.SimpleNamespace(
                    scheduler_stats=None,
                    outputs=[output("req-a", [5, 6], "stop"), output("req-b", [], "length")],
                ),
                1: types.SimpleNamespace(
                    scheduler_stats=None,
                    outputs=[output("req-c", [7], "stop")],
                ),
            },
            True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-async-tokens", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Core.step_with_batch_queue = p2_hook.wrap_engine_core_step_with_batch_queue(state)(
                Core.step_with_batch_queue
            )
            core = Core()
            core.results = [first, second]
            try:
                self.assertIs(core.step_with_batch_queue(), first)
                self.assertIs(core.step_with_batch_queue(), second)
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        first_tokens = [row for row in rows if row["event_type"] == "vllm.request.first_token"]
        finished = [row for row in rows if row["event_type"] == "vllm.request.engine_finished"]
        self.assertEqual({row["payload"]["vllm_request_id"] for row in first_tokens}, {"req-a", "req-b", "req-c"})
        self.assertEqual(len(first_tokens), 3)
        by_request = {row["payload"]["vllm_request_id"]: row["payload"] for row in finished}
        self.assertEqual(by_request["req-a"]["generation_tokens"], 3)
        self.assertEqual(by_request["req-b"]["generation_tokens"], 2)
        self.assertEqual(by_request["req-c"]["generation_tokens"], 2)
        self.assertEqual(by_request["req-b"]["finish_reason"], "length")
        self.assertIsNotNone(by_request["req-a"]["last_token_ns"])
        self.assertEqual(state._generation_token_counts, {})

    def test_engine_core_nested_wrappers_deduplicate_and_preserve_exceptions(self) -> None:
        result = (
            {
                0: types.SimpleNamespace(
                    scheduler_stats=None,
                    outputs=[
                        types.SimpleNamespace(
                            request_id="req-nested",
                            new_token_ids=[1],
                            finish_reason="stop",
                            finished=True,
                            num_cached_tokens=0,
                        )
                    ],
                )
            },
            True,
        )

        class Core:
            def step_with_batch_queue(self) -> object:
                return result

        class FailingCore:
            def step_with_batch_queue(self) -> object:
                raise LookupError("original failure")

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-nested", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            wrapped = p2_hook.wrap_engine_core_step_with_batch_queue(state)(Core.step_with_batch_queue)
            Core.step_with_batch_queue = p2_hook.wrap_engine_core_step(state)(wrapped)
            FailingCore.step_with_batch_queue = p2_hook.wrap_engine_core_step_with_batch_queue(state)(
                FailingCore.step_with_batch_queue
            )
            try:
                self.assertIs(Core().step_with_batch_queue(), result)
                with self.assertRaisesRegex(LookupError, "original failure"):
                    FailingCore().step_with_batch_queue()
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        self.assertEqual(sum(row["event_type"] == "vllm.request.first_token" for row in rows), 1)
        self.assertEqual(sum(row["event_type"] == "vllm.request.engine_finished" for row in rows), 1)

    def test_engine_core_observation_failure_is_fail_open(self) -> None:
        result = ({}, False)

        class Core:
            def step_with_batch_queue(self) -> object:
                return result

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-observe-fail", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Core.step_with_batch_queue = p2_hook.wrap_engine_core_step_with_batch_queue(state)(
                Core.step_with_batch_queue
            )
            try:
                with patch.object(p2_hook, "observe_engine_core_step_result", side_effect=RuntimeError("observer")):
                    self.assertIs(Core().step_with_batch_queue(), result)
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        error = next(row for row in rows if row["event_type"] == "vllm.adapter.error")
        self.assertEqual(error["payload"]["where"], "EngineCore.step_with_batch_queue.observe")

    def test_final_only_api_output_does_not_masquerade_as_first_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-final-only", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            try:
                first_engine_output = types.SimpleNamespace(
                    request_id="req-final-only",
                    new_token_ids=[101],
                    finish_reason=None,
                )
                final_engine_output = types.SimpleNamespace(
                    request_id="req-final-only",
                    new_token_ids=[102],
                    finish_reason="stop",
                )
                final_api_output = FakeRequestOutput("req-final-only", finished=True)
                with patch.object(p2_hook.time, "monotonic_ns", side_effect=[100, 300, 310, 350, 400]):
                    p2_hook.observe_engine_core_outputs(
                        state,
                        types.SimpleNamespace(outputs=[first_engine_output], scheduler_stats=None),
                    )
                    p2_hook.observe_engine_core_outputs(
                        state,
                        types.SimpleNamespace(outputs=[final_engine_output], scheduler_stats=None),
                    )
                    p2_hook.observe_request_output(state, final_api_output)
            finally:
                sink.close()

            rows = read_event_rows(events_path)
            report = build_report_from_events(rows)

        by_type = {row["event_type"]: row["payload"] for row in rows}
        self.assertEqual(by_type["vllm.request.first_token"]["first_token_ns"], 100)
        self.assertEqual(by_type["vllm.request.engine_finished"]["last_token_ns"], 300)
        self.assertEqual(by_type["vllm.request.engine_finished"]["engine_finished_ns"], 310)
        self.assertEqual(by_type["vllm.request.first_output"]["first_output_ns"], 350)
        self.assertEqual(by_type["vllm.request.finished"]["finished_ns"], 400)
        self.assertEqual(by_type["vllm.request.finished"]["computed_prefill_tokens"], 3)
        self.assertEqual(by_type["vllm.request.finished"]["uncached_prompt_tokens"], 3)
        self.assertIsNone(by_type["vllm.request.finished"]["recomputed_prompt_tokens"])
        self.assertEqual(
            by_type["vllm.request.finished"]["recomputed_token_quality"],
            "unavailable_not_observed",
        )
        self.assertEqual(
            by_type["vllm.request.finished"]["prompt_token_accounting_quality"],
            "derived_prompt_minus_cached",
        )
        self.assertEqual(
            by_type["vllm.request.first_token"]["first_token_timestamp_source"],
            "engine_core_output",
        )
        self.assertEqual(report["vllm_p2"]["requests"][0]["last_token_ns"], 300)
        self.assertAlmostEqual(report["vllm_p2"]["latency"]["exact_tpot"]["mean"], 0.0000002)
        self.assertEqual(state._last_token_ns, {})

    def test_streaming_generation_tokens_accumulate_and_engine_count_is_authoritative(self) -> None:
        class Completion:
            def __init__(self, token_ids: list[int], finish_reason: str | None = None) -> None:
                self.token_ids = token_ids
                self.finish_reason = finish_reason

        class Output:
            def __init__(self, request_id: str, token_ids: list[int], *, finished: bool = False) -> None:
                self.request_id = request_id
                self.finished = finished
                self.outputs = [Completion(token_ids, "stop" if finished else None)]
                self.prompt_token_ids = [1, 2, 3]
                self.num_cached_tokens = 0

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-streaming", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            try:
                p2_hook.observe_request_output(state, Output("stream", [10]), output_kind="delta")
                p2_hook.observe_request_output(state, Output("stream", [11]), output_kind="delta")
                p2_hook.observe_request_output(
                    state,
                    Output("stream", [12], finished=True),
                    output_kind="delta",
                )
                state.add_generation_tokens("engine-wins", 81)
                p2_hook.observe_request_output(
                    state,
                    Output("engine-wins", [99], finished=True),
                    output_kind="delta",
                )
                p2_hook.observe_request_output(
                    state,
                    Output("non-stream", [20, 21, 22], finished=True),
                    output_kind="final_only",
                )
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        finished = {
            row["payload"]["vllm_request_id"]: row["payload"]
            for row in rows
            if row["event_type"] == "vllm.request.finished"
        }
        self.assertEqual(finished["stream"]["generation_tokens"], 3)
        self.assertEqual(
            finished["stream"]["generation_tokens_source"],
            "async_llm_request_output_accumulated",
        )
        self.assertEqual(finished["engine-wins"]["generation_tokens"], 81)
        self.assertEqual(finished["engine-wins"]["api_generation_tokens_observed"], 1)
        self.assertEqual(finished["engine-wins"]["generation_tokens_consistency"], "mismatch")
        self.assertEqual(finished["non-stream"]["generation_tokens"], 3)
        self.assertEqual(state._generation_token_counts, {})
        self.assertEqual(state._api_generation_token_counts, {})

    def test_streaming_empty_terminal_chunk_does_not_replace_accumulated_tokens(self) -> None:
        class Completion:
            def __init__(self, token_ids: list[int], finish_reason: str | None = None) -> None:
                self.token_ids = token_ids
                self.finish_reason = finish_reason

        class Output:
            def __init__(self, token_ids: list[int], *, finished: bool = False) -> None:
                self.request_id = "stream-empty-terminal"
                self.finished = finished
                self.outputs = [Completion(token_ids, "stop" if finished else None)]
                self.prompt_token_ids = [1]
                self.num_cached_tokens = 0

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-stream-empty", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            try:
                p2_hook.observe_request_output(state, Output([10, 11]), output_kind="delta")
                p2_hook.observe_request_output(state, Output([12]), output_kind="delta")
                p2_hook.observe_request_output(
                    state,
                    Output([], finished=True),
                    output_kind="delta",
                )
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        finished = next(row for row in rows if row["event_type"] == "vllm.request.finished")
        self.assertEqual(finished["payload"]["generation_tokens"], 3)
        self.assertEqual(finished["payload"]["api_generation_tokens_observed"], 3)

    def test_report_preserves_cross_process_engine_generation_token_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-cross-process", events_path=events_path)
            try:
                sink.emit(
                    "vllm.request.engine_finished",
                    payload={
                        "vllm_request_id": "req-cross",
                        "engine_finished_ns": 200,
                        "generation_tokens": 81,
                        "engine_generation_tokens_observed": 81,
                        "generation_tokens_source": "engine_core_new_token_ids",
                    },
                )
                sink.emit(
                    "vllm.request.finished",
                    payload={
                        "vllm_request_id": "req-cross",
                        "finished_ns": 300,
                        "generation_tokens": 1,
                        "api_generation_tokens_observed": 1,
                        "generation_tokens_source": "async_llm_request_output_accumulated",
                    },
                )
            finally:
                sink.close()
            report = build_report_from_events(read_event_rows(events_path))

        request = report["vllm_p2"]["requests"][0]
        self.assertEqual(request["generation_tokens"], 81)
        self.assertEqual(request["generation_tokens_source"], "engine_core_new_token_ids")
        self.assertEqual(request["api_generation_tokens_observed"], 1)
        self.assertEqual(request["generation_tokens_consistency"], "mismatch")
        self.assertEqual(report["vllm_p2"]["tokens"]["generation_tokens"]["total"], 81)

    def test_async_generate_wrapper_reads_delta_output_kind_and_cleans_state(self) -> None:
        class Completion:
            def __init__(self, token_id: int) -> None:
                self.token_ids = [token_id]
                self.finish_reason = None

        class Output:
            def __init__(self, token_id: int, *, finished: bool = False) -> None:
                self.request_id = "stream-wrapper"
                self.finished = finished
                self.outputs = [Completion(token_id)]
                self.outputs[0].finish_reason = "stop" if finished else None
                self.prompt_token_ids = [1]
                self.num_cached_tokens = 0

        class Engine:
            async def generate(
                self,
                prompt: object,
                sampling_params: object,
                request_id: str,
                trace_headers: object = None,
            ) -> object:
                yield Output(1)
                yield Output(2)
                yield Output(3, finished=True)

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-stream-wrapper", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Engine.generate = p2_hook.wrap_async_generate(state)(Engine.generate)
            params = types.SimpleNamespace(output_kind=types.SimpleNamespace(name="DELTA"))

            async def consume() -> None:
                async for _ in Engine().generate(None, params, "stream-wrapper"):
                    pass

            try:
                asyncio.run(consume())
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        finished = next(row for row in rows if row["event_type"] == "vllm.request.finished")
        self.assertEqual(finished["payload"]["generation_tokens"], 3)
        self.assertEqual(finished["payload"]["api_output_kind"], "delta")
        self.assertEqual(state._api_generation_token_counts, {})

    def test_runtime_capability_reports_async_hook_and_prefix_cache_failures(self) -> None:
        scheduler = types.SimpleNamespace(
            cache_config=types.SimpleNamespace(enable_prefix_caching=True),
            vllm_config=types.SimpleNamespace(
                scheduler_config=types.SimpleNamespace(async_scheduling=True),
                cache_config=types.SimpleNamespace(enable_prefix_caching=True),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-capability", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            state.prefix_cache_expectation = "disabled"
            state.engine_core_step_present = True
            state.engine_core_step_hooked = True
            state.engine_core_step_with_batch_queue_present = True
            state.engine_core_step_with_batch_queue_hooked = False
            try:
                p2_hook.emit_scheduler_runtime_capability(state, scheduler)
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        payload = rows[-1]["payload"]
        details = payload["details"]
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(details["async_scheduling_enabled"])
        self.assertEqual(details["engine_token_boundary_collection"], "unsupported")
        self.assertIn("EngineCore.step_with_batch_queue", details["missing_required"])
        self.assertIn("prefix_cache_configuration", details["missing_required"])
        self.assertEqual(details["prefix_cache_expectation_status"], "failed")
        report = build_report_from_events(rows)
        self.assertEqual(report["vllm_p2"]["status"], "partial")
        self.assertEqual(report["vllm_p2"]["capability_required_failure_count"], 1)

    def test_install_capability_is_partial_when_async_method_exists_but_hook_fails(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        original_patch_method = p2_hook.patch_method

        def selective_patch(cls: type, method_name: str, wrapper_factory: object) -> bool:
            if cls.__name__ == "EngineCore" and method_name == "step_with_batch_queue":
                return False
            return original_patch_method(cls, method_name, wrapper_factory)

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-install-partial", events_path=events_path)
            try:
                with patch.object(p2_hook, "patch_method", side_effect=selective_patch):
                    result = p2_hook.install(sink=sink, force=True)
                self.assertEqual(result["status"], "partial")
                self.assertFalse(result["engine_core_step_with_batch_queue_hooked"])
                self.assertIn("EngineCore.step_with_batch_queue", result["missing_required"])
            finally:
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)
            rows = read_event_rows(events_path)

        capability = next(row for row in rows if row["event_type"] == "vllm.adapter.capability")
        self.assertEqual(capability["payload"]["status"], "partial")
        self.assertEqual(
            capability["payload"]["details"]["engine_token_boundary_collection"],
            "unsupported",
        )

    def test_disabled_prefix_cache_expectation_detects_runtime_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-prefix-violation", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            state.prefix_cache_expectation = "disabled"
            state.prefix_cache_enabled = False
            state.prefix_cache_config_source = "scheduler.cache_config.enable_prefix_caching"
            try:
                p2_hook.validate_prefix_cache_observation(
                    state,
                    cached_prompt_tokens=1280,
                    request_id="req-cache-hit",
                )
                p2_hook.validate_prefix_cache_observation(
                    state,
                    cached_prompt_tokens=1280,
                    request_id="req-cache-hit",
                )
            finally:
                sink.close()
            rows = read_event_rows(events_path)

        capability = [row for row in rows if row["event_type"] == "vllm.adapter.capability"]
        self.assertEqual(len(capability), 1)
        self.assertEqual(capability[0]["payload"]["status"], "partial")
        self.assertEqual(
            capability[0]["payload"]["details"]["prefix_cache_expectation_status"],
            "failed",
        )

    def test_version_gate_accepts_only_vllm_018_base_version(self) -> None:
        self.assertEqual(require_supported_vllm_version("0.18.0"), "0.18.0")
        self.assertEqual(require_supported_vllm_version("0.18.0+empty"), "0.18.0+empty")
        with self.assertRaises(UnsupportedVllmVersion):
            require_supported_vllm_version("0.19.0")

    def test_api_finished_time_is_not_inferred_as_engine_decode_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-no-fake-decode", events_path=events_path)
            try:
                sink.request_finished(
                    agent_request_id="agent-1",
                    vllm_request_id="engine-1",
                    correlation_quality="direct",
                    first_token_ns=100,
                    finished_ns=200,
                    generation_tokens=2,
                )
            finally:
                sink.close()

            payload = read_event_rows(events_path)[0]["payload"]

        self.assertEqual(payload["first_token_ns"], 100)
        self.assertEqual(payload["finished_ns"], 200)
        self.assertIsNone(payload["engine_finished_ns"])

    def test_kv_pool_formula_preserves_inputs_and_bytes_per_rank(self) -> None:
        pool = calculate_kv_pool(
            {
                "num_gpu_blocks": 10,
                "block_size": 16,
                "cache_dtype": "bfloat16",
                "num_layers_per_rank": 48,
                "num_kv_heads_per_rank": 1,
                "head_dim": 128,
            },
            source="cache_config",
        )

        self.assertEqual(pool["source_quality"], "complete")
        self.assertEqual(pool["bytes_per_block_per_rank"], 393216)
        self.assertEqual(pool["allocated_pool_bytes_per_rank"], 3932160)
        self.assertEqual(pool["formula_version"], "kv_pool_bytes_per_rank.v1")

    def test_p2_sink_emits_request_mapping_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-p2", events_path=events_path)
            try:
                sink.request_received(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    received_ns=100,
                    model_name="qwen3-coder-30b",
                    agentmon_run_id="agent-run",
                    agentmon_model_request_id="agent-1",
                )
                sink.request_finished(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    received_ns=100,
                    first_scheduled_ns=120,
                    first_token_ns=200,
                    finished_ns=300,
                    finish_reason="stop",
                    prompt_tokens=10,
                    generation_tokens=3,
                )
            finally:
                sink.close()

            rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["source"] for row in rows], ["vllm_p2", "vllm_p2"])
        self.assertEqual(rows[0]["event_type"], "vllm.request.received")
        self.assertEqual(rows[0]["payload"]["agent_request_id"], "agent-1")
        self.assertEqual(rows[0]["payload"]["agentmon_run_id"], "agent-run")
        self.assertEqual(rows[0]["payload"]["agentmon_model_request_id"], "agent-1")
        self.assertEqual(rows[0]["payload"]["vllm_request_id"], "vllm-1")
        self.assertEqual(rows[1]["event_type"], "vllm.request.finished")
        self.assertEqual(rows[1]["payload"]["first_token_ns"], 200)

    def test_report_summary_and_p0_sqlite_check_understand_p2_mapping_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-p2", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            p0_event = Event(run_id="run-p2", source="controller", event_type="lifecycle", quality="exact")
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(p0_event)
            sqlite = SqliteEventWriter(run_dir / "metrics.sqlite3")
            sqlite.write_event(p0_event)
            sqlite.close()

            sink = VllmP2EventSink(run_id="run-p2", events_path=run_dir / "events.jsonl")
            try:
                sink.request_received(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    received_ns=100,
                )
                sink.request_finished(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    finished_ns=300,
                )
            finally:
                sink.close()

            report = build_report(run_dir)
            summary = build_run_summary(report)

        self.assertEqual(report["event_log"]["jsonl_total_count"], 3)
        self.assertEqual(report["event_log"]["jsonl_p0_count"], 1)
        self.assertEqual(report["event_log"]["jsonl_p2_count"], 2)
        self.assertFalse(report["event_log"]["event_count_mismatch"])
        self.assertEqual(report["vllm_p2"]["status"], "partial")
        self.assertEqual(report["vllm_p2"]["lifecycle_incomplete_count"], 1)
        self.assertEqual(report["vllm_p2"]["request_count"], 1)
        self.assertEqual(report["vllm_p2"]["correlation"]["direct"], 1)
        self.assertEqual(summary["p2_status"], "partial")
        self.assertEqual(summary["p2_request_count"], 1)
        self.assertEqual(summary["p2_direct_correlation_count"], 1)

    def test_report_exposes_stable_request_ids_and_client_server_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-p2", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(
                    Event(
                        run_id="run-p2",
                        source="mini_swe",
                        event_type="model_request.start",
                        quality="exact",
                        monotonic_time_ns=100,
                        payload={
                            "span_id": "span-match",
                            "span_type": "model_request",
                            "start_mono_ns": 100,
                            "agentmon_model_request_id": "model-match",
                        },
                    )
                )
                writer.write_event(
                    Event(
                        run_id="run-p2",
                        source="mini_swe",
                        event_type="model_request.end",
                        quality="exact",
                        monotonic_time_ns=400,
                        payload={
                            "span_id": "span-match",
                            "span_type": "model_request",
                            "end_mono_ns": 400,
                            "api_request_id": "resp-match",
                        },
                    )
                )
                writer.write_event(
                    Event(
                        run_id="run-p2",
                        source="mini_swe",
                        event_type="model_request.start",
                        quality="exact",
                        monotonic_time_ns=500,
                        payload={
                            "span_id": "span-missing",
                            "span_type": "model_request",
                            "start_mono_ns": 500,
                        },
                    )
                )
                writer.write_event(
                    Event(
                        run_id="run-p2",
                        source="mini_swe",
                        event_type="model_request.end",
                        quality="exact",
                        monotonic_time_ns=800,
                        payload={
                            "span_id": "span-missing",
                            "span_type": "model_request",
                            "end_mono_ns": 800,
                            "api_request_id": "resp-missing",
                        },
                    )
                )
            sink = VllmP2EventSink(run_id="run-p2", events_path=run_dir / "events.jsonl")
            try:
                sink.request_received(
                    agent_request_id="agent-match",
                    vllm_request_id="vllm-match",
                    correlation_quality="direct",
                    received_ns=120,
                    agentmon_model_request_id="model-match",
                    api_request_id="resp-match",
                )
                sink.request_finished(
                    agent_request_id="agent-match",
                    vllm_request_id="vllm-match",
                    correlation_quality="direct",
                    finished_ns=350,
                    agentmon_model_request_id="model-match",
                    api_request_id="resp-match",
                )
                sink.request_received(
                    agent_request_id="agent-orphan",
                    vllm_request_id="vllm-orphan",
                    correlation_quality="direct",
                    received_ns=900,
                    agentmon_model_request_id="model-orphan",
                    api_request_id="resp-orphan",
                )
                sink.request_finished(
                    agent_request_id="agent-orphan",
                    vllm_request_id="vllm-orphan",
                    correlation_quality="direct",
                    finished_ns=950,
                    agentmon_model_request_id="model-orphan",
                    api_request_id="resp-orphan",
                )
            finally:
                sink.close()

            report = build_report(run_dir)

        requests = report["vllm_p2"]["requests"]
        matched_p2 = next(row for row in requests if row.get("api_request_id") == "resp-match")
        self.assertEqual(matched_p2["canonical_request_id"], "model-match")
        self.assertEqual(matched_p2["request_id"], "model-match")
        self.assertEqual(matched_p2["response_id"], "resp-match")
        self.assertEqual(matched_p2["trajectory_request_id"], "resp-match")
        self.assertEqual(matched_p2["run_id"], "run-p2")
        self.assertIn({"kind": "api_request_id", "value": "resp-match"}, matched_p2["request_id_aliases"])

        client_server = report["client_server"]
        audit = client_server["request_identity_audit"]
        self.assertEqual(client_server["matched_request_ids"], ["model-match"])
        self.assertEqual(client_server["unmatched_request_ids"], ["resp-missing"])
        self.assertEqual(client_server["orphan_request_ids"], ["model-orphan"])
        self.assertEqual(audit["matched_count"], 1)
        self.assertEqual(audit["unmatched_p1_count"], 1)
        self.assertEqual(audit["orphan_p2_count"], 1)
        self.assertEqual(audit["unmatched_p1_requests"][0]["missing_reason"], "p1_identifiers_not_found_in_p2")

    def test_report_and_summary_compute_p2_request_latencies_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-p2", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            sink = VllmP2EventSink(run_id="run-p2", events_path=run_dir / "events.jsonl")
            try:
                sink.request_received(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    received_ns=1_000_000_000,
                )
                sink.request_queued(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    queued_ns=1_100_000_000,
                )
                sink.request_first_scheduled(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    first_scheduled_ns=1_200_000_000,
                )
                sink.request_prefill_completed(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    prefill_completed_ns=1_700_000_000,
                    prompt_tokens=100,
                    cached_prompt_tokens=25,
                    computed_prefill_tokens=75,
                    recomputed_prompt_tokens=0,
                )
                sink.request_first_token(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    first_token_ns=2_000_000_000,
                )
                sink.emit(
                    "vllm.request.engine_finished",
                    payload={
                        "agent_request_id": "agent-1",
                        "vllm_request_id": "vllm-1",
                        "correlation_quality": "direct",
                        "engine_finished_ns": 3_800_000_000,
                    },
                )
                sink.request_finished(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    finished_ns=4_000_000_000,
                    generation_tokens=12,
                    preemption_count=0,
                )
            finally:
                sink.close()

            report = build_report(run_dir)
            summary = build_run_summary(report)
            aggregate = build_aggregate([summary], reports=[report])

        latency = report["vllm_p2"]["latency"]
        tokens = report["vllm_p2"]["tokens"]
        self.assertEqual(report["vllm_p2"]["request_count"], 1)
        self.assertAlmostEqual(latency["queue"]["mean"], 0.1)
        self.assertAlmostEqual(latency["prefill"]["mean"], 0.5)
        self.assertAlmostEqual(latency["ttft"]["mean"], 1.0)
        self.assertAlmostEqual(latency["decode"]["mean"], 1.8)
        self.assertAlmostEqual(latency["e2e"]["mean"], 3.0)
        self.assertEqual(latency["quality"]["queue"], "engine_process_monotonic")
        self.assertEqual(latency["quality"]["decode"], "engine_process_monotonic")
        self.assertEqual(latency["quality"]["e2e"], "api_process_monotonic")
        self.assertEqual(latency["quality_coverage"]["decode"]["coverage"], 1.0)
        self.assertEqual(tokens["prompt_tokens"]["total"], 100)
        self.assertEqual(tokens["cached_prompt_tokens"]["total"], 25)
        self.assertEqual(tokens["computed_prefill_tokens"]["total"], 75)
        self.assertEqual(tokens["generation_tokens"]["total"], 12)
        self.assertAlmostEqual(summary["p2_queue_latency"]["mean"], 0.1)
        self.assertAlmostEqual(summary["p2_prefill_latency"]["mean"], 0.5)
        self.assertAlmostEqual(summary["p2_prefill_to_first_token_latency"]["mean"], 0.8)
        self.assertAlmostEqual(summary["p2_decode_latency"]["mean"], 1.8)
        self.assertEqual(summary["p2_latency_quality_coverage"]["decode"]["coverage"], 1.0)
        self.assertEqual(summary["p2_prompt_tokens"], 100)
        self.assertEqual(summary["p2_cached_prompt_tokens"], 25)
        self.assertEqual(summary["p2_computed_prefill_tokens"], 75)
        self.assertEqual(summary["p2_generation_tokens"], 12)
        self.assertAlmostEqual(aggregate["p2_queue_latency"]["p50"], 0.1)
        self.assertAlmostEqual(aggregate["p2_prefill_to_first_token_latency"]["p50"], 0.8)
        self.assertEqual(aggregate["p2_prompt_tokens"]["total"], 100)
        self.assertEqual(aggregate["p2_generation_tokens"]["total"], 12)

    def test_report_summary_and_aggregate_include_p2_kv_pool_states_and_npu_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-p2", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            sink = VllmP2EventSink(run_id="run-p2", events_path=run_dir / "events.jsonl")
            try:
                sink.kv_snapshot(
                    prefix_cache_status="enabled",
                    prefix_cache_queries=4,
                    prefix_cache_hits=2,
                    kv_usage_ratio=0.25,
                    pool_inputs={
                        "num_gpu_blocks": 10,
                        "block_size": 16,
                        "cache_dtype": "bf16",
                        "num_layers_per_rank": 48,
                        "num_kv_heads_per_rank": 1,
                        "head_dim": 128,
                    },
                    source="cache_config",
                )
                sink.request_prompt_cache_accounted(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    prompt_tokens=100,
                    cached_prompt_tokens=50,
                    computed_prefill_tokens=50,
                    recomputed_prompt_tokens=5,
                )
                sink.request_preempted(
                    agent_request_id="agent-1",
                    vllm_request_id="vllm-1",
                    correlation_quality="direct",
                    preemption_count=1,
                )
                sink.kv_block_event(request_id="vllm-1", allocated_block_count=8, freed_block_count=7, evicted_block_count=1)
                sink.request_npu_attribution(
                    vllm_request_id="vllm-1",
                    agent_request_id="agent-1",
                    attribution_quality="exclusive",
                    npu_utilization_integral=12.5,
                    sample_count=3,
                )
            finally:
                sink.close()

            report = build_report(run_dir)
            summary = build_run_summary(report)
            aggregate = build_aggregate([summary], reports=[report])

        kv = report["vllm_p2"]["kv_cache"]
        self.assertEqual(kv["prefix_cache_status"], "enabled")
        self.assertEqual(kv["prefix_cache_hit_rate"], 0.5)
        self.assertEqual(kv["state_counts"]["hit"], 1)
        self.assertEqual(kv["state_counts"]["recomputed"], 1)
        self.assertEqual(kv["state_counts"]["evicted"], 1)
        self.assertEqual(kv["state_counts"]["preempted"], 1)
        self.assertEqual(kv["kv_pool"]["allocated_pool_bytes_per_rank"], 3932160)
        self.assertEqual(report["vllm_p2"]["npu_attribution"]["quality_counts"]["exclusive"], 1)
        self.assertEqual(summary["p2_kv_prefix_cache_status"], "enabled")
        self.assertEqual(summary["p2_kv_pool_bytes_per_rank"], 3932160)
        self.assertEqual(summary["p2_npu_attribution_quality_counts"]["exclusive"], 1)
        self.assertEqual(aggregate["p2_kv_state_counts"]["evicted"], 1)
        self.assertEqual(aggregate["p2_npu_attribution_quality_counts"]["exclusive"], 1)

    def test_p2_prefix_cache_disabled_zero_hit_and_no_sample_semantics(self) -> None:
        disabled = build_p2_kv_report(
            prefix_cache_status="disabled",
            prefix_cache_queries=10,
            prefix_cache_hits=0,
            prompt_tokens=10,
            cached_prompt_tokens=0,
        )
        self.assertEqual(disabled["prefix_cache_status"], "disabled")
        self.assertIsNone(disabled["prefix_cache_hit_rate"])
        self.assertEqual(disabled["prefix_cache_hit_rate_quality"], "disabled")
        self.assertEqual(disabled["state_counts"]["disabled"], 1)

        zero_hit = build_p2_kv_report(
            prefix_cache_status="enabled",
            prefix_cache_queries=10,
            prefix_cache_hits=0,
            prompt_tokens=10,
            cached_prompt_tokens=0,
        )
        self.assertEqual(zero_hit["prefix_cache_status"], "enabled")
        self.assertEqual(zero_hit["prefix_cache_hit_rate"], 0.0)
        self.assertEqual(zero_hit["prefix_cache_hit_rate_quality"], "exact")
        self.assertEqual(zero_hit["state_counts"]["enabled_zero_hit"], 1)

        no_sample = build_p2_kv_report(prefix_cache_status="enabled")
        self.assertEqual(no_sample["prefix_cache_status"], "enabled")
        self.assertIsNone(no_sample["prefix_cache_hit_rate"])
        self.assertEqual(no_sample["prefix_cache_hit_rate_quality"], "unavailable")

    def test_p2_prefix_cache_conflict_when_expected_disabled_but_snapshot_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-kv-conflict",
                        "status": "success",
                        "prefix_caching_status": "disabled",
                        "data_quality": {},
                    }
                ),
                encoding="utf-8",
            )
            sink = VllmP2EventSink(run_id="run-kv-conflict", events_path=run_dir / "events.jsonl")
            try:
                sink.kv_snapshot(
                    prefix_cache_status="enabled",
                    prefix_cache_queries=10,
                    prefix_cache_hits=0,
                    source="scheduler_stats",
                )
            finally:
                sink.close()

            report = build_report(run_dir)
            summary = build_run_summary(report)

        kv = report["vllm_p2"]["kv_cache"]
        self.assertEqual(kv["prefix_cache_status"], "CONFLICT")
        self.assertTrue(kv["prefix_cache_status_conflict"])
        self.assertIn("observed_status_conflicts_with_expected", kv["prefix_cache_status_conflict_reasons"])
        self.assertEqual(summary["p2_kv_prefix_cache_status"], "CONFLICT")

    def test_request_lifecycle_keeps_engine_id_when_api_response_id_differs(self) -> None:
        engine_request_id = "chatcmpl-abc123-deadbeef"
        api_request_id = "chatcmpl-abc123"

        class Engine:
            async def add_request(self, request_id: str, *args: object, **kwargs: object) -> object:
                return types.SimpleNamespace(request_id=engine_request_id)

            async def generate(self, *args: object, request_id: str, trace_headers: object = None, **kwargs: object) -> object:
                await self.add_request(request_id, trace_headers=trace_headers)
                yield FakeRequestOutput(api_request_id)

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-lifecycle", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Engine.add_request = p2_hook.wrap_async_add_request(state)(Engine.add_request)
            Engine.generate = p2_hook.wrap_async_generate(state)(Engine.generate)
            try:
                p2_hook.emit_queued_from_request(state, FakeRequest(engine_request_id))
                p2_hook.observe_scheduler_output(state, scheduler_output_for(engine_request_id))

                async def consume() -> None:
                    async for _ in Engine().generate(request_id=api_request_id):
                        pass

                asyncio.run(consume())
            finally:
                sink.close()

            rows = read_event_rows(events_path)

        payloads = {
            row["event_type"]: row["payload"]
            for row in rows
            if row["event_type"]
            in {
                "vllm.request.received",
                "vllm.request.queued",
                "vllm.request.first_scheduled",
                "vllm.request.finished",
            }
        }
        self.assertEqual(payloads["vllm.request.received"]["vllm_request_id"], engine_request_id)
        self.assertEqual(payloads["vllm.request.queued"]["vllm_request_id"], engine_request_id)
        self.assertEqual(payloads["vllm.request.first_scheduled"]["vllm_request_id"], engine_request_id)
        self.assertEqual(payloads["vllm.request.finished"]["vllm_request_id"], engine_request_id)
        self.assertEqual(payloads["vllm.request.finished"]["api_request_id"], api_request_id)

    def test_request_lifecycle_preserves_agentmon_header_context_across_engine_events(self) -> None:
        engine_request_id = "chatcmpl-context-deadbeef"
        api_request_id = "chatcmpl-context"
        headers = {
            "X-AgentMon-Run-Id": "agent-run-1",
            "X-AgentMon-Request-Id": "model-request-1",
            "X-AgentMon-Model-Call-Id": "model-request-1",
            "X-AgentMon-Client-Request-Id": "client-request-1",
        }

        class Engine:
            async def add_request(self, request_id: str, *args: object, **kwargs: object) -> object:
                return types.SimpleNamespace(request_id=engine_request_id)

            async def generate(self, *args: object, request_id: str, trace_headers: object = None, **kwargs: object) -> object:
                await self.add_request(request_id, trace_headers=trace_headers)
                yield FakeRequestOutput(api_request_id)

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="vllm-service", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Engine.add_request = p2_hook.wrap_async_add_request(state)(Engine.add_request)
            Engine.generate = p2_hook.wrap_async_generate(state)(Engine.generate)
            try:
                request = FakeRequest(engine_request_id)
                request.trace_headers = headers
                p2_hook.emit_queued_from_request(state, request)
                p2_hook.observe_scheduler_output(state, scheduler_output_for(engine_request_id))

                async def consume() -> None:
                    async for _ in Engine().generate(request_id=api_request_id, trace_headers=headers):
                        pass

                asyncio.run(consume())
            finally:
                sink.close()

            rows = read_event_rows(events_path)
            report = build_report_from_events(rows)

        payloads = [
            row["payload"]
            for row in rows
            if row["event_type"]
            in {
                "vllm.request.received",
                "vllm.request.queued",
                "vllm.request.first_scheduled",
                "vllm.request.finished",
            }
        ]
        self.assertTrue(payloads)
        for payload in payloads:
            self.assertEqual(payload["agentmon_run_id"], "agent-run-1")
            self.assertEqual(payload["agentmon_model_request_id"], "model-request-1")
            self.assertEqual(payload["agent_request_id"], "model-request-1")
            self.assertEqual(payload["model_call_id"], "model-request-1")
            self.assertEqual(payload["client_request_id"], "client-request-1")
        request_rows = report["vllm_p2"]["requests"]
        self.assertEqual(request_rows[0]["agentmon_run_id"], "agent-run-1")
        self.assertEqual(request_rows[0]["agentmon_model_request_id"], "model-request-1")

    def test_request_id_aliases_do_not_cross_interleaved_requests(self) -> None:
        pairs = [
            ("chatcmpl-a", "chatcmpl-a-11111111"),
            ("chatcmpl-b", "chatcmpl-b-22222222"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-interleaved", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            try:
                for api_id, engine_id in pairs:
                    state.record_request_id_alias(api_id, engine_id)
                    p2_hook.emit_queued_from_request(state, FakeRequest(engine_id))
                p2_hook.observe_scheduler_output(state, scheduler_output_for(pairs[1][1]))
                p2_hook.observe_scheduler_output(state, scheduler_output_for(pairs[0][1]))
                p2_hook.observe_request_output(state, FakeRequestOutput(pairs[1][0]), fallback_request_id=pairs[1][0])
                p2_hook.observe_request_output(state, FakeRequestOutput(pairs[0][0]), fallback_request_id=pairs[0][0])
            finally:
                sink.close()

            rows = read_event_rows(events_path)

        finished_by_api_id = {
            row["payload"].get("api_request_id"): row["payload"].get("vllm_request_id")
            for row in rows
            if row["event_type"] == "vllm.request.finished"
        }
        self.assertEqual(finished_by_api_id["chatcmpl-a"], "chatcmpl-a-11111111")
        self.assertEqual(finished_by_api_id["chatcmpl-b"], "chatcmpl-b-22222222")

    def test_request_id_aliases_clean_up_after_abort_and_generate_exception(self) -> None:
        class Aborter:
            async def abort(self, request_id: str) -> str:
                return "aborted"

        class ErrorEngine:
            async def add_request(self, request_id: str, *args: object, **kwargs: object) -> object:
                return types.SimpleNamespace(request_id="chatcmpl-error-feedface")

            async def generate(self, *args: object, request_id: str, **kwargs: object) -> object:
                await self.add_request(request_id)
                raise RuntimeError("boom")
                yield FakeRequestOutput(request_id)

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-cleanup", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            Aborter.abort = p2_hook.wrap_async_abort(state)(Aborter.abort)
            ErrorEngine.add_request = p2_hook.wrap_async_add_request(state)(ErrorEngine.add_request)
            ErrorEngine.generate = p2_hook.wrap_async_generate(state)(ErrorEngine.generate)
            try:
                state.record_request_id_alias("chatcmpl-abort", "chatcmpl-abort-deadbeef")
                state.add_generation_tokens("chatcmpl-abort-deadbeef", 5)
                state.record_api_generation_tokens("chatcmpl-abort-deadbeef", 5, "delta")
                self.assertEqual(asyncio.run(Aborter().abort("chatcmpl-abort")), "aborted")
                p2_hook.observe_request_output(
                    state,
                    FakeRequestOutput("chatcmpl-abort"),
                    fallback_request_id="chatcmpl-abort",
                )

                async def consume_error() -> None:
                    async for _ in ErrorEngine().generate(request_id="chatcmpl-error"):
                        pass

                with self.assertRaises(RuntimeError):
                    state.record_api_generation_tokens("chatcmpl-error-feedface", 3, "delta")
                    asyncio.run(consume_error())
                p2_hook.observe_request_output(
                    state,
                    FakeRequestOutput("chatcmpl-error"),
                    fallback_request_id="chatcmpl-error",
                )
            finally:
                sink.close()

            rows = read_event_rows(events_path)

        cancelled = next(row for row in rows if row["event_type"] == "vllm.request.cancelled")
        self.assertEqual(cancelled["payload"]["vllm_request_id"], "chatcmpl-abort-deadbeef")
        self.assertEqual(cancelled["payload"]["api_request_id"], "chatcmpl-abort")
        finished = [
            row["payload"]["vllm_request_id"]
            for row in rows
            if row["event_type"] == "vllm.request.finished"
        ]
        self.assertIn("chatcmpl-abort", finished)
        self.assertIn("chatcmpl-error", finished)
        self.assertNotIn("chatcmpl-abort-deadbeef", finished)
        self.assertNotIn("chatcmpl-error-feedface", finished)
        self.assertEqual(state._generation_token_counts, {})
        self.assertEqual(state._api_generation_token_counts, {})

    def test_runtime_hook_disabled_env_has_no_events_or_state(self) -> None:
        old_enable = os.environ.pop("AGENTMON_VLLM_P2_ENABLE", None)
        old_events_path = os.environ.get("AGENTMON_EVENTS_PATH")
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            os.environ["AGENTMON_EVENTS_PATH"] = str(events_path)
            try:
                p2_hook.uninstall_for_tests()
                result = p2_hook.install_from_env()
                self.assertEqual(result["status"], "disabled")
                self.assertFalse(events_path.exists())
                self.assertIsNone(p2_hook._STATE)
            finally:
                if old_enable is not None:
                    os.environ["AGENTMON_VLLM_P2_ENABLE"] = old_enable
                if old_events_path is None:
                    os.environ.pop("AGENTMON_EVENTS_PATH", None)
                else:
                    os.environ["AGENTMON_EVENTS_PATH"] = old_events_path

    def test_runtime_hook_patches_real_v1_targets_and_preserves_return_values(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-hook", events_path=events_path)
            old_visible = os.environ.get("ASCEND_RT_VISIBLE_DEVICES")
            os.environ["ASCEND_RT_VISIBLE_DEVICES"] = "0,1,2,3"
            try:
                result = p2_hook.install(sink=sink, force=True)
                self.assertEqual(result["status"], "supported")
                self.assertTrue(any("AsyncLLM.add_request" in item for item in result["patched"]))
                self.assertTrue(any("LLMEngine.add_request" in item for item in result["patched"]))
                self.assertTrue(any("EngineCore.step_with_batch_queue" in item for item in result["patched"]))
                self.assertTrue(result["engine_core_step_with_batch_queue_hooked"])

                from vllm.v1.core.kv_cache_manager import KVCacheManager
                from vllm.v1.core.sched.scheduler import Scheduler
                from vllm.v1.engine.async_llm import AsyncLLM
                from vllm.v1.engine.core import EngineCore
                from vllm.v1.engine.llm_engine import LLMEngine

                async_engine = AsyncLLM()
                self.assertEqual(
                    asyncio.run(
                        async_engine.add_request(
                            "async-req",
                            trace_headers={"X-AgentMon-Request-Id": "agent-async"},
                        )
                    ),
                    types.SimpleNamespace(request_id="async-req"),
                )

                engine = LLMEngine()
                self.assertEqual(
                    engine.add_request(
                        "req-1",
                        trace_headers={"X-AgentMon-Request-Id": "agent-1"},
                    ),
                    "req-1",
                )
                self.assertEqual([item.request_id for item in engine.step()], ["req-1"])

                core = EngineCore()
                request = FakeRequest("req-core")
                self.assertEqual(core.add_request(request), "core-added")
                core_result = core.step()
                self.assertEqual(core_result[1], True)
                async_core_result = core.step_with_batch_queue()
                self.assertEqual(async_core_result, (None, True))

                scheduler = Scheduler()
                self.assertEqual(scheduler.add_request(FakeRequest("req-scheduler")), "scheduler-added")
                scheduler_output = scheduler.schedule()
                self.assertEqual(scheduler_output.scheduled_new_reqs[0].req_id, "req-scheduler")
                stats = scheduler.make_stats()
                self.assertEqual(stats.prefix_cache_stats.queries, 10)

                kv = KVCacheManager(enable_caching=False)
                self.assertIs(kv.make_prefix_cache_stats(), kv.stats)
            finally:
                if old_visible is None:
                    os.environ.pop("ASCEND_RT_VISIBLE_DEVICES", None)
                else:
                    os.environ["ASCEND_RT_VISIBLE_DEVICES"] = old_visible
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)

            rows = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]

        event_types = [row["event_type"] for row in rows]
        self.assertIn("vllm.adapter.capability", event_types)
        capability = next(row for row in rows if row["event_type"] == "vllm.adapter.capability")
        self.assertEqual(capability["payload"]["visible_devices"], ["0", "1", "2", "3"])
        self.assertEqual(capability["payload"]["visible_devices_source"], "ASCEND_RT_VISIBLE_DEVICES")
        self.assertEqual(capability["payload"]["details"]["visible_devices"], ["0", "1", "2", "3"])
        self.assertTrue(capability["payload"]["details"]["engine_core_step_hooked"])
        self.assertTrue(
            capability["payload"]["details"]["engine_core_step_with_batch_queue_hooked"]
        )
        self.assertEqual(
            capability["payload"]["details"]["engine_token_boundary_collection"],
            "supported",
        )
        self.assertIn("vllm.request.received", event_types)
        self.assertIn("vllm.request.finished", event_types)
        self.assertIn("vllm.request.queued", event_types)
        self.assertIn("vllm.request.first_scheduled", event_types)
        self.assertIn("vllm.request.preempted", event_types)
        self.assertIn("vllm.kv.snapshot", event_types)
        self.assertIn("vllm.scheduler.snapshot", event_types)
        received_agent_ids = [
            row["payload"].get("agent_request_id")
            for row in rows
            if row["event_type"] == "vllm.request.received"
        ]
        self.assertIn("agent-async", received_agent_ids)
        self.assertIn("agent-1", received_agent_ids)
        disabled_snapshots = [
            row
            for row in rows
            if row["event_type"] == "vllm.kv.snapshot"
            and row["payload"].get("prefix_cache_status") == "disabled"
        ]
        self.assertTrue(disabled_snapshots)
        self.assertIsNone(disabled_snapshots[-1]["payload"]["prefix_cache_queries"])
        report = build_report_from_events(rows)
        self.assertEqual(report["vllm_p2"]["kv_cache"]["prefix_cache_status"], "disabled")
        self.assertIsNone(report["vllm_p2"]["kv_cache"]["prefix_cache_hit_rate"])
        self.assertEqual(report["vllm_p2"]["scheduler"]["running_requests"]["max"], 3)
        self.assertEqual(report["vllm_p2"]["scheduler"]["waiting_requests"]["max"], 2)

    def test_runtime_observation_failures_never_change_original_results(self) -> None:
        class HostileResult:
            @property
            def request_id(self) -> str:
                raise RuntimeError("result request id unavailable")

        class HostileRequest:
            @property
            def request_id(self) -> str:
                raise RuntimeError("request id unavailable")

        class AsyncEngine:
            async def add_request(self, request_id: str) -> object:
                return HostileResult()

        class Core:
            def add_request(self, request: object) -> str:
                return "core-result"

        class KVManager:
            @property
            def usage(self) -> float:
                raise RuntimeError("usage unavailable")

            def allocate_slots(self, request: object) -> list[int]:
                return [7, 8]

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-fail-open", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            AsyncEngine.add_request = p2_hook.wrap_async_add_request(state)(AsyncEngine.add_request)
            Core.add_request = p2_hook.wrap_engine_core_add_request(state)(Core.add_request)
            KVManager.allocate_slots = p2_hook.wrap_kv_allocate_slots(state)(KVManager.allocate_slots)
            try:
                async_result = asyncio.run(AsyncEngine().add_request("api-request"))
                self.assertIsInstance(async_result, HostileResult)
                self.assertEqual(Core().add_request(HostileRequest()), "core-result")
                self.assertEqual(KVManager().allocate_slots(HostileRequest()), [7, 8])
            finally:
                sink.close()

            rows = read_event_rows(events_path)

        errors = [row for row in rows if row["event_type"] == "vllm.adapter.error"]
        self.assertGreaterEqual(len(errors), 3)

    def test_deep_capability_separates_required_and_optional_targets(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        old_deep = os.environ.get("AGENTMON_VLLM_P2_DEEP")
        os.environ["AGENTMON_VLLM_P2_DEEP"] = "1"
        from vllm.v1.core.kv_cache_manager import KVCacheManager

        delattr(KVCacheManager, "evict_blocks")
        with tempfile.TemporaryDirectory() as tmp:
            sink = VllmP2EventSink(
                run_id="run-deep-partial",
                events_path=Path(tmp) / "events.jsonl",
            )
            try:
                result = p2_hook.install(sink=sink, force=True)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["deep_missing_required_targets"], [])
                self.assertEqual(
                    result["deep_missing_optional_targets"],
                    ["KVCacheManager.evict_blocks"],
                )
            finally:
                if old_deep is None:
                    os.environ.pop("AGENTMON_VLLM_P2_DEEP", None)
                else:
                    os.environ["AGENTMON_VLLM_P2_DEEP"] = old_deep
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)

    def test_runtime_hook_optional_kv_block_hot_path_records_request_usage_and_blocks(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        old_kv_blocks = os.environ.get("AGENTMON_VLLM_P2_HOOK_KV_BLOCKS")
        os.environ["AGENTMON_VLLM_P2_HOOK_KV_BLOCKS"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-kv-hot", events_path=events_path)
            try:
                result = p2_hook.install(sink=sink, force=True)
                self.assertTrue(any("KVCacheManager.allocate_slots" in item for item in result["patched"]))

                from vllm.v1.core.kv_cache_manager import KVCacheManager

                manager = KVCacheManager(enable_caching=True)
                request = FakeRequest("req-kv-hot")
                request.trace_headers = {
                    "X-AgentMon-Run-Id": "agent-run-kv",
                    "X-AgentMon-Request-Id": "model-request-kv",
                }
                self.assertEqual(manager.allocate_slots(request), [1, 2, 3])
                manager.free(request)
                manager.evict_blocks([9, 10])
            finally:
                if old_kv_blocks is None:
                    os.environ.pop("AGENTMON_VLLM_P2_HOOK_KV_BLOCKS", None)
                else:
                    os.environ["AGENTMON_VLLM_P2_HOOK_KV_BLOCKS"] = old_kv_blocks
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)

            rows = read_event_rows(events_path)

        block_events = [row for row in rows if row["event_type"] == "vllm.kv.block_event"]
        self.assertEqual(len(block_events), 3)
        allocated = next(row for row in block_events if row["payload"].get("allocated_block_count") == 3)
        freed = next(row for row in block_events if row["payload"].get("freed_block_count") == 3)
        evicted = next(row for row in block_events if row["payload"].get("evicted_block_count") == 2)
        self.assertEqual(allocated["payload"]["agentmon_run_id"], "agent-run-kv")
        self.assertEqual(allocated["payload"]["agentmon_model_request_id"], "model-request-kv")
        self.assertEqual(allocated["payload"]["kv_usage_before"], 0.5)
        self.assertEqual(allocated["payload"]["kv_usage_after"], 0.75)
        self.assertEqual(freed["payload"]["kv_usage_after"], 0.25)
        self.assertIsNone(evicted["payload"].get("agentmon_run_id"))

    def test_get_computed_blocks_records_cache_accounting_not_prefill_completion(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        old_hot_path = os.environ.get("AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS")
        os.environ["AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-cache-accounting", events_path=events_path)
            try:
                result = p2_hook.install(sink=sink, force=True)
                self.assertTrue(any("KVCacheManager.get_computed_blocks" in item for item in result["patched"]))
                from vllm.v1.core.kv_cache_manager import KVCacheManager

                manager = KVCacheManager(enable_caching=True)
                request = FakeRequest("req-cache-accounting")
                self.assertEqual(manager.get_computed_blocks(request), ([1], 2))
            finally:
                if old_hot_path is None:
                    os.environ.pop("AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS", None)
                else:
                    os.environ["AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS"] = old_hot_path
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)

            rows = read_event_rows(events_path)

        cache_event = next(row for row in rows if row["event_type"] == "vllm.request.prompt_cache_accounted")
        self.assertFalse(any(row["event_type"] == "vllm.request.prefill_completed" for row in rows))
        self.assertEqual(cache_event["payload"]["prompt_tokens"], 5)
        self.assertEqual(cache_event["payload"]["cached_prompt_tokens"], 2)
        self.assertEqual(cache_event["payload"]["computed_prefill_tokens"], 3)

    def test_deep_hook_records_execution_anchored_prefill_scheduler_batch_and_kv_lifecycle(self) -> None:
        fake_module_names = install_fake_vllm_modules()
        old_deep = os.environ.get("AGENTMON_VLLM_P2_DEEP")
        os.environ["AGENTMON_VLLM_P2_DEEP"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-deep", events_path=events_path)
            try:
                result = p2_hook.install(sink=sink, force=True)
                self.assertTrue(any("Scheduler.update_from_output" in item for item in result["patched"]))
                self.assertTrue(any("KVCacheManager.cache_blocks" in item for item in result["patched"]))
                self.assertEqual(result["deep_missing_required_targets"], [])
                self.assertEqual(result["deep_missing_optional_targets"], [])

                from vllm.v1.core.kv_cache_manager import KVCacheManager
                from vllm.v1.core.sched.scheduler import Scheduler

                scheduler = Scheduler()
                request = FakeRequest("req-scheduler")
                request.trace_headers = {
                    "X-AgentMon-Run-Id": "agent-run-deep",
                    "X-AgentMon-Request-Id": "model-request-deep",
                }
                scheduler.add_request(request)
                scheduler_output = scheduler.schedule()
                engine_outputs = scheduler.update_from_output(scheduler_output, object())
                self.assertIn(0, engine_outputs)

                manager = KVCacheManager(enable_caching=True)
                self.assertEqual(manager.get_computed_blocks(request), ([1], 2))
                self.assertEqual(manager.allocate_slots(request), [1, 2, 3])
                manager.cache_blocks(request, 5)
                manager.free(request)
                manager.evict_blocks([9, 10])
                self.assertTrue(manager.reset_prefix_cache())
            finally:
                if old_deep is None:
                    os.environ.pop("AGENTMON_VLLM_P2_DEEP", None)
                else:
                    os.environ["AGENTMON_VLLM_P2_DEEP"] = old_deep
                sink.close()
                p2_hook.uninstall_for_tests()
                clear_fake_modules(fake_module_names)

            rows = read_event_rows(events_path)

        scheduled = next(row for row in rows if row["event_type"] == "vllm.scheduler.iteration.scheduled")
        completed = next(row for row in rows if row["event_type"] == "vllm.scheduler.iteration.completed")
        prefill = next(row for row in rows if row["event_type"] == "vllm.request.prefill_completed")
        self.assertEqual(scheduled["payload"]["batch_request_count"], 1)
        self.assertEqual(scheduled["payload"]["prefill_request_count"], 1)
        self.assertEqual(scheduled["payload"]["decode_request_count"], 0)
        self.assertEqual(
            completed["payload"]["scheduler_iteration_id"],
            scheduled["payload"]["scheduler_iteration_id"],
        )
        self.assertGreaterEqual(
            prefill["payload"]["prefill_completed_ns"],
            scheduled["payload"]["schedule_end_ns"],
        )
        self.assertEqual(prefill["payload"]["prefill_boundary_source"], "scheduler.update_from_output")
        self.assertEqual(prefill["payload"]["prefill_boundary_quality"], "engine_execution_observed")
        self.assertEqual(prefill["payload"]["agentmon_model_request_id"], "model-request-deep")
        operations = {
            row["payload"].get("operation")
            for row in rows
            if row["event_type"] == "vllm.kv.block_event"
        }
        self.assertTrue({"lookup", "allocate", "cache", "free", "evict"}.issubset(operations))
        self.assertTrue(any(row["event_type"] == "vllm.kv.reset" for row in rows))

        report = build_report_from_events(rows)["vllm_p2"]
        self.assertEqual(
            {row.get("vllm_request_id") for row in report["requests"]},
            {"req-scheduler", "req-preempted"},
        )
        self.assertFalse(any(row.get("vllm_request_id") is None for row in report["requests"]))
        self.assertEqual(report["scheduler"]["iterations"]["scheduled_count"], 1)
        self.assertEqual(report["scheduler"]["iterations"]["completed_count"], 1)
        self.assertEqual(report["kv_cache"]["block_events"]["operation_counts"]["cache"], 1)
        self.assertEqual(report["kv_cache"]["block_events"]["operation_counts"]["evict"], 1)
        request_row = next(row for row in report["requests"] if row.get("vllm_request_id") == "req-scheduler")
        self.assertEqual(request_row["scheduler_iteration_count"], 1)
        self.assertEqual(request_row["prefill_iteration_count"], 1)
        self.assertIn("vllm.request.prefill_completed", request_row["lifecycle_event_types"])
        self.assertIsNotNone(request_row["kv_residency_s"])
        self.assertEqual(request_row["kv_residency_quality"], "exact_request_lifecycle")

    def test_runtime_hook_unsupported_version_does_not_patch(self) -> None:
        fake_module_names = install_fake_vllm_modules(version="0.19.0")
        try:
            result = p2_hook.install(force=True)
            self.assertEqual(result["status"], "unsupported")
            from vllm.v1.engine.llm_engine import LLMEngine

            self.assertFalse(getattr(LLMEngine.add_request, p2_hook.INSTALLED_ATTR, False))
        finally:
            p2_hook.uninstall_for_tests()
            clear_fake_modules(fake_module_names)

    def test_runtime_hook_prefers_imported_module_version_over_metadata(self) -> None:
        fake_module_names = install_fake_vllm_modules(version="0.19.0")
        try:
            with patch.object(p2_version.importlib.metadata, "version", return_value="0.18.0"):
                result = p2_hook.install(force=True)
            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["vllm_version_status"], "mismatch")
            self.assertEqual(result["vllm_version_evidence"]["compatibility_status"], "mismatch")
            self.assertEqual(
                result["vllm_version_evidence"]["selected_source"],
                "module.__version__",
            )
            self.assertEqual(result["vllm_version_evidence"]["version_source_consistency"], "conflict")
            self.assertEqual(result["vllm_version_evidence"]["source_consistency"], "conflict")
            from vllm.v1.engine.llm_engine import LLMEngine

            self.assertFalse(getattr(LLMEngine.add_request, p2_hook.INSTALLED_ATTR, False))
        finally:
            p2_hook.uninstall_for_tests()
            clear_fake_modules(fake_module_names)


def build_report_from_events(rows: list[dict[str, object]]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": "run-hook", "status": "success", "data_quality": {}}),
            encoding="utf-8",
        )
        (run_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        return build_report(run_dir)


def read_event_rows(events_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]


def scheduler_output_for(request_id: str) -> object:
    return types.SimpleNamespace(
        scheduled_new_reqs=[
            types.SimpleNamespace(
                req_id=request_id,
                prompt_token_ids=[1, 2, 3],
                num_computed_tokens=0,
            )
        ],
        preempted_req_ids=[],
    )


def build_p2_kv_report(
    *,
    prefix_cache_status: str,
    prefix_cache_queries: int | None = None,
    prefix_cache_hits: int | None = None,
    prompt_tokens: int | None = None,
    cached_prompt_tokens: int | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": "run-kv", "status": "success", "data_quality": {}}),
            encoding="utf-8",
        )
        sink = VllmP2EventSink(run_id="run-kv", events_path=run_dir / "events.jsonl")
        try:
            sink.kv_snapshot(
                prefix_cache_status=prefix_cache_status,
                prefix_cache_queries=prefix_cache_queries,
                prefix_cache_hits=prefix_cache_hits,
            )
            if prompt_tokens is not None or cached_prompt_tokens is not None:
                sink.request_prompt_cache_accounted(
                    agent_request_id=None,
                    vllm_request_id="req-kv",
                    correlation_quality="server_generated",
                    prompt_tokens=prompt_tokens,
                    cached_prompt_tokens=cached_prompt_tokens,
                )
        finally:
            sink.close()
        return build_report(run_dir)["vllm_p2"]["kv_cache"]


class FakeCompletion:
    def __init__(self) -> None:
        self.token_ids = [101, 102]
        self.finish_reason = "stop"


class FakeRequestOutput:
    def __init__(self, request_id: str, *, finished: bool = True) -> None:
        self.request_id = request_id
        self.finished = finished
        self.outputs = [FakeCompletion()]
        self.prompt_token_ids = [1, 2, 3]
        self.num_cached_tokens = 0


class FakeRequest:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.trace_headers = None
        self.num_prompt_tokens = 5
        self.num_cached_tokens = -1
        self.num_computed_tokens = 0
        self.num_preemptions = 0
        self.num_tokens = 5
        self.output_token_ids = []
        self.status = types.SimpleNamespace(name="WAITING")


class FakeNewRequestData:
    def __init__(self, req_id: str) -> None:
        self.req_id = req_id
        self.prompt_token_ids = [1, 2, 3]
        self.num_computed_tokens = 0


class FakeSchedulerOutput:
    def __init__(self) -> None:
        self.scheduled_new_reqs = [FakeNewRequestData("req-scheduler")]
        self.scheduled_cached_reqs = types.SimpleNamespace(
            req_ids=[],
            resumed_req_ids=set(),
            num_computed_tokens=[],
        )
        self.num_scheduled_tokens = {"req-scheduler": 5}
        self.total_num_scheduled_tokens = 5
        self.preempted_req_ids = {"req-preempted"}
        self.finished_req_ids = set()


class FakePrefixStats:
    def __init__(self, *, queries: int = 10, hits: int = 4) -> None:
        self.queries = queries
        self.hits = hits
        self.requests = 1


class FakeSchedulerStats:
    def __init__(self) -> None:
        self.prefix_cache_stats = FakePrefixStats()
        self.kv_cache_usage = 0.25
        self.kv_cache_eviction_events = [types.SimpleNamespace(block_ids=[1, 2])]
        self.num_running_reqs = 3
        self.num_waiting_reqs = 2
        self.num_scheduled_reqs = 4
        self.num_scheduled_tokens = 128


class FakeCacheConfig:
    def __init__(self) -> None:
        self.enable_prefix_caching = False
        self.num_gpu_blocks = 10
        self.block_size = 16
        self.cache_dtype = "bf16"


class FakeParallelConfig:
    pass


class FakeModelConfig:
    model = "qwen3-coder"

    def get_num_layers_by_block_type(self, parallel_config: object, block_type: str = "attention") -> int:
        return 48

    def get_num_kv_heads(self, parallel_config: object) -> int:
        return 1

    def get_head_size(self) -> int:
        return 128


class FakeVllmConfig:
    def __init__(self) -> None:
        self.cache_config = FakeCacheConfig()
        self.scheduler_config = types.SimpleNamespace(async_scheduling=True)
        self.parallel_config = FakeParallelConfig()
        self.model_config = FakeModelConfig()


def install_fake_vllm_modules(*, version: str = "0.18.0") -> list[str]:
    names = [
        "vllm",
        "vllm.v1",
        "vllm.v1.engine",
        "vllm.v1.engine.llm_engine",
        "vllm.v1.engine.core",
        "vllm.v1.engine.async_llm",
        "vllm.v1.core",
        "vllm.v1.core.sched",
        "vllm.v1.core.sched.scheduler",
        "vllm.v1.core.kv_cache_manager",
    ]
    for name in names:
        module = types.ModuleType(name)
        if name == "vllm":
            module.__version__ = version
        sys.modules[name] = module

    class LLMEngine:
        model_config = FakeModelConfig()

        def add_request(self, request_id: str, *args: object, **kwargs: object) -> str:
            return request_id

        def step(self) -> list[FakeRequestOutput]:
            return [FakeRequestOutput("req-1")]

    class EngineCore:
        def add_request(self, request: FakeRequest, request_wave: int = 0) -> str:
            return "core-added"

        def step(self) -> tuple[dict[int, object], bool]:
            outputs = types.SimpleNamespace(
                scheduler_stats=FakeSchedulerStats(),
                outputs=[
                    types.SimpleNamespace(
                        request_id="req-core",
                        new_token_ids=[101],
                        finished=True,
                        finish_reason="stop",
                        num_cached_tokens=0,
                    )
                ],
            )
            return {0: outputs}, True

        def step_with_batch_queue(self) -> tuple[dict[int, object] | None, bool]:
            return None, True

    class AsyncLLM:
        async def add_request(self, request_id: str, *args: object, **kwargs: object) -> object:
            return types.SimpleNamespace(request_id=request_id)

        async def generate(self, *args: object, **kwargs: object) -> object:
            yield FakeRequestOutput(str(kwargs.get("request_id") or "async-req"))

        async def abort(self, request_id: str, internal: bool = False) -> None:
            return None

    class Scheduler:
        def __init__(self) -> None:
            self.cache_config = FakeCacheConfig()
            self.vllm_config = FakeVllmConfig()
            self.parallel_config = self.vllm_config.parallel_config
            self.block_size = 16
            self.requests: dict[str, FakeRequest] = {}
            self.running: list[FakeRequest] = []
            self.waiting: list[FakeRequest] = []
            self.skipped_waiting: list[FakeRequest] = []

        def add_request(self, request: FakeRequest) -> str:
            self.requests[request.request_id] = request
            self.waiting.append(request)
            return "scheduler-added"

        def schedule(self) -> FakeSchedulerOutput:
            output = FakeSchedulerOutput()
            request = self.requests.get("req-scheduler")
            if request is not None:
                request.num_computed_tokens = 5
                request.status = types.SimpleNamespace(name="RUNNING")
                self.running = [request]
                self.waiting = []
            return output

        def update_from_output(self, scheduler_output: FakeSchedulerOutput, model_output: object) -> dict[int, object]:
            return {
                0: types.SimpleNamespace(
                    outputs=[types.SimpleNamespace(request_id="req-scheduler")],
                )
            }

        def make_stats(self) -> FakeSchedulerStats:
            return FakeSchedulerStats()

    class KVCacheManager:
        def __init__(self, *, enable_caching: bool = True) -> None:
            self.enable_caching = enable_caching
            self.usage = 0.5
            self.stats = FakePrefixStats()
            self.blocks_by_request: dict[str, list[int]] = {}

        def make_prefix_cache_stats(self) -> FakePrefixStats:
            return self.stats

        def get_computed_blocks(self, request: FakeRequest) -> tuple[list[int], int]:
            return [1], 2

        def allocate_slots(self, request: FakeRequest) -> list[int]:
            self.usage = 0.75
            self.blocks_by_request[request.request_id] = [1, 2, 3]
            return self.blocks_by_request[request.request_id]

        def cache_blocks(self, request: FakeRequest, num_computed_tokens: int = 0) -> None:
            self.blocks_by_request.setdefault(request.request_id, [1, 2, 3])

        def get_block_ids(self, request_id: str) -> list[int]:
            return self.blocks_by_request.get(request_id, [])

        def free(self, request: FakeRequest) -> None:
            self.blocks_by_request.pop(request.request_id, None)
            self.usage = 0.25

        def evict_blocks(self, block_ids: list[int]) -> None:
            self.usage = 0.2

        def reset_prefix_cache(self) -> bool:
            self.blocks_by_request.clear()
            self.usage = 0.0
            return True

    sys.modules["vllm.v1.engine.llm_engine"].LLMEngine = LLMEngine
    sys.modules["vllm.v1.engine.core"].EngineCore = EngineCore
    sys.modules["vllm.v1.engine.async_llm"].AsyncLLM = AsyncLLM
    sys.modules["vllm.v1.core.sched.scheduler"].Scheduler = Scheduler
    sys.modules["vllm.v1.core.kv_cache_manager"].KVCacheManager = KVCacheManager
    return names


def clear_fake_modules(names: list[str]) -> None:
    for name in names:
        sys.modules.pop(name, None)
