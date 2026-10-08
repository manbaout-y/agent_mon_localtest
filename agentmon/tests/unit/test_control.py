import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
import threading
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.config import load_config
from agentmon.control import GatedOpenAICompatibleProvider


class ControlCliTests(unittest.TestCase):
    def test_model_gate_status_reflects_optimizer_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(Path(tmp), max_running_runs=0)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["control", "model-gate", "status", "--config", str(config)])

            status = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(status["result_type"], "agentmon.control.model_gate_status")
            self.assertEqual(status["pressure"], "high")
            self.assertEqual(status["target_inflight_requests"], 2)
            self.assertEqual(status["max_inflight_requests"], 12)

    def test_tool_pool_status_reports_static_pools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(Path(tmp), max_running_runs=4)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(["control", "tool-pool", "status", "--config", str(config)])

            status = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(status["result_type"], "agentmon.control.tool_pool_status")
            pools = {row["pool_id"]: row for row in status["pools"]}
            self.assertEqual(pools["default"]["max_concurrent"], 8)
            self.assertEqual(pools["cpu_heavy"]["max_concurrent"], 3)
            self.assertEqual(pools["io_heavy"]["available_slots"], 2)

    def test_tool_pool_classify_assigns_cpu_heavy_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(Path(tmp), max_running_runs=4)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "classify",
                        "--config",
                        str(config),
                        "--",
                        "pytest",
                        "-q",
                    ]
                )

            assignment = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(assignment["result_type"], "agentmon.control.tool_pool_assignment")
            self.assertEqual(assignment["pool_id"], "cpu_heavy")
            self.assertEqual(assignment["executable"], "pytest")
            self.assertEqual(assignment["max_concurrent"], 3)

    def test_tool_pool_classify_assigns_io_heavy_shell_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(Path(tmp), max_running_runs=4)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "classify",
                        "--config",
                        str(config),
                        "--",
                        "bash",
                        "-lc",
                        "tar -xf archive.tar",
                    ]
                )

            assignment = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(assignment["pool_id"], "io_heavy")
            self.assertEqual(assignment["executable"], "tar")

    def test_model_gate_acquire_denies_in_enforce_mode_when_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_control_config(root, max_running_runs=0, control_mode="enforce")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "model-gate",
                        "acquire",
                        "--config",
                        str(config),
                        "--current-inflight",
                        "2",
                    ]
                )

            lease = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 75)
            self.assertEqual(lease["result_type"], "agentmon.control.model_gate_lease")
            self.assertFalse(lease["admitted"])
            self.assertEqual(lease["reason"], "target_inflight_reached")
            self.assertEqual(lease["target_inflight_requests"], 2)
            audit = root / "runs" / "_runtime" / "events.jsonl"
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertIn("control.model_gate.acquire", {row["event_type"] for row in rows})

    def test_tool_pool_acquire_denies_in_enforce_mode_when_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(Path(tmp), max_running_runs=4, control_mode="enforce")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "acquire",
                        "--config",
                        str(config),
                        "--running-count",
                        "3",
                        "--",
                        "pytest",
                        "-q",
                    ]
                )

            lease = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 75)
            self.assertEqual(lease["result_type"], "agentmon.control.tool_pool_lease")
            self.assertFalse(lease["admitted"])
            self.assertEqual(lease["pool_id"], "cpu_heavy")
            self.assertEqual(lease["reason"], "pool_capacity_reached")

    def test_tool_pool_exec_runs_command_when_slot_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_control_config(root, max_running_runs=4, control_mode="enforce")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "exec",
                        "--config",
                        str(config),
                        "--",
                        sys.executable,
                        "-c",
                        "print('pool-ok')",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["result_type"], "agentmon.control.tool_execution_result")
            self.assertTrue(result["accepted"])
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "pool-ok\n")
            audit = root / "runs" / "_runtime" / "events.jsonl"
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            event_types = {row["event_type"] for row in rows}
            self.assertIn("control.tool_pool.acquire", event_types)
            self.assertIn("control.tool_execution.completed", event_types)

    def test_tool_pool_exec_reports_resource_control_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = write_control_config(
                Path(tmp),
                max_running_runs=4,
                control_mode="enforce",
                nice_increment=1,
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "exec",
                        "--config",
                        str(config),
                        "--",
                        sys.executable,
                        "-c",
                        "print('resource-ok')",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["stdout"], "resource-ok\n")
            self.assertEqual(result["resource_control"]["nice_increment"], 1)
            self.assertEqual(result["resource_control"]["quality"], "best_effort_child_preexec")

    def test_tool_pool_exec_does_not_run_when_pool_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_control_config(root, max_running_runs=4, control_mode="enforce")
            marker = root / "should-not-run.txt"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "control",
                        "tool-pool",
                        "exec",
                        "--config",
                        str(config),
                        "--running-count",
                        "3",
                        "--",
                        "pytest",
                        "-q",
                        str(marker),
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 75)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["error"], "pool_capacity_reached")
            self.assertFalse(marker.exists())

    def test_gated_provider_rejects_before_http_when_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(
                config_path=write_control_config(Path(tmp), max_running_runs=0, control_mode="enforce")
            )
            provider = GatedOpenAICompatibleProvider(cfg)

            result = provider.chat_completions(
                {"model": "local", "messages": []},
                current_inflight_requests=12,
            )

            self.assertFalse(result.accepted)
            self.assertEqual(result.error, "target_inflight_reached")
            self.assertEqual(result.quality, "local_gate_rejected")

    def test_gated_provider_posts_openai_compatible_chat_request(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            received_path = ""
            received_body = {}
            received_request_id = ""

            def do_POST(self) -> None:
                type(self).received_path = self.path
                type(self).received_request_id = self.headers.get("X-AgentMon-Request-Id", "")
                length = int(self.headers.get("Content-Length", "0"))
                type(self).received_body = json.loads(self.rfile.read(length).decode("utf-8"))
                raw = json.dumps({"id": "cmpl-test", "choices": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            api_base = f"http://127.0.0.1:{server.server_port}/v1"
            with tempfile.TemporaryDirectory() as tmp:
                cfg = load_config(
                    config_path=write_control_config(Path(tmp), max_running_runs=4, api_base=api_base)
                )
                provider = GatedOpenAICompatibleProvider(cfg)
                payload = {"model": "local", "messages": [{"role": "user", "content": "hi"}]}

                result = provider.chat_completions(payload, request_id="req-test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(result.accepted)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body["id"], "cmpl-test")
        self.assertEqual(Handler.received_path, "/v1/chat/completions")
        self.assertEqual(Handler.received_body, payload)
        self.assertEqual(Handler.received_request_id, "req-test")


def write_control_config(
    root: Path,
    *,
    max_running_runs: int,
    control_mode: str = "advise",
    api_base: str = "http://127.0.0.1:1/v1",
    nice_increment: int = 0,
) -> Path:
    config = root / "agentmon-test.toml"
    runs = root / "runs"
    config.write_text(
        f"""
[general]
output_root = "{runs}"

[vllm]
metrics_url = "http://127.0.0.1:1/metrics"
api_base = "{api_base}"
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

[control]
mode = "{control_mode}"

[model_gate]
service_id = "local-vllm"
max_inflight_requests = 12
moderate_pressure_target = 6
high_pressure_target = 2
min_inflight_requests = 1

[tool_pool]
default_max_concurrent = 8
cpu_heavy_max_concurrent = 3
io_heavy_max_concurrent = 2
high_priority_max_concurrent = 1
nice_increment = {nice_increment}
""".strip(),
        encoding="utf-8",
    )
    return config
