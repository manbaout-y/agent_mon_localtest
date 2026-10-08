import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_agentmon_test_from_config.py"


def load_runner_module():
    spec = importlib.util.spec_from_file_location("run_agentmon_test_from_config", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ServerTestConfigRunnerTests(unittest.TestCase):
    def test_main_without_default_config_runs_builtin_self_check(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "reports"
            with patch.object(module, "DEFAULT_CONFIG", root / "missing.json"):
                with patch.object(module, "default_report_dir", return_value=str(report_dir)):
                    exit_code = module.main([])
            summary = json.loads((report_dir / "test_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["config_path"], "<builtin:self_check>")
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["actions"]["self_check"]["status"], "PASS")

    def test_missing_explicit_config_requires_init_without_creating_file(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "missing.json"
            exit_code = module.main([str(config_path)])

        self.assertEqual(exit_code, 64)
        self.assertFalse(config_path.exists())

    def test_init_config_writes_template_with_optional_server_profile(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon_server_test_config.json"
            exit_code = module.main(
                [
                    "--init-config",
                    str(config_path),
                    "--server-profile",
                    "examples/server_profiles/chenyang_ascend910b.json",
                ]
            )
            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["server_profile"], "examples/server_profiles/chenyang_ascend910b.json")

    def test_server_profile_expands_stable_paths_and_dynamic_repo(self) -> None:
        module = load_runner_module()
        with patch.dict(os.environ, {}, clear=True):
            resolved = module.resolve_test_config(
                {
                    "server_profile": "examples/server_profiles/chenyang_ascend910b.json",
                    "actions": ["self_check"],
                    "preflight": {"runtime_model": "override-model"},
                },
                ROOT / "agentmon_server_test_config.json",
            )

        self.assertEqual(resolved["resolved_server_profile"]["name"], "chenyang_ascend910b")
        self.assertEqual(
            resolved["preflight"]["agentmon_config"],
            str(ROOT / "examples" / "config.target.toml"),
        )
        self.assertEqual(
            resolved["preflight"]["vllm_python"],
            "/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python",
        )
        self.assertEqual(resolved["preflight"]["runtime_model"], "override-model")
        self.assertTrue(resolved["preflight"]["create_events_parent"])

    def test_offline_analysis_missing_input_needs_input_without_failure(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            result = module.run_offline_analysis({"offline_analysis": {}}, report_dir)
            payload = json.loads((report_dir / "offline_analysis.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(payload["missing_fields"], ["offline_analysis.input_path"])

    def test_preflight_deep_missing_vllm_python_needs_input(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            result = module.run_preflight({"preflight": {"deep": True}}, report_dir)
            payload = json.loads((report_dir / "preflight_wrapper.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(payload["missing_fields"], ["preflight.vllm_python"])

    def test_main_reports_partial_for_actions_waiting_on_input(self) -> None:
        module = load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agentmon_server_test_config.json"
            report_dir = root / "reports"
            config_path.write_text(
                json.dumps(
                    {
                        "actions": ["offline_analysis"],
                        "report_dir": str(report_dir),
                        "offline_analysis": {},
                    }
                ),
                encoding="utf-8",
            )
            exit_code = module.main([str(config_path)])
            summary = json.loads((report_dir / "test_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertEqual(summary["actions"]["offline_analysis"]["status"], "NEEDS_INPUT")


if __name__ == "__main__":
    unittest.main()
