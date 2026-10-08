from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentmon.cli import main as agentmon_main
from agentmon.config import load_config
from agentmon.experiments import snapshots as snapshots_module
from agentmon.experiments import replay as replay_module
from agentmon.experiments.evidence import build_evidence_outputs, load_batch_experiment, write_evidence_outputs
from agentmon.experiments.golden import compare_semantic_artifacts
from agentmon.experiments.preflight import build_optimizer_experiment_preflight
from agentmon.optimizer.harness.schemas import merge_schema_profile
from agentmon.optimizer.harness.runner import run_optimizer_experiment_harness


class OptimizerExperimentHarnessTests(unittest.TestCase):
    def test_empty_batch_is_unresolved_and_does_not_emit_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "empty-batch"
            batch.mkdir()
            output = root / "output"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "optimizer",
                        "experiment",
                        "--batch-root",
                        str(batch),
                        "--output-dir",
                        str(output),
                        "--strategy",
                        "static_threshold",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 3)
            self.assertEqual(result["status"], "UNRESOLVED")
            self.assertEqual(result["snapshot_count"], 0)
            self.assertEqual(result["decision_count"], 0)
            self.assertIn("no_observed_timeline_timepoints", result["blocking_reasons"])
            self.assertTrue((output / "optimizer_preflight.json").is_file())
            self.assertTrue((output / "data_quality.json").is_file())
            self.assertFalse((output / "optimizer_decisions.jsonl").exists())

    def test_explicit_run_search_root_resolves_stale_batch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            search_root = root / "historical-runs"
            search_root.mkdir()
            for run_id in ("run-a", "run-b"):
                destination = search_root / "nested" / run_id
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(batch / run_id), str(destination))

            data = load_batch_experiment(batch, run_search_roots=[search_root])

            self.assertEqual(data.source_status["run_events"]["status"], "COMPLETE")
            self.assertEqual(
                data.source_status["run_events"]["resolution_source_counts"],
                {"explicit_run_search_root": 2},
            )
            self.assertTrue(all(item.events_path and item.events_path.is_file() for item in data.run_events.values()))
            self.assertTrue(all(not item.events for item in data.run_events.values()))

    def test_ambiguous_run_search_root_does_not_choose_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            search_root = root / "historical-runs"
            for branch in ("first", "second"):
                candidate = search_root / branch / "run-a"
                candidate.mkdir(parents=True)
                shutil.copy2(batch / "run-a" / "events.jsonl", candidate / "events.jsonl")
            (batch / "run-a" / "events.jsonl").unlink()

            data = load_batch_experiment(batch, run_search_roots=[search_root])

            self.assertEqual(data.run_events["run-a"].status, "UNRESOLVED")
            self.assertIsNone(data.run_events["run-a"].run_dir)
            self.assertEqual(len(data.run_events["run-a"].resolution_candidates), 2)
            self.assertEqual(data.source_status["run_events"]["status"], "UNRESOLVED")

    def test_m1_builds_evidence_outputs_without_content_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            data = load_batch_experiment(batch)
            outputs = build_evidence_outputs(data)
            output_dir = root / "m1"
            paths = write_evidence_outputs(outputs, output_dir)

            self.assertEqual(outputs["data_quality"]["overall_status"], "COMPLETE")
            self.assertEqual(outputs["experiment_manifest"]["analysis_sop_phase"], "04_evidence")
            self.assertIn("Do not treat API first output", outputs["experiment_manifest"]["forbidden_conclusions"][0])
            self.assertGreaterEqual(len(outputs["evidence_graph"]["nodes"]), 5)
            self.assertTrue(outputs["critical_path"])
            self.assertTrue(outputs["workload_normalized_metrics"])
            metric = outputs["workload_normalized_metrics"][0]
            self.assertIn("actual_prefill_tokens_status", metric)
            self.assertFalse(outputs["experiment_manifest"]["privacy_defaults"]["prompts_included"])
            self.assertNotIn("raw_tool_output", json.dumps(outputs["evidence_graph"]).lower())
            for path in paths.values():
                self.assertTrue(path.is_file(), path)

    def test_m2_replays_multiple_optimizer_strategies_and_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            config = write_config(root)
            cfg = load_config(config)
            result = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=root / "optimizer-experiment",
                cfg=cfg,
                strategies=["static_threshold", "workload_aware"],
            )
            output_dir = Path(result["output_dir"])
            timeline = read_csv(output_dir / "optimizer_decision_timeline.csv")
            audit = json.loads((output_dir / "optimizer_risk_audit.json").read_text(encoding="utf-8"))
            summary = json.loads((output_dir / "optimizer_strategy_summary.json").read_text(encoding="utf-8"))
            prediction = json.loads((output_dir / "prediction_variable_validation.json").read_text(encoding="utf-8"))
            prediction_rows = read_csv(output_dir / "prediction_variable_validation.csv")
            comparison = json.loads((output_dir / "mechanism_comparison.json").read_text(encoding="utf-8"))
            conflict = json.loads((output_dir / "conflict_audit.json").read_text(encoding="utf-8"))
            ablation = read_csv(output_dir / "ablation_table.csv")
            contribution = read_csv(output_dir / "contribution_decomposition.csv")
            decisions = [
                json.loads(line)
                for line in (output_dir / "optimizer_decisions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            report = (output_dir / "optimizer_replay_report.md").read_text(encoding="utf-8")
            evaluation_report = (output_dir / "evaluation_report.md").read_text(encoding="utf-8")
            provenance = json.loads((output_dir / "artifact_provenance.json").read_text(encoding="utf-8"))

            self.assertEqual(result["data_quality_status"], "COMPLETE")
            self.assertEqual(result["baseline_strategy"], "static_threshold")
            self.assertEqual({row["strategy"] for row in timeline}, {"static_threshold", "workload_aware"})
            self.assertIn("high", {row["admission_pressure"] for row in timeline})
            self.assertIn("True", {row["would_defer"] for row in timeline})
            self.assertIn("raw_p2_events", {row["model_service_quality"] for row in timeline})
            self.assertIn("0.92", {row["model_kv_usage_ratio"] for row in timeline})
            self.assertEqual(len(decisions), len(timeline))
            self.assertEqual(summary["snapshot_count"] * 2, summary["decision_count"])
            self.assertEqual(audit["result_type"], "agentmon.optimizer_experiment.risk_audit")
            self.assertEqual(prediction["result_type"], "agentmon.optimizer_experiment.prediction_variable_validation")
            self.assertTrue(prediction_rows)
            self.assertEqual(comparison["result_type"], "agentmon.optimizer_experiment.mechanism_comparison")
            self.assertEqual(conflict["result_type"], "agentmon.optimizer_experiment.conflict_audit")
            self.assertEqual(conflict["streaming_contract"]["max_resident_decision_group_count"], 1)
            self.assertEqual({row["strategy"] for row in ablation}, {"static_threshold", "workload_aware"})
            self.assertTrue(contribution)
            self.assertIn("Forbidden Conclusions", report)
            self.assertIn("does not claim causal speedup", evaluation_report)
            self.assertEqual(provenance["validation_status"], "COMPLETE")
            self.assertFalse(provenance["privacy_defaults"]["prompts_included"])
            self.assertTrue(provenance["source_identity"]["source_tree_sha256"])

    def test_repeated_streaming_runs_have_matching_semantic_golden_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            first = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=root / "first",
                cfg=cfg,
                strategies=["static_threshold", "workload_aware"],
            )
            second = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=root / "second",
                cfg=cfg,
                strategies=["static_threshold", "workload_aware"],
            )
            names = (
                "experiment_manifest.json",
                "evidence_graph.json",
                "critical_path.csv",
                "bottleneck_attribution.json",
                "workload_normalized_metrics.csv",
                "data_quality.json",
                "optimizer_decision_timeline.csv",
                "optimizer_decisions.jsonl",
                "optimizer_strategy_summary.json",
                "optimizer_risk_audit.json",
                "prediction_variable_validation.json",
                "mechanism_comparison.json",
                "ablation_table.csv",
                "contribution_decomposition.csv",
                "conflict_audit.json",
            )
            comparison = compare_semantic_artifacts(
                {name: Path(first["output_dir"]) / name for name in names},
                {name: Path(second["output_dir"]) / name for name in names},
            )

            self.assertEqual(comparison["status"], "PASS", comparison)

    def test_schema_profile_overrides_p2_event_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            write_custom_p2_events(batch / "run-a" / "events.jsonl", "run-a")
            write_custom_p2_events(batch / "run-b" / "events.jsonl", "run-b")
            config = write_config(root)
            cfg = load_config(config)
            schema_profile = {
                "p2_service_event_profile": {
                    "event_sources": ["custom_p2"],
                    "scheduler_event_types": ["custom.scheduler.sample"],
                    "kv_event_types": ["custom.kv.sample"],
                    "running_request_fields": ["active"],
                    "waiting_request_fields": ["queued"],
                    "kv_usage_ratio_fields": ["cache_pressure"],
                }
            }

            result = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=root / "custom-profile-output",
                cfg=cfg,
                strategies=["workload_aware"],
                schema_profile=schema_profile,
            )

            timeline = read_csv(Path(result["output_dir"]) / "optimizer_decision_timeline.csv")
            self.assertIn("raw_p2_events", {row["model_service_quality"] for row in timeline})
            self.assertIn("0.81", {row["model_kv_usage_ratio"] for row in timeline})

    def test_snapshot_timeline_uses_indexed_model_service_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            data = load_batch_experiment(batch)

            with patch.object(
                snapshots_module,
                "latest_model_service_sample",
                side_effect=AssertionError("snapshot timeline must use indexed service samples"),
            ):
                snapshots = snapshots_module.build_snapshot_timeline(data, cfg)

            raw_service_snapshots = [
                snapshot
                for snapshot in snapshots
                if snapshot.model_service and snapshot.model_service.quality == "raw_p2_events"
            ]
            self.assertTrue(raw_service_snapshots)
            self.assertIn(
                0.92,
                {snapshot.model_service.kv_usage_ratio for snapshot in raw_service_snapshots if snapshot.model_service},
            )

    def test_lazy_step_index_replays_with_same_snapshot_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            materialized = load_batch_experiment(batch)
            lazy = load_batch_experiment(batch, materialize_step_rows=False)

            self.assertFalse(lazy.step_rows_materialized)
            self.assertEqual(lazy.step_rows, [])
            self.assertEqual(
                list(lazy.iter_step_rows()),
                materialized.step_rows,
            )
            self.assertEqual(
                [snapshot.to_dict() for snapshot in snapshots_module.iter_snapshot_timeline(lazy, cfg)],
                [snapshot.to_dict() for snapshot in snapshots_module.iter_snapshot_timeline(materialized, cfg)],
            )

    def test_incremental_snapshot_timeline_matches_reference_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            data = load_batch_experiment(batch)
            profile = merge_schema_profile()
            samples = snapshots_module.build_model_service_event_samples(
                data,
                profile["p2_service_event_profile"],
            )
            sample_index = snapshots_module.build_model_service_sample_index(samples)
            run_intervals = snapshots_module.build_run_intervals(data, profile["step_csv_profile"])

            timeline = snapshots_module.build_snapshot_timeline(data, cfg)

            for snapshot in timeline:
                timestamp = snapshot.timestamp_mono_ns
                assert timestamp is not None
                expected_model = snapshots_module.model_service_snapshot_at(
                    data,
                    timestamp,
                    step_profile=profile["step_csv_profile"],
                    model_service_samples=samples,
                    model_service_sample_index=sample_index,
                )
                expected_active = snapshots_module.active_runs_at(run_intervals, timestamp)
                self.assertEqual(snapshot.model_service.to_dict(), expected_model.to_dict())
                self.assertEqual(snapshot.queue.running_count, len(expected_active))
                self.assertEqual(
                    [run.run_id for run in snapshot.queue.runs],
                    [interval.run_id for interval in expected_active],
                )

    def test_replay_strategy_instances_are_built_once_per_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            original = replay_module.build_optimizer_strategy

            with patch.object(replay_module, "build_optimizer_strategy", wraps=original) as build_strategy:
                run_optimizer_experiment_harness(
                    batch_root=batch,
                    output_dir=root / "output",
                    cfg=cfg,
                    strategies=["static_threshold", "workload_aware"],
                )

            self.assertEqual(build_strategy.call_count, 2)

    def test_harness_uses_streaming_replay_not_materialized_snapshot_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            with patch.object(
                replay_module,
                "build_snapshot_timeline",
                side_effect=AssertionError("streaming harness must not materialize snapshot list"),
            ):
                result = run_optimizer_experiment_harness(
                    batch_root=batch,
                    output_dir=root / "streaming-output",
                    cfg=cfg,
                    strategies=["static_threshold"],
                )

            manifest = json.loads(
                (Path(result["output_dir"]) / "stages" / "m2_decision_replay" / "snapshot_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["snapshot_count"], result["snapshot_count"])
            self.assertTrue(manifest["parts"])
            self.assertEqual(manifest["streaming_contract"]["max_resident_snapshot_count"], 1)
            self.assertEqual(manifest["streaming_contract"]["static_timepoint_ordering"], "external_merge_unique_parts")
            self.assertEqual(manifest["streaming_contract"]["queue_percentile_backend"], "stage_private_sqlite_treap_exact")
            self.assertEqual(manifest["streaming_contract"]["prediction_observation_backend"], "stage_private_sqlite_ordered_index")
            self.assertEqual(manifest["streaming_contract"]["step_input_adapter"], "source_only_csv_iterator")
            self.assertFalse(manifest["streaming_contract"]["metrics_sqlite_used"])
            self.assertEqual(result["stage_outcomes"]["m2_decision_replay"], "COMPLETE")

    def test_streaming_replay_matches_materialized_compatibility_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            data = load_batch_experiment(batch)

            materialized = replay_module.run_optimizer_advice_replay(
                data,
                cfg,
                ["static_threshold"],
            )
            streaming = replay_module.run_optimizer_advice_replay_streaming(
                data,
                cfg,
                ["static_threshold"],
                root / "streaming-output",
                work_dir=root / "streaming-stage",
            )

            self.assertEqual(len(materialized["snapshots"]), streaming["snapshot_count"])
            self.assertEqual(len(materialized["decision_timeline"]), streaming["decision_count"])
            self.assertEqual(
                materialized["strategy_summary"],
                json.loads(Path(streaming["paths"]["optimizer_strategy_summary"]).read_text(encoding="utf-8")),
            )
            self.assertEqual(
                materialized["risk_audit"],
                json.loads(Path(streaming["paths"]["optimizer_risk_audit"]).read_text(encoding="utf-8")),
            )
            self.assertEqual(
                materialized["prediction_variable_validation"],
                json.loads(Path(streaming["paths"]["prediction_variable_validation"]).read_text(encoding="utf-8")),
            )

    def test_disk_order_statistic_matches_exact_linear_percentile_with_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = snapshots_module.DiskOrderStatistic(Path(tmp) / "queue-percentile.sqlite3")
            try:
                for value in (1.0, 8.0, 2.0, 2.0, 10.0):
                    index.add(value)
                self.assertEqual(index.percentile(0), 1.0)
                self.assertEqual(index.percentile(50), 2.0)
                self.assertEqual(index.percentile(95), 9.6)
            finally:
                index.close()

    def test_snapshot_stream_uses_external_static_timepoint_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            data = load_batch_experiment(batch)

            with patch.object(
                snapshots_module,
                "step_item_timepoints",
                side_effect=AssertionError("streaming path must not build a complete step timestamp list"),
            ):
                snapshots = list(
                    snapshots_module.iter_snapshot_timeline(
                        data,
                        cfg,
                        work_dir=root / "snapshot-work",
                        memory_budget_bytes=64,
                    )
                )

            self.assertTrue(snapshots)
            self.assertTrue(list((root / "snapshot-work" / "static-timepoint-parts").glob("timestamps-*.txt")))

    def test_streaming_replay_rejects_nonpositive_memory_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))

            result = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=root / "invalid-budget",
                cfg=cfg,
                strategies=["static_threshold"],
                memory_budget_bytes=0,
            )

            self.assertEqual(result["stage_outcomes"]["m2_decision_replay"], "FAILED")
            self.assertEqual(result["status"], "FAILED")
            self.assertTrue(Path(result["report"]).is_file())
            self.assertTrue(Path(result["artifact_provenance"]).is_file())

    def test_cli_returns_nonzero_when_replay_stage_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "failed-cli-output"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "optimizer",
                        "experiment",
                        "--batch-root",
                        str(make_batch(root)),
                        "--output-dir",
                        str(output),
                        "--strategy",
                        "static_threshold",
                        "--memory-budget-bytes",
                        "0",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "FAILED")

    def test_pipeline_validates_m3_outputs_before_writing_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_optimizer_experiment_harness(
                batch_root=make_batch(root),
                output_dir=root / "ordered-output",
                cfg=load_config(write_config(root)),
                strategies=["static_threshold"],
            )

            manifest = json.loads(Path(result["pipeline_manifest"]).read_text(encoding="utf-8"))
            stage_order = [row["stage_name"] for row in manifest["stages"]]
            provenance = json.loads(Path(result["artifact_provenance"]).read_text(encoding="utf-8"))
            artifact_names = {row["artifact_name"] for row in provenance["artifacts"]}

            self.assertLess(stage_order.index("m3_mechanism_evaluation"), stage_order.index("report_build"))
            self.assertLess(stage_order.index("report_build"), stage_order.index("artifact_validation"))
            self.assertIn("evaluation:mechanism_comparison", artifact_names)
            self.assertIn("evaluation:evaluation_report", artifact_names)

    def test_resume_reuses_completed_replay_only_when_input_identity_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            output = root / "resume-output"
            first = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=output,
                cfg=cfg,
                strategies=["static_threshold"],
            )
            with patch.object(
                replay_module,
                "iter_snapshot_timeline",
                side_effect=AssertionError("completed replay should be reused"),
            ):
                second = run_optimizer_experiment_harness(
                    batch_root=batch,
                    output_dir=output,
                    cfg=cfg,
                    strategies=["static_threshold"],
                    resume=True,
                )

            self.assertEqual(first["decision_count"], second["decision_count"])
            self.assertEqual(second["stage_outcomes"]["m2_decision_replay"], "COMPLETE")

    def test_resume_rejects_changed_strategy_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            output = root / "resume-strategy-output"
            run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=output,
                cfg=cfg,
                strategies=["static_threshold"],
            )

            result = run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=output,
                cfg=cfg,
                strategies=["workload_aware"],
                resume=True,
            )
            summary = json.loads((output / "optimizer_strategy_summary.json").read_text(encoding="utf-8"))

            self.assertEqual(result["strategies"], ["workload_aware"])
            self.assertEqual([row["strategy"] for row in summary["strategies"]], ["workload_aware"])

    def test_resume_rejects_corrupted_completed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            cfg = load_config(write_config(root))
            output = root / "resume-corrupt-output"
            run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=output,
                cfg=cfg,
                strategies=["static_threshold"],
            )
            summary_path = output / "optimizer_strategy_summary.json"
            summary_path.write_text("corrupted\n", encoding="utf-8")

            run_optimizer_experiment_harness(
                batch_root=batch,
                output_dir=output,
                cfg=cfg,
                strategies=["static_threshold"],
                resume=True,
            )

            repaired = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["strategies"][0]["strategy"], "static_threshold")

    def test_conflicting_shared_service_samples_remain_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            conflict = {
                "schema_version": "1.0",
                "run_id": "run-b",
                "source": "vllm_p2",
                "event_type": "vllm.scheduler.snapshot",
                "quality": "exact",
                "monotonic_time_ns": ns(1.2),
                "payload": {"running_requests": 1, "waiting_requests": 2},
            }
            with (batch / "run-b" / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(conflict) + "\n")
            cfg = load_config(write_config(root))
            data = load_batch_experiment(batch)

            preflight = build_optimizer_experiment_preflight(data)
            timeline = snapshots_module.build_snapshot_timeline(data, cfg)
            conflicted_snapshot = next(
                snapshot for snapshot in timeline if snapshot.timestamp_mono_ns == ns(1.2)
            )

            self.assertEqual(preflight["status"], "PARTIAL")
            self.assertGreater(
                preflight["timeline_evidence"]["conflicting_model_service_sample_count"],
                0,
            )
            self.assertIsNone(conflicted_snapshot.model_service.waiting_requests)
            self.assertEqual(conflicted_snapshot.model_service.quality, "PARTIAL")

    def test_missing_step_and_event_data_remains_partial_or_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "partial-batch"
            batch.mkdir()
            (batch / "batch-manifest.json").write_text(
                json.dumps({"schema_version": "3.0", "task_count": 1}),
                encoding="utf-8",
            )
            write_rows(
                batch / "batch-results.tsv",
                [
                    {
                        "task_id": "task-a",
                        "run_id": "run-a",
                        "status": "failed",
                    }
                ],
                delimiter="\t",
            )

            outputs = build_evidence_outputs(load_batch_experiment(batch))

            self.assertEqual(outputs["data_quality"]["overall_status"], "PARTIAL")
            self.assertEqual(outputs["data_quality"]["sources"]["step_csv"]["status"], "UNKNOWN")
            self.assertEqual(outputs["data_quality"]["sources"]["run_events"]["status"], "UNKNOWN")
            self.assertEqual(outputs["bottleneck_attribution"]["dominant_observed_component"], "UNKNOWN")

    def test_partial_run_event_coverage_does_not_report_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            (batch / "run-b" / "events.jsonl").unlink()

            outputs = build_evidence_outputs(load_batch_experiment(batch))

            self.assertEqual(outputs["data_quality"]["overall_status"], "PARTIAL")
            self.assertEqual(outputs["data_quality"]["sources"]["run_events"]["status"], "PARTIAL")
            self.assertEqual(outputs["data_quality"]["sources"]["run_events"]["missing_run_event_count"], 1)

    def test_cli_optimizer_experiment_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = make_batch(root)
            config = write_config(root)
            stdout = io.StringIO()
            status_file = root / "optimizer-status.json"

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "optimizer",
                        "experiment",
                        "--config",
                        str(config),
                        "--batch-root",
                        str(batch),
                        "--strategy",
                        "static_threshold",
                        "--output-dir",
                        str(root / "cli-output"),
                        "--status-file",
                        str(status_file),
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.optimizer_experiment.run_result")
            self.assertTrue(Path(result["report"]).is_file())
            status = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(status["stage"], "complete")
            self.assertEqual(status["state"], "COMPLETE")
            self.assertEqual(status["detail"]["decision_count"], result["decision_count"])


def make_batch(root: Path) -> Path:
    batch = root / "batch"
    step_dir = batch / "step-sequence"
    run_a = batch / "run-a"
    run_b = batch / "run-b"
    for path in (step_dir, run_a, run_b):
        path.mkdir(parents=True)
    (batch / "batch-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "manifest_type": "agentmon.batch_experiment",
                "task_count": 2,
                "concurrency": 1,
                "configuration_fingerprint": "fixture-fingerprint",
            }
        ),
        encoding="utf-8",
    )
    (batch / "batch-summary.json").write_text(
        json.dumps({"configured_concurrency": 1, "task_count": 2}),
        encoding="utf-8",
    )
    write_rows(
        batch / "batch-results.tsv",
        [
            {
                "task_id": "task-a",
                "run_id": "run-a",
                "run_dir": str(run_a),
                "status": "success",
                "execution_success": "true",
                "workload_start_mono_ns": ns(0),
                "workload_end_mono_ns": ns(4),
                "workload_wall_s": "4.0",
            },
            {
                "task_id": "task-b",
                "run_id": "run-b",
                "run_dir": str(run_b),
                "status": "success",
                "execution_success": "true",
                "workload_start_mono_ns": ns(1),
                "workload_end_mono_ns": ns(5),
                "workload_wall_s": "4.0",
            },
        ],
        delimiter="\t",
    )
    write_rows(
        step_dir / "all-steps.csv",
        [
            model_row("run-a", ns(0.5), ns(2.0), waiting=0),
            tool_row("run-a", ns(2.1), ns(3.0)),
            model_row("run-b", ns(1.0), ns(3.0), waiting=1),
            tool_row("run-b", ns(3.1), ns(4.0)),
        ],
    )
    write_events(run_a / "events.jsonl", "run-a")
    write_events(run_b / "events.jsonl", "run-b")
    return batch


def model_row(run_id: str, start: int, end: int, *, waiting: int) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_id": f"task-{run_id[-1]}",
        "step_id": f"step-{run_id}",
        "step_index": 1,
        "item_type": "model",
        "span_id": f"model-{run_id}",
        "api_request_id": f"api-{run_id}",
        "p2_match_status": "matched",
        "p2_lifecycle_status": "complete",
        "item_start_mono_ns": start,
        "item_end_mono_ns": end,
        "model_call_e2e_s": (end - start) / 1_000_000_000,
        "p2_queue_s": "0.25",
        "p2_ttft_s": "0.75",
        "p2_prefill_s": "0.5",
        "p2_decode_s": "0.75",
        "p2_computed_prefill_tokens": "10",
        "p2_generation_tokens": "5",
        "step_scheduler_running_requests_max": "1",
        "step_scheduler_waiting_requests_max": str(waiting),
    }


def tool_row(run_id: str, start: int, end: int) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_id": f"task-{run_id[-1]}",
        "step_id": f"tool-{run_id}",
        "step_index": 2,
        "item_type": "tool",
        "span_id": f"tool-{run_id}",
        "tool_call_id": f"tool-{run_id}",
        "item_start_mono_ns": start,
        "item_end_mono_ns": end,
        "tool_call_duration_s": (end - start) / 1_000_000_000,
        "command_hash": f"hash-{run_id}",
    }


def write_events(path: Path, run_id: str) -> None:
    rows = [
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "vllm_p2",
            "event_type": "vllm.request.received",
            "monotonic_time_ns": ns(1),
            "payload": {"api_request_id": f"api-{run_id}", "vllm_request_id": f"engine-{run_id}"},
        },
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "vllm_p2",
            "event_type": "vllm.scheduler.snapshot",
            "quality": "exact",
            "monotonic_time_ns": ns(1.2),
            "payload": {"running_requests": 1, "waiting_requests": 1},
        },
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "vllm_p2",
            "event_type": "vllm.kv.snapshot",
            "quality": "exact",
            "monotonic_time_ns": ns(1.3),
            "payload": {"kv_usage_ratio": 0.92},
        },
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "host",
            "event_type": "sample",
            "monotonic_time_ns": ns(1),
            "payload": {"meminfo": {"MemTotal": 1000, "MemAvailable": 500}},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_custom_p2_events(path: Path, run_id: str) -> None:
    rows = [
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "custom_p2",
            "event_type": "custom.scheduler.sample",
            "monotonic_time_ns": ns(1.2),
            "payload": {"active": 1, "queued": 2},
        },
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": "custom_p2",
            "event_type": "custom.kv.sample",
            "monotonic_time_ns": ns(1.3),
            "payload": {"cache_pressure": 0.81},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_config(root: Path) -> Path:
    config = root / "agentmon-test.toml"
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
max_running_runs = 1

[optimizer]
strategy = "static_threshold"
mode = "advise"
""".strip(),
        encoding="utf-8",
    )
    return config


def write_rows(path: Path, rows: list[dict[str, object]], *, delimiter: str = ",") -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
