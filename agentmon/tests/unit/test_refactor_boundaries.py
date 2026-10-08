from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentmon.analysis.execution import STAGE_NAMES as EXECUTION_STAGE_NAMES
from agentmon.analysis.execution import AnalysisRunContext
from agentmon.analysis import existing_data
from agentmon.analysis import step_csv
from agentmon.analysis.stages import STAGE_NAMES, stage_dag_edges, stage_dag_nodes, stage_spec
from agentmon.batch.policy import should_build_combined_step_csv, should_build_timeline
from agentmon.batch.runtime import build_parser
from agentmon.reporting import event_log
from agentmon.reporting import report
from agentmon.server import preflight
from agentmon.step_sequence import core as step_core
from agentmon.step_sequence import io as step_io


class RefactorBoundaryTests(unittest.TestCase):
    def test_lightweight_analysis_import_does_not_load_pipeline_or_orchestration(self) -> None:
        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import agentmon.analysis.step_csv; "
                    "assert 'agentmon.analysis.pipeline' not in sys.modules; "
                    "assert 'agentmon.experiments.orchestration' not in sys.modules"
                ),
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_experiment_evidence_import_does_not_load_orchestration(self) -> None:
        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import agentmon.experiments.evidence; "
                    "assert 'agentmon.experiments.orchestration' not in sys.modules"
                ),
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_main_cli_parser_does_not_load_optimizer_harness_or_orchestration(self) -> None:
        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from agentmon.cli import build_parser; build_parser(); "
                    "assert 'agentmon.optimizer.harness.runner' not in sys.modules; "
                    "assert 'agentmon.experiments.orchestration' not in sys.modules"
                ),
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_analysis_stage_catalog_is_execution_source_of_truth(self) -> None:
        self.assertEqual(EXECUTION_STAGE_NAMES, STAGE_NAMES)
        self.assertFalse(stage_spec("final_summary").cacheable)
        self.assertTrue(stage_spec("normalize_runs").cacheable)
        self.assertEqual(stage_dag_edges()[0], {"from": "input_inventory", "to": "baseline_check"})
        self.assertIn(
            {"from": "normalize_requests", "to": "validation"},
            stage_dag_edges(),
        )
        self.assertEqual(stage_dag_nodes()[-1]["stage_name"], "final_summary")

    def test_analysis_compatibility_facade_emits_shared_pipeline_result_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            context = AnalysisRunContext(bundle_root=bundle, progress_interval_seconds=60)
            try:
                context.start_stage("input_inventory")
                context.finish_stage("input_inventory", status="PASS", row_counts={"files": 1})
                payload = json.loads((bundle / "pipeline_manifest.json").read_text(encoding="utf-8"))
                dag = json.loads((bundle / "pipeline_dag.json").read_text(encoding="utf-8"))
            finally:
                context.complete("PARTIAL")

        stage = next(row for row in payload["stages"] if row["stage_name"] == "input_inventory")
        self.assertEqual(payload["pipeline_kind"], "analysis_compatibility_facade")
        self.assertEqual(stage["outcome"], "COMPLETE")
        self.assertEqual(stage["row_counts"], {"files": 1})
        pending = next(row for row in payload["stages"] if row["stage_name"] == "final_summary")
        self.assertEqual(pending["reason_codes"], ["legacy_stage_not_started"])
        self.assertEqual(dag["pipeline_kind"], "analysis_compatibility_facade")

    def test_batch_policy_module_matches_runtime_flags(self) -> None:
        parser = build_parser()
        quick = parser.parse_args(
            [
                "--batch-root",
                "/tmp/batch",
                "--task-file",
                "/tmp/tasks.tsv",
                "--command-template",
                "true",
                "--analysis-preset",
                "quick",
            ]
        )
        full = parser.parse_args(
            [
                "--batch-root",
                "/tmp/batch",
                "--task-file",
                "/tmp/tasks.tsv",
                "--command-template",
                "true",
            ]
        )

        self.assertFalse(should_build_combined_step_csv(quick))
        self.assertFalse(should_build_timeline(quick))
        self.assertTrue(should_build_combined_step_csv(full))
        self.assertTrue(should_build_timeline(full))

    def test_reporting_event_log_module_is_public_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "events.jsonl").write_text('{"event_id":"e1"}\nnot-json\n', encoding="utf-8")

            direct = event_log.read_event_log(run_dir)
            via_facade = report.read_event_log(run_dir)

        self.assertEqual(direct["valid_lines"], 1)
        self.assertEqual(direct["invalid_lines"], 1)
        self.assertEqual(via_facade["valid_lines"], 1)

    def test_step_sequence_io_module_feeds_core_without_script_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            run_dir.mkdir(exist_ok=True)
            (run_dir / "events.jsonl").write_text("", encoding="utf-8")

            event_log = step_io.read_jsonl(run_dir / "events.jsonl")
            analysis = step_core.build_step_sequence(run_dir, run_event_log=event_log)

        self.assertEqual(event_log["events"], [])
        self.assertEqual(analysis["analysis_type"], "agentmon.step_sequence")

    def test_script_implementations_live_under_src_packages(self) -> None:
        self.assertIn("--batch", existing_data.build_parser().format_help())
        self.assertIn("csv_path", step_csv.build_parser().format_help())
        self.assertIn("--agentmon-repo", preflight.build_parser().format_help())

        root = Path(__file__).resolve().parents[2]
        for script in (
            root / "scripts" / "agentmon_existing_data_analysis.py",
            root / "scripts" / "agentmon_analyze_steps.py",
            root / "scripts" / "agentmon_server_preflight.py",
        ):
            line_count = len(script.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(line_count, 80, script)


if __name__ == "__main__":
    unittest.main()
