from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentmon.reporting.canonical import build_canonical_tables, write_canonical_tables


class CanonicalDatasetTests(unittest.TestCase):
    def test_builds_stable_request_ids_and_exact_proxy_boundaries(self) -> None:
        events = [
            event(
                "model_request.start",
                90,
                {
                    "span_id": "model-1",
                    "span_type": "model_request",
                    "model_call_id": "model-1",
                    "client_request_id": "model-1",
                    "start_mono_ns": 90,
                    "clock_domain": "host:boot",
                    "context_compression_status": "COMPLETE",
                    "context_compression_trigger_strategy": "token_ratio",
                    "context_compression_trigger_reason": "ratio_reached",
                    "context_compression_strategy": "recent_groups",
                    "context_compression_applied": True,
                    "context_compression_original_tokens": 80,
                    "context_compression_compressed_tokens": 20,
                    "context_compression_removed_tokens": 60,
                    "context_compression_target_tokens": 20,
                    "context_compression_target_achieved": True,
                    "context_compression_measurement_quality": "estimated_test",
                    "context_compression_limitations": ["test_limit"],
                },
            ),
            event(
                "model_request.end",
                510,
                {
                    "span_id": "model-1",
                    "span_type": "model_request",
                    "end_mono_ns": 510,
                    "duration_ns": 420,
                    "api_request_id": "api-1",
                    "server_request_id": "api-1",
                    "clock_domain": "host:boot",
                },
            ),
        ]
        report = {
            "run_id": "run-1",
            "status": "success",
            "duration_s": 1.0,
            "manifest": {
                "run_id": "run-1",
                "label": "task",
                "clock_domain_id": "host:boot",
            },
            "analysis_publication": {"status": "PUBLISHABLE"},
            "vllm_p2": {
                "requests": [
                    {
                        "model_call_id": "model-1",
                        "api_request_id": "api-1",
                        "vllm_request_id": "engine-1",
                        "received_ns": 100,
                        "queued_ns": 110,
                        "first_scheduled_ns": 130,
                        "prefill_completed_ns": 200,
                        "first_token_ns": 230,
                        "last_token_ns": 430,
                        "first_output_ns": 250,
                        "engine_finished_ns": 450,
                        "finished_ns": 480,
                        "received_clock_domain": "host:boot",
                        "queued_clock_domain": "host:boot",
                        "first_scheduled_clock_domain": "host:boot",
                        "prefill_completed_clock_domain": "host:boot",
                        "first_token_clock_domain": "host:boot",
                        "last_token_clock_domain": "host:boot",
                        "first_output_clock_domain": "host:boot",
                        "engine_finished_clock_domain": "host:boot",
                        "finished_clock_domain": "host:boot",
                        "first_token_timestamp_source": "engine_core",
                        "engine_finished_timestamp_source": "engine_core",
                        "prefill_boundary_quality": "execution_observed",
                        "prompt_tokens": 10,
                        "cached_prompt_tokens": 4,
                        "generation_tokens": 3,
                        "lifecycle_status": "complete",
                    }
                ]
            },
        }

        tables = build_canonical_tables("/tmp/run-1", report=report, events=events)
        request = tables["requests"][0]

        self.assertEqual(request["model_call_id"], "model-1")
        self.assertEqual(request["client_request_id"], "model-1")
        self.assertEqual(request["server_request_id"], "api-1")
        self.assertEqual(request["engine_request_id"], "engine-1")
        self.assertEqual(request["uncached_prompt_tokens"], 6)
        self.assertIsNone(request["engine_recomputed_tokens"])
        self.assertAlmostEqual(request["exact_prefill_s"], 70 / 1_000_000_000)
        self.assertAlmostEqual(request["exact_decode_s"], 220 / 1_000_000_000)
        self.assertAlmostEqual(request["exact_tpot_s"], 100 / 1_000_000_000)
        self.assertAlmostEqual(request["proxy_ttft_s"], 150 / 1_000_000_000)
        self.assertEqual(request["context_compression_status"], "COMPLETE")
        self.assertEqual(
            request["context_compression_trigger_strategy"],
            "token_ratio",
        )
        self.assertEqual(
            request["context_compression_trigger_reason"],
            "ratio_reached",
        )
        self.assertEqual(request["context_compression_removed_tokens"], 60)
        self.assertEqual(
            json.loads(request["context_compression_limitations_json"]),
            ["test_limit"],
        )

    def test_writer_always_emits_jsonl_csv_and_audits_parquet(self) -> None:
        tables = {
            "runs": [{"schema_version": "1.0", "run_id": "run-1"}],
            "requests": [],
            "tools": [],
            "events": [],
            "system_samples": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "canonical"
            manifest = write_canonical_tables(
                tables,
                output,
                formats=("jsonl", "csv", "parquet-if-available"),
                source_runs=["/runs/run-1"],
            )

            self.assertTrue((output / "runs.jsonl").is_file())
            self.assertTrue((output / "runs.csv").is_file())
            self.assertIn(manifest["parquet_status"], {"complete", "unavailable"})
            stored = json.loads((output / "canonical_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["table_row_counts"]["runs"], 1)


def event(event_type: str, mono_ns: int, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": f"event-{event_type}",
        "run_id": "run-1",
        "source": "mini_swe",
        "event_type": event_type,
        "wall_time_ns": mono_ns,
        "monotonic_time_ns": mono_ns,
        "quality": "exact",
        "payload": payload,
    }


if __name__ == "__main__":
    unittest.main()
