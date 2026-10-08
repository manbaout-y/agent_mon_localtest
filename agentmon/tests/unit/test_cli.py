import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentmon.cli import format_live_p1, main as agentmon_main
from agentmon.events import Event
from agentmon.storage.jsonl import JsonlWriter


ROOT = Path(__file__).resolve().parents[2]


class CliTests(unittest.TestCase):
    def run_agentmon(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "agentmon", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )

    def test_version_command(self) -> None:
        result = self.run_agentmon("--version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agentmon", result.stdout)

    def test_doctor_returns_machine_readable_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_report = {
                "schema_version": "1.0",
                "command": "doctor",
                "status": "partial",
                "capabilities": {
                    "python_output": {"status": "supported"},
                    "host": {"status": "supported"},
                    "ascend": {"status": "not_found"},
                    "vllm": {"status": "unsupported"},
                    "mini_swe": {"status": "not_found"},
                    "p1": {"status": "not_found"},
                },
            }
            stdout = io.StringIO()
            with patch("agentmon.cli.run_doctor", return_value=(fake_report, 0)) as run_doctor:
                with redirect_stdout(stdout):
                    exit_code = agentmon_main(["doctor", "--output-root", tmp])

        self.assertEqual(exit_code, 0)
        run_doctor.assert_called_once()
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["command"], "doctor")
        self.assertIn(data["status"], {"supported", "partial"})
        self.assertIn("capabilities", data)
        for capability in data["capabilities"].values():
            self.assertIsInstance(capability.get("status"), str)

    def test_canonical_cli_writes_machine_readable_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            run_dir = runs_root / "run-canonical"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-canonical", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text("", encoding="utf-8")
            output = root / "canonical"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "canonical",
                        "--output-root",
                        str(runs_root),
                        "--output-dir",
                        str(output),
                        "--format",
                        "jsonl",
                        "--format",
                        "csv",
                        "run-canonical",
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["dataset_type"], "agentmon.canonical")
            self.assertEqual(result["table_row_counts"]["runs"], 1)
            self.assertTrue((output / "runs.jsonl").is_file())
            self.assertTrue((output / "requests.csv").is_file())

    def test_matrix_cli_propagates_repeat_failures(self) -> None:
        fake_result = {"failure_count": 1, "repeat_completed": 2}
        stdout = io.StringIO()
        with patch("agentmon.cli.run_repeated_matrix", return_value=fake_result) as runner:
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    ["matrix", "--repeat", "2", "--output-root", "/tmp/matrix", "--", "true"]
                )

        self.assertEqual(exit_code, 1)
        runner.assert_called_once()
        self.assertEqual(json.loads(stdout.getvalue())["repeat_completed"], 2)

    def test_live_p1_summary_reads_events_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with JsonlWriter(run_dir / "events.jsonl") as writer:
                writer.write_event(
                    Event(
                        run_id="run-live",
                        source="mini_swe",
                        event_type="tool_call.start",
                        quality="exact",
                        payload={"span_id": "tool-1", "span_type": "tool_call"},
                    )
                )
                writer.write_event(
                    Event(
                        run_id="run-live",
                        source="mini_swe",
                        event_type="tool_call.end",
                        quality="exact",
                        payload={
                            "span_id": "tool-1",
                            "span_type": "tool_call",
                            "status": "success",
                            "duration_ns": 1000,
                        },
                    )
                )

            self.assertIn("tools 1", format_live_p1(run_dir))

    def test_import_p2_imports_offset_slice_and_regenerates_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            run_dir = runs_root / "run-batch"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-batch", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text("", encoding="utf-8")
            p2_path = root / "service-p2.jsonl"
            prefix = p2_line("old-service", "vllm.request.finished", {"vllm_request_id": "old"})
            middle = [
                p2_line(
                    "service-run",
                    "vllm.request.received",
                    {
                        "run_id": "service-run",
                        "agent_request_id": "model-1",
                        "vllm_request_id": "vllm-1",
                        "correlation_quality": "direct",
                        "received_ns": 100,
                    },
                ),
                p2_line(
                    "service-run",
                    "vllm.request.finished",
                    {
                        "run_id": "service-run",
                        "agent_request_id": "model-1",
                        "vllm_request_id": "vllm-1",
                        "correlation_quality": "direct",
                        "finished_ns": 300,
                        "prompt_tokens": 10,
                        "generation_tokens": 3,
                    },
                ),
            ]
            suffix = p2_line("new-service", "vllm.request.finished", {"vllm_request_id": "new"})
            p2_path.write_text(prefix + "".join(middle) + suffix, encoding="utf-8")
            start_offset = len(prefix.encode("utf-8"))
            end_offset = start_offset + len("".join(middle).encode("utf-8"))

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "import-p2",
                        "--output-root",
                        str(runs_root),
                        "--run-id",
                        "run-batch",
                        "--start-offset",
                        str(start_offset),
                        "--end-offset",
                        str(end_offset),
                        "--quality",
                        "service_exclusive",
                        str(p2_path),
                    ]
                )

            result = json.loads(stdout.getvalue())
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            rows = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["imported_event_count"], 2)
        self.assertEqual(result["vllm_p2_status"], "partial")
        self.assertEqual(result["vllm_p2_request_count"], 1)
        self.assertEqual(report["vllm_p2"]["status"], "partial")
        self.assertEqual(report["vllm_p2"]["lifecycle_incomplete_count"], 1)
        self.assertEqual(report["vllm_p2"]["request_count"], 1)
        self.assertEqual({row["run_id"] for row in rows}, {"run-batch"})
        self.assertTrue(all(row["event_id"].startswith("p2-import-") for row in rows))
        self.assertEqual(rows[0]["labels"]["p2_import_quality"], "service_exclusive")
        self.assertEqual(rows[0]["payload"]["run_id"], "run-batch")
        self.assertEqual(rows[0]["payload"]["service_run_id"], "service-run")
        self.assertEqual(rows[0]["payload"]["_agentmon_p2_import"]["source_run_id"], "service-run")
        self.assertEqual(rows[1]["payload"]["vllm_request_id"], "vllm-1")

    def test_import_p2_filters_by_mini_swe_trajectory_response_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            run_dir = runs_root / "run-batch"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-batch", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text("", encoding="utf-8")
            trajectory = root / "mini.traj.json"
            trajectory.write_text(
                json.dumps(
                    {
                        "trajectory_format": "mini-swe-agent-1.1",
                        "messages": [
                            {
                                "role": "assistant",
                                "tool_calls": [{"id": "call-a", "type": "function"}],
                                "extra": {"response": {"id": "resp-a"}},
                            },
                            {"role": "tool", "tool_call_id": "call-a", "extra": {"returncode": 0}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            p2_path = root / "service-p2.jsonl"
            p2_path.write_text(
                "".join(
                    [
                        p2_line("service-run", "vllm.adapter.capability", {"visible_devices": ["0", "1"]}, mono=5),
                        p2_line("service-run", "vllm.kv.snapshot", {"kv_usage_ratio": 0.1}, mono=90),
                        p2_line(
                            "service-run",
                            "vllm.request.received",
                            {"api_request_id": "resp-a", "vllm_request_id": "vllm-a", "received_ns": 100},
                            mono=100,
                        ),
                        p2_line(
                            "service-run",
                            "vllm.request.first_token",
                            {"api_request_id": "resp-a", "vllm_request_id": "vllm-a", "first_token_ns": 200},
                            mono=200,
                        ),
                        p2_line(
                            "service-run",
                            "vllm.kv.block_event",
                            {"request_id": "vllm-a", "allocated_block_count": 2},
                            mono=250,
                        ),
                        p2_line(
                            "service-run",
                            "vllm.request.finished",
                            {
                                "api_request_id": "resp-a",
                                "vllm_request_id": "vllm-a",
                                "finished_ns": 300,
                                "prompt_tokens": 10,
                                "generation_tokens": 3,
                            },
                            mono=300,
                        ),
                        p2_line(
                            "service-run",
                            "vllm.request.received",
                            {"api_request_id": "resp-other", "vllm_request_id": "vllm-o", "received_ns": 120},
                            mono=120,
                        ),
                        p2_line(
                            "service-run",
                            "vllm.request.finished",
                            {"api_request_id": "resp-other", "vllm_request_id": "vllm-o", "finished_ns": 320},
                            mono=320,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "import-p2",
                        "--output-root",
                        str(runs_root),
                        "--run-id",
                        "run-batch",
                        "--quality",
                        "direct",
                        "--filter-response-ids-from-trajectory",
                        str(trajectory),
                        str(p2_path),
                    ]
                )

            result = json.loads(stdout.getvalue())
            rows = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            request_api_ids = {
                row["payload"].get("api_request_id")
                for row in rows
                if str(row.get("event_type", "")).startswith("vllm.request.")
            }

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["filter_api_request_id_count"], 1)
        self.assertEqual(request_api_ids, {"resp-a"})
        self.assertTrue(any(row["event_type"] == "vllm.kv.block_event" for row in rows))
        self.assertFalse(any(row["event_type"] == "vllm.kv.snapshot" for row in rows))
        self.assertFalse(any(row["event_type"] == "vllm.adapter.capability" for row in rows))
        self.assertFalse(any(row["payload"].get("api_request_id") == "resp-other" for row in rows))
        self.assertEqual(result["p2_request_count"], 1)
        self.assertEqual(result["p2_global_event_count"], 0)
        self.assertEqual(result["p2_sample_event_count"], 0)
        self.assertEqual(result["matched_p2_request_count"], 1)
        self.assertEqual(result["p2_requests_in_task_window_not_in_trajectory_count"], 1)

    def test_import_p2_filter_run_id_matches_agentmon_run_id_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            run_dir = runs_root / "run-batch"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run-batch", "status": "success", "data_quality": {}}),
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text("", encoding="utf-8")
            p2_path = root / "service-p2.jsonl"
            p2_path.write_text(
                "".join(
                    [
                        p2_line(
                            "service-run",
                            "vllm.request.finished",
                            {
                                "agentmon_run_id": "run-batch",
                                "vllm_request_id": "vllm-keep",
                                "finished_ns": 100,
                            },
                        ),
                        p2_line(
                            "service-run",
                            "vllm.request.finished",
                            {
                                "agentmon_run_id": "other-run",
                                "vllm_request_id": "vllm-drop",
                                "finished_ns": 200,
                            },
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "import-p2",
                        "--output-root",
                        str(runs_root),
                        "--run-id",
                        "run-batch",
                        "--filter-run-id",
                        "run-batch",
                        str(p2_path),
                    ]
                )

            result = json.loads(stdout.getvalue())
            rows = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["imported_event_count"], 1)
        self.assertEqual(rows[0]["payload"]["vllm_request_id"], "vllm-keep")


def p2_line(run_id: str, event_type: str, payload: dict, *, mono: int = 1) -> str:
    return (
        json.dumps(
            {
                "schema_version": "1.0",
                "event_id": f"{run_id}-{event_type}",
                "run_id": run_id,
                "source": "vllm_p2",
                "event_type": event_type,
                "quality": "exact",
                "wall_time_ns": mono,
                "monotonic_time_ns": mono,
                "labels": {},
                "payload": payload,
            }
        )
        + "\n"
    )
