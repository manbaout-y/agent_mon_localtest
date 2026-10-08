import tempfile
import unittest
from pathlib import Path

from agentmon.adapters.agent_frameworks.mini_swe.runtime_control import RuntimeControlledModel
from agentmon.config import load_config
from agentmon.control import LocalToolResourceActuator, ModelRequestGate, ToolPoolManager
from agentmon.optimizer import StaticThresholdOptimizer
from agentmon.optimizer.types import QueueSnapshot
from agentmon.runtime import RunSpec
from agentmon.scheduler import FixedConcurrencyScheduler


class RuntimeOptimizationSwitchTests(unittest.TestCase):
    def disabled_config(self, root: Path):
        cfg = load_config(
            env={"AGENTMON_RUNTIME_OPTIMIZATION_ENABLED": "false"},
            cli_overrides={"general.output_root": str(root / "runs")},
        )
        cfg.scheduler.max_running_runs = 0
        cfg.control.mode = "enforce"
        cfg.model_gate.max_inflight_requests = 1
        cfg.model_gate.moderate_pressure_target = 1
        cfg.model_gate.high_pressure_target = 1
        cfg.tool_pool.default_max_concurrent = 0
        cfg.tool_pool.nice_increment = 5
        return cfg

    def test_master_switch_bypasses_scheduler_optimizer_and_active_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.disabled_config(Path(tmp))
            spec = RunSpec(task_id="task-disabled", backend="process")
            scheduler_decision = FixedConcurrencyScheduler(cfg).decide(spec)
            optimizer_decision = StaticThresholdOptimizer(cfg).evaluate(
                QueueSnapshot(running_count=1, max_running_runs=0, run_count=1)
            )
            gate = ModelRequestGate(cfg)
            gate_lease = gate.try_acquire(current_inflight_requests=1)
            pool_lease = ToolPoolManager(cfg).try_acquire(
                ["echo", "ok"],
                running_counts={"default": 1},
            )
            resource_plan = LocalToolResourceActuator(cfg).plan()

        self.assertEqual(scheduler_decision.action, "admit")
        self.assertEqual(scheduler_decision.reason, "runtime_optimization_disabled")
        self.assertEqual(optimizer_decision.admission_pressure, "none")
        self.assertEqual(optimizer_decision.reason, "runtime_optimization_disabled")
        self.assertTrue(gate_lease.admitted)
        self.assertEqual(gate_lease.reason, "gate_disabled")
        self.assertTrue(pool_lease.admitted)
        self.assertEqual(pool_lease.reason, "pool_disabled")
        self.assertFalse(resource_plan.applied_in_child)
        self.assertEqual(resource_plan.nice_increment, 0)
        self.assertEqual(resource_plan.quality, "runtime_optimization_disabled")

    def test_mini_swe_model_wrapper_directly_bypasses_optimizer_when_disabled(self) -> None:
        class Model:
            def __init__(self) -> None:
                self.calls = []

            def query(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                return "ok"

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.disabled_config(Path(tmp))
            model = Model()
            wrapped = RuntimeControlledModel(model, cfg)
            result = wrapped.query(
                ["message"],
                agentmon_model_request_id="request-id",
                temperature=0,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(model.calls, [(["message"], {"temperature": 0})])


if __name__ == "__main__":
    unittest.main()
