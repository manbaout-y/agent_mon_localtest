import json
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

from agentmon.adapters.inference.vllm_018 import hook as p2_hook
from agentmon.adapters.inference.vllm_018.events import VllmP2EventSink
from agentmon.adapters.inference.vllm_018.importer import is_p2_run_capability_event
from agentmon.adapters.inference.vllm_018.moe import (
    decode_route_chunk,
    moe_chunk_tokens_from_env,
    moe_mode_from_env,
    routed_expert_events,
)
from agentmon.adapters.inference.vllm_018.p2_index import build_p2_batch_index
from agentmon.reporting.report import summarize_vllm_p2_events
from agentmon.runtime.p2_import import select_p2_events_for_api_request_ids


class MoeRoutingTests(unittest.TestCase):
    def test_capability_scope_is_owned_by_the_vllm_adapter(self) -> None:
        self.assertTrue(
            is_p2_run_capability_event({"event_type": "vllm.moe.routing.capability"})
        )
        self.assertFalse(
            is_p2_run_capability_event({"event_type": "vllm.scheduler.snapshot"})
        )

    def test_mode_and_chunk_configuration_are_explicit_and_bounded(self) -> None:
        self.assertEqual(moe_mode_from_env({}), "off")
        self.assertEqual(moe_mode_from_env({"AGENTMON_VLLM_P2_MOE": "summary"}), "summary")
        self.assertEqual(moe_mode_from_env({"AGENTMON_VLLM_P2_MOE": "trace"}), "trace")
        self.assertEqual(moe_mode_from_env({"AGENTMON_VLLM_P2_MOE": "invalid"}), "off")
        self.assertEqual(
            moe_chunk_tokens_from_env({"AGENTMON_VLLM_P2_MOE_CHUNK_TOKENS": "0"}),
            1,
        )

    def test_trace_chunks_round_trip_without_losing_logical_expert_ids(self) -> None:
        routes = np.array(
            [
                [[0, 1], [2, 3]],
                [[1, 2], [2, 0]],
                [[3, 0], [1, 2]],
            ],
            dtype=np.int64,
        )
        completion = types.SimpleNamespace(token_ids=[20, 21], routed_experts=routes)
        output = types.SimpleNamespace(prompt_token_ids=[10, 11], outputs=[completion])

        events = routed_expert_events(
            output,
            mode="trace",
            chunk_tokens=2,
            prompt_tokens=2,
            configured_expert_count=4,
        )

        summary = events[0][1]
        chunks = [payload for event_type, payload in events if event_type.endswith(".chunk")]
        rebuilt = np.concatenate([decode_route_chunk(payload) for payload in chunks], axis=0)
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(summary["token_alignment"], "MATCH")
        self.assertEqual(summary["shape"], [3, 2, 2])
        self.assertEqual(summary["trace_chunk_count"], 2)
        np.testing.assert_array_equal(rebuilt, routes.astype("<i4"))

    def test_missing_routed_experts_stays_unresolved(self) -> None:
        output = types.SimpleNamespace(
            prompt_token_ids=[1],
            outputs=[types.SimpleNamespace(token_ids=[2], routed_experts=None)],
        )
        events = routed_expert_events(
            output,
            mode="summary",
            chunk_tokens=1,
            prompt_tokens=1,
        )
        self.assertEqual(events[0][1]["status"], "UNRESOLVED")
        self.assertIn("routed_experts_unavailable", events[0][1]["limitations"])

    def test_single_run_import_selection_keeps_moe_request_and_capability_events(self) -> None:
        def event(event_type, payload, when):
            return {
                "source": "vllm_p2",
                "event_type": event_type,
                "monotonic_time_ns": when,
                "payload": payload,
            }

        identity = {"api_request_id": "api-moe", "vllm_request_id": "engine-moe"}
        rows = [
            event("vllm.moe.routing.capability", {"status": "SUPPORTED"}, 1),
            event("vllm.request.received", identity, 2),
            event("vllm.moe.routing.summary", {**identity, "status": "COMPLETE"}, 3),
            event("vllm.moe.routing.chunk", {**identity, "chunk_index": 0}, 4),
            event("vllm.request.finished", identity, 5),
        ]
        selected = select_p2_events_for_api_request_ids(
            rows,
            {"api-moe"},
            include_window_extras=False,
            window_margin_seconds=1.0,
            include_global_events=True,
        )
        self.assertEqual(
            {row["event_type"] for row in selected},
            {
                "vllm.moe.routing.capability",
                "vllm.request.received",
                "vllm.moe.routing.summary",
                "vllm.moe.routing.chunk",
                "vllm.request.finished",
            },
        )

    def test_hook_report_and_batch_index_keep_moe_events_request_attributed(self) -> None:
        routes = np.array(
            [
                [[0, 1], [1, 2]],
                [[1, 2], [2, 3]],
                [[2, 3], [3, 0]],
            ],
            dtype=np.int32,
        )
        completion = types.SimpleNamespace(
            token_ids=[20, 21],
            routed_experts=routes,
            finish_reason="stop",
        )
        output = types.SimpleNamespace(
            request_id="request-moe",
            prompt_token_ids=[10, 11],
            num_cached_tokens=0,
            outputs=[completion],
            finished=True,
            finish_reason="stop",
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="run-moe", events_path=events_path)
            state = p2_hook.VllmP2HookState(sink=sink)
            state.moe_mode = "trace"
            state.moe_chunk_tokens = 2
            state.moe_expert_count = 4
            state.moe_return_routed_experts_enabled = True
            try:
                p2_hook.emit_moe_routing_capability(state)
                p2_hook.observe_request_output(
                    state,
                    output,
                    agent_request_id="model-call-moe",
                    fallback_request_id="request-moe",
                    output_kind="cumulative",
                )
            finally:
                sink.close()
            rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            report = summarize_vllm_p2_events(rows)
            index = build_p2_batch_index(events_path)

        summary_event = next(
            row for row in rows if row["event_type"] == "vllm.moe.routing.summary"
        )
        self.assertEqual(summary_event["payload"]["vllm_request_id"], "request-moe")
        self.assertEqual(
            summary_event["payload"]["agentmon_model_request_id"],
            "model-call-moe",
        )
        self.assertEqual(report["moe_routing"]["status"], "complete")
        self.assertTrue(report["moe_routing"]["enable_return_routed_experts"])
        self.assertEqual(report["moe_routing"]["request_count"], 1)
        self.assertEqual(report["requests"][0]["moe_routing_chunk_count"], 2)
        self.assertIn(
            summary_event["event_id"],
            {event["event_id"] for event in index["request_events"]},
        )
        self.assertIn(
            "vllm.moe.routing.capability",
            {event["event_type"] for event in index["global_events"]},
        )


if __name__ == "__main__":
    unittest.main()
