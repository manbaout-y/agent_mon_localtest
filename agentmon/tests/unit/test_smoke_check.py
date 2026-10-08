import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.reporting.summary import build_run_summary
from tests.unit.test_summary import make_report


ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = ROOT / "scripts" / "agentmon_p1_smoke_check.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("agentmon_p1_smoke_check", SMOKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SmokeCheckTests(unittest.TestCase):
    def test_smoke_check_allows_nonzero_commands_as_metric_only(self) -> None:
        module = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-ok"
            run_dir.mkdir()
            report = make_report(run_id="run-ok")
            summary = build_run_summary(report)
            (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            exit_code, output = run_script(module, str(run_dir), "--smoke-rc", "0", "--final-test-rc", "0")

        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["task_success"])
        self.assertEqual(payload["command_nonzero_count"], 1)
        self.assertEqual(payload["issues"], [])

    def test_smoke_check_fails_on_final_test_rc_not_error_suffix(self) -> None:
        module = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-failed"
            run_dir.mkdir()
            summary = build_run_summary(make_report(run_id="run-failed"))
            (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            exit_code, output = run_script(module, str(run_dir), "--smoke-rc", "0", "--final-test-rc", "1")

        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["task_success"])
        self.assertEqual(payload["issues"], ["final_test_rc"])

    def test_smoke_check_report_fallback_populates_quality_criteria(self) -> None:
        module = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-report"
            run_dir.mkdir()
            report = make_report(run_id="run-report")
            report["ascend"]["device_summary"] = {"target_attribution_status": "complete"}
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

            exit_code, output = run_script(module, str(run_dir), "--smoke-rc", "0", "--final-test-rc", "0")

        payload = json.loads(output)
        criteria = payload["criteria"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(criteria["trajectory_status"], "complete")
        self.assertEqual(criteria["p1_status"], "complete")
        self.assertEqual(criteria["proc_agent_process_count_max"], 1)
        self.assertEqual(criteria["npu_target_attribution_status"], "complete")
        self.assertEqual(criteria["token_source"], "trajectory")
        self.assertEqual(criteria["prefix_cache_status"], "enabled")
        self.assertEqual(criteria["p2_status"], "complete")
        self.assertEqual(criteria["p2_request_count"], 2)


def run_script(module, *args: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = module.main(list(args))
    return exit_code, stdout.getvalue()
