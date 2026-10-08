from __future__ import annotations

import gzip
import io
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from agentmon.analysis.analyzers import analyze_scheduler
from agentmon.analysis.artifacts import build_artifact_manifest
from agentmon.analysis.claims import build_claim_registry
from agentmon.analysis.config import BaselineConfig, default_analysis_config
from agentmon.analysis.discovery import classify_file, discover_run_dirs
from agentmon.analysis.execution import STAGE_NAMES, build_stage_execution_plan, stage_cache_artifacts_ready
from agentmon.analysis.io import read_bundle_table, write_json
from agentmon.analysis.normalize import p2_eligibility, request_identity_audit_row
from agentmon.analysis.p2_index import build_or_reuse_p2_index, query_p2_events
from agentmon.analysis.pipeline import run_all_analysis
from agentmon.analysis.preflight import module_check, run_analysis_preflight
from agentmon.analysis.profiles import available_profiles, resolve_profile
from agentmon.analysis.resume import normalized_cache_ready, source_fingerprint
from agentmon.cli import main as agentmon_main
from agentmon.reporting.report import read_event_log


class AnalysisRepairTests(unittest.TestCase):
    def test_required_dependency_check_fails_without_import(self) -> None:
        with patch("agentmon.analysis.preflight.importlib.import_module", side_effect=ModuleNotFoundError("missing")):
            row = module_check("pandas", category="REQUIRED", purpose="test")
        self.assertEqual(row["status"], "FAIL")
        self.assertEqual(row["reason"], "pandas_not_installed")

    def test_csv_preflight_does_not_report_missing_parquet_engine_as_smoke_pass(self) -> None:
        with patch.dict("sys.modules", {"pyarrow": None, "pyarrow.parquet": None}):
            from agentmon.analysis.preflight import parquet_smoke_check

            row = parquet_smoke_check(output_format="csv")
        self.assertEqual(row["category"], "OPTIONAL")
        self.assertEqual(row["status"], "UNAVAILABLE")

    def test_preflight_required_failure_does_not_scan_input_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            root.mkdir()
            failed_dependency = {
                "name": "pandas", "category": "REQUIRED", "status": "FAIL",
                "reason": "pandas_not_installed",
            }
            with patch("agentmon.analysis.preflight.dependency_checks", return_value=[failed_dependency]), patch(
                "agentmon.analysis.preflight.parquet_smoke_check",
                return_value={"name": "parquet_smoke", "category": "OPTIONAL", "status": "PASS"},
            ), patch("agentmon.analysis.preflight.build_input_inventory") as inventory:
                result = run_analysis_preflight(input_root=root, output_root=Path(tmp) / "out", output_format="csv")
            inventory.assert_not_called()
            self.assertEqual(result["required_dependencies_status"], "FAIL")
            self.assertEqual(result["heavy_analysis_allowed"], 0)
            self.assertEqual(next(row for row in result["checks"] if row["name"] == "input_inventory")["status"], "SKIPPED")

    def test_strict_baseline_mismatch_stops_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            with redirect_stdout(io.StringIO()):
                rc = agentmon_main(
                    [
                        "analysis", "run-all", "--input", str(root), "--output", str(out),
                        "--output-format", "csv", "--expected-runs", "2", "--strict",
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertFalse((out / "01_normalized" / "runs.csv.gz").exists())
            self.assertEqual(json.loads((out / "analysis_ready.json").read_text())["overall_status"], "FAIL")

    def test_repeated_request_ids_across_runs_keep_all_fifty_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=2, requests_per_run=50, include_p2=True)
            result = run_all_analysis(
                input_root=root,
                output_root=out,
                cfg=default_analysis_config(),
                output_format="csv",
                no_figures=True,
                log_level="ERROR",
            )
            requests = read_bundle_table(out, "01_normalized", "requests")
            p2 = read_bundle_table(out, "01_normalized", "p2_requests")
            audit = json.loads((out / "01_normalized" / "request_parse_audit_summary.json").read_text())
            self.assertEqual(result["request_attempts_parsed"], 100)
            self.assertEqual(len(requests), 100)
            self.assertEqual(len(p2), 100)
            self.assertEqual(len({row["request_attempt_id"] for row in requests}), 100)
            self.assertEqual(audit["unknown_loss_count"], 0)
            self.assertEqual(audit["request_conservation_status"], "PASS")

    def test_within_run_duplicate_and_malformed_request_candidates_are_explained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            event_path = next(root.rglob("events.jsonl"))
            lines = event_path.read_text(encoding="utf-8").splitlines()
            unsupported = event("run-0", 99, "custom", "custom.request.unknown", {"request_id": "unsupported"})
            event_path.write_text("\n".join([lines[0], lines[0], *lines[1:], json.dumps(unsupported), "{malformed"]) + "\n", encoding="utf-8")
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            audit = read_bundle_table(out, "01_normalized", "request_parse_audit")[0]
            conservation = read_bundle_table(out, "02_validation", "request_conservation")
            self.assertEqual(int(audit["parsed_request_attempts"]), 1)
            self.assertEqual(int(audit["duplicates_removed"]), 1)
            self.assertEqual(int(audit["unsupported_request_candidates"]), 1)
            self.assertEqual(int(audit["parse_errors"]), 1)
            self.assertEqual(int(audit["unknown_loss_count"]), 0)
            self.assertEqual(conservation[0]["status"], "PASS")

    def test_error_eligibility_tracks_whether_the_api_boundary_was_crossed(self) -> None:
        profile = available_profiles(default_analysis_config())["agentmon"]
        before_eligible, _, _ = p2_eligibility({"status": "error"}, profile)
        after_eligible, _, _ = p2_eligibility({"status": "error", "api_request_id": "api-1"}, profile)
        before = request_identity_audit_row(
            {"request_status": "error", "p2_eligible": before_eligible, "lifecycle_complete": False},
            {},
            {},
            "run",
        )
        after = request_identity_audit_row(
            {"request_status": "error", "p2_eligible": after_eligible, "lifecycle_complete": False},
            {},
            {},
            "run",
        )
        self.assertEqual(before["classification"], "ERROR_BEFORE_API")
        self.assertFalse(before["publishability_denominator_member"])
        self.assertFalse(before["produces_gap"])
        self.assertEqual(after["classification"], "ERROR_AFTER_API_ACCEPTED")
        self.assertTrue(after["publishability_denominator_member"])
        self.assertTrue(after["produces_gap"])

    def test_shared_p2_index_scans_once_then_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw-p2" / "events.jsonl"
            source.parent.mkdir()
            source.write_text(
                json.dumps(
                    {
                        "event_id": "p2-1",
                        "source": "vllm_p2",
                        "event_type": "vllm.request.received",
                        "payload": {"agentmon_run_id": "run-a", "api_request_id": "req-a"},
                    }
                ) + "\n",
                encoding="utf-8",
            )
            progress: list[dict[str, object]] = []
            first = build_or_reuse_p2_index(
                [source],
                cache_root=root / "cache",
                cfg=default_analysis_config(),
                progress_callback=progress.append,
            )
            second = build_or_reuse_p2_index([source], cache_root=root / "cache", cfg=default_analysis_config())
            events, query = query_p2_events(second.index_path, run_id="run-a", task_id=None, aliases=[])
            self.assertEqual(first.metrics["full_scan_count"], 1)
            self.assertEqual(progress[-1]["source_files_completed"], 1)
            self.assertEqual(progress[-1]["events_parsed"], 1)
            self.assertEqual(second.metrics["full_scan_count"], 0)
            self.assertTrue(second.metrics["index_reused"])
            self.assertEqual(len(events), 1)
            self.assertEqual(query["bytes_scanned_per_run"], 0)

    def test_unscoped_shared_p2_with_cross_run_repeated_alias_is_not_double_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=2, requests_per_run=1, include_p2=False)
            shared = root / "raw-p2" / "events.jsonl"
            shared.parent.mkdir()
            rows = []
            for sequence, name in enumerate(("received", "queued", "first_scheduled", "first_token", "engine_finished", "finished"), start=1):
                rows.append(event("", sequence, "vllm_p2", f"vllm.request.{name}", {"api_request_id": "request-0", "vllm_request_id": "engine-shared"}))
                rows[-1].pop("run_id", None)
            shared.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            requests = read_bundle_table(out, "01_normalized", "requests")
            lifecycle = read_bundle_table(out, "01_normalized", "request_lifecycle")
            runs = read_bundle_table(out, "01_normalized", "runs")
            identity = read_bundle_table(out, "01_normalized", "request_identity_audit")
            p2 = read_bundle_table(out, "01_normalized", "p2_requests")
            self.assertEqual(sum(str(row.get("p2_matched")).lower() == "true" for row in requests), 0)
            self.assertTrue(all(row["classification"] == "P2_ATTRIBUTION_FAILURE" for row in identity))
            self.assertTrue(all(row["clock_domain_status"] == "ambiguous_p2_attribution" for row in lifecycle))
            self.assertTrue(all(row.get("server_e2e_s") in {None, ""} for row in lifecycle))
            self.assertTrue(all(float(row["p2_match_rate"]) == 0.0 for row in runs))
            self.assertTrue(all(row.get("p2_lifecycle_rate") in {None, ""} for row in runs))
            self.assertEqual(len(p2), 1)
            self.assertEqual(p2[0]["attribution_status"], "AMBIGUOUS_CROSS_RUN_ALIAS")

    def test_shared_p2_index_incrementally_reads_appended_bytes_and_time_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "events.jsonl"
            first_event = {"event_id": "one", "event_type": "vllm.request.received", "wall_time_ns": 10, "payload": {"batch_id": "b", "api_request_id": "a"}}
            second_event = {"event_id": "two", "event_type": "vllm.request.finished", "wall_time_ns": 20, "payload": {"batch_id": "b", "api_request_id": "a"}}
            source.write_text(json.dumps(first_event) + "\n", encoding="utf-8")
            first = build_or_reuse_p2_index([source], cache_root=root / "cache", cfg=default_analysis_config())
            with source.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second_event) + "\n")
            second = build_or_reuse_p2_index([source], cache_root=root / "cache", cfg=default_analysis_config())
            events, _ = query_p2_events(second.index_path, run_id=None, task_id=None, aliases=[], batch_id="b", wall_start_ns=15)
            self.assertEqual(first.metrics["full_scan_count"], 1)
            self.assertEqual(second.metrics["full_scan_count"], 0)
            self.assertEqual(second.metrics["incremental_scan_count"], 1)
            self.assertEqual(second.metrics["events_indexed_delta"], 1)
            self.assertEqual([row["event_id"] for row in events], ["two"])

    def test_p2_index_scope_fields_are_supplied_by_schema_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "events.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "event_id": "custom",
                        "event_type": "custom.request.received",
                        "payload": {"custom_run": "run-custom", "custom_request": "request-custom"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cfg = default_analysis_config()
            cfg = replace(
                cfg,
                profile_detection=replace(
                    cfg.profile_detection,
                    definitions={
                        "custom": {
                            "run_id_keys": ["custom_run"],
                            "request_id_keys": ["custom_request"],
                        }
                    },
                ),
            )
            index = build_or_reuse_p2_index([source], cache_root=root / "cache", cfg=cfg)
            rows, _ = query_p2_events(index.index_path, run_id="run-custom", task_id=None, aliases=[])
            self.assertEqual([row["event_id"] for row in rows], ["custom"])

    def test_p2_index_resumes_after_last_committed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = []
            for index in range(2):
                source = root / f"source-{index}.jsonl"
                source.write_text(
                    json.dumps(
                        {
                            "event_id": f"event-{index}",
                            "event_type": "vllm.request.received",
                            "payload": {"agentmon_run_id": f"run-{index}", "api_request_id": f"request-{index}"},
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                sources.append(source)

            def interrupt_after_first_source(row: dict[str, object]) -> None:
                if row.get("source_files_completed") == 1:
                    raise RuntimeError("synthetic interruption")

            with self.assertRaises(RuntimeError):
                build_or_reuse_p2_index(
                    sources,
                    cache_root=root / "cache",
                    cfg=default_analysis_config(),
                    progress_callback=interrupt_after_first_source,
                )
            resumed = build_or_reuse_p2_index(sources, cache_root=root / "cache", cfg=default_analysis_config())
            self.assertTrue(resumed.metrics["index_partial_resume"])
            self.assertTrue(resumed.metrics["index_reused"])
            self.assertEqual(resumed.metrics["full_scan_count"], 1)
            self.assertEqual(resumed.metrics["events_indexed"], 2)

    def test_gzip_run_events_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            event = {"event_id": "e", "run_id": "r", "source": "x", "event_type": "sample", "payload": {}}
            with gzip.open(run / "events.jsonl.gz", "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
            result = read_event_log(run)
            self.assertEqual(result["valid_lines"], 1)
            self.assertTrue(result["path"].endswith(".gz"))

    def test_zstd_run_file_is_discovered_before_optional_decompression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "manifest.json").write_text('{"run_id": "run"}\n', encoding="utf-8")
            events = run / "events.jsonl.zst"
            events.write_bytes(b"fixture-only")
            self.assertEqual(discover_run_dirs(Path(tmp)), [run])
            self.assertEqual(classify_file(events), "run_events")

    def test_resume_rejects_changed_input_without_overwriting_compatibility_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            before = json.loads((out / "analysis_manifest.json").read_text())
            throughput_checkpoint = (out / "stages" / "throughput_analysis" / "stage-status.json").read_text()
            event_path = next(root.rglob("events.jsonl"))
            event_path.write_text(event_path.read_text() + json.dumps({"event_id": "new", "run_id": "run-0", "source": "host", "event_type": "sample", "payload": {}}) + "\n")
            result = run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", resume=True, no_figures=True, log_level="ERROR")
            after = json.loads((out / "analysis_manifest.json").read_text())
            self.assertEqual(result["resume_status"], "REJECTED")
            self.assertIn("input_fingerprint_changed", result["resume"]["reason"])
            self.assertEqual(before["input_fingerprint"], after["input_fingerprint"])
            self.assertEqual(
                throughput_checkpoint,
                (out / "stages" / "throughput_analysis" / "stage-status.json").read_text(),
            )
            self.assertTrue((out / "resume_rejected_attempt.json").is_file())

    def test_resume_rejects_profile_change_and_partial_normalization_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            initial = replace(default_analysis_config(), profile="agentmon")
            run_all_analysis(input_root=root, output_root=out, cfg=initial, output_format="csv", no_figures=True, log_level="ERROR")
            changed = replace(default_analysis_config(), profile="agentmon_vllm")
            result = run_all_analysis(input_root=root, output_root=out, cfg=changed, output_format="csv", resume=True, no_figures=True, log_level="ERROR")
            self.assertEqual(result["resume_status"], "REJECTED")
            self.assertIn("profile_changed", result["resume"]["reason"])
            manifest = json.loads((out / "analysis_manifest.json").read_text())
            manifest["stage_completion_status"]["normalize_runs"] = "PARTIAL"
            self.assertFalse(normalized_cache_ready(out, analysis_manifest=manifest))

    def test_resume_reuses_completed_normalization_but_reruns_after_partial_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=2, include_p2=True)
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            result = run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", resume=True, no_figures=True, log_level="ERROR")
            stages = json.loads((out / "analysis_status.json").read_text())["STAGE_STATUS"]
            self.assertEqual(result["resume_status"], "ACCEPTED")
            self.assertEqual(stages["normalize_runs"], "CACHED")
            self.assertEqual(stages["validation"], "PARTIAL")
            self.assertEqual(stages["throughput_analysis"], "PASS")
            self.assertEqual(stages["evidence_build"], "PASS")
            self.assertEqual(stages["model_pack_build"], "PASS")

    def test_resume_rejects_source_fingerprint_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            with patch("agentmon.analysis.resume.source_fingerprint", return_value="changed-source"):
                result = run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", resume=True, no_figures=True, log_level="ERROR")
            self.assertEqual(result["resume_status"], "REJECTED")
            self.assertIn("source_fingerprint_changed", result["resume"]["reason"])

    def test_source_fingerprint_includes_schema_and_profile_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "agentmon"
            package.mkdir()
            module = package / "__init__.py"
            schema = package / "analysis" / "schemas" / "schema.yaml"
            schema.parent.mkdir(parents=True)
            module.write_text("VERSION = 1\n", encoding="utf-8")
            schema.write_text('{"event": "first"}\n', encoding="utf-8")
            with patch("agentmon.analysis.resume.agentmon.__file__", str(module)):
                before = source_fingerprint()
                schema.write_text('{"event": "second"}\n', encoding="utf-8")
                after = source_fingerprint()
            self.assertNotEqual(before, after)

    def test_module_packs_claim_registry_and_artifact_categories_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=2, include_p2=True)
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            modules = list((out / "model_input_pack" / "modules").glob("*/report_prompt.md"))
            registry = json.loads((out / "04_evidence" / "claim_registry.json").read_text())
            manifest = build_artifact_manifest(out, default_analysis_config())
            categories = {row["category"] for row in manifest["artifacts"]}
            self.assertEqual(len(modules), 10)
            self.assertEqual(registry["status"], "PASS")
            self.assertIn("REQUIRED_CORE", categories)
            self.assertIn("REQUIRED_FOR_PROFILE", categories)
            self.assertIn("OPTIONAL", categories)
            self.assertTrue((out / "04_evidence" / "claim_registry.csv").is_file())
            self.assertTrue((out / "artifact_manifest.csv").is_file())
            self.assertTrue((out / "01_normalized" / "request_parse_audit.csv").is_file())

    def test_claim_statement_change_supersedes_prior_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "04_evidence").mkdir()
            (bundle / "02_validation").mkdir()
            (bundle / "manifest").mkdir()
            write_json(bundle / "02_validation" / "analysis_ready.json", {"overall_status": "PASS", "module_status": {"inventory": "PASS"}})
            write_json(bundle / "manifest" / "experiment_manifest.json", {"experiment_start": "stable"})
            write_json(bundle / "04_evidence" / "verified_facts.json", [{"fact_id": "fact.same", "statement": "First statement", "conclusion_class": "OBSERVED_FACT"}])
            for name in ("statistical_associations", "mechanism_inferences", "unresolved_questions", "excluded_hypotheses"):
                write_json(bundle / "04_evidence" / f"{name}.json", [])
            build_claim_registry(bundle)
            write_json(bundle / "04_evidence" / "verified_facts.json", [{"fact_id": "fact.same", "statement": "Replacement statement", "conclusion_class": "OBSERVED_FACT"}])
            result = build_claim_registry(bundle)
            self.assertEqual(result["superseded_claim_count"], 1)
            self.assertTrue(any(row["status"] == "SUPERSEDED" for row in result["claims"]))

    def test_dry_run_writes_plan_without_reading_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            with redirect_stdout(io.StringIO()):
                rc = agentmon_main(
                    ["analysis", "run-all", "--input", str(Path(tmp) / "missing"), "--output", str(output), "--output-format", "csv", "--dry-run"]
                )
            self.assertEqual(rc, 0)
            self.assertTrue((output / "stage_execution_plan.json").is_file())
            self.assertFalse((output / "00_inventory" / "input_inventory_summary.json").exists())

    def test_to_stage_stops_real_execution_and_writes_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            result = run_all_analysis(
                input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv",
                to_stage="input_inventory", no_figures=True, log_level="ERROR",
            )
            self.assertEqual(result["reason"], "requested_terminal_stage_completed")
            self.assertEqual(result["terminal_stage"], "input_inventory")
            self.assertEqual(result["terminal_stage_status"], "PASS")
            self.assertFalse((out / "01_normalized" / "runs.csv.gz").exists())
            self.assertEqual(json.loads((out / "stages" / "discovery" / "stage-status.json").read_text())["status"], "SKIPPED")
            self.assertTrue((out / "stage_runtime_summary.csv").is_file())

    def test_failed_stage_invalidates_all_linear_downstream_cache_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            statuses = {stage: "PASS" for stage in STAGE_NAMES}
            statuses["lifecycle_analysis"] = "FAIL"
            write_json(out / "analysis_manifest.json", {"stage_completion_status": statuses})
            for stage, status in statuses.items():
                output_files: list[str] = []
                output_hashes: dict[str, str] = {}
                if stage not in {"discovery", "schema_resolution"}:
                    artifact = out / "checkpoint-fixtures" / f"{stage}.txt"
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text(stage, encoding="utf-8")
                    output_files = [str(artifact)]
                    output_hashes[str(artifact)] = hashlib.sha256(stage.encode("utf-8")).hexdigest()
                write_json(
                    out / "stages" / stage / "stage-status.json",
                    {"status": status, "output_files": output_files, "output_hashes": output_hashes},
                )
            plan = build_stage_execution_plan(out, resume=True)
            actions = {row["stage_name"]: row["action"] for row in plan["stages"]}
            self.assertEqual(actions["concurrency_analysis"], "CACHED")
            self.assertEqual(actions["lifecycle_analysis"], "RUN")
            self.assertEqual(actions["workload_analysis"], "RUN")
            self.assertEqual(actions["model_pack_build"], "RUN")

    def test_stage_cache_rejects_an_output_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            artifact = out / "artifact.csv"
            artifact.write_text("before\n", encoding="utf-8")
            write_json(
                out / "stages" / "throughput_analysis" / "stage-status.json",
                {
                    "status": "PASS",
                    "output_files": [str(artifact)],
                    "output_hashes": {str(artifact): hashlib.sha256(b"before\n").hexdigest()},
                },
            )
            self.assertTrue(stage_cache_artifacts_ready(out, "throughput_analysis"))
            artifact.write_text("after\n", encoding="utf-8")
            self.assertFalse(stage_cache_artifacts_ready(out, "throughput_analysis"))

    def test_profile_auto_uses_all_events_and_reports_ambiguity(self) -> None:
        cfg = default_analysis_config()
        late_signal_events = (
            event("run", index + 1, "host", "host.sample", {})
            for index in range(20)
        )
        events = list(late_signal_events)
        events.extend(
            [
                event("run", 30, "mini_swe", "model_request.start", {"model_call_id": "a"}),
                event("run", 31, "mini_swe", "model_request.end", {"model_call_id": "a"}),
            ]
        )
        profile, audit = resolve_profile(cfg, iter(events), run_id="run")
        self.assertEqual(profile.name, "agentmon")
        self.assertEqual(audit["status"], "PASS")
        profile, audit = resolve_profile(cfg, [event("x", 1, "host", "host.sample", {})], run_id="x")
        self.assertIsNone(profile)
        self.assertEqual(audit["status"], "AMBIGUOUS")

    def test_mixed_profile_input_can_be_explicitly_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=2, requests_per_run=1, include_p2=True)
            first_run_events = sorted(root.rglob("events.jsonl"))[0]
            rows = [json.loads(line) for line in first_run_events.read_text().splitlines()]
            first_run_events.write_text("".join(json.dumps(row) + "\n" for row in rows if not str(row.get("event_type")).startswith("vllm.")))
            cfg = default_analysis_config()
            cfg = replace(cfg, profile_detection=replace(cfg.profile_detection, mixed_schema_policy="reject"))
            result = run_all_analysis(input_root=root, output_root=out, cfg=cfg, output_format="csv", no_figures=True, log_level="ERROR")
            self.assertEqual(result["final_status"], "FAIL")
            self.assertIn("mixed schema profiles rejected", result["exception_message"])

    def test_allow_baseline_mismatch_continues_with_recorded_partial_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            cfg = replace(default_analysis_config(), baseline=BaselineConfig(expected_runs=2, allow_baseline_mismatch=True))
            result = run_all_analysis(input_root=root, output_root=out, cfg=cfg, output_format="csv", no_figures=True, log_level="ERROR")
            self.assertEqual(result["input_baseline_status"], "PARTIAL")
            self.assertTrue((out / "01_normalized" / "runs.csv.gz").is_file())

    def test_strict_cli_returns_nonzero_for_non_pass_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            with redirect_stdout(io.StringIO()):
                rc = agentmon_main([
                    "analysis", "run-all", "--input", str(root), "--output", str(out),
                    "--output-format", "csv", "--strict", "--no-figures",
                ])
            self.assertEqual(rc, 5)

    def test_analysis_does_not_modify_authoritative_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=2, include_p2=True)
            event_path = next(root.rglob("events.jsonl"))
            before = hashlib.sha256(event_path.read_bytes()).hexdigest()
            run_all_analysis(input_root=root, output_root=out, cfg=default_analysis_config(), output_format="csv", no_figures=True, log_level="ERROR")
            self.assertEqual(before, hashlib.sha256(event_path.read_bytes()).hexdigest())
            status = json.loads((out / "analysis_status.json").read_text())
            self.assertIn(status["RUN_STATUS"], {"COMPLETE", "FAIL"})
            self.assertEqual(status["CURRENT_PHASE"], "complete")
            self.assertIn("MEMORY_RSS_BYTES", status)
            self.assertIn("TEMP_DISK_FREE_BYTES", status)
            self.assertIn("BYTE_RATE_PER_SECOND", status)

    def test_configured_timeout_writes_checkpointed_failure_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=1, requests_per_run=1, include_p2=False)
            cfg = default_analysis_config()
            cfg = replace(cfg, runtime=replace(cfg.runtime, timeout_seconds=1e-12))
            result = run_all_analysis(
                input_root=root,
                output_root=out,
                cfg=cfg,
                output_format="csv",
                no_figures=True,
                log_level="ERROR",
            )
            diagnosis = json.loads((out / "analysis_failure_ledger.json").read_text())
            self.assertEqual(result["final_status"], "FAIL")
            self.assertEqual(diagnosis["failure_category"], "TIMEOUT")

    def test_process_pool_failure_is_audited_before_serial_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "out"
            write_request_experiment(root, run_count=2, requests_per_run=1, include_p2=False)
            cfg = default_analysis_config()
            cfg = replace(cfg, runtime=replace(cfg.runtime, jobs=2))
            with patch("agentmon.analysis.normalize.process_pool_launch_safe", return_value=True), patch(
                "agentmon.analysis.normalize.ProcessPoolExecutor",
                side_effect=RuntimeError("synthetic pool launch failure"),
            ):
                result = run_all_analysis(
                    input_root=root,
                    output_root=out,
                    cfg=cfg,
                    output_format="csv",
                    no_figures=True,
                    log_level="ERROR",
                )
            execution = json.loads((out / "01_normalized" / "normalization_execution.json").read_text())
            self.assertEqual(result["runs_parsed"], 2)
            self.assertEqual(execution["parallel_status"], "FALLBACK_SERIAL")
            self.assertEqual(execution["effective_jobs"], 1)
            self.assertIn("synthetic pool launch failure", execution["fallback_reason"])

    def test_engine_metrics_keep_windows_distinct_and_gate_token_budget(self) -> None:
        rows = []
        for second in range(0, 70, 10):
            rows.append(
                {
                    "trial_id": "t", "batch_id": "b", "configured_concurrency": 4,
                    "sample_time": second * 1_000_000_000,
                    "running_requests": 4, "waiting_requests": 2,
                    "scheduled_token_count": 90, "scheduled_token_budget": 100,
                }
            )
        result = analyze_scheduler({"scheduler_iterations": rows}, default_analysis_config())
        summary = result["scheduler_summary"][0]
        state = result["engine_state_classification"][0]
        self.assertEqual(summary["whole_workload_running_tw_mean"], 4.0)
        self.assertIsNotNone(summary["steady_state_running_tw_mean"])
        self.assertEqual(state["state"], "TOKEN_BUDGET_CANDIDATE")
        self.assertTrue(state["token_budget_evidence"])

    def test_long_context_state_requires_configured_threshold_and_sustained_waiting(self) -> None:
        rows = [
            {
                "trial_id": "t",
                "batch_id": "b",
                "configured_concurrency": 2,
                "sample_time": second * 1_000_000_000,
                "running_requests": 2,
                "waiting_requests": 1,
                "active_prompt_tokens": 200,
            }
            for second in range(0, 70, 10)
        ]
        cfg = default_analysis_config()
        cfg = replace(
            cfg,
            engine_metrics=replace(cfg.engine_metrics, long_context_signal_threshold=100),
        )
        state = analyze_scheduler({"scheduler_iterations": rows}, cfg)["engine_state_classification"][0]
        self.assertEqual(state["state"], "LONG_CONTEXT_PRESSURE_CANDIDATE")
        self.assertTrue(state["long_context_evidence"])

    def test_engine_sensitivity_time_slice_changes_the_computation(self) -> None:
        rows = [
            {
                "trial_id": "t",
                "batch_id": "b",
                "configured_concurrency": 2,
                "sample_time": second * 1_000_000_000,
                "running_requests": 2,
                "waiting_requests": 1 if second % 5 == 0 else 0,
            }
            for second in range(61)
        ]
        sensitivity = analyze_scheduler({"scheduler_iterations": rows}, default_analysis_config())["scheduler_threshold_sensitivity"]
        selected = {
            row["time_slice_s"]: row["qualifying_duration_s"]
            for row in sensitivity
            if row["waiting_threshold"] == 0.0
            and row["plateau_fraction"] == 0.10
            and row["minimum_duration_s"] == 10.0
        }
        self.assertGreater(selected[1.0], selected[30.0])


def write_request_experiment(root: Path, *, run_count: int, requests_per_run: int, include_p2: bool) -> None:
    batch = root / "trial" / "batch"
    batch.mkdir(parents=True)
    (batch / "batch-manifest.json").write_text(json.dumps({"trial_id": "trial", "task_count": run_count}) + "\n")
    results = ["task_id\trun_id\trun_dir\tworker_id\texecution_success"]
    for run_index in range(run_count):
        run_id = f"run-{run_index}"
        run = batch / run_id
        run.mkdir()
        (run / "manifest.json").write_text(json.dumps({"run_id": run_id, "status": "success", "duration_ns": 10_000_000_000}) + "\n")
        events = []
        sequence = 0
        for request_index in range(requests_per_run):
            request_id = f"request-{request_index}"
            span_id = f"span-{request_index}"
            sequence += 1
            events.append(event(run_id, sequence, "mini_swe", "model_request.start", {"span_id": span_id, "span_type": "model_request", "model_call_id": request_id}))
            sequence += 1
            events.append(event(run_id, sequence, "mini_swe", "model_request.end", {"span_id": span_id, "span_type": "model_request", "model_call_id": request_id, "status": "success", "usage": {"prompt_tokens": 2, "completion_tokens": 2}}))
            if include_p2:
                fields = (
                    ("received", "received_ns"), ("queued", "queued_ns"), ("first_scheduled", "first_scheduled_ns"),
                    ("first_token", "first_token_ns"), ("engine_finished", "engine_finished_ns"), ("finished", "finished_ns"),
                )
                for event_name, timestamp_field in fields:
                    sequence += 1
                    payload = {
                        "agentmon_run_id": run_id,
                        "agentmon_model_request_id": request_id,
                        "api_request_id": request_id,
                        "vllm_request_id": f"{run_id}-engine-{request_index}",
                        "generation_tokens": 2,
                        timestamp_field: sequence * 1_000_000,
                    }
                    events.append(event(run_id, sequence, "vllm_p2", f"vllm.request.{event_name}", payload))
        (run / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
        results.append(f"task-{run_index}\t{run_id}\t{run}\tw{run_index}\ttrue")
    (batch / "batch-results.tsv").write_text("\n".join(results) + "\n")


def event(run_id: str, sequence: int, source: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": f"{run_id}-{sequence}",
        "run_id": run_id,
        "source": source,
        "event_type": event_type,
        "wall_time_ns": sequence,
        "monotonic_time_ns": sequence * 1_000_000,
        "payload": payload,
    }


if __name__ == "__main__":
    unittest.main()
