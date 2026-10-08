import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.contracts import ResourceBudget
from agentmon.runtime import TaskStatePolicy, build_task_state_from_events, fingerprint_tool_result


class TaskStateTests(unittest.TestCase):
    def test_task_state_detects_low_progress_context_and_repeated_tools(self) -> None:
        events = [
            event("agent_step.end", {"step_id": "s1", "new_evidence_count": 1}, sequence=1),
            event("tool_call.end", {"executable": "pytest"}, sequence=2),
            event("tool_call.end", {"executable": "pytest"}, sequence=3),
            event("tool_call.end", {"executable": "pytest"}, sequence=4),
            event(
                "model_request.end",
                {
                    "context_prompt_tokens": 9000,
                    "context_prompt_tokens_delta": 2500,
                    "prompt_tokens": 9000,
                    "completion_tokens": 30,
                    "abnormal_path_probability": 0.7,
                },
                sequence=5,
            ),
        ]

        snapshot = build_task_state_from_events(
            events,
            task_id="task-low",
            run_id="run-low",
            policy=TaskStatePolicy(
                normal_progress_threshold=0.55,
                stalled_progress_threshold=0.25,
                repeat_tool_window=3,
                high_context_growth_tokens=2048,
                base_progress_score=0.5,
                positive_signal_weight=0.1,
                negative_signal_weight=0.15,
            ),
            resource_budget=ResourceBudget(max_prompt_tokens=100000),
        ).to_dict()

        self.assertEqual(snapshot["identity"]["task_id"], "task-low")
        self.assertEqual(snapshot["progress"]["state"], "LOW_PROGRESS")
        self.assertIn("repeat_tool_window_reached", snapshot["progress"]["negative_signals"])
        self.assertIn("context_growth_high", snapshot["progress"]["negative_signals"])
        self.assertIn("compact_dynamic_context", snapshot["context"]["recommended_actions"])
        self.assertEqual(snapshot["prediction"]["abnormal_path_probability"], 0.7)
        self.assertEqual(snapshot["budget"]["remaining_prompt_tokens"], 91000)

    def test_task_state_keeps_missing_prediction_unknown(self) -> None:
        snapshot = build_task_state_from_events([], task_id="task-empty").to_dict()

        self.assertEqual(snapshot["progress"]["state"], "UNKNOWN")
        self.assertEqual(snapshot["prediction"]["quality"], "UNKNOWN")
        self.assertIn("no_events_available", snapshot["limitations"])

    def test_tool_result_fingerprint_is_content_addressed_without_content_storage(self) -> None:
        first = fingerprint_tool_result(tool="pytest", content="FAILED test_a\n").to_dict()
        second = fingerprint_tool_result(
            tool="pytest",
            content="FAILED test_a\n",
            previous_content_hash=first["content_hash"],
        ).to_dict()

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(second["novelty_ratio"], 0.0)
        self.assertEqual(second["structured_summary"]["line_count"], 1)

    def test_runtime_submit_writes_and_reads_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_config(root)
            child = (
                "import json, os, time; "
                "p=os.environ['AGENTMON_EVENTS_PATH']; "
                "row={'schema_version':'1.0','event_id':'e1','run_id':os.environ['AGENTMON_RUN_ID'],"
                "'event_type':'model_request.end','wall_time_ns':time.time_ns(),"
                "'payload':{'context_prompt_tokens':100,'prompt_tokens':100,'completion_tokens':5}}; "
                "open(p,'a',encoding='utf-8').write(json.dumps(row)+'\\n')"
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = agentmon_main(
                    [
                        "runtime",
                        "submit",
                        "--config",
                        str(config),
                        "--task-id",
                        "task-state-runtime",
                        "--",
                        sys.executable,
                        "-c",
                        child,
                    ]
                )
            self.assertEqual(exit_code, 0)
            result = json.loads(stdout.getvalue())
            run_dir = Path(result["handle"]["run_dir"])
            self.assertTrue((run_dir / "runtime_task_state.json").is_file())

            state_stdout = io.StringIO()
            with redirect_stdout(state_stdout):
                state_code = agentmon_main(
                    [
                        "runtime",
                        "task-state",
                        "--config",
                        str(config),
                        result["handle"]["run_id"],
                    ]
                )
            state = json.loads(state_stdout.getvalue())
            self.assertEqual(state_code, 0)
            self.assertEqual(state["identity"]["task_id"], "task-state-runtime")
            self.assertEqual(state["cost"]["prompt_tokens"], 100)


def event(event_type: str, payload: dict, *, sequence: int) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"e-{sequence}",
        "event_type": event_type,
        "wall_time_ns": sequence,
        "payload": payload,
    }


def write_config(root: Path) -> Path:
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
""".strip(),
        encoding="utf-8",
    )
    return config
