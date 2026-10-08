import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentmon.cli import main as agentmon_main
from agentmon.experiments.prefix_cache import detect_prefix_cache_hint
from tests.unit.test_summary import make_report


class PrefixCacheExperimentTests(unittest.TestCase):
    def test_prefix_cache_hint_accepts_discovered_negative_option_without_fixed_spelling(self) -> None:
        self.assertEqual(
            detect_prefix_cache_hint("vllm serve model --disable-prefix-cache-local\n"),
            "disabled",
        )

    def test_prefix_cache_planner_outputs_non_mutating_json_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run-baseline"
            run_dir.mkdir()
            report = make_report(run_id="run-baseline", prefix_status="disabled")
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            variant = root / "variant.toml"
            variant.write_text("enable-prefix-caching = true\n", encoding="utf-8")

            exit_code, output = run_cli(
                "experiment",
                "plan",
                "prefix-cache",
                "--output-root",
                str(root),
                "--baseline-run",
                "run-baseline",
                "--variant-config",
                str(variant),
                "--format",
                "json",
            )

        plan = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertEqual(plan["experiment_type"], "prefix-cache")
        self.assertFalse(plan["mutates_service"])
        self.assertEqual(plan["variant_config"]["prefix_cache_hint"], "enabled")
        self.assertEqual(plan["baseline_run_count"], 1)
        self.assertIn("request_ttft_mean", plan["metrics_to_compare"])

    def test_prefix_cache_planner_table_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run-baseline"
            run_dir.mkdir()
            (run_dir / "report.json").write_text(
                json.dumps(make_report(run_id="run-baseline", prefix_status="disabled")),
                encoding="utf-8",
            )
            variant = root / "variant.toml"
            variant.write_text("--enable-prefix-caching\n", encoding="utf-8")

            exit_code, output = run_cli(
                "experiment",
                "plan",
                "prefix-cache",
                "--output-root",
                str(root),
                "--baseline-run",
                "run-baseline",
                "--variant-config",
                str(variant),
                "--format",
                "table",
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("mutates_service", output)
        self.assertIn("check.variant_config_exists", output)


def run_cli(*args: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = agentmon_main(list(args))
    return exit_code, stdout.getvalue()
