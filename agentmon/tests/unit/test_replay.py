import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main


class ReplayCliTests(unittest.TestCase):
    def test_optimizer_replay_uses_snapshot_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_replay_config(root)
            snapshot = root / "scheduler-snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "result_type": "agentmon.scheduler.snapshot",
                        "strategy": "fixed_concurrency",
                        "enabled": True,
                        "max_running_runs": 2,
                        "running_count": 2,
                        "state_counts": {"running": 2},
                        "runs": [
                            {
                                "run_id": "run-a",
                                "task_id": "task-a",
                                "state": "running",
                                "backend": "process",
                            },
                            {
                                "run_id": "run-b",
                                "task_id": "task-b",
                                "state": "running",
                                "backend": "process",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "optimizer",
                        "replay",
                        "--config",
                        str(config),
                        "--snapshot",
                        str(snapshot),
                    ]
                )

            result = json.loads(stdout.getvalue())
            decision = result["optimizer_decision"]
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.replay.optimizer_decision")
            self.assertEqual(decision["admission_pressure"], "high")
            self.assertIn("scheduler_capacity_full", decision["risk_flags"])
            self.assertEqual(decision["queue"]["running_count"], 2)

    def test_runtime_replay_events_reconstructs_queue_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_replay_config(root)
            events = root / "runtime-events.jsonl"
            rows = [
                runtime_event("runtime.queue.enqueued", {"queue_id": "q-1", "task_id": "task-1"}),
                runtime_event(
                    "runtime.queue.running",
                    {"queue_id": "q-1", "task_id": "task-1", "lease_owner": "daemon-a"},
                ),
                runtime_event("runtime.queue.heartbeat", {"queue_id": "q-1", "task_id": "task-1"}),
                runtime_event("runtime.queue.finished", {"queue_id": "q-1", "task_id": "task-1", "state": "succeeded"}),
                runtime_event("runtime.daemon.pass_completed", {"processed_count": 1, "started_count": 1}),
                runtime_event("optimizer.decision", {"admission_pressure": "high"}, source="optimizer"),
                runtime_event("control.model_gate.acquire", {"admitted": False}, source="control"),
            ]
            events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "replay-events",
                        "--config",
                        str(config),
                        "--events",
                        str(events),
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.replay.runtime_events")
            self.assertEqual(result["event_count"], 7)
            self.assertEqual(result["queue_state_by_id"]["q-1"]["state"], "succeeded")
            self.assertEqual(result["event_type_counts"]["runtime.queue.heartbeat"], 1)
            self.assertEqual(result["source_counts"]["optimizer"], 1)
            self.assertEqual(result["source_counts"]["control"], 1)
            self.assertEqual(result["daemon_passes"][0]["started_count"], 1)
            self.assertEqual(result["optimizer_decisions"][0]["admission_pressure"], "high")
            self.assertEqual(result["control_events"][0]["event_type"], "control.model_gate.acquire")


def runtime_event(event_type: str, payload: dict[str, object], *, source: str = "runtime") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_type,
        "source": source,
        "event_type": event_type,
        "wall_time_ns": 1,
        "quality": "exact",
        "payload": payload,
    }


def write_replay_config(root: Path) -> Path:
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
max_running_runs = 4

[optimizer]
strategy = "static_threshold"
mode = "advise"
""".strip(),
        encoding="utf-8",
    )
    return config
