from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

from agentmon.server.quick_smoke import main as quick_smoke_main, resolve_smoke_tasks


class ServerQuickSmokeTests(unittest.TestCase):
    def test_smoke_task_is_discovered_from_nested_runtime_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = Path(tmp) / "benchmark.zip"
            write_nested_benchmark(benchmark, ["case-zeta", "case-alpha"])

            tasks, evidence = resolve_smoke_tasks([], str(benchmark))

            self.assertEqual(tasks, ["case-alpha"])
            self.assertEqual(evidence["status"], "discovered")
            self.assertEqual(evidence["available_task_count"], 2)

    def test_smoke_task_remains_unresolved_without_configuration_or_benchmark(self) -> None:
        tasks, evidence = resolve_smoke_tasks([], "")

        self.assertEqual(tasks, [])
        self.assertEqual(evidence["status"], "unresolved")

    def test_prepare_synthesizes_self_contained_runtime_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "agentmon-src"
            (source / "examples").mkdir(parents=True)
            (source / "src").mkdir()
            (source / "examples" / "config.target.toml").write_text(
                "\n".join(
                    [
                        "[vllm]",
                        'api_base = "http://127.0.0.1:8000/v1"',
                        'models_url = "http://127.0.0.1:8000/v1/models"',
                        'metrics_url = "http://127.0.0.1:8000/metrics"',
                        'model_name = "old-model"',
                        "",
                        "[model]",
                        'path = "/old/model"',
                        "max_model_len = 8192",
                        "tensor_parallel_size = 1",
                        "",
                        "[ascend]",
                        'device_ids = "auto"',
                        "",
                        "[finalization]",
                        'p2_events_path = ""',
                    ]
                ),
                encoding="utf-8",
            )
            mini_config = root / "mini.yaml"
            mini_config.write_text(
                'model_kwargs:\n  api_base: "http://127.0.0.1:8000/v1"\n',
                encoding="utf-8",
            )
            profile = write_profile(root, mini_config)
            request = root / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "experiment": {
                            "name": "Qwen3 2-card agent smoke",
                            "intent": "run agent benchmark smoke",
                        },
                        "source": {"repo": str(source)},
                        "workload": {
                            "benchmark_zip": "/data/node0_disk3/chenyang/ver_0730/dist.zip",
                            "smoke_tasks": ["py-001"],
                            "pilot_tasks": ["py-007", "py-036", "py-060"],
                        },
                        "resources": {
                            "cards": 2,
                            "preferred_devices": [0, 1],
                            "preferred_port": 8001,
                        },
                        "model": {
                            "served_model": "qwen3-coder-30b",
                            "model_path": "/data/model/qwen3_coder_30b",
                        },
                        "requirements": {
                            "agent_tool_calling": "required",
                            "prefix_cache": "disabled",
                            "startup_watchdog_seconds": 60,
                        },
                    }
                ),
                encoding="utf-8",
            )
            out = root / "out"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = quick_smoke_main(
                    [
                        "--request",
                        str(request),
                        "--server-profile",
                        str(profile),
                        "--output-dir",
                        str(out),
                        "--prefix-cache-args=--disable-prefix-cache",
                    ]
                )
            output = stdout.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("STEP=QUICK_SMOKE_PREPARE", output)
            self.assertIn("STATUS=PASS", output)
            self.assertIn("NEXT=RESOURCE_GATE", output)
            contract = json.loads((out / "quick-smoke-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["status"], "PASS")
            self.assertEqual(contract["stage_result"]["status"], "PASS")
            self.assertEqual(contract["stage_result"]["next"], "RESOURCE_GATE")
            self.assertEqual(contract["config_consistency"]["status"], "PASS")
            self.assertEqual(contract["synthesized"]["base_url"], "http://127.0.0.1:8001/v1")
            self.assertEqual(contract["synthesized"]["tool_call_parser"], "qwen3_coder")

            prepare = json.loads((out / "quick-smoke-prepare.json").read_text(encoding="utf-8"))
            self.assertEqual(prepare["stage_result"]["pass_condition"], "contract status PASS and blocking_reasons empty")
            self.assertIn("resource_gate", prepare["artifacts"])
            self.assertIn("service_start_gate", prepare["artifacts"])
            self.assertIn("tool_call_gate", prepare["artifacts"])

            start_script = (out / "start-vllm-p2.sh").read_text(encoding="utf-8")
            self.assertIn("--enable-auto-tool-choice", start_script)
            self.assertIn("--tool-call-parser qwen3_coder", start_script)
            self.assertIn("--disable-prefix-cache", start_script)
            self.assertIn("--port 8001", start_script)

            resource_gate = (out / "resource-gate.sh").read_text(encoding="utf-8")
            service_gate = (out / "service-start-gate.sh").read_text(encoding="utf-8")
            tool_gate = (out / "tool-call-gate.sh").read_text(encoding="utf-8")
            mini_probe = (out / "mini-swe-noninteractive-probe.sh").read_text(encoding="utf-8")
            watchdog = (out / "startup-watchdog.sh").read_text(encoding="utf-8")
            self.assertIn("STEP_NAME=RESOURCE_GATE", resource_gate)
            self.assertIn("STEP_NAME=SERVICE_START_GATE", service_gate)
            self.assertIn("step = 'TOOL_CALL_GATE'", tool_gate)
            self.assertIn("STEP_NAME=MINI_SWE_NONINTERACTIVE_GATE", mini_probe)
            self.assertIn("STEP_NAME=STARTUP_WATCHDOG_GATE", watchdog)

            mini_runtime = (out / "mini_model_runtime.yaml").read_text(encoding="utf-8")
            agentmon_runtime = (out / "agentmon_config_runtime.toml").read_text(encoding="utf-8")
            self.assertIn("http://127.0.0.1:8001/v1", mini_runtime)
            self.assertIn('api_base = "http://127.0.0.1:8001/v1"', agentmon_runtime)
            self.assertNotIn("http://127.0.0.1:8000/v1", mini_runtime)

    def test_prepare_keeps_unknown_prefix_cache_args_as_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "agentmon-src"
            (source / "examples").mkdir(parents=True)
            (source / "src").mkdir()
            profile = write_profile(root, None)
            request = root / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "experiment": {"name": "Needs Prefix Args"},
                        "source": {"repo": str(source)},
                        "resources": {"preferred_port": 8001, "preferred_devices": [0, 1]},
                        "model": {
                            "served_model": "qwen3-coder-30b",
                            "model_path": "/data/model/qwen3_coder_30b",
                        },
                        "requirements": {
                            "agent_tool_calling": "required",
                            "prefix_cache": "disabled",
                        },
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = quick_smoke_main(
                    [
                        "--request",
                        str(request),
                        "--server-profile",
                        str(profile),
                        "--output-dir",
                        str(root / "out"),
                    ]
                )
            output = stdout.getvalue()

            self.assertEqual(rc, 0)
            self.assertIn("STEP=QUICK_SMOKE_PREPARE", output)
            self.assertIn("STATUS=PARTIAL", output)
            self.assertIn("NEXT=STOP", output)
            self.assertIn("SUGGESTION=Rerun static preflight", output)
            contract = json.loads((root / "out" / "quick-smoke-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["status"], "PARTIAL")
            self.assertEqual(contract["stage_result"]["next"], "STOP")
            self.assertIn("prefix_cache_disable_args_unresolved", contract["blocking_reasons"])
            self.assertEqual(contract["discovered"]["prefix_cache"]["status"], "unresolved")


def write_profile(root: Path, mini_config: Path | None) -> Path:
    profile = root / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "name": "unit-profile",
                "environment": {
                    "AGENTMON_PY": sys.executable,
                    "VLLM_PY": sys.executable,
                    "VLLM_BIN": "/bin/echo",
                    "MINI_SWE_BIN": "/bin/echo",
                    "MINI_MODEL_CONFIG": str(mini_config) if mini_config else "",
                    "AGENTMON_HOME": str(root / "agentmon"),
                    "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
                    "CANN_ROOT": str(root / "cann"),
                    "MODEL_PATH": "/data/model/qwen3_coder_30b",
                    "SERVED_MODEL": "qwen3-coder-30b",
                    "VLLM_HOST": "127.0.0.1",
                    "VLLM_PORT": "8000",
                    "MAX_MODEL_LEN": "32768",
                },
                "preflight": {
                    "agentmon_config": "${AGENTMON_REPO}/examples/config.target.toml",
                    "expect_prefix_cache": "any",
                    "tool_call_parser_candidates": [
                        {
                            "model_name_contains": ["qwen3", "coder"],
                            "parser": "qwen3_coder",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return profile


def write_nested_benchmark(path: Path, task_ids: list[str]) -> None:
    nested_bytes = io.BytesIO()
    with tarfile.open(fileobj=nested_bytes, mode="w:gz") as archive:
        for task_id in task_ids:
            payload = json.dumps({"task_id": task_id}).encode("utf-8")
            member = tarfile.TarInfo(f"runtime/tasks/{task_id}/metadata.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    with zipfile.ZipFile(path, mode="w") as outer:
        outer.writestr("dist/runtime.tar.gz", nested_bytes.getvalue())


if __name__ == "__main__":
    unittest.main()
