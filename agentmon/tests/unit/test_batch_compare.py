from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from agentmon.analysis.batch_compare import compare_batch_steps


class BatchCompareTests(unittest.TestCase):
    def test_pairs_same_task_step_and_reports_metric_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "c1"
            target = root / "c5"
            write_batch(baseline, e2e=2.0, task_ids=["task-1"])
            write_batch(target, e2e=3.5, task_ids=["task-1"])

            comparison = compare_batch_steps(baseline, target, pair_by="task,step")

        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["pair_count"], 1)
        pair = comparison["paired_rows"][0]
        self.assertEqual(pair["pair_status"], "paired")
        self.assertAlmostEqual(pair["p2_e2e_s_delta"], 1.5)
        self.assertEqual(comparison["metric_delta_statistics"]["p2_e2e_s"]["count"], 1)

    def test_task_set_mismatch_blocks_comparability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "c1"
            target = root / "c5"
            write_batch(baseline, e2e=2.0, task_ids=["task-1"])
            write_batch(target, e2e=2.0, task_ids=["task-2"])

            comparison = compare_batch_steps(baseline, target)

        self.assertFalse(comparison["comparable"])
        check = next(row for row in comparison["checks"] if row["name"] == "task_id_set")
        self.assertEqual(check["status"], "error")


def write_batch(root: Path, *, e2e: float, task_ids: list[str]) -> None:
    (root / "step-sequence").mkdir(parents=True)
    comparability = {
        "task_file_sha256": "tasks",
        "task_package_sha256": "package",
        "agentmon_source_sha256": "agentmon",
        "runner_sha256": "runner",
        "vllm_version": "0.18.0",
        "vllm_ascend_version": "0.18.0",
        "cann_version": "9.0.0",
        "model_config_sha256": "model",
        "p2_filter_mode": "run-id",
    }
    (root / "batch-manifest.json").write_text(
        json.dumps({"task_ids": task_ids, "comparability": comparability}),
        encoding="utf-8",
    )
    with (root / "batch-results.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["task_id", "run_id"], delimiter="\t")
        writer.writeheader()
        for task_id in task_ids:
            writer.writerow({"task_id": task_id, "run_id": f"run-{task_id}"})
    with (root / "step-sequence" / "all-steps.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "run_id",
            "item_type",
            "step_type",
            "step_index",
            "model_call_index_in_step",
            "model_call_id",
            "p2_e2e_s",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for task_id in task_ids:
            writer.writerow(
                {
                    "run_id": f"run-{task_id}",
                    "item_type": "model",
                    "step_type": "agent_step",
                    "step_index": 1,
                    "model_call_index_in_step": 1,
                    "model_call_id": f"model-{task_id}",
                    "p2_e2e_s": e2e,
                }
            )


if __name__ == "__main__":
    unittest.main()
