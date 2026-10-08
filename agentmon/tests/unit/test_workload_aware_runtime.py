import json
import tempfile
import unittest
from pathlib import Path

from agentmon.config import load_config
from agentmon.optimizer import OptimizerService
from agentmon.runtime import RunSpec
from agentmon.scheduler import build_scheduler


class WorkloadAwareRuntimeTests(unittest.TestCase):
    def test_workload_aware_scheduler_defers_when_configured_prompt_budget_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_config(root, scheduler_strategy="workload_aware", prompt_budget=1000)
            write_run_manifest(
                root / "runs" / "run-active",
                state="running",
                task_state={
                    "path_state": "NORMAL",
                    "context_active_tokens": 100,
                    "prediction": {"remaining_prompt_tokens_p90": 800},
                },
            )
            cfg = load_config(config_path=config)
            scheduler = build_scheduler(cfg)

            decision = scheduler.decide(
                RunSpec(
                    task_id="task-new",
                    backend="process",
                    command=("true",),
                    input={"workload_prediction": {"remaining_prompt_tokens_p90": 300}},
                )
            )

            self.assertEqual(decision.action, "defer")
            self.assertEqual(decision.reason, "prompt_tokens_p90_budget_exceeded")
            self.assertEqual(decision.metadata["workload_checks"][0]["known_total"], 1100)

    def test_workload_aware_scheduler_keeps_unknown_prediction_as_admissible_under_base_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_config(root, scheduler_strategy="workload_aware", prompt_budget=1000)
            cfg = load_config(config_path=config)
            scheduler = build_scheduler(cfg)

            decision = scheduler.decide(RunSpec(task_id="task-unknown", backend="process", command=("true",)))

            self.assertEqual(decision.action, "admit")
            self.assertEqual(decision.reason, "capacity_and_workload_budget_available")
            self.assertGreaterEqual(decision.metadata["workload_checks"][0]["unknown_count"], 1)

    def test_workload_aware_optimizer_reports_task_state_budget_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = write_config(root, optimizer_strategy="workload_aware")
            write_run_manifest(
                root / "runs" / "run-stalled",
                state="running",
                task_state={
                    "path_state": "STALLED",
                    "context_active_tokens": 9000,
                    "prediction": {"remaining_prompt_tokens_p90": 500},
                },
            )
            cfg = load_config(config_path=config)

            decision = OptimizerService(cfg).decision()

            self.assertEqual(decision.strategy, "workload_aware")
            self.assertEqual(decision.admission_pressure, "moderate")
            self.assertIn("stalled_runtime_tasks_present", decision.risk_flags)
            self.assertEqual(
                decision.budget_recommendations["run-stalled"]["action"],
                "defer_or_degrade_future_model_requests",
            )


def write_run_manifest(run_dir: Path, *, state: str, task_state: dict) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "runtime_backend": "process",
                "runtime_task_id": f"task-{run_dir.name}",
                "runtime_state": state,
                "status": state,
                "runtime_task_state": task_state,
            }
        ),
        encoding="utf-8",
    )


def write_config(
    root: Path,
    *,
    scheduler_strategy: str = "fixed_concurrency",
    optimizer_strategy: str = "static_threshold",
    prompt_budget: int | None = None,
) -> Path:
    config = root / "agentmon-test.toml"
    prompt_budget_line = (
        f"workload_prompt_tokens_p90_budget = {prompt_budget}" if prompt_budget is not None else ""
    )
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
strategy = "{scheduler_strategy}"
max_running_runs = 4
{prompt_budget_line}

[optimizer]
strategy = "{optimizer_strategy}"
mode = "advise"
""".strip(),
        encoding="utf-8",
    )
    return config
