from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_existing_data_analysis.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_existing_data_analysis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExistingDataAnalysisTests(unittest.TestCase):
    def test_inflight_change_point_detects_unit_latency_slowdown(self) -> None:
        module = load_module()
        rows = []
        for inflight, tpot in ((1, 0.1), (2, 0.1), (3, 0.2), (4, 0.2)):
            rows.extend(
                {
                    "inflight_model_requests": inflight,
                    "tpot_s": tpot,
                    "p2_e2e_s": tpot * 10,
                }
                for _ in range(4)
            )

        result = module.detect_inflight_change_point(rows)

        self.assertEqual(result["status"], "detected")
        self.assertEqual(result["threshold_inflight_requests"], 2)
        self.assertGreaterEqual(result["relative_slowdown"], 0.9)

    def test_script_help_uses_real_cli_entrypoint(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--batch", completed.stdout)
        self.assertIn("--output-dir", completed.stdout)

    def test_request_metrics_are_null_safe_and_use_effective_decode_tokens(self) -> None:
        module = load_module()
        row = model_row("run-a", "api-a", 1_000_000_000, 5_000_000_000)
        row.update(
            {
                "p2_prompt_tokens": "100",
                "p2_cached_prompt_tokens": "20",
                "p2_generation_tokens": "11",
                "p2_decode_s": "2",
                "p2_ttft_s": "3",
                "p2_computed_prefill_tokens": "80",
                "p2_prefill_to_first_token_s": "4",
            }
        )
        audit = {
            "p2_match_status": "exact_match",
            "p2_candidate_count": 1,
            "run_id": "run-a",
            "task_id": "task-a",
        }

        result = module.derive_one_request_metric({"label": "C1", "path": Path("/tmp")}, row, audit, None)

        self.assertEqual(result["uncached_prompt_tokens"], 80)
        self.assertEqual(result["total_tokens"], 111)
        self.assertEqual(result["effective_decode_tokens"], 10)
        self.assertAlmostEqual(result["tpot_s"], 0.2)
        self.assertAlmostEqual(result["decode_tokens_per_second"], 5.0)
        self.assertAlmostEqual(result["ttft_per_1k_prompt_tokens_s"], 30.0)
        self.assertAlmostEqual(result["prefill_proxy_tokens_per_second"], 20.0)
        self.assertEqual(result["prefill_metric_quality"], "proxy_not_exact_prefill")

        row["p2_generation_tokens"] = "1"
        one_token = module.derive_one_request_metric({"label": "C1", "path": Path("/tmp")}, row, audit, None)
        self.assertIsNone(one_token["effective_decode_tokens"])
        self.assertIsNone(one_token["tpot_s"])

        row["p2_prompt_tokens"] = "N/A"
        missing = module.derive_one_request_metric({"label": "C1", "path": Path("/tmp")}, row, audit, None)
        self.assertIsNone(missing["prompt_tokens"])
        self.assertIsNone(missing["uncached_prompt_tokens"])

    def test_p2_candidate_index_reports_multiple_without_choosing_one(self) -> None:
        module = load_module()
        events = [
            p2_event("vllm.request.received", 1, "api-a", "engine-1"),
            p2_event("vllm.request.finished", 2, "api-a", "engine-1"),
            p2_event("vllm.request.received", 1, "api-a", "engine-2"),
            p2_event("vllm.request.finished", 2, "api-a", "engine-2"),
        ]
        index = module.build_p2_event_index(events)
        row = model_row("run-a", "api-a", 1, 2)

        candidates = module.p2_candidates_for_row(row, index)

        self.assertEqual(candidates, {"engine-1", "engine-2"})
        self.assertEqual(module.normalized_match_status(row, len(candidates)), "multiple_candidates")

    def test_raw_p2_evidence_overrides_stale_matched_label_and_audits_extra(self) -> None:
        module = load_module()
        row = model_row("run-a", "api-missing", 1, 2)
        events = complete_p2_events("api-extra", "engine-extra", 10)
        index = module.build_p2_event_index(events)
        batch = {
            "label": "C1",
            "path": Path("/tmp"),
            "concurrency": 1,
            "step_rows": [row],
            "run_results": {"run-a": {"task_id": "task-a"}},
        }

        metrics, audit = module.derive_request_metrics(
            batch,
            {"run-a": {"p2_index": index}},
        )

        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["p2_match_status"], "unmatched_p1")
        self.assertEqual({item["p2_match_status"] for item in audit}, {"unmatched_p1", "unmatched_p2"})
        extra = next(item for item in audit if item["p2_match_status"] == "unmatched_p2")
        self.assertEqual(extra["vllm_request_id"], "engine-extra")
        self.assertEqual(extra["lifecycle_status"], "complete")

    def test_validation_applies_p2_threshold_to_each_batch(self) -> None:
        module = load_module()
        rows = [
            {"batch_label": "C1", "p2_match_status": "exact_match", "lifecycle_status": "complete"},
            {"batch_label": "C5", "p2_match_status": "exact_match", "lifecycle_status": "complete"},
            {"batch_label": "C5", "p2_match_status": "unmatched_p1", "lifecycle_status": None},
        ]
        correlation = module.summarize_correlation(rows)

        validation = module.build_validation_checks(
            [{"label": "C1", "step_rows": [{}]}, {"label": "C5", "step_rows": [{}]}],
            correlation,
            [],
            min_p2_match_rate=0.75,
            min_p2_lifecycle_rate=0.75,
        )

        failures = set(validation["blocking_failures"])
        self.assertNotIn("C1.p2_exact_match_rate", failures)
        self.assertIn("C5.p2_exact_match_rate", failures)
        self.assertIn("C5.p2_lifecycle_complete_rate", failures)

    def test_client_server_proxy_requires_matching_clock_domain(self) -> None:
        module = load_module()
        row = model_row("run-a", "api-a", 1_000_000_000, 5_000_000_000)
        context = {
            "received_ns": 2_000_000_000,
            "finished_ns": 4_000_000_000,
            "received_ns_clock_domain": "other",
            "finished_ns_clock_domain": "other",
            "payload": {},
        }

        inbound, outbound, quality, reason = module.client_server_proxies(row, context)

        self.assertIsNone(inbound)
        self.assertIsNone(outbound)
        self.assertIsNone(quality)
        self.assertEqual(reason, "clock_domain_mismatch_or_unverified")

        context["received_ns_clock_domain"] = "clock-a"
        context["finished_ns_clock_domain"] = "clock-a"
        context["received_ns_host_boot_id"] = "boot-a"
        context["finished_ns_host_boot_id"] = "boot-a"
        inbound, outbound, quality, reason = module.client_server_proxies(row, context)
        self.assertEqual(inbound, 1.0)
        self.assertEqual(outbound, 1.0)
        self.assertEqual(quality, "same_host_monotonic_proxy")
        self.assertIsNone(reason)

    def test_request_metrics_recover_from_same_clock_raw_p2_events(self) -> None:
        module = load_module()
        events = complete_p2_events("api-a", "engine-a", 1_000_000_000)
        events[-1]["payload"].update(
            {"prompt_tokens": 100, "cached_prompt_tokens": 25, "generation_tokens": 11}
        )
        index = module.build_p2_event_index(events)
        context = index["contexts"]["engine-a"]
        row = model_row("run-a", "api-a", 900_000_000, 1_800_000_000)
        audit = {"p2_match_status": "exact_match", "p2_candidate_count": 1}

        result = module.derive_one_request_metric(
            {"label": "C1", "path": Path("/tmp")}, row, audit, context
        )

        self.assertAlmostEqual(result["p2_e2e_s"], 0.7)
        self.assertAlmostEqual(result["p2_queue_s"], 0.1)
        self.assertAlmostEqual(result["p2_ttft_s"], 0.4)
        self.assertAlmostEqual(result["p2_decode_s"], 0.2)
        self.assertEqual(result["prompt_tokens"], 100)
        self.assertTrue(result["raw_p2_fallback_used"])

    def test_timeline_finds_overlapping_models_and_item_outside_run(self) -> None:
        module = load_module()
        batch = {
            "label": "C2",
            "concurrency": 2,
            "manifest": {"task_count": 1},
            "results": [
                {
                    "task_id": "task-a",
                    "run_id": "run-a",
                    "workload_start_mono_ns": "100",
                    "workload_end_mono_ns": "500",
                }
            ],
            "step_rows": [
                model_row("run-a", "api-a", 110, 300, span_id="model-a"),
                model_row("run-a", "api-b", 250, 400, span_id="model-b"),
                tool_row("run-a", 450, 550),
            ],
        }

        _, violations, checks = module.analyze_timeline(batch)

        kinds = {row["violation_type"] for row in violations}
        self.assertIn("overlapping_models_in_run", kinds)
        self.assertIn("item_outside_run", kinds)
        check_values = {row["name"]: row["passed"] for row in checks}
        self.assertFalse(check_values["C2.one_active_model_per_run"])
        self.assertFalse(check_values["C2.items_contained_by_run"])

    def test_timeline_requires_started_and_completed_counts_to_match(self) -> None:
        module = load_module()
        batch = {
            "label": "C1",
            "concurrency": 1,
            "manifest": {"task_count": 1},
            "summary": {},
            "results": [
                {
                    "task_id": "task-a",
                    "run_id": "run-a",
                    "workload_start_mono_ns": "100",
                    "workload_end_mono_ns": "",
                }
            ],
            "step_rows": [model_row("run-a", "api-a", 110, 300)],
        }

        _, _, checks = module.analyze_timeline(batch)

        values = {row["name"]: row for row in checks}
        self.assertFalse(values["C1.started_completed_count_equal"]["passed"])
        self.assertEqual(values["C1.started_completed_count_equal"]["details"]["completed_count"], 0)

    def test_product_decomposition_reconciles_duration_delta(self) -> None:
        module = load_module()
        component = module.product_decomposition(10, 20, 15, 45)

        self.assertIsNotNone(component)
        assert component is not None
        self.assertAlmostEqual(component["workload_contribution_s"], 10.0)
        self.assertAlmostEqual(component["unit_rate_contribution_s"], 15.0)
        self.assertAlmostEqual(
            component["workload_contribution_s"] + component["unit_rate_contribution_s"],
            25.0,
        )

    def test_tail_classifier_uses_paired_baseline_evidence(self) -> None:
        module = load_module()
        baseline = {
            "batch_label": "C1",
            "task_id": "task-a",
            "run_id": "run-1",
            "run_wall_s": 10,
            "queue_sum_s": 1,
            "prefill_proxy_sum_s": 2,
            "decode_sum_s": 4,
            "effective_decode_tokens": 20,
            "prompt_tokens": 100,
            "generation_tokens": 21,
            "model_call_count": 1,
            "tool_call_count": 1,
            "step_count": 1,
            "model_residual_sum_s": 0,
            "agent_gap_s": 1,
        }
        candidate = {**baseline, "batch_label": "C5", "run_id": "run-5", "run_wall_s": 20, "queue_sum_s": 4}

        rows = module.classify_tails([baseline, candidate], "C1")

        tail = next(row for row in rows if row["batch_label"] == "C5")
        self.assertTrue(tail["is_tail"])
        self.assertIn("queue_tail", tail["categories"])
        self.assertEqual(tail["confidence"], "high")

    def test_device_imbalance_handles_partial_metrics_without_rank_claim(self) -> None:
        module = load_module()
        batch = {
            "label": "C1",
            "concurrency": 1,
            "step_rows": [
                {
                    "run_id": "run-a",
                    "task_id": "task-a",
                    "step_id": "step-a",
                    "step_index": "1",
                    "step_npu_device_metrics_json": json.dumps(
                        {
                            "0": {"peak_hbm_used_bytes": 100, "max_aicore_util_pct": 80},
                            "1": {"peak_hbm_used_bytes": 200},
                        }
                    ),
                }
            ],
        }

        rows = module.analyze_device_imbalance(batch)

        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["hbm_imbalance_pct"], 100 / 150 * 100)
        self.assertEqual(rows[0]["aicore_device_sample_count"], 1)
        self.assertEqual(rows[0]["aicore_metric_quality"], "partial")
        self.assertIsNone(rows[0]["rank_id"])
        self.assertEqual(rows[0]["rank_mapping_quality"], "unavailable")
        long_rows = module.expand_device_metrics(rows)
        self.assertEqual({row["device_id"] for row in long_rows}, {"0", "1"})
        self.assertTrue(all(row["rank_id"] is None for row in long_rows))

    def test_host_counter_reset_is_null_not_negative(self) -> None:
        module = load_module()

        value, reset = module.counter_rate(100, 90, 1.0)

        self.assertIsNone(value)
        self.assertTrue(reset)

    def test_host_metrics_parse_load_memory_and_pressure(self) -> None:
        module = load_module()
        first = host_event(1_000_000_000, 100, 1000)
        second = host_event(2_000_000_000, 110, 1100)
        second["payload"]["loadavg"] = "2.0 1.5 1.0 1/100 42"
        second["payload"]["pressure"] = {
            "cpu": {"some": {"avg10": 0.5}},
            "memory": {"full": {"avg10": 0.25}},
        }
        batch = {
            "label": "C1",
            "concurrency": 1,
            "run_results": {"run-a": {"task_id": "task-a"}},
        }

        rows = module.analyze_host_metrics(
            batch,
            {"run-a": {"events": [first, second]}},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["load1"], 2.0)
        self.assertEqual(rows[0]["load1_per_cpu"], 2.0)
        self.assertAlmostEqual(rows[0]["memory_used_ratio"], 0.4)
        self.assertEqual(rows[0]["cpu_psi_some_avg10"], 0.5)
        self.assertEqual(rows[0]["memory_psi_full_avg10"], 0.25)

    def test_empty_batch_writes_diagnostics_and_strict_fails(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "empty"
            output = root / "output"
            batch.mkdir()

            return_code = module.main(
                ["--batch", f"C1={batch}", "--output-dir", str(output), "--strict"]
            )

            self.assertEqual(return_code, 1)
            validation = json.loads((output / "validation_checks.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["publishability_status"], "UNPUBLISHABLE")
            self.assertTrue((output / "existing_data_gaps.md").is_file())

    def test_end_to_end_existing_batch_analysis_writes_all_artifacts(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = make_batch(root, "C1", "run-1", "task-a", duration=10.0, decode=2.0)
            candidate = make_batch(root, "C5", "run-5", "task-a", duration=20.0, decode=4.0)
            output = root / "analysis"

            analysis = module.analyze_existing_batches(
                [("C1", baseline), ("C5", candidate)], baseline_label="C1"
            )
            module.write_analysis(analysis, output)

            self.assertEqual(len(analysis["paired_task_decomposition"]), 1)
            self.assertEqual(analysis["paired_task_decomposition"][0]["pair_status"], "paired")
            self.assertEqual(analysis["validation_checks"]["publishability_status"], "PUBLISHABLE")
            for name in (
                "request_metrics.csv",
                "request_metrics_summary.json",
                "p2_correlation_audit.csv",
                "p2_correlation_audit.json",
                "request_timeline.csv",
                "paired_task_decomposition.csv",
                "paired_model_calls.csv",
                "tail_classification.csv",
                "tail_summary.json",
                "device_imbalance.csv",
                "device_metrics_long.csv",
                "host_metrics.csv",
                "host_metrics_summary.json",
                "validation_checks.json",
                "offline_root_cause_report.md",
            ):
                self.assertTrue((output / name).is_file(), name)


def make_batch(
    root: Path,
    label: str,
    run_id: str,
    task_id: str,
    *,
    duration: float,
    decode: float,
) -> Path:
    batch = root / label
    steps_dir = batch / "step-sequence"
    run_dir = root / "runs" / run_id
    steps_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    start = 1_000_000_000
    end = start + int(duration * 1_000_000_000)
    manifest = {
        "schema_version": "3.0",
        "task_count": 1,
        "task_ids": [task_id],
        "concurrency": int(label.removeprefix("C")),
    }
    (batch / "batch-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (batch / "batch-summary.json").write_text(
        json.dumps({"configured_concurrency": manifest["concurrency"]}), encoding="utf-8"
    )
    write_rows(
        batch / "batch-results.tsv",
        [
            {
                "task_id": task_id,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "execution_success": "true",
                "analysis_pipeline_success": "true",
                "workload_start_mono_ns": start,
                "workload_end_mono_ns": end,
                "workload_wall_s": duration,
                "analysis_wall_s": 1,
            }
        ],
        delimiter="\t",
    )
    model = model_row(run_id, f"api-{run_id}", start + 1_000_000_000, end - 2_000_000_000)
    model.update(
        {
            "p2_e2e_s": str(duration - 3),
            "p2_queue_s": "1",
            "p2_prefill_to_first_token_s": "2",
            "p2_decode_s": str(decode),
            "p2_prompt_tokens": "100",
            "p2_cached_prompt_tokens": "20",
            "p2_computed_prefill_tokens": "80",
            "p2_generation_tokens": "11",
            "p2_recomputed_prompt_tokens": "0",
            "p2_recomputed_prompt_rate": "0",
            "p2_evicted_block_count": "0",
            "run_wall_s": str(duration),
            "step_npu_device_metrics_json": json.dumps(
                {
                    "0": {"peak_hbm_used_bytes": 100, "max_aicore_util_pct": 80},
                    "1": {"peak_hbm_used_bytes": 110, "max_aicore_util_pct": 75},
                }
            ),
        }
    )
    tool = tool_row(run_id, end - 2_000_000_000, end - 1_000_000_000)
    write_rows(steps_dir / "all-steps.csv", [model, tool])
    events = [
        *complete_p2_events(f"api-{run_id}", f"engine-{run_id}", start + 1_200_000_000),
        host_event(start, 100, 1000),
        host_event(start + 1_000_000_000, 110, 1100),
    ]
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return batch


def model_row(
    run_id: str,
    api_id: str,
    start: int,
    end: int,
    *,
    span_id: str = "model-1",
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "step_id": f"step-{span_id}",
        "step_index": "1",
        "step_type": "agent_step",
        "item_type": "model",
        "span_id": span_id,
        "api_request_id": api_id,
        "response_id": api_id,
        "vllm_request_id": "",
        "p2_match_status": "matched",
        "p2_match_method": "api_response_id",
        "p2_match_confidence": "exact",
        "p2_lifecycle_status": "complete",
        "item_start_mono_ns": str(start),
        "item_end_mono_ns": str(end),
        "clock_domain_id": "clock-a",
        "host_boot_id": "boot-a",
    }


def tool_row(run_id: str, start: int, end: int) -> dict[str, str]:
    return {
        "run_id": run_id,
        "step_id": "step-tool",
        "step_index": "2",
        "step_type": "agent_step",
        "item_type": "tool",
        "span_id": "tool-1",
        "tool_call_id": "tool-1",
        "item_start_mono_ns": str(start),
        "item_end_mono_ns": str(end),
        "tool_call_duration_s": str((end - start) / 1_000_000_000),
        "returncode": "0",
        "timed_out": "false",
        "command_hash": "hash-a",
        "clock_domain_id": "clock-a",
        "host_boot_id": "boot-a",
    }


def p2_event(event_type: str, mono_ns: int, api_id: str, engine_id: str) -> dict:
    field = {
        "vllm.request.received": "received_ns",
        "vllm.request.queued": "queued_ns",
        "vllm.request.first_scheduled": "first_scheduled_ns",
        "vllm.request.prefill_completed": "prefill_completed_ns",
        "vllm.request.first_token": "first_token_ns",
        "vllm.request.engine_finished": "engine_finished_ns",
        "vllm.request.finished": "finished_ns",
    }.get(event_type)
    payload = {
        "api_request_id": api_id,
        "vllm_request_id": engine_id,
        "clock_domain": "clock-a",
        "host_boot_id": "boot-a",
        "source_host": "host-a",
    }
    if field:
        payload[field] = mono_ns
    return {
        "schema_version": "1.0",
        "run_id": "service",
        "source": "vllm_p2",
        "event_type": event_type,
        "monotonic_time_ns": mono_ns,
        "payload": payload,
    }


def complete_p2_events(api_id: str, engine_id: str, start_ns: int) -> list[dict]:
    return [
        p2_event("vllm.request.received", start_ns, api_id, engine_id),
        p2_event("vllm.request.queued", start_ns + 100_000_000, api_id, engine_id),
        p2_event("vllm.request.first_scheduled", start_ns + 200_000_000, api_id, engine_id),
        p2_event("vllm.request.first_token", start_ns + 400_000_000, api_id, engine_id),
        p2_event("vllm.request.engine_finished", start_ns + 600_000_000, api_id, engine_id),
        p2_event("vllm.request.finished", start_ns + 700_000_000, api_id, engine_id),
    ]


def host_event(mono_ns: int, ctxt: int, pgfault: int) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "run",
        "source": "host",
        "event_type": "sample",
        "monotonic_time_ns": mono_ns,
        "payload": {
            "proc_stat": {
                "cpus": {"cpu0": [10, 0, 10, 80, 0] if ctxt == 100 else [20, 0, 20, 160, 0]},
                "ctxt": ctxt,
                "processes": ctxt,
                "procs_running": 1,
                "procs_blocked": 0,
            },
            "vmstat": {"pgfault": pgfault, "pgmajfault": 1, "pswpin": 0, "pswpout": 0},
            "meminfo": {"MemTotal": 1000, "MemAvailable": 600},
            "pressure": {"cpu": {"some": {"avg10": 0.1}}},
        },
    }


def write_rows(path: Path, rows: list[dict], *, delimiter: str = ",") -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
