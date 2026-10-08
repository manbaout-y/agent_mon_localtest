from __future__ import annotations

import csv
from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentmon.cli import main
from agentmon.reporting.timeline import generate_batch_timeline


class TimelineTests(unittest.TestCase):
    def test_generates_batch_timeline_and_integral_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch = make_batch(Path(tmp))

            summary = generate_batch_timeline(batch)

            self.assertEqual(summary["publishability_status"], "PUBLISHABLE")
            self.assertEqual(summary["run_count"], 2)
            self.assertEqual(summary["model_interval_count"], 2)
            self.assertEqual(summary["tool_interval_count"], 2)
            output = batch / "timeline"
            for name in (
                "run_timeline.csv",
                "service_resource_windows.csv",
                "batch_timeline_1s.csv",
                "batch_timeline_5s.csv",
                "phase_summary.csv",
                "tail_summary.csv",
                "transition_gap.csv",
                "concurrency_integral_check.csv",
                "timeline_report.md",
                "validation_checks.json",
            ):
                self.assertTrue((output / name).is_file(), name)
            checks = read_csv(output / "concurrency_integral_check.csv")
            self.assertTrue(all(row["passed"] == "True" for row in checks))
            self.assertTrue(all(float(row["relative_error"]) < 0.01 for row in checks))
            resources = read_csv(output / "service_resource_windows.csv")
            self.assertEqual(len(resources), 2)
            self.assertTrue(all(row["observation_scope"] == "shared_service_observation" for row in resources))
            self.assertTrue(all(row["request_attribution"] == "not_request_attributed" for row in resources))

    def test_strict_cli_fails_when_clock_domains_are_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch = make_batch(Path(tmp), mixed_clock=True)

            with redirect_stdout(io.StringIO()):
                rc = main(["timeline", str(batch), "--strict", "--format", "json"])

            self.assertEqual(rc, 1)
            validation = json.loads((batch / "timeline" / "validation_checks.json").read_text(encoding="utf-8"))
            self.assertIn("clock_domain_consistent", validation["blocking_failures"])


def make_batch(root: Path, *, mixed_clock: bool = False) -> Path:
    batch = root / "batch"
    step_dir = batch / "step-sequence"
    step_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "3.0",
        "task_count": 2,
        "concurrency": 2,
        "start_mono_ns": ns(0),
        "end_mono_ns": ns(7),
        "all_steps_csv": str(step_dir / "all-steps.csv"),
        "phase_timings": {
            "preprocessing_s": 0.1,
            "workload_phase_s": 6.0,
            "p2_index_s": 0.1,
            "analysis_phase_s": 1.0,
            "finalization_s": 0.2,
            "postprocessing_s": 1.3,
            "pipeline_wall_s": 7.3,
        },
    }
    (batch / "batch-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (batch / "batch-summary.json").write_text(
        json.dumps({"stage_counts": {"usable_run": 2}}),
        encoding="utf-8",
    )
    write_rows(
        batch / "batch-results.tsv",
        [
            {
                "task_id": "task-a",
                "run_id": "run-a",
                "status": "success",
                "execution_success": True,
                "analysis_pipeline_success": True,
                "workload_start_mono_ns": ns(1),
                "workload_end_mono_ns": ns(5),
            },
            {
                "task_id": "task-b",
                "run_id": "run-b",
                "status": "success",
                "execution_success": True,
                "analysis_pipeline_success": True,
                "workload_start_mono_ns": ns(2),
                "workload_end_mono_ns": ns(6),
            },
        ],
        delimiter="\t",
    )
    write_rows(
        step_dir / "all-steps.csv",
        [
            item("run-a", "model", "model-a", 1.2, 3.2, "clock-a"),
            item("run-a", "tool", "tool-a", 3.3, 4.0, "clock-a"),
            item("run-b", "model", "model-b", 2.2, 4.8, "clock-b" if mixed_clock else "clock-a"),
            item("run-b", "tool", "tool-b", 4.9, 5.5, "clock-a"),
        ],
    )
    return batch


def item(run_id: str, item_type: str, span_id: str, start: float, end: float, clock: str) -> dict:
    return {
        "run_id": run_id,
        "step_id": f"step-{run_id}",
        "step_index": 1,
        "item_type": item_type,
        "span_id": span_id,
        "status": "ok",
        "item_start_mono_ns": ns(start),
        "item_end_mono_ns": ns(end),
        "step_start_mono_ns": ns(start),
        "step_end_mono_ns": ns(end),
        "clock_domain_id": clock,
        "host_boot_id": "boot-a",
        "step_npu_observation_scope": "shared_service_observation",
        "step_npu_request_attribution": "not_request_attributed",
        "step_target_peak_hbm_used_bytes": 1000,
        "step_target_avg_hbm_used_bytes": 900,
        "step_target_max_aicore_util_pct": 80,
        "step_target_avg_aicore_util_pct": 60,
        "step_npu_device_metrics_json": '{"0":{"peak_hbm_used_bytes":1000}}',
        "step_scheduler_running_requests_max": 2,
        "step_scheduler_waiting_requests_max": 1,
    }


def write_rows(path: Path, rows: list[dict], *, delimiter: str = ",") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
