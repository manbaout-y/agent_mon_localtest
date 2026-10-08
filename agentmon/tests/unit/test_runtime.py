import io
import json
import multiprocessing
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.config import load_config
from agentmon.runtime import ResourceBudget, RunSpec
from agentmon.runtime.queue import RuntimeQueueStore


class RuntimeCliTests(unittest.TestCase):
    def test_runtime_reexports_contract_budget_type(self) -> None:
        from agentmon.contracts import ResourceBudget as ContractResourceBudget

        self.assertIs(ResourceBudget, ContractResourceBudget)

    def test_runtime_capabilities_reports_process_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_fast_config(Path(tmp))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["runtime", "capabilities", "--config", str(config)])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertIn("process", payload)
            self.assertTrue(payload["process"]["start"])
            self.assertFalse(payload["process"]["pause"])

    def test_runtime_submit_process_backend_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "submit",
                        "--config",
                        str(config),
                        "--task-id",
                        "task-runtime",
                        "--label",
                        "runtime-smoke",
                        "--",
                        sys.executable,
                        "-c",
                        "import sys; sys.exit(0)",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.runtime.run_result")
            self.assertEqual(result["handle"]["backend"], "process")
            self.assertEqual(result["status"]["state"], "succeeded")

            run_dir = Path(result["handle"]["run_dir"])
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["runtime_backend"], "process")
            self.assertEqual(manifest["runtime_task_id"], "task-runtime")
            self.assertEqual(manifest["runtime_state"], "succeeded")
            self.assertEqual(manifest["runtime_spec"]["task_id"], "task-runtime")
            self.assertEqual(manifest["label"], "runtime-smoke")

            status_stdout = io.StringIO()
            with redirect_stdout(status_stdout):
                status_code = agentmon_main(
                    [
                        "runtime",
                        "status",
                        "--config",
                        str(config),
                        result["handle"]["run_id"],
                    ]
                )
            status = json.loads(status_stdout.getvalue())
            self.assertEqual(status_code, 0)
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["task_id"], "task-runtime")

            queue_stdout = io.StringIO()
            with redirect_stdout(queue_stdout):
                queue_code = agentmon_main(["runtime", "queue", "--config", str(config)])
            queue = json.loads(queue_stdout.getvalue())
            self.assertEqual(queue_code, 0)
            self.assertEqual(queue["run_count"], 1)
            self.assertEqual(queue["state_counts"]["succeeded"], 1)
            self.assertEqual(queue["scheduler"]["max_running_runs"], 64)

    def test_runtime_submit_defers_when_scheduler_capacity_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root, max_running_runs=0)
            marker = root / "should-not-exist.txt"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "submit",
                        "--config",
                        str(config),
                        "--task-id",
                        "task-deferred",
                        "--",
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 75)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["result_type"], "agentmon.runtime.submit_deferred")
            self.assertEqual(result["scheduler_decision"]["action"], "defer")
            self.assertEqual(result["scheduler_decision"]["reason"], "max_running_runs_reached")
            self.assertFalse(marker.exists())

    def test_runtime_submit_queue_and_daemon_executes_later(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root)
            marker = root / "daemon-ran.txt"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "submit",
                        "--queue",
                        "--config",
                        str(config),
                        "--task-id",
                        "task-queued",
                        "--",
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ]
                )

            queued = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(queued["result_type"], "agentmon.runtime.enqueue_result")
            self.assertEqual(queued["state"], "queued")
            self.assertFalse(marker.exists())

            status_stdout = io.StringIO()
            with redirect_stdout(status_stdout):
                status_code = agentmon_main(
                    [
                        "runtime",
                        "status",
                        "--config",
                        str(config),
                        queued["queue_id"],
                    ]
                )
            queued_status = json.loads(status_stdout.getvalue())
            self.assertEqual(status_code, 0)
            self.assertEqual(queued_status["result_type"], "agentmon.runtime.queued_status")
            self.assertEqual(queued_status["state"], "queued")
            self.assertEqual(queued_status["task_id"], "task-queued")

            queue_stdout = io.StringIO()
            with redirect_stdout(queue_stdout):
                queue_code = agentmon_main(["runtime", "queue", "--config", str(config)])
            queue = json.loads(queue_stdout.getvalue())
            self.assertEqual(queue_code, 0)
            self.assertEqual(queue["persistent_queue"]["pending_count"], 1)
            self.assertEqual(queue["persistent_queue"]["records"][0]["task_id"], "task-queued")

            daemon_stdout = io.StringIO()
            with redirect_stdout(daemon_stdout):
                daemon_code = agentmon_main(["runtime", "daemon", "--once", "--config", str(config)])
            daemon = json.loads(daemon_stdout.getvalue())
            self.assertEqual(daemon_code, 0)
            self.assertEqual(daemon["result_type"], "agentmon.runtime.daemon_result")
            self.assertEqual(daemon["processed_count"], 1)
            self.assertEqual(daemon["started_count"], 1)
            self.assertTrue(marker.exists())

            after_stdout = io.StringIO()
            with redirect_stdout(after_stdout):
                after_code = agentmon_main(["runtime", "queue", "--config", str(config)])
            after = json.loads(after_stdout.getvalue())
            self.assertEqual(after_code, 0)
            self.assertEqual(after["persistent_queue"]["pending_count"], 0)
            self.assertEqual(after["persistent_queue"]["state_counts"]["succeeded"], 1)
            audit_events = [
                json.loads(line)
                for line in Path(after["audit_log"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            audit_types = {row["event_type"] for row in audit_events}
            self.assertIn("runtime.queue.enqueued", audit_types)
            self.assertIn("runtime.queue.finished", audit_types)
            self.assertIn("runtime.daemon.pass_completed", audit_types)

    def test_runtime_daemon_defers_persisted_queue_when_capacity_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root, max_running_runs=0)
            marker = root / "should-not-run.txt"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                submit_code = agentmon_main(
                    [
                        "runtime",
                        "submit",
                        "--queue",
                        "--config",
                        str(config),
                        "--task-id",
                        "task-still-deferred",
                        "--",
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                    ]
                )
            self.assertEqual(submit_code, 0)

            daemon_stdout = io.StringIO()
            with redirect_stdout(daemon_stdout):
                daemon_code = agentmon_main(["runtime", "daemon", "--once", "--config", str(config)])

            daemon = json.loads(daemon_stdout.getvalue())
            self.assertEqual(daemon_code, 0)
            self.assertEqual(daemon["processed_count"], 1)
            self.assertEqual(daemon["deferred_count"], 1)
            self.assertFalse(marker.exists())

            queue_stdout = io.StringIO()
            with redirect_stdout(queue_stdout):
                queue_code = agentmon_main(["runtime", "queue", "--config", str(config)])
            queue = json.loads(queue_stdout.getvalue())
            self.assertEqual(queue_code, 0)
            self.assertEqual(queue["persistent_queue"]["state_counts"]["deferred"], 1)

    def test_runtime_daemon_loop_exits_after_idle_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_fast_config(Path(tmp))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "daemon",
                        "--loop",
                        "--max-idle-cycles",
                        "1",
                        "--idle-sleep",
                        "0",
                        "--config",
                        str(config),
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.runtime.daemon_loop_result")
            self.assertEqual(result["pass_count"], 1)
            self.assertEqual(result["processed_count"], 0)

    def test_runtime_queue_heartbeat_extends_running_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(config_path=write_fast_config(Path(tmp)))
            store = RuntimeQueueStore(cfg)
            queued = store.enqueue(RunSpec(task_id="task-heartbeat", backend="process", command=("true",)))
            record = store.get(queued.queue_id)
            self.assertIsNotNone(record)
            running = store.claim_running(
                record,
                {"action": "admit", "strategy": "test"},
                owner="owner-a",
                lease_seconds=-1,
            )
            self.assertIsNotNone(running)
            expired_lease = running.lease_expires_wall_ns

            updated = store.heartbeat(queued.queue_id, owner="owner-a", lease_seconds=30)

            self.assertIsNotNone(updated)
            self.assertGreater(updated.lease_expires_wall_ns, expired_lease)
            self.assertEqual(updated.lease_owner, "owner-a")

    def test_runtime_recover_defers_expired_running_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root)
            cfg = load_config(config_path=config)
            store = RuntimeQueueStore(cfg)
            queued = store.enqueue(RunSpec(task_id="task-orphan", backend="process", command=("true",)))
            record = store.get(queued.queue_id)
            self.assertIsNotNone(record)
            running = store.claim_running(
                record,
                {"action": "admit", "strategy": "test"},
                owner="dead-owner",
                lease_seconds=-1,
            )
            self.assertIsNotNone(running)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "recover",
                        "--config",
                        str(config),
                        "--stale-seconds",
                        "0",
                        "--action",
                        "defer",
                    ]
                )

            result = json.loads(stdout.getvalue())
            recovered = store.get(queued.queue_id)
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.runtime.recovery_result")
            self.assertEqual(result["recovered_count"], 1)
            self.assertEqual(recovered.state, "deferred")
            self.assertIsNone(recovered.lease_owner)

    def test_runtime_queue_claim_allows_only_one_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_fast_config(root)
            cfg = load_config(config_path=config)
            store = RuntimeQueueStore(cfg)
            queued = store.enqueue(RunSpec(task_id="task-race", backend="process", command=("true",)))
            ctx = multiprocessing.get_context("spawn")
            results = ctx.Queue()
            processes = [
                ctx.Process(
                    target=claim_queue_record_worker,
                    args=(str(config), queued.queue_id, f"owner-{index}", results),
                )
                for index in range(2)
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)

            self.assertTrue(all(process.exitcode == 0 for process in processes))
            claimed = [results.get(timeout=2) for _ in processes]
            self.assertEqual(sum(1 for item in claimed if item["claimed"]), 1)
            record = store.get(queued.queue_id)
            self.assertEqual(record.state, "running")
            self.assertIn(record.lease_owner, {"owner-0", "owner-1"})


def write_fast_config(root: Path, *, max_running_runs: int | None = None) -> Path:
    config = root / "agentmon-test.toml"
    missing_npu = root / "missing-npu-smi"
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
npu_smi = "{missing_npu}"
command_timeout_seconds = 0.05

[scheduler]
max_running_runs = {64 if max_running_runs is None else max_running_runs}
""".strip(),
        encoding="utf-8",
    )
    return config


def claim_queue_record_worker(config_path: str, queue_id: str, owner: str, results) -> None:
    cfg = load_config(config_path=config_path)
    store = RuntimeQueueStore(cfg)
    record = store.get(queue_id)
    claimed = False
    if record is not None:
        claimed = store.claim_running(
            record,
            {"action": "admit", "strategy": "worker-test"},
            owner=owner,
            lease_seconds=30,
        ) is not None
    results.put({"owner": owner, "claimed": claimed})
