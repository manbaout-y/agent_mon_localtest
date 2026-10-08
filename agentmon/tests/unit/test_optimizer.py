import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main


class OptimizerCliTests(unittest.TestCase):
    def test_optimizer_decision_reports_available_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_optimizer_config(Path(tmp), max_running_runs=4)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["optimizer", "decisions", "--config", str(config)])

            decision = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(decision["result_type"], "agentmon.optimizer.decision")
            self.assertEqual(decision["admission_pressure"], "none")
            self.assertEqual(decision["reason"], "scheduler capacity is available")
            self.assertEqual(decision["target_concurrency"]["runtime.default"], 4)

    def test_optimizer_decision_reports_unavailable_scheduler_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_optimizer_config(Path(tmp), max_running_runs=0)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["optimizer", "decisions", "--config", str(config)])

            decision = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(decision["admission_pressure"], "high")
            self.assertIn("scheduler_capacity_unavailable", decision["risk_flags"])

    def test_optimizer_decision_reports_near_capacity_from_run_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            run_dir = runs / "run-active"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-active",
                        "runtime_backend": "process",
                        "runtime_task_id": "task-active",
                        "runtime_state": "running",
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )
            config = write_optimizer_config(root, max_running_runs=2, high_utilization_ratio=0.5)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["optimizer", "decisions", "--config", str(config)])

            decision = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(decision["admission_pressure"], "moderate")
            self.assertIn("scheduler_capacity_near_limit", decision["risk_flags"])
            self.assertEqual(decision["queue"]["running_count"], 1)

    def test_optimizer_decision_uses_model_service_snapshot_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_optimizer_config(root, max_running_runs=4)
            snapshot = root / "model-service.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "service_id": "local-vllm",
                        "provider": "vllm",
                        "running_requests": 1,
                        "waiting_requests": 3,
                        "queue_latency_p95_s": 3.5,
                        "kv_usage_ratio": 0.93,
                        "quality": "fixture",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "optimizer",
                        "decisions",
                        "--config",
                        str(config),
                        "--model-service-snapshot",
                        str(snapshot),
                    ]
                )

            decision = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(decision["admission_pressure"], "high")
            self.assertIn("model_waiting_requests_high", decision["risk_flags"])
            self.assertIn("model_queue_latency_high", decision["risk_flags"])
            self.assertIn("model_kv_usage_high", decision["risk_flags"])
            self.assertEqual(decision["model_service"]["service_id"], "local-vllm")
            audit = root / "runs" / "_runtime" / "events.jsonl"
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertIn("optimizer.decision", {row["event_type"] for row in rows})


def write_optimizer_config(
    root: Path,
    *,
    max_running_runs: int,
    high_utilization_ratio: float = 0.8,
) -> Path:
    config = root / "agentmon-test.toml"
    runs = root / "runs"
    config.write_text(
        f"""
[general]
output_root = "{runs}"

[vllm]
metrics_url = "http://127.0.0.1:1/metrics"
api_base = "http://127.0.0.1:1/v1"
models_url = "http://127.0.0.1:1/v1/models"
request_timeout_seconds = 0.05

[ascend]
npu_smi = "{root / "missing-npu-smi"}"
command_timeout_seconds = 0.05

[scheduler]
max_running_runs = {max_running_runs}

[optimizer]
strategy = "static_threshold"
mode = "advise"
high_utilization_ratio = {high_utilization_ratio}
""".strip(),
        encoding="utf-8",
    )
    return config
