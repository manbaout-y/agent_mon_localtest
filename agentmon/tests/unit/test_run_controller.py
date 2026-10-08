import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RunControllerTests(unittest.TestCase):
    def test_run_wraps_command_and_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentmon",
                    "run",
                    "--output-root",
                    tmp,
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; sys.exit(7)",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 7, result.stderr)
            run_dirs = [path for path in Path(tmp).iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["exit_code"], 7)
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue((run_dirs[0] / "events.jsonl").exists())
            self.assertFalse((run_dirs[0] / "metrics.sqlite3").exists())
            self.assertTrue((run_dirs[0] / "report.json").exists())
            self.assertTrue((run_dirs[0] / "summary.json").exists())

            report = json.loads((run_dirs[0] / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["event_log"]["sqlite_status"], "DISABLED_OR_MISSING")
            self.assertIsNone(report["evidence"]["sqlite"])

    def test_run_can_opt_in_to_sqlite_cache_with_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AGENTMON_STORAGE_SQLITE"] = "true"
            result, run_dir = run_agentmon_fake_child(
                tmp,
                [sys.executable, "-c", "pass"],
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((run_dir / "metrics.sqlite3").is_file())

    def test_run_cleans_child_pythonpath_and_expands_run_id_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_child_pythonpath = str(ROOT / "src")
            inherited_pythonpath = os.pathsep.join(
                [
                    "/usr/local/Ascend/fake-pythonpath",
                    "/tmp/legacy-pythonpath",
                    expected_child_pythonpath,
                ]
            )
            config_path = Path(tmp) / "agentmon-test.toml"
            config_path.write_text(
                f"""
[general]
output_root = "{Path(tmp) / "runs"}"

[agent]
env_unset = ["PYTHONPATH", "PYTHONHOME"]
env_set = [
  "PYTHONPATH={expected_child_pythonpath}",
  "PYTHONNOUSERSITE=1",
  "NO_PROXY=127.0.0.1,localhost,::1",
  "no_proxy=127.0.0.1,localhost,::1",
]

[vllm]
metrics_url = "http://127.0.0.1:1/metrics"
api_base = "http://127.0.0.1:1/v1"
models_url = "http://127.0.0.1:1/v1/models"
request_timeout_seconds = 0.05

[ascend]
npu_smi = "{Path(tmp) / "missing-npu-smi"}"
command_timeout_seconds = 0.05
""".strip(),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = inherited_pythonpath
            output_template = str(Path(tmp) / "{run_id}.env.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentmon",
                    "run",
                    "--config",
                    str(config_path),
                    "--",
                    sys.executable,
                    "-c",
                    (
                        "import json, os, sys; "
                        "json.dump({"
                        "'run_id': os.environ.get('AGENTMON_RUN_ID'), "
                        "'pythonpath': os.environ.get('PYTHONPATH'), "
                        "'pythonhome': os.environ.get('PYTHONHOME'), "
                        "'py_no_user_site': os.environ.get('PYTHONNOUSERSITE'), "
                        "'no_proxy': os.environ.get('NO_PROXY'), "
                        "'path': sys.argv[1]"
                        "}, open(sys.argv[1], 'w'))"
                    ),
                    output_template,
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_dirs = [path for path in (Path(tmp) / "runs").iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            run_id = run_dirs[0].name
            child_data = json.loads((Path(tmp) / f"{run_id}.env.json").read_text(encoding="utf-8"))
            self.assertEqual(child_data["run_id"], run_id)
            self.assertEqual(child_data["path"], str(Path(tmp) / f"{run_id}.env.json"))
            self.assertEqual(child_data["pythonpath"], expected_child_pythonpath)
            self.assertNotIn("/usr/local/Ascend", child_data["pythonpath"])
            self.assertNotIn("/tmp/legacy-pythonpath", child_data["pythonpath"])
            self.assertIsNone(child_data["pythonhome"])
            self.assertEqual(child_data["py_no_user_site"], "1")
            self.assertEqual(child_data["no_proxy"], "127.0.0.1,localhost,::1")

    def test_run_imports_detected_trajectory_output_with_run_id_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_script = write_fake_mini_swe_child(Path(tmp))
            output_template = str(Path(tmp) / "trajectories" / "task-{run_id}.traj.json")
            result, run_dir = run_agentmon_fake_child(
                tmp,
                [
                    sys.executable,
                    str(child_script),
                    "-o",
                    output_template,
                ],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["data_quality"]["trajectory"], "complete")
            self.assertEqual(report["agent"]["status"], "complete")
            self.assertEqual(report["agent"]["message_count"], 2)
            self.assertTrue((Path(tmp) / "trajectories" / f"task-{run_dir.name}.traj.json").exists())
            imported = latest_trajectory_sample(run_dir)
            self.assertIn(run_dir.name, imported["source_path"])

    def test_run_imports_detected_absolute_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_script = write_fake_mini_swe_child(Path(tmp))
            output_path = str(Path(tmp) / "fixed-output.traj.json")
            result, run_dir = run_agentmon_fake_child(
                tmp,
                [
                    sys.executable,
                    str(child_script),
                    f"--output={output_path}",
                ],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["agent"]["status"], "complete")
            self.assertEqual(latest_trajectory_sample(run_dir)["source_path"], output_path)

    def test_run_marks_detected_missing_trajectory_without_failing_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_script = write_fake_mini_swe_child(Path(tmp))
            missing_path = str(Path(tmp) / "missing.traj.json")
            env = os.environ.copy()
            env["WRITE_TRAJ"] = "0"
            result, run_dir = run_agentmon_fake_child(
                tmp,
                [
                    sys.executable,
                    str(child_script),
                    "-o",
                    missing_path,
                ],
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["data_quality"]["trajectory"], "missing")
            self.assertEqual(report["agent"]["status"], "missing")
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            trajectory_errors = [
                event
                for event in events
                if event["source"] == "trajectory" and event["event_type"] == "error"
            ]
            self.assertEqual(trajectory_errors[-1]["payload"]["status"], "MISSING_AFTER_TIMEOUT")
            self.assertEqual(trajectory_errors[-1]["payload"]["path"], missing_path)

    def test_run_reports_p1_spans_and_imported_trajectory_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            child_script = write_fake_mini_swe_child(Path(tmp))
            output_path = str(Path(tmp) / "with-p1.traj.json")
            env = os.environ.copy()
            env["WRITE_P1"] = "1"
            result, run_dir = run_agentmon_fake_child(
                tmp,
                [
                    sys.executable,
                    str(child_script),
                    "--output",
                    output_path,
                ],
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertNotEqual(report["agent"]["status"], "missing")
            self.assertEqual(report["p1_spans"]["status"], "complete")
            self.assertEqual(report["p1_spans"]["agent_steps"], 1)


def run_agentmon_fake_child(
    tmp: str,
    child_argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    config_path = write_fast_config(Path(tmp))
    run_env = env or os.environ.copy()
    run_env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentmon",
            "run",
            "--config",
            str(config_path),
            "--",
            *child_argv,
        ],
        cwd=ROOT,
        env=run_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    runs_root = Path(tmp) / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    if len(run_dirs) != 1:
        raise AssertionError(f"expected one run dir, got {run_dirs}; stderr={result.stderr}")
    return result, run_dirs[0]


def write_fast_config(tmp: Path) -> Path:
    config_path = tmp / "agentmon-test.toml"
    config_path.write_text(
        f"""
[general]
output_root = "{tmp / "runs"}"

[agent]
env_unset = ["PYTHONPATH", "PYTHONHOME"]
env_set = [
  "PYTHONPATH={ROOT / "src"}",
  "PYTHONNOUSERSITE=1",
  "NO_PROXY=127.0.0.1,localhost,::1",
  "no_proxy=127.0.0.1,localhost,::1",
]

[vllm]
metrics_url = "http://127.0.0.1:1/metrics"
api_base = "http://127.0.0.1:1/v1"
models_url = "http://127.0.0.1:1/v1/models"
request_timeout_seconds = 0.05

[ascend]
npu_smi = "{tmp / "missing-npu-smi"}"
command_timeout_seconds = 0.05
""".strip(),
        encoding="utf-8",
    )
    return config_path


def write_fake_mini_swe_child(tmp: Path) -> Path:
    script = tmp / "fake_mini_swe_child.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys
            import time
            from pathlib import Path


            def output_path(argv):
                index = 0
                while index < len(argv):
                    item = argv[index]
                    if item in {"-o", "--output"} and index + 1 < len(argv):
                        return argv[index + 1]
                    if item.startswith("--output="):
                        return item.split("=", 1)[1]
                    index += 1
                return None


            path = output_path(sys.argv[1:])
            if os.environ.get("WRITE_TRAJ", "1") != "0" and path:
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(
                        {
                            "trajectory_format": "mini-swe-test",
                            "info": {
                                "mini_version": "2.4.3",
                                "exit_status": "submitted",
                                "model_stats": {"api_calls": 1, "instance_cost": 0.25},
                            },
                            "messages": [
                                {"role": "user", "content": "task"},
                                {
                                    "role": "assistant",
                                    "content": "answer",
                                    "tool_calls": [{"name": "bash"}],
                                    "extra": {
                                        "usage": {
                                            "prompt_tokens": 10,
                                            "completion_tokens": 3,
                                            "cached_tokens": 2,
                                        }
                                    },
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            if os.environ.get("WRITE_P1") == "1":
                from agentmon.events import Event
                from agentmon.storage.jsonl import JsonlWriter

                writer = JsonlWriter(os.environ["AGENTMON_EVENTS_PATH"])
                try:
                    writer.write_event(
                        Event(
                            run_id=os.environ["AGENTMON_RUN_ID"],
                            source="mini_swe",
                            event_type="agent_step.start",
                            quality="exact",
                            payload={
                                "span_id": "agent-step-1",
                                "span_type": "agent_step",
                                "start_mono_ns": time.monotonic_ns(),
                            },
                        )
                    )
                    writer.write_event(
                        Event(
                            run_id=os.environ["AGENTMON_RUN_ID"],
                            source="mini_swe",
                            event_type="agent_step.end",
                            quality="exact",
                            payload={
                                "span_id": "agent-step-1",
                                "span_type": "agent_step",
                                "status": "success",
                                "duration_ns": 1,
                                "end_mono_ns": time.monotonic_ns(),
                            },
                        )
                    )
                finally:
                    writer.close()
            """
        ),
        encoding="utf-8",
    )
    return script


def latest_trajectory_sample(run_dir: Path) -> dict[str, object]:
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    samples = [
        event["payload"]
        for event in events
        if event["source"] == "trajectory" and event["event_type"] == "sample"
    ]
    if not samples:
        raise AssertionError("no trajectory sample event found")
    return samples[-1]
