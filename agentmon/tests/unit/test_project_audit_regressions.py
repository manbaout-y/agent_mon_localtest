from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentmon.adapters.agent_frameworks.mini_swe.context import AgentMonContext
from agentmon.adapters.agent_frameworks.mini_swe.context_compression import MiniSweContextCompressor
from agentmon.adapters.agent_frameworks.mini_swe.environment import MiniSweLocalExecutionMixin
from agentmon.adapters.agent_frameworks.mini_swe.event_sink import AdapterEventSink
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedModel, InstrumentedModelMixin
from agentmon.analysis.analyzers import analyze_workload
from agentmon.analysis.compare import build_normalized_deltas, build_paired_deltas
from agentmon.analysis.config import default_analysis_config
from agentmon.analysis.io import write_table
from agentmon.analysis.statistics import time_weighted_summary
from agentmon.batch.runtime import build_parser, run_batch
from agentmon.config import AgentMonConfig, load_config
from agentmon.control.resource_actuator import LocalToolResourceActuator
from agentmon.control.tool_executor import PooledToolExecutor
from agentmon.experiments.orchestration import evaluate_activation_probe, flatten_mapping, orchestrate_experiment
from agentmon.experiments.request import generate_experiment_contract
from agentmon.server.quick_smoke import write_mini_swe_probe, write_service_start_gate_script


class BatchMeasurementTests(unittest.TestCase):
    def test_reanalysis_preserves_execution_evidence_across_repeated_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks.tsv"
            tasks.write_text("task_id\ntask-1\n")
            batch = root / "batch"
            common = ["--batch-root", str(batch), "--task-file", str(tasks),
                      "--analysis-preset", "quick", "--runtime-source-isolation", "off",
                      "--runtime-source-integrity", "off", "--p1-mode", "off"]
            result = {"task_id": "task-1", "run_id": "run-1", "status": "workload_complete",
                      "execution_success": True, "workload_wall_s": 0.02}

            def workload(*args):
                time.sleep(0.02)
                return dict(result)

            with patch("agentmon.batch.runtime.run_workload_task", side_effect=workload), \
                 patch("agentmon.batch.runtime.collect_npu_process_mapping", return_value={}), \
                 patch("agentmon.batch.runtime.analyze_one_task", side_effect=lambda args, task, row, *rest: row):
                run_batch(build_parser().parse_args(common + ["--command-template", "true", "--execution-only"]))
                original = json.loads((batch / "batch-summary.json").read_text())
                original_manifest = json.loads((batch / "batch-manifest.json").read_text())
                phases = (batch / "batch-phase-events.jsonl").read_bytes()
                profile = (batch / "pipeline-profile.json").read_bytes()
                resume_paths = []
                for _ in range(2):
                    run_batch(build_parser().parse_args(common + ["--analyze-existing"]))
                    current = json.loads((batch / "batch-summary.json").read_text())
                    manifest = json.loads((batch / "batch-manifest.json").read_text())
                    for key in ("batch_wall_s", "phase_timings", "phase_timestamps", "phase_resource_profiles"):
                        self.assertEqual(current[key], original[key], key)
                    self.assertEqual(current["throughput"]["workload_task_throughput"], original["throughput"]["workload_task_throughput"])
                    self.assertEqual(manifest["end_wall_ns"], original_manifest["end_wall_ns"])
                    self.assertEqual((batch / "batch-phase-events.jsonl").read_bytes(), phases)
                    self.assertEqual((batch / "pipeline-profile.json").read_bytes(), profile)
                    resume_paths.append(current["analysis_resume"]["phase_events"])
                    self.assertTrue(Path(resume_paths[-1]).is_file())
                    self.assertIsNone(current["analysis_resume"]["phase_timings"]["workload_phase_s"])
                    self.assertGreater(current["analysis_resume"]["phase_timings"]["pipeline_wall_s"], 0)
                self.assertNotEqual(*resume_paths)

    def test_historical_batch_without_timing_does_not_invent_throughput(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks.tsv").write_text("task_id\ntask-1\n")
            (root / "batch-results.tsv").write_text("task_id\trun_id\texecution_success\ntask-1\trun-1\ttrue\n")
            args = build_parser().parse_args(["--batch-root", tmp, "--task-file", str(root / "tasks.tsv"),
                                              "--analyze-existing", "--analysis-preset", "quick"])
            with patch("agentmon.batch.runtime.analyze_one_task", side_effect=lambda args, task, row, *rest: row):
                run_batch(args)
            summary = json.loads((root / "batch-summary.json").read_text())
            self.assertIsNone(summary["throughput"]["workload_task_throughput"])
            self.assertEqual(summary["throughput"]["standard_definitions"]["workload_task_throughput"]["confidence"], "unavailable")
            self.assertFalse((root / "batch-phase-events.jsonl").exists())


class AdapterSemanticsTests(unittest.TestCase):
    def test_environment_preserves_config_and_per_call_directory_and_timeout(self):
        class Environment(MiniSweLocalExecutionMixin):
            def _check_finished(self, output):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured, override = root / "configured", root / "override"
            configured.mkdir()
            override.mkdir()
            env = Environment()
            env.config = SimpleNamespace(cwd=str(configured), env={}, timeout=5)
            env.agentmon_sink = AdapterEventSink(AgentMonContext(run_id="cwd-test", run_dir=None, events_path=None))
            self.assertEqual(Path(env.execute({"command": "pwd"})["output"].strip()), configured.resolve())
            self.assertEqual(Path(env.execute({"command": "pwd"}, str(override))["output"].strip()), override.resolve())
            result = env.execute({"command": "sleep 2"}, timeout=0.02)
            self.assertNotEqual(result["returncode"], 0)
            self.assertIn("timed out", result["exception_info"])
            env.agentmon_sink.close()

    def test_summary_accepts_text_but_main_query_keeps_action_validation(self):
        class ActionModel:
            def __init__(self):
                self.calls = []
                self.costs = []

            def _query(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                return {"content": "Verified state retained.",
                        "actions": [] if kwargs.get("tool_choice") == "none" else ["pwd"],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 8}}

            def _parse_actions(self, response):
                if not response["actions"]:
                    raise ValueError("an action is required")
                return response["actions"]

            def query(self, messages, **kwargs):
                response = self._query(messages, **kwargs)
                self.costs.append(1)
                self._parse_actions(response)
                return response

        class MixedModel(InstrumentedModelMixin, ActionModel):
            pass

        for mixin in (False, True):
            with self.subTest(mixin=mixin), tempfile.TemporaryDirectory() as tmp:
                cfg = AgentMonConfig()
                cfg.context_compression.enabled = True
                cfg.context_compression.window_tokens = 300
                messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "task"},
                            {"role": "assistant", "content": "old " * 800}, {"role": "tool", "content": "old output"},
                            {"role": "assistant", "content": "new"}, {"role": "tool", "content": "latest"}]
                sink = AdapterEventSink(AgentMonContext(run_id="summary-test", run_dir=Path(tmp), events_path=Path(tmp) / "events.jsonl"))
                inner = MixedModel() if mixin else ActionModel()
                if mixin:
                    inner.agentmon_sink = sink
                    inner.agentmon_context_compressor = MiniSweContextCompressor(cfg)
                    model = inner
                else:
                    model = InstrumentedModel(inner, sink=sink, cfg=cfg)
                model.query(messages)
                sink.close()
                self.assertEqual(len(inner.calls), 2)
                self.assertEqual(len(inner.costs), 2)
                self.assertEqual(inner.calls[0][1]["tool_choice"], "none")
                self.assertNotIn("tool_choice", inner.calls[-1][1])
                self.assertNotIn(messages[2], inner.calls[-1][0])
                with self.assertRaises(ValueError):
                    inner._parse_actions({"actions": []})
                events = [json.loads(line) for line in (Path(tmp) / "events.jsonl").read_text().splitlines()]
                decision = next(row["payload"] for row in events if row["event_type"] == "context_compression.decision")
                self.assertTrue(decision["context_compression_applied"])
                self.assertEqual(decision["context_summary_input_tokens"], 100)


class ExperimentGateTests(unittest.TestCase):
    def test_formal_rechecks_evidence_created_by_prior_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            contract = generate_experiment_contract({"mechanism": ["model_gate"], "activation_evidence_paths": [str(events)]})
            payload = json.dumps({"event_type": "control.model_gate.acquire", "payload": {"reason": "advise_only"}}) + "\n"
            contract["commands"] = {
                "ab_single_task": [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])", str(events), payload],
                "formal_matrix": [sys.executable, "-c", "print('formal')"],
            }
            report = orchestrate_experiment(contract, output_dir=root / "out", execute=True, run_formal=True,
                                            stages=["ab_single_task", "formal_matrix"])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["ready_for_formal"], "YES")
            self.assertEqual(report["command_results"][-1]["status"], "PASS")

    def test_command_start_failure_and_timeout_write_failed_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            for command in (
                {"command": [str(Path(tmp) / "missing-command")]},
                {"command": [sys.executable, "-c", "import time; print('started',flush=True); time.sleep(2)"], "timeout_seconds": 0.1},
            ):
                with self.subTest(command=command):
                    contract = {"variants": {"A": {"config": {}}},
                                "commands": {"preflight": command},
                                "formal_gates": {"require_activation": False}}
                    report = orchestrate_experiment(contract, output_dir=tmp, execute=True, stages=["preflight"])
                    self.assertEqual(report["status"], "FAIL")
                    self.assertEqual(report["ready_for_formal"], "NO")
                    self.assertTrue(Path(report["command_results"][0]["stderr_path"]).is_file())

    def test_preflight_failure_blocks_formal_and_ready_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            events.write_text(json.dumps({"event_type": "control.model_gate.acquire", "payload": {"reason": "advise_only"}}) + "\n")
            contract = generate_experiment_contract({"mechanism": ["model_gate"], "activation_evidence_paths": [str(events)]})
            marker = root / "formal-ran"
            contract["commands"] = {
                "preflight": [sys.executable, "-c", "raise SystemExit(1)"],
                "formal_matrix": [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
            }
            report = orchestrate_experiment(contract, output_dir=root / "out", execute=True, run_formal=True,
                                            stages=["preflight", "formal_matrix"])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["ready_for_formal"], "NO")
            self.assertEqual(report["command_results"][-1]["reason"], "prerequisite_failed:preflight")
            self.assertFalse(marker.exists())

    def test_generated_control_modes_load_as_runtime_config(self):
        for mechanism in ("model_gate", "tool_pool"):
            for mode in (None, "advice", "advise", "enforce"):
                with self.subTest(mechanism=mechanism, mode=mode):
                    request = {"mechanism": [mechanism]}
                    if mode is not None:
                        request["mode"] = mode
                    contract = generate_experiment_contract(request)
                    for variant in contract["variants"].values():
                        cfg = load_config(env={}, cli_overrides=flatten_mapping(variant["config"]))
                        self.assertEqual(cfg.control.mode, "enforce" if mode == "enforce" else "advise")

    def test_required_coverage_needs_a_measured_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(json.dumps({"event_type": "context_compression.decision", "payload": {"context_compression_status": "APPLIED"}}) + "\n")
            probe = {"mechanism": "context_compression", "evidence_paths": [str(path)]}
            self.assertEqual(evaluate_activation_probe(probe)["status"], "FAIL")
            self.assertEqual(evaluate_activation_probe({**probe, "coverage_denominator": 1})["status"], "PASS")
            self.assertEqual(evaluate_activation_probe({**probe, "coverage_denominator": 2})["status"], "FAIL")
            contract = generate_experiment_contract({"mechanism": ["context_compression"],
                                                     "evidence": {"activation_paths": [str(path)], "coverage_denominator": 1}})
            report = orchestrate_experiment(contract, output_dir=Path(tmp) / "out")
            self.assertEqual(report["ready_for_formal"], "YES")


class AnalysisComparisonTests(unittest.TestCase):
    def test_identical_bundles_pair_tasks_by_concurrency_and_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundles = [Path(tmp) / name for name in ("a", "b")]
            runs = [{"task_id": "task", "trial_id": trial, "configured_concurrency": concurrency,
                     "run_id": f"{trial}-c{concurrency}", "run_wall_s": duration}
                    for trial, concurrency, duration in (("trial-1", 1, 10), ("trial-1", 5, 20), ("trial-2", 1, 30))]
            for bundle in bundles:
                write_table(bundle / "01_normalized", "runs", runs, output_format="csv")
            deltas = build_paired_deltas(bundles)
            self.assertEqual(len(deltas), 3)
            self.assertTrue(all(row["run_wall_delta_s"] == 0 for row in deltas))
            rates = [{"trial_id": "trial-1", "configured_concurrency": 1, "task_throughput_per_hour": 100},
                     {"trial_id": "trial-2", "configured_concurrency": 1, "task_throughput_per_hour": 150}]
            normalized = build_normalized_deltas(bundles, [rates, rates])
            self.assertTrue(all(row["delta"] == 0 for row in normalized if row["metric"] == "task_throughput_per_hour"))

    def test_ambiguous_trial_metadata_is_not_silently_overwritten(self):
        rows = [{"configured_concurrency": 1}, {"configured_concurrency": 1}]
        with self.assertRaisesRegex(ValueError, "ambiguous comparison"):
            build_normalized_deltas([Path("a"), Path("b")], [rows, rows])

    def test_concurrency_quantiles_are_invariant_to_interval_splitting(self):
        original = time_weighted_summary([(0, 99, 1), (99, 100, 10)])
        split = time_weighted_summary([(i, i + 1, 1) for i in range(99)] + [(99, 100, 10)])
        self.assertEqual(original, split)
        self.assertEqual(original["p95"], 1)
        self.assertEqual(original["median"], 1)

    def test_workload_baseline_is_the_lowest_numeric_concurrency(self):
        rows = [{"task_id": "t", "configured_concurrency": c, "run_wall_s": c, "run_id": str(c)} for c in (2, 4, 8, 16)]
        output = analyze_workload({"runs": rows}, default_analysis_config())
        paired = output["task_paired_analysis"]
        self.assertTrue(all(row["baseline_concurrency"] == 2 for row in paired))
        self.assertEqual(next(row["delta_run_wall_s"] for row in paired if row["target_concurrency"] == 16), 14)

    def test_workload_deltas_use_the_same_trial(self):
        rows = [{"task_id": "t", "trial_id": trial, "configured_concurrency": c,
                 "run_wall_s": wall, "run_id": f"{trial}-{c}"}
                for trial, c, wall in (("trial-1", 2, 10), ("trial-1", 10, 20), ("trial-2", 2, 100), ("trial-2", 10, 110))]
        paired = analyze_workload({"runs": rows}, default_analysis_config())["task_paired_analysis"]
        self.assertEqual([row["delta_run_wall_s"] for row in paired], [0, 10, 0, 10])


class ToolLifecycleTests(unittest.TestCase):
    def test_advise_keeps_resource_plan_without_applying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AgentMonConfig()
            cfg.general.output_root = tmp
            cfg.control.mode = "advise"
            cfg.tool_pool.memory_limit_bytes = 1024**4
            actuator = LocalToolResourceActuator(cfg)
            self.assertFalse(actuator.plan().applied_in_child)
            self.assertEqual(actuator.plan().memory_limit_bytes, 1024**4)
            self.assertIsNone(actuator.preexec_fn())
            if os.name == "posix":
                import resource
                result = PooledToolExecutor(cfg).execute([sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"])
                self.assertEqual(int(result.stdout), resource.getrlimit(resource.RLIMIT_AS)[0])
            cfg.control.mode = "enforce"
            self.assertTrue(actuator.plan().applied_in_child)

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is a POSIX contract")
    def test_timeout_captures_text_and_stops_descendants_before_releasing_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AgentMonConfig()
            cfg.general.output_root = tmp
            cfg.control.mode = "enforce"
            marker = Path(tmp) / "orphan-output"
            child = f"import time; from pathlib import Path; time.sleep(.8); Path({str(marker)!r}).touch()"
            parent = f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',{child!r}]); print('started',flush=True); p.wait()"
            executor = PooledToolExecutor(cfg)
            result = executor.execute([sys.executable, "-c", parent], timeout_seconds=0.3)
            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.stdout, "started\n")
            self.assertEqual(json.loads(json.dumps(result.to_dict()))["error"], "tool_timeout")
            time.sleep(0.6)
            self.assertFalse(marker.exists())
            self.assertEqual(executor.execute([sys.executable, "-c", "print('next')"]).stdout, "next\n")


class GeneratedShellGateTests(unittest.TestCase):
    def test_service_gate_exits_on_first_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe_python = root / "probe-python"
            probe_python.write_text(f"#!{sys.executable}\nimport os,sys\nif len(sys.argv)==4 and sys.argv[1]=='-':\n sys.stdin.read(); sys.exit(0)\nos.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n")
            probe_python.chmod(0o700)
            start = root / "start.sh"
            start.write_text("#!/bin/sh\nexec sleep 10\n")
            start.chmod(0o700)
            values = {"AGENTMON_PY": str(probe_python), "P2_ROOT": tmp, "SERVICE_LOG": str(root / "service.log"),
                      "SERVICE_START_SCRIPT": str(start), "VLLM_HOST": "127.0.0.1", "VLLM_PORT": "12345",
                      "AGENTMON_EVENTS_PATH": str(root / "events.jsonl"), "PID_FILE": str(root / "service.pid"),
                      "VLLM_BASE_URL": "http://127.0.0.1:12345/v1", "SERVED_MODEL": "demo"}
            env_path = root / "instance.env"
            env_path.write_text("".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items()))
            gate, report = root / "gate.sh", root / "gate.json"
            write_service_start_gate_script(gate, report, env_path)
            try:
                result = subprocess.run(["bash", str(gate)], capture_output=True, text=True, timeout=3,
                                        env={**os.environ, "SERVICE_START_TIMEOUT_SECONDS": "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.count("STATUS=PASS"), 1)
                self.assertEqual(json.loads(report.read_text())["reason"], "model_listed")
            finally:
                if (root / "service.pid").exists():
                    try:
                        os.kill(int((root / "service.pid").read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def test_mini_probe_requires_zero_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeout = root / "timeout"
            timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
            timeout.chmod(0o700)
            for rc in (0, 1, 7, 124, 127):
                with self.subTest(returncode=rc):
                    mini = root / "mini"
                    mini.write_text(f"#!/bin/sh\nexit {rc}\n")
                    mini.chmod(0o700)
                    probe = root / "probe.sh"
                    write_mini_swe_probe(probe, {"p2_root": tmp, "mini_swe_bin": str(mini), "agentmon_repo": str(root)})
                    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, timeout=10,
                                            env={**os.environ, "PATH": tmp + os.pathsep + os.environ["PATH"]})
                    self.assertEqual(result.returncode == 0, rc == 0)
                    self.assertIn("STATUS=PASS" if rc == 0 else "STATUS=FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
