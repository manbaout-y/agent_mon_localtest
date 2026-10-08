from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agentmon.sampling import npu_mapping


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_batch_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_batch_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BatchRuntimeTests(unittest.TestCase):
    def test_analyze_existing_does_not_require_a_workload_command(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            batch_root = root / "batch"
            batch_root.mkdir()
            (batch_root / "batch-results.tsv").write_text(
                "task_id\trun_id\trun_dir\texecution_success\n"
                f"t-1\tr-1\t{root / 'missing-run'}\ttrue\n",
                encoding="utf-8",
            )
            (batch_root / "batch-manifest.json").write_text(
                json.dumps(
                    {
                        "task_count": 1,
                        "runtime_source_safety": {
                            "status": "PASS",
                            "snapshot_root": "/historical/runtime-snapshot",
                        },
                        "workspace_safety": {
                            "status": "PASS",
                            "contract_index": "/historical/workspace-contracts.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(batch_root),
                    "--task-file",
                    str(task_file),
                    "--analyze-existing",
                ]
            )

            results = module.run_batch(args)

            manifest = json.loads((batch_root / "batch-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "run_directory_missing")
        self.assertEqual(
            manifest["runtime_source_safety"]["snapshot_root"],
            "/historical/runtime-snapshot",
        )
        self.assertEqual(
            manifest["workspace_safety"]["contract_index"],
            "/historical/workspace-contracts.json",
        )

    def test_pipeline_profile_avoids_implicit_recursive_file_scan(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.bin").write_bytes(b"1234")
            light = module.capture_pipeline_resources(root)
            inventory = module.capture_file_inventory(root)

        self.assertIsNone(light["file_count"])
        self.assertEqual(inventory["file_count"], 1)
        self.assertEqual(inventory["file_bytes"], 4)
    def test_main_fails_when_execution_failed_even_if_analysis_succeeded(self) -> None:
        module = load_module()
        result = {
            "pipeline_success": True,
            "analysis_pipeline_success": True,
            "execution_success": False,
            "overall_pipeline_success": False,
        }

        with patch.object(module, "run_batch", return_value=[result]):
            return_code = module.main(
                [
                    "--batch-root",
                    "/tmp/batch",
                    "--task-file",
                    "/tmp/tasks.tsv",
                    "--command-template",
                    "mini -t {task_id}",
                ]
            )

        self.assertEqual(return_code, 1)

    def test_main_fails_execution_only_workload_failure_before_analysis(self) -> None:
        module = load_module()
        result = {
            "execution_success": False,
            "overall_pipeline_success": None,
        }

        with patch.object(module, "run_batch", return_value=[result]):
            return_code = module.main(
                [
                    "--batch-root",
                    "/tmp/batch",
                    "--task-file",
                    "/tmp/tasks.tsv",
                    "--command-template",
                    "false",
                    "--execution-only",
                    "--runtime-source-isolation",
                    "off",
                ]
            )

        self.assertEqual(return_code, 1)

    def test_execution_only_missing_run_id_marks_batch_invalid(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            workload = {
                "task_id": "t-1",
                "status": "agentmon_run_id_missing",
                "run_id_required": True,
                "run_id": "",
                "run_dir": "",
                "execution_success": True,
                "pipeline_success": False,
                "overall_pipeline_success": False,
                "publishability_status": "UNPUBLISHABLE",
                "report_blocking_failures": ["agentmon_run_id_missing"],
            }
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(root / "batch"),
                    "--task-file",
                    str(task_file),
                    "--command-template",
                    "true",
                    "--execution-only",
                    "--runtime-source-isolation",
                    "off",
                ]
            )

            with patch.object(module, "run_workload_task", return_value=workload):
                results = module.run_batch(args)

            manifest = json.loads((root / "batch" / "batch-manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((root / "batch" / "batch-summary.json").read_text(encoding="utf-8"))

        self.assertTrue(module.result_is_failure(results[0]))
        self.assertEqual(manifest["status"], "execution_invalid_analysis_deferred")
        self.assertFalse(manifest["experiment_valid"])
        self.assertEqual(manifest["completion"]["agentmon_run_id_missing"], 1)
        self.assertEqual(summary["validity_status"], "INVALID")

    def test_workspace_preflight_rejects_agentmon_source_before_workload(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            batch_root = root / "batch"
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(batch_root),
                    "--task-file",
                    str(task_file),
                    "--command-template",
                    "true",
                    "--workspace-policy",
                    "external",
                    "--workspace-template",
                    str(ROOT),
                ]
            )

            with patch.object(module, "run_workload_task") as workload:
                with self.assertRaisesRegex(ValueError, "overlaps protected AgentMon source"):
                    module.run_batch(args)

            preflight = json.loads(
                (batch_root / "workspace-contracts.json").read_text(encoding="utf-8")
            )

        workload.assert_not_called()
        self.assertEqual(preflight["status"], "FAIL")
        self.assertTrue(preflight["forbidden_conclusions"])

    def test_defer_analysis_alias_sets_execution_only(self) -> None:
        module = load_module()

        args = module.build_parser().parse_args(
            [
                "--batch-root",
                "/tmp/batch",
                "--task-file",
                "/tmp/tasks.tsv",
                "--command-template",
                "true",
                "--defer-analysis",
            ]
        )

        self.assertTrue(args.execution_only)
        self.assertFalse(args.analyze_existing)

    def test_large_p2_threshold_defers_analysis_without_indexing(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            p2_events = root / "service.events.jsonl"
            p2_events.write_text("x" * 32, encoding="utf-8")
            run_dir = root / "runs" / "r-1"
            run_dir.mkdir(parents=True)
            workload = {
                "task_id": "t-1",
                "status": "workload_complete",
                "execution_success": True,
                "overall_pipeline_success": None,
                "run_id": "r-1",
                "run_dir": str(run_dir),
                "workspace": str(root / "workspace"),
                "home": str(root / "home"),
                "xdg_config_home": str(root / "xdg"),
            }
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(root / "batch"),
                    "--task-file",
                    str(task_file),
                    "--command-template",
                    "true",
                    "--p2-events",
                    str(p2_events),
                    "--defer-analysis-if-p2-over-bytes",
                    "1",
                    "--runtime-source-isolation",
                    "off",
                ]
            )

            with patch.object(module, "run_workload_task", return_value=workload), patch.object(
                module, "build_or_load_p2_batch_index"
            ) as build_index:
                results = module.run_batch(args)

            manifest = json.loads((root / "batch" / "batch-manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((root / "batch" / "batch-summary.json").read_text(encoding="utf-8"))
            saved_rows = module.read_results_tsv(root / "batch" / "batch-results.tsv")

        build_index.assert_not_called()
        self.assertTrue(results[0]["analysis_deferred"])
        self.assertEqual(results[0]["analysis_deferred_reason"], "p2_source_size_over_threshold")
        self.assertEqual(manifest["status"], "execution_complete_analysis_deferred")
        self.assertTrue(manifest["analysis_deferred"])
        self.assertEqual(manifest["analysis_deferred_reason"]["threshold_bytes"], 1)
        self.assertTrue(summary["analysis_deferred"])
        self.assertTrue(saved_rows[0]["analysis_deferred"])

    def test_analyze_existing_ignores_large_p2_auto_defer_threshold(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            p2_events = root / "service.events.jsonl"
            p2_events.write_text("x" * 32, encoding="utf-8")
            batch_root = root / "batch"
            batch_root.mkdir()
            run_dir = root / "missing-run"
            (batch_root / "batch-results.tsv").write_text(
                "task_id\trun_id\trun_dir\texecution_success\tanalysis_deferred\n"
                f"t-1\tr-1\t{run_dir}\ttrue\ttrue\n",
                encoding="utf-8",
            )
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(batch_root),
                    "--task-file",
                    str(task_file),
                    "--analyze-existing",
                    "--p2-events",
                    str(p2_events),
                    "--defer-analysis-if-p2-over-bytes",
                    "1",
                ]
            )

            with patch.object(module, "build_or_load_p2_batch_index", return_value={"metrics": {}, "global_events": []}) as build_index:
                results = module.run_batch(args)

        build_index.assert_called_once()
        self.assertEqual(results[0]["status"], "run_directory_missing")

    def test_task_validation_consumes_run_report_publishability(self) -> None:
        module = load_module()
        result = {
            "run_id": "r",
            "task_id": "t",
            "execution_success": True,
            "report_generation_success": True,
            "report_publishability_status": "UNPUBLISHABLE",
            "report_blocking_failures": ["invalid_json_lines"],
            "p1_analysis_success": True,
            "trajectory_import_success": True,
            "p2_source_path": "",
        }

        validation = module.build_task_validation(result)

        self.assertEqual(validation["publishability_status"], "UNPUBLISHABLE")
        self.assertIn("run_report_publishable", validation["blocking_failures"])
        report_check = next(
            item for item in validation["checks"] if item["name"] == "run_report_publishable"
        )
        self.assertEqual(report_check["details"]["blocking_failures"], ["invalid_json_lines"])

    def test_missing_run_report_validation_is_unpublishable(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            validation = module.read_report_validation(Path(tmp))

        self.assertEqual(validation["publishability_status"], "UNPUBLISHABLE")
        self.assertIn("missing_or_invalid_run_validation", validation["blocking_failures"])

    def test_configure_p1_command_injects_native_adapter_classes(self) -> None:
        module = load_module()

        command, status = module.configure_p1_command(
            "mini-swe-agent -t /tmp/task.md -m qwen",
            "required",
        )

        self.assertEqual(status, "injected")
        for flag, class_path in module.P1_CLASSES.items():
            self.assertIn(flag, command)
            self.assertIn(class_path, command)

        existing, existing_status = module.configure_p1_command(command, "required")
        self.assertEqual(existing_status, "already_configured")
        self.assertEqual(existing, command)

        server_command, server_status = module.configure_p1_command(
            "/data/envs/mini-swe-v243/bin/mini -t task.md",
            "required",
        )
        self.assertEqual(server_status, "injected")
        self.assertIn("--environment-class", server_command)

    def test_configure_p1_command_refuses_unsafe_shell_composition(self) -> None:
        module = load_module()

        command, status = module.configure_p1_command("mini-swe-agent -t task.md > run.log", "auto")

        self.assertEqual(status, "compound_command_not_injected")
        self.assertEqual(command, "mini-swe-agent -t task.md > run.log")
        with self.assertRaisesRegex(ValueError, "compound, piped, or redirected"):
            module.configure_p1_command("mini-swe-agent -t task.md | tee run.log", "required")
        with self.assertRaisesRegex(ValueError, "directly recognizable mini-SWE executable"):
            module.configure_p1_command("python wrapper.py --task task.md", "required")

    def test_batch_summary_separates_pipeline_stages_and_computes_throughput(self) -> None:
        module = load_module()
        results = [
            {
                "status": "success",
                "publishability_status": "PUBLISHABLE",
                "execution_success": True,
                "monitoring_success": True,
                "trajectory_import_success": True,
                "p1_native_status": "complete",
                "p2_import_success": True,
                "judge_success": True,
                "pipeline_success": True,
                "p2_import_mode": "trajectory",
                "model_request_count": 4,
                "tool_call_count": 3,
                "prompt_tokens": 100,
                "generation_tokens": 20,
                "p2_request_count": 4,
                "p2_request_event_count": 24,
                "p2_global_event_count": 0,
                "p2_sample_event_count": 0,
                "unmatched_trajectory_id_count": 0,
                "multiple_p2_api_request_id_count": 1,
                "p2_requests_in_task_window_not_in_trajectory_count": 1,
            },
            {
                "status": "judge_failed",
                "publishability_status": "UNPUBLISHABLE",
                "execution_success": True,
                "monitoring_success": True,
                "trajectory_import_success": True,
                "p1_native_status": "missing",
                "p2_import_success": False,
                "judge_success": False,
                "pipeline_success": False,
                "p2_import_mode": "run-id",
                "model_request_count": 2,
                "tool_call_count": 2,
                "prompt_tokens": 50,
                "generation_tokens": 10,
                "unmatched_trajectory_id_count": 2,
            },
        ]

        summary = module.build_batch_summary(results, batch_wall_s=10.0)

        self.assertEqual(summary["stage_counts"]["execution_success"], 2)
        self.assertEqual(summary["stage_counts"]["p2_import_failure"], 1)
        self.assertEqual(summary["stage_counts"]["judge_failure"], 1)
        self.assertEqual(summary["stage_counts"]["pipeline_failure"], 1)
        self.assertEqual(summary["totals"]["unmatched_trajectory_ids"], 2.0)
        self.assertEqual(summary["totals"]["multiple_p2_api_request_ids"], 1.0)
        self.assertEqual(summary["totals"]["p2_requests_in_task_windows_not_in_trajectory"], 1.0)
        self.assertAlmostEqual(summary["throughput"]["generation_tokens_per_second"], 3.0)
        self.assertAlmostEqual(summary["throughput"]["workload_task_throughput"], 0.2)
        self.assertAlmostEqual(summary["throughput"]["pipeline_task_throughput"], 0.2)
        self.assertAlmostEqual(summary["throughput"]["successful_workload_task_throughput"], 0.2)
        self.assertAlmostEqual(summary["throughput"]["publishable_pipeline_task_throughput"], 0.1)
        self.assertIn("publishable_pipeline_task_throughput", summary["throughput"]["standard_definitions"])

        missing = module.build_batch_summary(
            [
                {
                    "status": "success",
                    "execution_success": True,
                    "monitoring_success": True,
                    "pipeline_success": True,
                    "prompt_tokens": None,
                    "generation_tokens": None,
                    "run_model_e2e_sum_s": None,
                    "run_tool_duration_sum_s": None,
                }
            ],
            batch_wall_s=1.0,
        )
        self.assertIsNone(missing["totals"]["prompt_tokens"])
        self.assertIsNone(missing["totals"]["run_model_e2e_sum_s"])
        self.assertEqual(missing["metric_coverage"]["prompt_tokens"]["coverage"], 0.0)
        self.assertIsNone(missing["throughput"]["generation_tokens_per_second"])

    def test_phase_recorder_writes_precise_phase_events_and_validation(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch-phase-events.jsonl"
            recorder = module.BatchPhaseRecorder(path)
            recorder.mark("pipeline", "started")
            recorder.mark("preprocessing", "started")
            recorder.mark("preprocessing", "finished")
            recorder.mark("workload_phase", "started")
            recorder.mark("workload_phase", "finished")
            recorder.mark("p2_index", "started")
            recorder.mark("p2_index", "finished")
            recorder.mark("analysis_phase", "started")
            recorder.mark("analysis_phase", "finished")
            recorder.mark("finalization", "started")
            recorder.mark("finalization", "finished")
            recorder.mark("pipeline", "finished")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 12)
        self.assertIn("pipeline_started_at", recorder.timestamps)
        self.assertIn("finalization_finished_at", recorder.timestamps)
        validation = module.validate_phase_timings(recorder.phase_timings())
        self.assertEqual(validation["status"], "PASS")

    def test_p2_index_cache_records_alias_offsets_and_metrics(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "service.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event(
                        "vllm.request.received",
                        1.0,
                        {"api_request_id": "api-a", "vllm_request_id": "v-a", "client_request_id": "client-a"},
                    ),
                    p2_event("vllm.request.finished", 2.0, {"api_request_id": "api-a", "vllm_request_id": "v-a"}),
                ],
            )
            source_size = source.stat().st_size
            source_hash = module.file_hash_if_exists(source)

            first = module.build_or_load_p2_batch_index(source, root / "batch")
            second = module.build_or_load_p2_batch_index(source, root / "batch")

        self.assertFalse(first["metrics"]["p2_index_cache_hit"])
        self.assertTrue(second["metrics"]["p2_index_cache_hit"])
        self.assertIn("client-a", first["by_alias_id"])
        self.assertEqual(first["metrics"]["p2_total_jsonl_lines"], 2)
        self.assertEqual(first["metrics"]["p2_input_bytes"], source_size)
        self.assertEqual(first["source_sha256"], source_hash)
        self.assertEqual(first["metrics"]["p2_source_hash_scans"], 0)

    def test_p2_index_cache_can_be_shared_across_batch_roots(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "service.events.jsonl"
            cache_root = root / "shared-p2-cache"
            write_jsonl(
                source,
                [
                    p2_event("vllm.request.received", 1.0, {"api_request_id": "api-a", "vllm_request_id": "v-a"}),
                    p2_event("vllm.request.finished", 2.0, {"api_request_id": "api-a", "vllm_request_id": "v-a"}),
                ],
            )

            first = module.build_or_load_p2_batch_index(source, root / "batch-a", cache_root=cache_root)
            second = module.build_or_load_p2_batch_index(source, root / "batch-b", cache_root=cache_root)

        self.assertFalse(first["metrics"]["p2_index_cache_hit"])
        self.assertTrue(second["metrics"]["p2_index_cache_hit"])
        self.assertIn(str(cache_root), second["metrics"]["p2_cache_path"])

    def test_prefiltered_p2_import_reuses_events_without_subprocess(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(root / "batch"),
                    "--task-file",
                    str(task_file),
                    "--command-template",
                    "true",
                ]
            )
            run_dir = root / "run"
            log_dir = root / "logs"
            run_dir.mkdir()
            log_dir.mkdir()
            selected = [
                p2_event(
                    "vllm.request.received",
                    1.0,
                    {"api_request_id": "api-a", "vllm_request_id": "v-a"},
                ),
                p2_event(
                    "vllm.request.finished",
                    2.0,
                    {"api_request_id": "api-a", "vllm_request_id": "v-a"},
                ),
            ]
            filtered = root / "filtered.events.jsonl"
            write_jsonl(filtered, selected)

            with patch.object(module, "run_logged") as run_logged:
                result = module.import_p2(
                    args,
                    "run-a",
                    filtered,
                    {},
                    log_dir,
                    prefiltered=True,
                    prefiltered_events=selected,
                    run_dir=run_dir,
                    defer_report=True,
                )

            imported = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]

        run_logged.assert_not_called()
        self.assertEqual(result["import_execution_mode"], "in_process_prefiltered")
        self.assertEqual(result["p2_request_count"], 1)
        self.assertEqual(len(imported), 2)
        self.assertTrue(all(event["run_id"] == "run-a" for event in imported))

    def test_p2_index_cache_persists_one_event_table_and_hydrates_references(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "service.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event(
                        "vllm.request.received",
                        1.0,
                        {"api_request_id": "api-a", "vllm_request_id": "v-a"},
                    ),
                    p2_event(
                        "vllm.request.finished",
                        2.0,
                        {"api_request_id": "api-a", "vllm_request_id": "v-a"},
                    ),
                ],
            )

            first = module.build_or_load_p2_batch_index(source, root / "batch")
            cache_path = Path(first["metrics"]["p2_cache_path"])
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            second = module.build_or_load_p2_batch_index(source, root / "batch")

        self.assertEqual(cached["cache_schema_version"], 3)
        self.assertEqual(len(cached["events"]), 2)
        self.assertNotIn("by_api_id", cached)
        self.assertEqual(cached["by_api_id_indices"]["api-a"], [0, 1])
        self.assertIs(second["events"][0], second["by_api_id"]["api-a"][0])
        self.assertEqual(second["metrics"]["p2_source_hash_scans"], 0)
        self.assertEqual(second["metrics"]["p2_source_parse_scans"], 0)

    def test_p2_request_window_uses_time_index_instead_of_full_stream_scan(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.events.jsonl"
            rows = []
            for request_index in range(100):
                request_id = f"api-{request_index}"
                rows.extend(
                    [
                        p2_event(
                            "vllm.request.received",
                            request_index + 0.1,
                            {"api_request_id": request_id, "vllm_request_id": f"v-{request_index}"},
                        ),
                        p2_event(
                            "vllm.request.finished",
                            request_index + 0.2,
                            {"api_request_id": request_id, "vllm_request_id": f"v-{request_index}"},
                        ),
                    ]
                )
            write_jsonl(source, rows)
            index = module.build_p2_batch_index(source)

            selected, diagnostics, _ = module.select_indexed_p2_events(
                index,
                run_id="run-a",
                response_ids={"api-50"},
                filter_mode="trajectory",
                include_window_extras=False,
                window_margin_seconds=0.0,
            )

        self.assertEqual(len(selected), 2)
        self.assertTrue(diagnostics["p2_window_time_index_used"])
        self.assertEqual(diagnostics["p2_window_candidate_event_count"], 2)
        self.assertLess(diagnostics["p2_events_scanned_per_run"], len(rows))

    def test_p2_global_window_keeps_prior_state_and_drops_distant_events(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event("vllm.adapter.capability", 1.0, {"details": {"visible_devices": ["0"]}}),
                    p2_event("vllm.kv.snapshot", 9.0, {"kv_usage_ratio": 0.1}),
                    p2_event("vllm.scheduler.snapshot", 9.5, {"waiting": 1}),
                    p2_event("vllm.scheduler.iteration.scheduled", 10.2, {"scheduled": 2}),
                    p2_event("vllm.kv.snapshot", 10.5, {"kv_usage_ratio": 0.2}),
                    p2_event("vllm.kv.snapshot", 30.0, {"kv_usage_ratio": 0.9}),
                ],
            )
            index = module.build_p2_batch_index(source)

            selected, diagnostics = module.select_indexed_p2_global_events(
                index,
                start_ns=ns(10.0),
                end_ns=ns(11.0),
            )

        event_types = [event["event_type"] for event in selected]
        usages = [
            usage
            for event in selected
            if (usage := event.get("payload", {}).get("kv_usage_ratio")) is not None
        ]
        self.assertIn("vllm.adapter.capability", event_types)
        self.assertIn("vllm.scheduler.snapshot", event_types)
        self.assertIn("vllm.scheduler.iteration.scheduled", event_types)
        self.assertEqual(usages, [0.1, 0.2])
        self.assertTrue(diagnostics["p2_service_global_time_index_used"])
        self.assertEqual(diagnostics["p2_service_global_written_event_count"], 5)

    def test_p2_global_window_can_skip_full_fallback_when_window_missing(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event("vllm.adapter.capability", 1.0, {"details": {"visible_devices": ["0"]}}),
                    p2_event("vllm.kv.snapshot", 9.0, {"kv_usage_ratio": 0.1}),
                ],
            )
            index = module.build_p2_batch_index(source)

            selected, diagnostics = module.select_indexed_p2_global_events(
                index,
                start_ns=None,
                end_ns=None,
                missing_window_policy="skip",
            )

        self.assertEqual(selected, [])
        self.assertEqual(diagnostics["p2_service_global_filter_mode"], "skipped_missing_task_window")
        self.assertEqual(diagnostics["p2_service_global_written_event_count"], 0)

    def test_quick_analysis_preset_skips_large_batch_artifacts(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\nt-1\n", encoding="utf-8")
            p2_events = root / "service.events.jsonl"
            write_jsonl(
                p2_events,
                [
                    p2_event("vllm.adapter.capability", 1.0, {"details": {"visible_devices": ["0"]}}),
                    p2_event("vllm.scheduler.iteration.scheduled", 2.0, {"batch_request_count": 1}),
                ],
            )
            batch_root = root / "batch"
            batch_root.mkdir()
            (batch_root / "batch-results.tsv").write_text(
                "task_id\trun_id\trun_dir\texecution_success\n"
                f"t-1\tr-1\t{root / 'missing-run'}\ttrue\n",
                encoding="utf-8",
            )
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(batch_root),
                    "--task-file",
                    str(task_file),
                    "--analyze-existing",
                    "--analysis-preset",
                    "quick",
                    "--p2-events",
                    str(p2_events),
                ]
            )

            module.run_batch(args)

            manifest = json.loads((batch_root / "batch-manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((batch_root / "batch-summary.json").read_text(encoding="utf-8"))

            self.assertFalse((batch_root / "p2-service-global.events.jsonl").exists())
            self.assertEqual(manifest["p2_service_global_copy"]["status"], "skipped")
            self.assertEqual(summary["timeline_publishability_status"], "SKIPPED")
            self.assertIsNone(summary["artifacts"]["all_steps_csv"])

    def test_collect_npu_process_mapping_writes_environment_level_evidence(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(root),
                    "--task-file",
                    str(root / "tasks.tsv"),
                    "--command-template",
                    "true",
                ]
            )
            with patch.object(npu_mapping, "discover_service_process_rows", return_value=[]), patch.object(
                npu_mapping,
                "collect_npu_smi_process_rows",
                return_value=[],
            ):
                mapping = module.collect_npu_process_mapping(
                    args,
                    {"ASCEND_RT_VISIBLE_DEVICES": "0,1"},
                    root,
                )
                mapping_json_exists = (root / "npu-process-mapping.json").is_file()
                mapping_csv_exists = (root / "npu-mapping-evidence.csv").is_file()

        self.assertEqual(mapping["evidence_level"], "LEVEL_3")
        self.assertEqual(mapping["status"], "PARTIAL")
        self.assertTrue(mapping_json_exists)
        self.assertTrue(mapping_csv_exists)

    def test_batch_manifest_records_reproducibility_metadata(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("task_id\npy-001\n", encoding="utf-8")
            task_package = root / "task-package.json"
            task_package.write_text('{"name":"dist50"}\n', encoding="utf-8")
            task_package_hash = module.file_hash_if_exists(task_package)
            launch_file = root / "vllm-command.txt"
            launch_file.write_text(
                "python -m vllm.entrypoints.openai.api_server --model /models/qwen "
                "--max-num-seqs 64 --max-num-batched-tokens=8192 --enable-prefix-caching\n",
                encoding="utf-8",
            )
            launch_hash = module.file_hash_if_exists(launch_file)
            args = module.build_parser().parse_args(
                [
                    "--batch-root",
                    str(root / "batch"),
                    "--task-file",
                    str(task_file),
                    "--command-template",
                    "mini-swe-agent -t {task_id}",
                    "--vllm-launch-command-file",
                    str(launch_file),
                    "--task-package",
                    str(task_package),
                    "--temperature",
                    "0.2",
                    "--seed",
                    "42",
                    "--metadata",
                    "experiment=smoke",
                ]
            )

            manifest = module.build_batch_manifest(
                args,
                [{"task_id": "py-001"}],
                {
                    "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
                    "CANN_VERSION": "9.0.0",
                    "AGENTMON_VLLM_P2_ENABLE": "1",
                    "AGENTMON_VLLM_P2_DEEP": "1",
                    "AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS": "128",
                    "AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE": "disabled",
                },
                started_wall_ns=123,
            )

        self.assertEqual(manifest["task_ids"], ["py-001"])
        self.assertEqual(manifest["runtime"]["visible_devices"], "0,1,2,3")
        self.assertEqual(manifest["runtime"]["cann_version"], "9.0.0")
        self.assertEqual(manifest["scheduler_environment"]["AGENTMON_VLLM_P2_DEEP"], "1")
        self.assertEqual(
            manifest["scheduler_environment"]["AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS"],
            "128",
        )
        self.assertEqual(
            manifest["scheduler_environment"]["AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE"],
            "disabled",
        )
        self.assertEqual(manifest["metadata"]["experiment"], "smoke")
        self.assertEqual(manifest["request_config"], {"temperature": 0.2, "seed": 42})
        self.assertEqual(manifest["comparability"]["task_package_sha256"], task_package_hash)
        self.assertEqual(manifest["vllm_launch"]["settings"]["max_num_seqs"], 64)
        self.assertEqual(manifest["vllm_launch"]["settings"]["max_num_batched_tokens"], 8192)
        self.assertTrue(manifest["vllm_launch"]["settings"]["enable_prefix_caching"])
        self.assertEqual(manifest["vllm_launch"]["command_file_sha256"], launch_hash)
        self.assertTrue(manifest["configuration_fingerprint"])

    def test_vllm_launch_settings_parse_disabled_prefix_cache_flag(self) -> None:
        module = load_module()

        settings = module.parse_vllm_launch_settings(
            "python -m vllm.entrypoints.openai.api_server "
            "--model /models/qwen --no-enable-prefix-caching"
        )

        self.assertEqual(settings["parse_status"], "ok")
        self.assertFalse(settings["enable_prefix_caching"])

    def test_reads_tasks_env_and_renders_templates(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "tasks.tsv"
            task_file.write_text("instance_id\trepo\npy-001\t/tmp/repo\n", encoding="utf-8")
            env_file = root / "agentmon.env"
            env_file.write_text("export OPENAI_BASE_URL='http://127.0.0.1:8000/v1'\nMODEL=qwen\n", encoding="utf-8")

            tasks = module.read_tasks(task_file, task_id_column="task_id")
            env = module.read_env_file(env_file)
            rendered = module.render_template(
                "run {task_id} in {repo} under {batch_root}",
                {**tasks[0], "batch_root": str(root)},
            )

        self.assertEqual(tasks[0]["task_id"], "py-001")
        self.assertEqual(env["OPENAI_BASE_URL"], "http://127.0.0.1:8000/v1")
        self.assertEqual(env["MODEL"], "qwen")
        self.assertIn("py-001", rendered)

    def test_extracts_mini_swe_response_ids_from_assistant_tool_steps(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            trajectory = Path(tmp) / "mini.traj.json"
            trajectory.write_text(
                json.dumps(
                    {
                        "trajectory_format": "mini-swe-agent-1.1",
                        "messages": [
                            {"role": "user", "content": "hi"},
                            assistant("resp-a", "call-a"),
                            {"role": "tool", "tool_call_id": "call-a", "extra": {"returncode": 0}},
                            assistant("resp-b", "call-b"),
                            assistant("resp-a", "call-a2"),
                            {"role": "assistant", "extra": {"response": {"id": "no-tools"}}},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            ids = module.extract_trajectory_response_ids(trajectory)

        self.assertEqual(ids, ["resp-a", "resp-b"])

    def test_filters_p2_events_by_response_ids_without_concurrent_noise(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "service.events.jsonl"
            output = root / "filtered.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event("vllm.adapter.capability", 9.0, {"visible_devices": ["0", "1"]}),
                    p2_event("vllm.kv.snapshot", 9.5, {"kv_usage_ratio": 0.1}),
                    p2_event("vllm.request.received", 10.0, {"api_request_id": "resp-a", "vllm_request_id": "vllm-a"}),
                    p2_event("vllm.request.first_token", 10.5, {"api_request_id": "resp-a", "vllm_request_id": "vllm-a"}),
                    p2_event("vllm.kv.block_event", 10.6, {"request_id": "vllm-a", "allocated_block_count": 2}),
                    p2_event("vllm.request.finished", 11.0, {"api_request_id": "resp-a", "vllm_request_id": "vllm-a"}),
                    p2_event("vllm.kv.snapshot", 11.5, {"kv_usage_ratio": 0.2}),
                    p2_event("vllm.request.received", 10.2, {"api_request_id": "resp-other", "vllm_request_id": "vllm-o"}),
                    p2_event("vllm.request.finished", 11.2, {"api_request_id": "resp-other", "vllm_request_id": "vllm-o"}),
                    p2_event("vllm.kv.snapshot", 30.0, {"kv_usage_ratio": 0.9}),
                ],
            )

            result = module.filter_p2_events_for_response_ids(
                source,
                output,
                response_ids={"resp-a"},
                include_window_extras=False,
                window_margin_seconds=1.0,
            )
            filtered = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result["matched_api_request_count"], 1)
        request_ids = {
            event.get("payload", {}).get("api_request_id")
            for event in filtered
            if str(event.get("event_type", "")).startswith("vllm.request.")
        }
        self.assertEqual(request_ids, {"resp-a"})
        self.assertTrue(any(event["event_type"] == "vllm.kv.block_event" for event in filtered))
        self.assertFalse(any(event["event_type"] == "vllm.kv.snapshot" for event in filtered))
        self.assertFalse(any(event.get("payload", {}).get("api_request_id") == "resp-other" for event in filtered))
        self.assertFalse(any(event.get("payload", {}).get("kv_usage_ratio") == 0.9 for event in filtered))

    def test_p2_service_bounds_reads_runtime_capability_metadata(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event(
                        "vllm.adapter.capability",
                        1.0,
                        {
                            "status": "supported",
                            "details": {
                                "detected_vllm_version": "0.18.0",
                                "detected_vllm_ascend_version": "0.18.0",
                                "visible_devices": ["0", "1", "2", "3"],
                                "hot_path_get_computed_blocks": True,
                                "hot_path_kv_blocks": True,
                                "async_scheduling_enabled": True,
                                "engine_core_step_with_batch_queue_hooked": True,
                                "engine_token_boundary_collection": "supported",
                                "prefix_cache_enabled": False,
                                "prefix_cache_config_source": "scheduler.cache_config.enable_prefix_caching",
                            },
                        },
                    ),
                    p2_event("vllm.kv.snapshot", 2.0, {"kv_usage_ratio": 0.2}),
                ],
            )

            bounds = module.p2_service_bounds(source)

        self.assertEqual(bounds["vllm_version"], "0.18.0")
        self.assertEqual(bounds["vllm_ascend_version"], "0.18.0")
        self.assertEqual(bounds["visible_devices"], ["0", "1", "2", "3"])
        self.assertTrue(bounds["hook_get_computed_blocks"])
        self.assertTrue(bounds["async_scheduling_enabled"])
        self.assertTrue(bounds["engine_core_step_with_batch_queue_hooked"])
        self.assertEqual(bounds["engine_token_boundary_collection"], "supported")
        self.assertFalse(bounds["prefix_cache_enabled"])
        self.assertEqual(bounds["event_count"], 2)

    def test_combines_per_task_step_csvs_for_batch_analysis(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.csv"
            second = root / "b.csv"
            first.write_text("run_id,step_index,p2_e2e_s\nrun-a,1,2.0\n", encoding="utf-8")
            second.write_text("run_id,step_index,p2_e2e_s\nrun-b,1,3.0\n", encoding="utf-8")
            output = root / "all-steps.csv"

            result = module.combine_step_csvs(
                [{"step_sequence_csv": str(first)}, {"step_sequence_csv": str(second)}],
                output,
            )
            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(result, output)
        self.assertEqual([row["run_id"] for row in rows], ["run-a", "run-b"])
        self.assertEqual([row["p2_e2e_s"] for row in rows], ["2.0", "3.0"])

    def test_can_optionally_include_time_window_extra_p2_requests(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "service.events.jsonl"
            output = root / "filtered.events.jsonl"
            write_jsonl(
                source,
                [
                    p2_event("vllm.request.received", 10.0, {"api_request_id": "resp-a", "vllm_request_id": "vllm-a"}),
                    p2_event("vllm.request.finished", 11.0, {"api_request_id": "resp-a", "vllm_request_id": "vllm-a"}),
                    p2_event("vllm.request.received", 10.2, {"api_request_id": "extra", "vllm_request_id": "vllm-extra"}),
                    p2_event("vllm.request.finished", 10.9, {"api_request_id": "extra", "vllm_request_id": "vllm-extra"}),
                    p2_event("vllm.request.received", 30.0, {"api_request_id": "outside", "vllm_request_id": "vllm-out"}),
                ],
            )

            module.filter_p2_events_for_response_ids(
                source,
                output,
                response_ids={"resp-a"},
                include_window_extras=True,
                window_margin_seconds=0.5,
            )
            filtered = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        request_ids = {
            event.get("payload", {}).get("api_request_id")
            for event in filtered
            if str(event.get("event_type", "")).startswith("vllm.request.")
        }
        self.assertEqual(request_ids, {"resp-a", "extra"})

    def test_batch_index_reports_one_api_id_mapped_to_multiple_engine_requests(self) -> None:
        module = load_module()
        events = [
            p2_event(
                "vllm.request.received",
                10.0,
                {"api_request_id": "resp-a", "vllm_request_id": "vllm-a-1"},
            ),
            p2_event(
                "vllm.request.finished",
                11.0,
                {"api_request_id": "resp-a", "vllm_request_id": "vllm-a-1"},
            ),
            p2_event(
                "vllm.request.received",
                10.2,
                {"api_request_id": "resp-a", "vllm_request_id": "vllm-a-2"},
            ),
            p2_event(
                "vllm.request.finished",
                11.2,
                {"api_request_id": "resp-a", "vllm_request_id": "vllm-a-2"},
            ),
        ]
        index = {
            "request_events": events,
            "by_run_id": {},
            "by_api_id": {"resp-a": events},
            "event_count": len(events),
        }

        selected, diagnostics, mode = module.select_indexed_p2_events(
            index,
            run_id="run-a",
            response_ids={"resp-a"},
            filter_mode="trajectory",
            include_window_extras=False,
            window_margin_seconds=0.0,
        )

        self.assertEqual(mode, "trajectory")
        self.assertEqual(len(selected), 4)
        self.assertEqual(diagnostics["matched_p2_request_count"], 0)
        self.assertEqual(diagnostics["multiple_p2_api_request_id_count"], 1)
        self.assertEqual(diagnostics["multiple_p2_api_request_ids"], ["resp-a"])


def assistant(response_id: str, tool_call_id: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": "bash"}}],
        "extra": {"response": {"id": response_id}},
    }


def p2_event(event_type: str, seconds: float, payload: dict) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"{event_type}-{seconds}",
        "run_id": "service",
        "source": "vllm_p2",
        "event_type": event_type,
        "quality": "exact",
        "wall_time_ns": ns(seconds),
        "monotonic_time_ns": ns(seconds),
        "labels": {},
        "payload": payload,
    }


def ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
