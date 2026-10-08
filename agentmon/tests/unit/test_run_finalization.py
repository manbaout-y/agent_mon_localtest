import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agentmon.config import load_config
from agentmon.events import Event
from agentmon.reporting.report import build_report, summarize_p1_spans
from agentmon.run_controller import RunController


ROOT = Path(__file__).resolve().parents[2]


class RunFinalizationTests(unittest.TestCase):
    def test_run_waits_for_delayed_trajectory_rename_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = write_delayed_trajectory_child(root)
            trajectory = root / "trajectory" / "delayed.traj.json"
            result, run_dir = run_agentmon(
                root,
                [sys.executable, str(child), "-o", str(trajectory)],
                trajectory_timeout=2.0,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            events = read_events(run_dir)

        self.assertEqual(report["agent"]["status"], "complete")
        self.assertEqual(report["finalization"]["trajectory_status"], "IMPORTED")
        self.assertTrue(report["event_snapshot"]["snapshot_after_writer_close"])
        self.assertEqual(report["event_snapshot"]["valid_event_count"], len(events))
        self.assertEqual(summary["event_count"], len(events))
        self.assertFalse(
            any(
                event.get("source") == "trajectory"
                and event.get("event_type") == "error"
                and event.get("payload", {}).get("status") == "MISSING_AFTER_TIMEOUT"
                for event in events
            )
        )

    def test_run_imports_p2_byte_window_filtered_by_trajectory_response_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p2_events = root / "service-p2.events.jsonl"
            p2_events.write_text(
                p2_line("history", "vllm.request.finished", {"api_request_id": "resp-history"}),
                encoding="utf-8",
            )
            child = write_p1_trajectory_and_p2_child(root)
            trajectory = root / "trajectory" / "task.traj.json"
            result, run_dir = run_agentmon(
                root,
                [sys.executable, str(child), "-o", str(trajectory)],
                p2_events=p2_events,
                trajectory_timeout=2.0,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            events = read_events(run_dir)
            p2_api_ids = {
                event["payload"].get("api_request_id")
                for event in events
                if event.get("source") == "vllm_p2"
            }

        self.assertEqual(report["vllm_p2"]["request_count"], 1)
        self.assertNotEqual(report["vllm_p2"]["status"], "missing")
        self.assertEqual(report["client_server"]["matched_request_count"], 1)
        self.assertEqual(summary["p2_request_count"], 1)
        self.assertEqual(p2_api_ids, {"resp-keep"})
        self.assertEqual(report["finalization"]["p2"]["selection_mode"], "trajectory")
        self.assertEqual(report["finalization"]["p2"]["p2_start_offset"], len(p2_line("history", "vllm.request.finished", {"api_request_id": "resp-history"}).encode("utf-8")))
        self.assertEqual(report["event_snapshot"]["valid_event_count"], len(events))
        self.assertTrue(report["finalization"]["event_writer_joined"])

    def test_sampler_final_event_is_in_report_snapshot(self) -> None:
        class FakeSampler:
            source = "host"

            def __init__(self, sink, *args, **kwargs) -> None:
                self.sink = sink

            def start(self) -> None:
                return None

            def stop(self, *, timeout_seconds=None):
                self.sink.emit(
                    "host",
                    "sample",
                    "sampled",
                    payload={"loadavg": "0 0 0", "final_sample": True},
                    critical=True,
                )
                return SimpleNamespace(
                    to_dict=lambda: {
                        "source": self.source,
                        "stop_requested": True,
                        "joined": True,
                        "timeout_seconds": timeout_seconds,
                        "thread_name": "fake",
                        "thread_ident": None,
                        "alive": False,
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_config(root, trajectory_timeout=0.1)
            cfg = load_config(config_path=config)
            with mock.patch("agentmon.run_controller.ProcSampler", FakeSampler), mock.patch(
                "agentmon.run_controller.HostSampler", FakeSampler
            ), mock.patch("agentmon.run_controller.VllmMetricsSampler", FakeSampler), mock.patch(
                "agentmon.run_controller.AscendSmiSampler", FakeSampler
            ):
                controller = RunController(cfg, [sys.executable, "-c", "pass"], write_final_report=True)
                exit_code = controller.run()
                run_dir = controller.run_dir
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            events = read_events(run_dir)

        self.assertEqual(exit_code, 0)
        self.assertTrue(any(event.get("payload", {}).get("final_sample") is True for event in events))
        self.assertEqual(report["event_snapshot"]["valid_event_count"], len(events))
        self.assertTrue(report["finalization"]["collectors_joined"])
        self.assertTrue(report["event_snapshot"]["snapshot_after_writer_close"])

    def test_error_cancelled_and_timeout_events_close_spans(self) -> None:
        events = [
            span_event("model_request.start", "model-1", "model_request"),
            span_event("model_request.error", "model-1", "model_request", duration_ns=10),
            span_event("agent_step.start", "step-1", "agent_step"),
            span_event("agent_step.timeout", "step-1", "agent_step", duration_ns=20),
            span_event("tool_call.start", "tool-1", "tool_call"),
            span_event("tool_call.cancelled", "tool-1", "tool_call", duration_ns=30),
        ]

        spans = summarize_p1_spans(events, run_end_mono_ns=1000, finalize=True)

        self.assertEqual(spans["model_requests"], 1)
        self.assertEqual(spans["agent_steps"], 1)
        self.assertEqual(spans["tool_calls"], 1)
        self.assertEqual(spans["completed_span_count"], 3)
        self.assertEqual(spans["open_span_count"], 0)
        self.assertEqual(spans["abandoned_span_count"], 0)


def run_agentmon(
    root: Path,
    child_argv: list[str],
    *,
    p2_events: Path | None = None,
    trajectory_timeout: float = 1.0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    config = write_config(root, p2_events=p2_events, trajectory_timeout=trajectory_timeout)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    if p2_events is not None:
        env["P2_SERVICE_PATH"] = str(p2_events)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentmon",
            "run",
            "--config",
            str(config),
            "--",
            *child_argv,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    runs_root = root / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    if len(run_dirs) != 1:
        raise AssertionError(f"expected one run dir, got {run_dirs}; stderr={result.stderr}")
    return result, run_dirs[0]


def write_config(root: Path, *, p2_events: Path | None = None, trajectory_timeout: float = 1.0) -> Path:
    config = root / "agentmon-test.toml"
    p2_line_config = f'p2_events_path = "{p2_events}"' if p2_events is not None else 'p2_events_path = ""'
    config.write_text(
        f"""
[general]
output_root = "{root / "runs"}"

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
npu_smi = "{root / "missing-npu-smi"}"
command_timeout_seconds = 0.05

[finalization]
trajectory_wait_timeout_seconds = {trajectory_timeout}
trajectory_poll_interval_seconds = 0.02
trajectory_stable_checks = 2
collector_join_timeout_seconds = 1.0
writer_close_timeout_seconds = 2.0
{p2_line_config}
""".strip(),
        encoding="utf-8",
    )
    return config


def write_delayed_trajectory_child(root: Path) -> Path:
    script = root / "delayed_trajectory_child.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import subprocess
            import sys
            from pathlib import Path


            def output_path(argv):
                for index, item in enumerate(argv):
                    if item in {"-o", "--output"} and index + 1 < len(argv):
                        return argv[index + 1]
                    if item.startswith("--output="):
                        return item.split("=", 1)[1]
                return None


            target = output_path(sys.argv[1:])
            helper = (
                "import json, os, time\\n"
                "from pathlib import Path\\n"
                "target = Path(os.environ['TRAJ_TARGET'])\\n"
                "target.parent.mkdir(parents=True, exist_ok=True)\\n"
                "time.sleep(0.2)\\n"
                "tmp = target.with_name(target.name + '.tmp')\\n"
                "tmp.write_text(json.dumps({'trajectory_format': 'mini-swe-test', 'messages': [{'role': 'user', 'content': 'task'}, {'role': 'assistant', 'tool_calls': [], 'extra': {'response': {'id': 'resp-delayed'}}}]}), encoding='utf-8')\\n"
                "os.replace(tmp, target)\\n"
            )
            env = os.environ.copy()
            env["TRAJ_TARGET"] = str(target)
            subprocess.Popen([sys.executable, "-c", helper], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            """
        ),
        encoding="utf-8",
    )
    return script


def write_p1_trajectory_and_p2_child(root: Path) -> Path:
    script = root / "p1_trajectory_p2_child.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys
            import time
            from pathlib import Path

            from agentmon.events import Event
            from agentmon.storage.jsonl import JsonlWriter


            def output_path(argv):
                for index, item in enumerate(argv):
                    if item in {"-o", "--output"} and index + 1 < len(argv):
                        return argv[index + 1]
                    if item.startswith("--output="):
                        return item.split("=", 1)[1]
                return None


            run_id = os.environ["AGENTMON_RUN_ID"]
            writer = JsonlWriter(os.environ["AGENTMON_EVENTS_PATH"])
            try:
                writer.write_event(Event(run_id=run_id, source="agent_adapter", event_type="model_request.start", quality="exact", payload={"span_id": "model-1", "span_type": "model_request", "start_mono_ns": 100}))
                writer.write_event(Event(run_id=run_id, source="agent_adapter", event_type="model_request.end", quality="exact", payload={"span_id": "model-1", "span_type": "model_request", "response_id": "resp-keep", "end_mono_ns": 400, "duration_ns": 300, "status": "success"}))
            finally:
                writer.close()

            target = Path(output_path(sys.argv[1:]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"trajectory_format": "mini-swe-test", "messages": [{"role": "assistant", "tool_calls": [{"id": "call-1"}], "extra": {"response": {"id": "resp-keep"}}}]}), encoding="utf-8")

            p2_path = Path(os.environ["P2_SERVICE_PATH"])
            with p2_path.open("a", encoding="utf-8") as handle:
                for api_id, base in (("resp-keep", 1000), ("resp-other", 2000)):
                    for event_type, offset in (("vllm.request.received", 0), ("vllm.request.queued", 10), ("vllm.request.first_scheduled", 20), ("vllm.request.finished", 100)):
                        handle.write(json.dumps({"schema_version": "1.0", "event_id": f"{api_id}-{event_type}", "run_id": "service-run", "source": "vllm_p2", "event_type": event_type, "quality": "exact", "payload": {"api_request_id": api_id, "vllm_request_id": f"engine-{api_id}", event_type.rsplit(".", 1)[-1] + "_ns": base + offset}}) + "\\n")
                handle.flush()
                os.fsync(handle.fileno())
            """
        ),
        encoding="utf-8",
    )
    return script


def p2_line(run_id: str, event_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "event_id": f"{run_id}-{event_type}",
            "run_id": run_id,
            "source": "vllm_p2",
            "event_type": event_type,
            "quality": "exact",
            "payload": payload,
        }
    ) + "\n"


def read_events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def span_event(event_type: str, span_id: str, span_type: str, *, duration_ns: int | None = None) -> dict:
    payload = {"span_id": span_id, "span_type": span_type}
    if event_type.endswith(".start"):
        payload["start_mono_ns"] = 1
    else:
        payload.update({"end_mono_ns": 2, "duration_ns": duration_ns, "status": event_type.rsplit(".", 1)[-1]})
    return Event(run_id="run", source="agent_adapter", event_type=event_type, quality="exact", payload=payload).to_dict()
