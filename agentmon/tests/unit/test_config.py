import tempfile
import unittest
from pathlib import Path

from agentmon.config import ConfigError, _parse_basic_toml, load_config


class ConfigTests(unittest.TestCase):
    def test_context_compression_config_defaults_off_and_supports_env_overrides(self) -> None:
        default = load_config(env={})
        self.assertFalse(default.context_compression.enabled)
        self.assertEqual(default.context_compression.trigger_strategy, "token_ratio")
        self.assertEqual(default.context_compression.strategy, "model_summary")
        self.assertEqual(default.context_compression.trigger_ratio, 0.80)
        self.assertEqual(default.context_compression.target_ratio, 0.20)
        configured = load_config(
            env={
                "AGENTMON_CONTEXT_COMPRESSION_ENABLED": "true",
                "AGENTMON_CONTEXT_COMPRESSION_STRATEGY": "recent_groups",
                "AGENTMON_CONTEXT_COMPRESSION_WINDOW_TOKENS": "32768",
            }
        )
        self.assertTrue(configured.context_compression.enabled)
        self.assertEqual(configured.context_compression.window_tokens, 32768)

    def test_context_compression_rejects_invalid_ratios_and_options(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(
                env={
                    "AGENTMON_CONTEXT_COMPRESSION_TRIGGER_RATIO": "0.2",
                    "AGENTMON_CONTEXT_COMPRESSION_TARGET_RATIO": "0.8",
                }
            )
        with self.assertRaises(ConfigError):
            load_config(
                env={
                    "AGENTMON_CONTEXT_COMPRESSION_STRATEGY_OPTIONS_JSON": "[]",
                }
            )
        with self.assertRaises(ConfigError):
            load_config(
                env={
                    "AGENTMON_CONTEXT_COMPRESSION_TRIGGER_OPTIONS_JSON": "[]",
                }
            )

    def test_python_310_toml_fallback_parses_literal_json_strings(self) -> None:
        parsed = _parse_basic_toml(
            """
[context_compression]
trigger_options_json = '{}'
strategy_options_json = '{"min_recent_groups":1}' # comment
""".strip()
        )

        self.assertEqual(parsed["context_compression"]["trigger_options_json"], "{}")
        self.assertEqual(
            parsed["context_compression"]["strategy_options_json"],
            '{"min_recent_groups":1}',
        )

    def test_runtime_optimization_master_switch_defaults_on_and_has_env_override(self) -> None:
        self.assertTrue(load_config(env={}).runtime_optimization.enabled)
        self.assertFalse(
            load_config(
                env={"AGENTMON_RUNTIME_OPTIMIZATION_ENABLED": "false"}
            ).runtime_optimization.enabled
        )

    def test_sqlite_cache_is_opt_in_and_environment_can_enable_it(self) -> None:
        self.assertFalse(load_config(env={}).storage.sqlite)
        self.assertTrue(load_config(env={"AGENTMON_STORAGE_SQLITE": "true"}).storage.sqlite)

    def test_config_override_order_is_cli_env_file_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            config_path.write_text(
                """
[general]
output_root = "/from-file"
log_level = "DEBUG"

[vllm]
metrics_url = "http://127.0.0.1:9000/metrics"
interval_seconds = 3.5
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(
                config_path=config_path,
                env={
                    "AGENTMON_GENERAL_OUTPUT_ROOT": "/from-env",
                    "AGENTMON_VLLM_INTERVAL_SECONDS": "2.0",
                },
                cli_overrides={
                    "general.output_root": "/from-cli",
                    "storage.queue_max_items": 123,
                },
            )

        self.assertEqual(cfg.general.output_root, "/from-cli")
        self.assertEqual(cfg.general.log_level, "DEBUG")
        self.assertEqual(cfg.vllm.metrics_url, "http://127.0.0.1:9000/metrics")
        self.assertEqual(cfg.vllm.interval_seconds, 2.0)
        self.assertEqual(cfg.storage.queue_max_items, 123)

    def test_agentmon_config_env_selects_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "selected.toml"
            config_path.write_text(
                """
[general]
output_root = "/selected"
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(env={"AGENTMON_CONFIG": str(config_path)})

        self.assertEqual(cfg.general.output_root, "/selected")

    def test_unknown_config_key_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "bad.toml"
            config_path.write_text(
                """
[general]
not_a_real_key = true
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path=config_path)

    def test_target_config_example_loads(self) -> None:
        root = Path(__file__).resolve().parents[2]
        cfg = load_config(
            config_path=root / "examples" / "config.target.toml",
            env={"AGENTMON_REPO": "/data/node0_disk3/chenyang/agentmon-ascend"},
        )

        self.assertEqual(cfg.general.output_root, "/data/node0_disk3/chenyang/agentmon/runs")
        self.assertEqual(cfg.vllm.models_url, "http://127.0.0.1:8000/v1/models")
        self.assertIn("VLLM::Worker_TP", cfg.vllm.process_match)
        self.assertIn("PYTHONPATH", cfg.agent.env_unset)
        self.assertIn("PYTHONPATH=/data/node0_disk3/chenyang/agentmon-ascend/src", cfg.agent.env_set)
        self.assertIn("MSWEA_COST_TRACKING=ignore_errors", cfg.agent.env_set)
        self.assertFalse(cfg.storage.sqlite)
        self.assertTrue(cfg.context_compression.enabled)
        self.assertEqual(cfg.context_compression.trigger_strategy, "token_ratio")

    def test_scheduler_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            config_path.write_text(
                """
[scheduler]
enabled = true
strategy = "workload_aware"
max_running_runs = 3
defer_when_full = true
workload_prompt_tokens_p90_budget = 120000
long_context_token_threshold = 32000
long_context_limit = 2
abnormal_path_risk_threshold = 0.8
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(config_path=config_path)

        self.assertTrue(cfg.scheduler.enabled)
        self.assertEqual(cfg.scheduler.strategy, "workload_aware")
        self.assertEqual(cfg.scheduler.max_running_runs, 3)
        self.assertEqual(cfg.scheduler.workload_prompt_tokens_p90_budget, 120000)
        self.assertEqual(cfg.scheduler.long_context_token_threshold, 32000)
        self.assertEqual(cfg.scheduler.long_context_limit, 2)
        self.assertEqual(cfg.scheduler.abnormal_path_risk_threshold, 0.8)

    def test_optimizer_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            config_path.write_text(
                """
[optimizer]
enabled = true
mode = "advise"
strategy = "static_threshold"
high_utilization_ratio = 0.75
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(config_path=config_path)

        self.assertTrue(cfg.optimizer.enabled)
        self.assertEqual(cfg.optimizer.mode, "advise")
        self.assertEqual(cfg.optimizer.strategy, "static_threshold")
        self.assertEqual(cfg.optimizer.high_utilization_ratio, 0.75)

    def test_task_state_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            config_path.write_text(
                """
[task_state]
enabled = true
normal_progress_threshold = 0.6
stalled_progress_threshold = 0.2
low_progress_patience = 4
repeat_tool_window = 5
high_context_growth_tokens = 4096
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(config_path=config_path)

        self.assertTrue(cfg.task_state.enabled)
        self.assertEqual(cfg.task_state.normal_progress_threshold, 0.6)
        self.assertEqual(cfg.task_state.stalled_progress_threshold, 0.2)
        self.assertEqual(cfg.task_state.low_progress_patience, 4)
        self.assertEqual(cfg.task_state.repeat_tool_window, 5)
        self.assertEqual(cfg.task_state.high_context_growth_tokens, 4096)

    def test_finalization_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            p2_events = Path(tmp) / "p2.events.jsonl"
            config_path.write_text(
                f"""
[finalization]
collector_join_timeout_seconds = 3.0
writer_close_timeout_seconds = 4.0
trajectory_wait_timeout_seconds = 1.5
trajectory_poll_interval_seconds = 0.05
trajectory_stable_checks = 3
p2_events_path = "{p2_events}"
p2_import_quality = "service_exclusive"
p2_window_margin_seconds = 0.25
p2_include_window_extras = false
p2_global_event_policy = "auto"
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(config_path=config_path)

        self.assertEqual(cfg.finalization.collector_join_timeout_seconds, 3.0)
        self.assertEqual(cfg.finalization.writer_close_timeout_seconds, 4.0)
        self.assertEqual(cfg.finalization.trajectory_wait_timeout_seconds, 1.5)
        self.assertEqual(cfg.finalization.trajectory_stable_checks, 3)
        self.assertEqual(cfg.finalization.p2_events_path, str(p2_events))
        self.assertEqual(cfg.finalization.p2_import_quality, "service_exclusive")

    def test_control_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agentmon.toml"
            config_path.write_text(
                """
[control]
enabled = true
mode = "advise"

[model_gate]
service_id = "local-vllm"
max_inflight_requests = 12
moderate_pressure_target = 6
high_pressure_target = 2
min_inflight_requests = 1

[tool_pool]
default_max_concurrent = 8
cpu_heavy_max_concurrent = 3
io_heavy_max_concurrent = 2
high_priority_max_concurrent = 1
nice_increment = 3
memory_limit_bytes = 1048576
cpu_affinity_cpus = ["0", "1"]
""".strip(),
                encoding="utf-8",
            )

            cfg = load_config(config_path=config_path)

        self.assertTrue(cfg.control.enabled)
        self.assertEqual(cfg.control.mode, "advise")
        self.assertEqual(cfg.model_gate.service_id, "local-vllm")
        self.assertEqual(cfg.model_gate.high_pressure_target, 2)
        self.assertEqual(cfg.tool_pool.cpu_heavy_max_concurrent, 3)
        self.assertEqual(cfg.tool_pool.nice_increment, 3)
        self.assertEqual(cfg.tool_pool.memory_limit_bytes, 1048576)
        self.assertEqual(cfg.tool_pool.cpu_affinity_cpus, ["0", "1"])
        self.assertIn("pytest", cfg.tool_pool.cpu_heavy_executables)
        self.assertIn("tar", cfg.tool_pool.io_heavy_executables)
