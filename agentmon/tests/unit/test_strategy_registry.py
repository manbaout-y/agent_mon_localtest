import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.config import load_config
from agentmon.optimizer import OptimizerService, register_optimizer_strategy, unregister_optimizer_strategy
from agentmon.optimizer.types import OptimizerDecision, QueueSnapshot
from agentmon.scheduler import (
    build_scheduler,
    register_scheduler_strategy,
    unregister_scheduler_strategy,
)
from agentmon.scheduler.types import SchedulingDecision


class StrategyRegistryTests(unittest.TestCase):
    def test_strategy_list_reports_builtin_strategies(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = agentmon_main(["strategy", "list"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["result_type"], "agentmon.strategy.catalog")
        names = {(row["domain"], row["name"]) for row in payload["strategies"]}
        self.assertIn(("scheduler", "fixed_concurrency"), names)
        self.assertIn(("scheduler", "workload_aware"), names)
        self.assertIn(("optimizer", "static_threshold"), names)
        self.assertIn(("optimizer", "workload_aware"), names)
        self.assertIn(("context_compression", "recent_groups"), names)
        self.assertIn(("context_compression_trigger", "token_ratio"), names)

    def test_custom_scheduler_strategy_can_be_registered_and_selected(self) -> None:
        class AlwaysDeferScheduler:
            strategy = "always_defer_test"

            def __init__(self, cfg) -> None:
                self.cfg = cfg

            def decide(self, spec) -> SchedulingDecision:
                return SchedulingDecision(
                    action="defer",
                    strategy=self.strategy,
                    task_id=spec.task_id,
                    reason="custom_scheduler_selected",
                    running_count=0,
                    max_running_runs=self.cfg.scheduler.max_running_runs,
                )

            def snapshot(self) -> dict[str, object]:
                return {
                    "schema_version": "1.0",
                    "result_type": "agentmon.scheduler.snapshot",
                    "strategy": self.strategy,
                    "enabled": True,
                    "max_running_runs": self.cfg.scheduler.max_running_runs,
                    "running_count": 0,
                    "state_counts": {},
                    "runs": [],
                }

        register_scheduler_strategy(
            "always_defer_test",
            AlwaysDeferScheduler,
            description="test scheduler",
            replace=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = load_config(config_path=write_strategy_config(Path(tmp), scheduler_strategy="always_defer_test"))
                scheduler = build_scheduler(cfg)

            self.assertEqual(scheduler.snapshot()["strategy"], "always_defer_test")
        finally:
            unregister_scheduler_strategy("always_defer_test")

    def test_custom_optimizer_strategy_can_be_registered_and_selected(self) -> None:
        class AlwaysHighOptimizer:
            name = "always_high_test"

            def __init__(self, cfg) -> None:
                self.cfg = cfg

            def evaluate(self, queue: QueueSnapshot) -> OptimizerDecision:
                return OptimizerDecision(
                    strategy=self.name,
                    mode=self.cfg.optimizer.mode,
                    admission_pressure="high",
                    target_concurrency={"runtime.default": queue.max_running_runs},
                    risk_flags=["custom_optimizer_selected"],
                    reason="custom optimizer selected",
                    confidence=0.5,
                    queue=queue,
                )

        register_optimizer_strategy(
            "always_high_test",
            AlwaysHighOptimizer,
            description="test optimizer",
            replace=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = load_config(config_path=write_strategy_config(Path(tmp), optimizer_strategy="always_high_test"))
                decision = OptimizerService(cfg).decision()

            self.assertEqual(decision.strategy, "always_high_test")
            self.assertEqual(decision.admission_pressure, "high")
            self.assertIn("custom_optimizer_selected", decision.risk_flags)
        finally:
            unregister_optimizer_strategy("always_high_test")


def write_strategy_config(
    root: Path,
    *,
    scheduler_strategy: str = "fixed_concurrency",
    optimizer_strategy: str = "static_threshold",
) -> Path:
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
strategy = "{scheduler_strategy}"
max_running_runs = 4

[optimizer]
strategy = "{optimizer_strategy}"
mode = "advise"
""".strip(),
        encoding="utf-8",
    )
    return config
